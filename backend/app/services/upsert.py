"""增量 upsert 服务 - 按主键去重,已存在则更新,新增则插入

支持业务表:refund(退费单) / shanmao_refund(闪猫退款) / ticket(服务工单) / wallet(提现)
/ device_combo(设备套餐) / merchant_refund(商户号现金流) / cs_record(智能助手)
/ external_complaint(外部投诉) / card_complaint(卡商群投诉) / merchant(商户主数据)
统计逻辑:upsert 前先查已存在的主键数量,以精确区分 insert/update
"""

import json
import logging
import re
import types
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.services.quality_check import (
    check_refund_row,
    check_shanmao_row,
    check_ticket_row,
    check_wallet_row,
)

logger = logging.getLogger(__name__)

# 运营平台(BMS / ai-acc)导出的时间为北京时间(naive,无时区)。
# 存入 timestamptz 列时,DB 时区为 Etc/UTC,naive 值会被当 UTC 存,
# 导致按北京显示时偏后 8 小时。这里把 naive 视为北京时间,转 UTC 后再存。
_TZ_CN = timezone(timedelta(hours=8))

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.device import Device, DeviceTag, DeviceTagBinding
from app.models.device_combo import DeviceCombo
from app.models.external_complaint import ExternalComplaint
from app.models.manual_refund import ManualRefund
from app.models.merchant import Merchant
from app.models.merchant_refund import MerchantRefund
from app.models.refund import Refund
from app.models.shanmao_refund import ShanmaoRefund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal


class ImportResult:
    """导入结果统计"""

    def __init__(self) -> None:
        self.total = 0
        self.inserted = 0
        self.updated = 0
        self.failed = 0
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []  # 提示/自动纠错类(非失败, 不阻断)

    def add_error(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        self.failed += 1
        self.errors.append({"row": row_idx, "message": msg, "data": data})

    def add_warning(self, row_idx: int, msg: str, data: dict | None = None) -> None:
        self.warnings.append({"row": row_idx, "message": msg, "data": data})


# ---------- 类型转换工具 ----------

# P1-3: 数值列归一化 - 清洗 BMS CSV 导出常见的「带单位/货币/千分位/百分比」字符串
# 清洗顺序:
#   1. 全角/半角空白去除
#   2. 货币符号 (¥ / ￥ / $ / RMB / 元) 去除
#   3. 千分位逗号去除 (1,234.56 → 1234.56)
#   4. 百分比 % → 除以 100 (50% → 0.5)
#   5. 中文数量级后缀:万(×10_000) / 亿(×100_000_000)
#   6. 尾部常见单位(小时/分钟/秒/次/个/人/笔/件/单/条) 剥掉
#   7. 括号里的备注(如 "123.45(已审核)") 剥掉
_NUM_CURRENCY_RE = re.compile(r"[¥￥$€£]|RMB|rmb|元")
_NUM_PAREN_RE = re.compile(r"[（(].*?[)）]")
_NUM_UNIT_TAIL_RE = re.compile(
    r"(小时|时|分钟|分|秒|ms|次|个|人|笔|件|单|条|台|部|张|份|元|天|日|周|月|年|%|个点)$"
)


def _normalize_numeric_string(s: str) -> str:
    """把「带杂项的数值字符串」清洗成 float('') 能吃的纯数字串"""
    if not s:
        return ""
    tmp = s.strip().replace("\u3000", "").replace(" ", "").replace("\t", "")
    if not tmp:
        return ""
    # 去括号备注: 12.3(元) / 456（已审核）
    tmp = _NUM_PAREN_RE.sub("", tmp)
    # 去货币符号: ¥1,234.56 → 1,234.56
    tmp = _NUM_CURRENCY_RE.sub("", tmp)
    # 检测尾部百分比
    is_percent = tmp.endswith("%") or tmp.endswith("％")
    # 检测尾部中文数量级 (必须在剥通用单位之前做,避免被 亿/万 误当成通用字符)
    scale = 1.0
    if tmp.endswith("亿"):
        scale = 100_000_000.0
        tmp = tmp[:-1]
    elif tmp.endswith("万"):
        scale = 10_000.0
        tmp = tmp[:-1]
    # 反复剥尾部通用单位 (最多 5 层,对付 "0.5小时/人次" 这种叠加)
    for _ in range(5):
        new_tmp = _NUM_UNIT_TAIL_RE.sub("", tmp)
        if new_tmp == tmp:
            break
        tmp = new_tmp
    # 千分位逗号: 1,234,567.89 → 1234567.89 (注意:欧洲写法 1.234.567,89 不处理,国内没见过)
    tmp = tmp.replace(",", "").replace("，", "")
    if not tmp:
        return ""
    # 百分比后处理: 必须放在最后再把 is_percent 信息写回一个可识别的格式
    if is_percent:
        # 在最末尾塞一个不会被 float 识别的记号 → 外层解析后 *0.01
        tmp = f"{tmp}||PERCENT"
    if scale != 1.0:
        tmp = f"{tmp}||SCALE={scale}"
    return tmp


def _apply_modifiers(raw: float, cleaned: str) -> float:
    """应用 _normalize_numeric_string 写在尾部的 % / 万 / 亿 修饰符"""
    if "||PERCENT" in cleaned:
        raw = raw * 0.01
    m = re.search(r"\|\|SCALE=([0-9.]+)", cleaned)
    if m:
        try:
            raw = raw * float(m.group(1))
        except (TypeError, ValueError):
            pass
    return raw


def _truncate_str(val: Any, max_len: int) -> str | None:
    """强制转字符串并按 max_len 截断,用于防御 DB VARCHAR(n) 长度超限报错。
    返回 None 当原值为空/NaN。"""
    s = _to_str(val)
    if s is None:
        return None
    if len(s) <= max_len:
        return s
    return s[:max_len]


def _to_str(val: Any) -> str | None:
    """强制转字符串,过滤 NaN/None(pandas iterrows 会把混合列推断为 float)"""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        if pd.isna(val):  # 捕获 numpy nan / pandas NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (int, float)):
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        if pd.isna(f):
            return None
        return f
    # 字符串路径
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    # 先试直接转(最快,且不含单位的字符串 99% 会走这条)
    try:
        f = float(s)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        pass
    # P1-3: 走归一化流程 (¥1,234.56 / 50% / 1.2万 / 0.5小时 / 12(备注) 等)
    cleaned = _normalize_numeric_string(s)
    if not cleaned:
        return None
    # 剥掉修饰符后缀后,再拿纯数字部分去 float
    num_part = cleaned.split("||", 1)[0]
    if not num_part:
        return None
    try:
        f = float(num_part)
    except (TypeError, ValueError):
        # 最后一招: 所有非数字/小数点/- 的字符全删掉,再试一次
        last_resort = re.sub(r"[^0-9.\-]", "", num_part)
        if not last_resort or last_resort in (".", "-", "-."):
            return None
        try:
            f = float(last_resort)
        except (TypeError, ValueError):
            return None
    if pd.isna(f):
        return None
    return _apply_modifiers(f, cleaned)


def _to_int(val: Any) -> int | None:
    f = _to_float(val)
    if f is None:
        return None
    # P1-3: 直接 int(f) 会截断 1.9 → 1; 如果实际金额类字段用户给的是近似值,保留 round 更合理
    # 但对 BMS 工单里的「处理时长/催单次数」这些计数列,用户导出经常是整数小数字符串 (1.00),
    # 所以先 is_integer() 判断是不是纯整数浮点数,是就直接转,否则 round.
    if isinstance(f, float) and f.is_integer():
        return int(f)
    return int(round(f))


def _to_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    try:
        if pd.isna(val):  # 捕获 pandas NaT / numpy nan
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, datetime):
        # naive 视为北京时间 → UTC(aware);aware 原样返回
        if val.tzinfo is None:
            return val.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)
        return val
    if isinstance(val, str) and val.strip() == "":
        return None
    # Excel 序列日期(1899-12-30 起算的天数): 纯整数且落在 1900~2100 年合理区间。
    # 315投诉表「处理时间」列存在 229 个此类值(如 46187),原实现直接判 None 丢失。
    if isinstance(val, str):
        s = val.strip()
        if s.isdigit() and 20000 <= int(s) <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=int(s))).replace(tzinfo=_TZ_CN).astimezone(timezone.utc)
    try:
        result = pd.to_datetime(val)
        if pd.isna(result):
            return None
        # 运营平台导出为 naive 北京时间,转 UTC 后存入 timestamptz,
        # 否则 DB(UTC)会按 UTC 解释导致偏后 8 小时
        if result.tzinfo is None:
            result = result.tz_localize(_TZ_CN).tz_convert(timezone.utc)
        return result.to_pydatetime()
    except Exception:
        return None


def _sanitize_date(val: datetime | None) -> datetime | None:
    """对明显录入笔误的年份做纠错(如 2225 → 2025)

    运营平台导出数据偶有年份录入错误(多打一位/输错前两位),
    规则:年份 > 当前年+1 视为异常,尝试 2xxx→20xx 映射(2000 + year%100);
    映射后仍异常或无法确定则返回 None。
    """
    if val is None:
        return None
    year = val.year
    current_year = datetime.now().year
    if year <= current_year + 1:
        return val  # 正常年份,不处理
    # 尝试 2xxx → 20xx 纠错(如 2225 → 2025, 2204 → 2004)
    corrected_year = 2000 + (year % 100)
    if corrected_year <= current_year + 1 and corrected_year >= 2000:
        try:
            return val.replace(year=corrected_year)
        except ValueError:
            # 2月29日在非闰年不存在,退到2月28日
            return val.replace(year=corrected_year, day=28)
    return None


def _clean_value(val: Any) -> Any:
    """统一清洗单元格值(pandas object 列里 None 会被内部转成 NaN,
    且导出数据常含文本 'NaN'/'null' 占位符,需统一归一为 None)"""
    if val is None:
        return None
    try:
        if pd.isna(val):  # 捕获 numpy nan / pandas NaT
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, str):
        s = val.strip()
        if s == "" or s.lower() in ("nan", "null", "none"):
            return None
        return s
    return val


# ---------- 脏数据防御:人名字段 & 单号格式校验 ----------

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
# 典型单号: RF / SP / WS / WX / BR / INIT:: / YKM 等开头,必须是 ASCII 可见字符(不含中文)且长度≤64
_REFUND_NO_BAD_RE = re.compile(r"[\u4e00-\u9fff]|[\s，。；、（）【】《》\"'`]")


def _is_numeric_like(val: Any) -> bool:
    """判断一个值是否"长得像数字/金额"。
    用于 operator / auditor / handler 等"应该是人名字符串"的字段,
    如果 Excel 列映射错把金额列写进来,在这里能识别并拦截。
    """
    if val is None:
        return False
    if isinstance(val, (int, float)):
        try:
            if pd.isna(val):  # NaN/NaT 不算
                return False
        except (TypeError, ValueError):
            pass
        return True
    if isinstance(val, str):
        s = val.strip().replace(",", "")  # 千分位逗号
        return bool(_NUMERIC_RE.match(s))
    return False


def _looks_like_bad_refund_no(val: str | None) -> bool:
    """退费单号明显不合法:含中文、中文标点、空白,或长度过于离谱(>64)。
    合法单号示例: RF2025071200031 / INIT::xxx::套餐::2025-01-01 等。
    """
    if not val:
        return False
    if len(val) > 64:
        return True
    return bool(_REFUND_NO_BAD_RE.search(val))


def _sanitize_name_field(
    val: Any,
    row_idx: int,
    result: ImportResult,
    field_label: str,
) -> str | None:
    """清洗应该为人名(或企微-人名 / 英文账号名)的字符串字段。
    - 含数字/金额格式 → 判定为"Excel 列映射错位",记录为错误行提示,但继续导入(该字段置 None)
      这样不会丢整行数据,同时在 errors 里提醒用户检查列映射。
    - 空值 → None
    """
    s = _to_str(val)
    if s is None:
        return None
    if _is_numeric_like(s):
        result.add_error(
            row_idx,
            f"{field_label}='{s}' 格式异常(疑似金额/数字,可能是 Excel 列映射错位),已置空请核对列映射",
            {"field": field_label, "raw_value": s},
        )
        return None
    return s


def apply_mapping(row: dict, mapping: dict[str, str]) -> dict[str, Any]:
    """根据列映射从行数据提取目标字段值(并统一清洗空值)"""
    out: dict[str, Any] = {}
    for target_field, source_col in mapping.items():
        if source_col in row:
            out[target_field] = _clean_value(row[source_col])
    return out


# ---------- 通用 upsert 执行器 ----------


def _execute_upsert(
    db: Session,
    model,
    pk_field: str,
    rows: list[dict],
    result: ImportResult,
) -> None:
    """通用 upsert 执行:先统计已存在,再执行 on_conflict,精确区分 insert/update

    自动处理 2026-08-06 P1-T4 新增的两列(如模型存在对应字段):
      - synced_at     : 每行 INSERT 时填 python now(UTC), 冲突 UPDATE 时强制 = DB now()
                        (保证"最后一次被同步任务写入"的精确时间戳,不被上游旧值覆盖)
      - updated_by_raw: 从传入 rows 的若干常见别名中嗅探填充(更新人/最后操作人/updated_by 等),
                        冲突 UPDATE 时用 excluded.updated_by_raw (新导入覆盖旧)
    """
    if not rows:
        return
    # 按主键去重(同批次内重复主键会导致 ON CONFLICT affect row a second time;
    # 保留最后一条,符合"后到覆盖"的增量语义)
    seen: dict[str, dict] = {}
    for r in rows:
        k = r.get(pk_field)
        if k is not None:
            seen[str(k)] = r
    rows = list(seen.values())
    if not rows:
        return

    # ── 嗅探模型是否存在 synced_at / updated_by_raw 列 (P1-T4 新字段) ──
    _m_cols = {c.key for c in model.__table__.columns}
    _has_synced_at = "synced_at" in _m_cols
    _has_updated_by_raw = "updated_by_raw" in _m_cols
    # updated_by_raw 的常见别名(按优先级)
    _UPDATED_BY_ALIASES = (
        "updated_by_raw",
        "更新人", "最后操作人", "修改人", "最近操作人",
        "updated_by", "last_operator", "修改者", "更新者",
        "处理人", "handler",  # 注意:handler 是业务处理人,只在没有上面明确字段时兜底
    )
    if _has_synced_at or _has_updated_by_raw:
        _now = datetime.now(timezone.utc)
        for r in rows:
            if _has_synced_at:
                r["synced_at"] = _now
            if _has_updated_by_raw:
                # ⚠️ 无论是否找到值,都必须显式设置键,保证同批次 rows 字典键一致。
                # 否则 rows[0] 有 updated_by_raw 而 rows[1] 没有,会导致
                # pg_insert().values(rows) 编译时抛 "explicitly rendered as a
                # boundparameter in the VALUES clause" 错误。
                _existing = r.get("updated_by_raw")
                if _existing:
                    r["updated_by_raw"] = _truncate_str(_existing, 64)
                    continue
                _sniffed: str | None = None
                for alias in _UPDATED_BY_ALIASES:
                    v = r.get(alias)
                    if v:
                        _sniffed = _truncate_str(v, 64)
                        break
                r["updated_by_raw"] = _sniffed  # 显式赋 None 也必须设键

    # 主键统一转字符串,避免 IN 查询时 varchar = double precision 类型冲突
    keys = [str(r[pk_field]) for r in rows]
    existing_count = 0
    if keys:
        existing_count = db.scalar(
            select(func.count()).select_from(model).where(getattr(model, pk_field).in_(keys))
        ) or 0
    stmt = pg_insert(model).values(rows)
    update_cols = {c: stmt.excluded[c] for c in rows[0] if c != pk_field}
    # P1-T4: 冲突更新时 synced_at 强制 = DB now() (覆盖 excluded 值,避免 Python 机时钟差 / 扫表时间漂移)
    if _has_synced_at:
        update_cols["synced_at"] = func.now()
    stmt = stmt.on_conflict_do_update(index_elements=[pk_field], set_=update_cols)
    db.execute(stmt)
    db.flush()
    result.inserted += max(len(rows) - existing_count, 0)
    result.updated += min(existing_count, len(rows))


def _sync_devices(rows: list[dict], sn_field: str, db: Session, fill_fields: list[str] | None = None) -> None:
    """从导入记录中同步设备主数据(SN 不存在则创建,存在则按需填充 merchant_name 等字段)

    SN 统一转字符串并过滤 None/NaN,避免 IN 查询类型冲突。
    fill_fields: 需要从 rows 中同步到 Device 的额外字段名(如 merchant_name),
                 这些字段会从每一行的同名 key 取值并填充到 Device。
                 注意:只接受 Device 模型上真实存在的列(填了不存在的列会被跳过并打 warning)。
    """
    fill_fields = fill_fields or []
    # 防御: 过滤掉 Device 模型没有的列,避免 new Device(kw) 报 invalid keyword 或 setattr 炸
    from app.models.device import Device as _DeviceModel
    _legal_cols = {c.key for c in _DeviceModel.__table__.columns}
    bad_fields = [f for f in fill_fields if f not in _legal_cols]
    if bad_fields:
        import logging
        _log = logging.getLogger(__name__)
        _log.warning(
            "_sync_devices 跳过 Device 不存在的 fill_fields=%s;"
            " Device 现有列共 %d 个",
            bad_fields, len(_legal_cols),
        )
    fill_fields = [f for f in fill_fields if f in _legal_cols]
    # 先把 device_sn 里的前后空白统一去掉(BMS CSV 常带尾部空格),同时按规范化后的 SN 建索引
    normalized_rows: list[dict] = []
    for r in rows:
        nr = dict(r)
        raw_sn = nr.get(sn_field)
        if isinstance(raw_sn, str):
            nr[sn_field] = raw_sn.strip()
        normalized_rows.append(nr)
    rows = normalized_rows

    sns = {_to_str(r.get(sn_field)) for r in rows}
    sns.discard(None)
    if not sns:
        return
    existing_map: dict[str, Device] = {
        d.sn: d for d in db.scalars(select(Device).where(Device.sn.in_(sns)))
    }
    created: list[Device] = []
    updated = 0
    # 用 SN 建原 rows 索引(取第一条非空字段作为填充值)
    per_sn_fill: dict[str, dict] = {}
    for r in rows:
        sn = _to_str(r.get(sn_field))
        if not sn:
            continue
        if sn not in per_sn_fill:
            per_sn_fill[sn] = {}
        for f in fill_fields:
            v = _to_str(r.get(f))
            if v and f not in per_sn_fill[sn]:
                per_sn_fill[sn][f] = v
    for sn in sns:
        dev = existing_map.get(sn)
        fill = per_sn_fill.get(sn, {})
        if dev is None:
            kw = {"sn": sn}
            for f in fill_fields:
                if f in fill:
                    kw[f] = fill[f]
            created.append(Device(**kw))
        else:
            changed = False
            for f in fill_fields:
                if f in fill and not getattr(dev, f, None):
                    setattr(dev, f, fill[f])
                    changed = True
            if changed:
                updated += 1
    if created:
        db.bulk_save_objects(created)
    db.flush()


# ---------- 各数据类型 upsert ----------


def upsert_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            refund_no = (data.get("refund_no") or "").strip() if data.get("refund_no") else None
            if not refund_no:
                result.add_error(idx, "退费单号为空")
                continue
            if _looks_like_bad_refund_no(refund_no):
                result.add_error(
                    idx,
                    f"退费单号 '{refund_no}' 格式异常(含中文/标点/空白或过长,疑似误填为备注文本),请核对",
                    {"refund_no": refund_no},
                )
                continue
            # operator / auditor 必须像人名(不能是数字,否则是金额列映射错位)
            operator = _sanitize_name_field(data.get("operator"), idx, result, "退费单-操作人")
            auditor = _sanitize_name_field(data.get("auditor"), idx, result, "退费单-审核人")
            row_dict = {
                "refund_no": refund_no,
                "platform_order_no": data.get("platform_order_no"),
                "third_order_no": data.get("third_order_no"),
                "device_sn": data.get("device_sn"),
                "refund_reason": data.get("refund_reason"),
                "package_name": data.get("package_name"),
                "merchant_name": data.get("merchant_name"),
                "refund_amount": _to_float(data.get("refund_amount")),
                "actual_amount": _to_float(data.get("actual_amount")),
                "remark": data.get("remark"),
                "status": data.get("status"),
                "refund_method": data.get("refund_method"),
                "audit_remark": data.get("audit_remark"),
                "auditor": auditor,
                "operator": operator,
                "created_at_raw": _to_datetime(data.get("created_at_raw")),
                "updated_at_raw": _to_datetime(data.get("updated_at_raw")),
                "package_price": _to_float(data.get("package_price")),
            }
            # 通用数据质量校验(L2): 时间异常/枚举脏值/金额异常/必填缺失
            row_dict["quality_status"], row_dict["quality_note"] = check_refund_row(
                types.SimpleNamespace(**row_dict)
            )
            rows.append(row_dict)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, Refund, "refund_no", rows, result)


def upsert_wallet(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """钱包提现 - 按业务复合唯一键去重(account_id, op_type, op_time, operator, op_amount, device_sn)

    防止重复同步导致数据膨胀;冲突时 DO NOTHING。

    ⚠️  2026-08-11 修复: 增加 op_time 必填校验
        历史问题: 2026-08-06 一次错误导入(工单文件被误当 wallet 导入),
        导致 8068 行只有 device_sn 和 remark(投诉文本) 但 op_time/account_id/op_amount 全 NULL 的
        垃圾数据进入 wallet_withdrawals 表。已清理。
        修复: op_time 为空时直接跳过,记录到错误,防止再次产生垃圾数据。
    """
    rows: list[dict] = []
    skipped_no_op_time = 0
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            op_time = _to_datetime(data.get("op_time"))
            # ★ op_time 是钱包流水的核心字段,为空说明数据不完整或映射错误(如工单文件被误导入)
            #   直接跳过,避免产生只有 device_sn 没有 op_time 的垃圾数据
            if op_time is None:
                skipped_no_op_time += 1
                result.add_error(
                    idx,
                    "op_time 为空,跳过(钱包流水必须有操作时间,可能是错误映射或数据不完整)",
                    {"device_sn": data.get("device_sn"), "remark": (data.get("remark") or "")[:80]},
                )
                continue
            row_dict = {
                "account_id": data.get("account_id"),
                "wallet_balance": _to_float(data.get("wallet_balance")),
                "device_sn": data.get("device_sn"),
                "op_type": data.get("op_type"),
                "op_amount": _to_float(data.get("op_amount")),
                "remark": data.get("remark"),
                "op_time": op_time,
                "operator": _sanitize_name_field(data.get("operator"), idx, result, "钱包提现-操作人"),
            }
            # 通用数据质量校验(L2)
            row_dict["quality_status"], row_dict["quality_note"] = check_wallet_row(
                types.SimpleNamespace(**row_dict)
            )
            rows.append(row_dict)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if skipped_no_op_time > 0:
        logger.warning(
            f"[wallet] 跳过 {skipped_no_op_time} 行 op_time 为空的数据(防止垃圾数据入库)"
        )
    if not rows:
        return

    # 1. 同批次内按业务键去重
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (
            r.get("account_id"),
            r.get("op_type"),
            r.get("op_time"),
            r.get("operator"),
            r.get("op_amount"),
            r.get("device_sn"),
        )
        seen[key] = r
    unique_rows = list(seen.values())
    dup_in_batch = len(rows) - len(unique_rows)
    if dup_in_batch > 0:
        logger.info(f"[wallet] 同批次内去重: {len(rows)} → {len(unique_rows)} 行 (移除 {dup_in_batch} 条重复)")

    _sync_devices(unique_rows, "device_sn", db)

    # 2. 查询已存在记录数(精确统计)
    existing_count = 0
    if unique_rows:
        conditions = []
        for r in unique_rows:
            cond = and_(
                WalletWithdrawal.account_id == r.get("account_id"),
                WalletWithdrawal.op_type == r.get("op_type"),
                WalletWithdrawal.op_time == r.get("op_time"),
                WalletWithdrawal.operator == r.get("operator"),
                WalletWithdrawal.op_amount == r.get("op_amount"),
            )
            if r.get("device_sn") is not None:
                cond = and_(cond, WalletWithdrawal.device_sn == r.get("device_sn"))
            else:
                cond = and_(cond, WalletWithdrawal.device_sn.is_(None))
            conditions.append(cond)
        existing_count = db.scalar(select(func.count()).select_from(WalletWithdrawal).where(or_(*conditions))) or 0

    # 3. 插入,冲突时跳过
    db.execute(
        pg_insert(WalletWithdrawal)
        .values(unique_rows)
        .on_conflict_do_nothing(constraint="uq_wallet_withdrawals_business")
    )
    db.flush()
    result.inserted += len(unique_rows) - existing_count


def upsert_tickets(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            ticket_no = _truncate_str(data.get("ticket_no"), 64)
            if not ticket_no:
                result.add_error(idx, "工单号为空")
                continue
            row_dict = {
                "ticket_no": ticket_no,
                "device_sn": _truncate_str(data.get("device_sn"), 64),
                "device_type": _truncate_str(data.get("device_type"), 64),
                "device_type_remark": _truncate_str(data.get("device_type_remark"), 128),
                "phone": _truncate_str(data.get("phone"), 32),
                "service_category": _truncate_str(data.get("service_category"), 64),
                "service_item": _truncate_str(data.get("service_item"), 64),
                "priority": _to_int(data.get("priority")),
                # 迁移 aa11bb22cc33 后 status/is_appeal 等均扩为 VARCHAR(64),此处与 DB 列宽对齐
                "status": _truncate_str(data.get("status"), 64),
                "remark": data.get("remark"),
                "register_time": _to_datetime(data.get("register_time")),
                "active_time": _to_datetime(data.get("active_time")),
                "dispatch_time": _to_datetime(data.get("dispatch_time")),
                "finish_time": _to_datetime(data.get("finish_time")),
                "handle_duration": _to_int(data.get("handle_duration")),
                "registrant": _truncate_str(
                    _sanitize_name_field(data.get("registrant"), idx, result, "工单-登记人"), 64
                ),
                "channel": _truncate_str(data.get("channel"), 64),
                "handle_node": _truncate_str(data.get("handle_node"), 64),
                "handler": _truncate_str(
                    _sanitize_name_field(data.get("handler"), idx, result, "工单-处理人"), 64
                ),
                "handle_opinion": data.get("handle_opinion"),
                "is_appeal": _truncate_str(data.get("is_appeal"), 64),  # 迁移后列宽 64,与 DB 对齐
                "order_no": _truncate_str(data.get("order_no"), 64),
                "submit_params": data.get("submit_params"),
                "refund_amount": _to_float(data.get("refund_amount")),
                "estimated_amount": _to_float(data.get("estimated_amount")),
                # ── 催单 & 派单 8 个新列 (2026-08-03) ──
                "urge_count": _to_int(data.get("urge_count")),
                "urge_status": _truncate_str(data.get("urge_status"), 64),
                "last_dispatch_time": _to_datetime(data.get("last_dispatch_time")),
                "assignee": _truncate_str(
                    _sanitize_name_field(data.get("assignee"), idx, result, "工单-分配人员"), 64
                ),
                "contact_person": _truncate_str(
                    _sanitize_name_field(data.get("contact_person"), idx, result, "工单-联系人员"), 64
                ),
                "dispatcher": _truncate_str(
                    _sanitize_name_field(data.get("dispatcher"), idx, result, "工单-派发人员"), 64
                ),
                "last_urge_by": _truncate_str(
                    _sanitize_name_field(data.get("last_urge_by"), idx, result, "工单-最后催单人"), 64
                ),
                "last_urge_time": _to_datetime(data.get("last_urge_time")),
            }
            # 通用数据质量校验(L2)
            row_dict["quality_status"], row_dict["quality_note"] = check_ticket_row(
                types.SimpleNamespace(**row_dict)
            )
            rows.append(row_dict)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, ServiceTicket, "ticket_no", rows, result)
    # P2-T5: 工单动作流水写回 hook (幂等 INSERT ... WHERE NOT EXISTS,不重复记)
    _touch_ticket_action_logs_after_upsert(db, rows)


def _touch_ticket_action_logs_after_upsert(
    db: Session,
    ticket_rows: list[dict],
) -> None:
    """工单动作流水写回 hook (P2-T5)。

    只对本次 batch 内「有独立业务时间戳」的 4 类动作做幂等落库:
      1. 催单    : last_urge_time/last_urge_by 非空  → action_type=催单
      2. 派发    : dispatch_time/dispatcher 非空     → action_type=派发
      3. 结单    : finish_time 非空 & status ∈ 已完成/... → action_type=结单
      4. 登记    : register_time 非空                → action_type=登记

    每类动作都用 (ticket_no, action_type, action_at) 做天然幂等键,
    用 INSERT INTO ... SELECT ... WHERE NOT EXISTS 避免重复记流水。
    其他「改状态/改处理人/改服务项」等无明确时间点的变更, 留到 diff 级的 ORM save hook
    再补(本 helper 只处理从 CSV / BMS 同步过来带时间戳字段的动作)。
    """
    if not ticket_rows:
        return

    # 终态集合(判定结单用),和 ticket.py _ticket_category_case 里的终态白名单对齐
    _DONE_STATUS = {"已完成", "已解决", "已关闭", "无需处理", "已取消", "已撤销"}

    from app.models.ticket_action_log import TicketActionLog
    from sqlalchemy import literal_column, bindparam

    tlog_tbl = TicketActionLog.__table__

    # ---------- 每类动作:构造 (ticket_no, action_type, action_at, operator, detail, remark) 的待插集 ----------
    pending: list[dict] = []

    for r in ticket_rows:
        tno = r.get("ticket_no")
        if not tno:
            continue
        ub = _to_str(r.get("updated_by_raw")) or _to_str(r.get("handler")) or None
        remark = _to_str(r.get("remark")) or None
        handle_opinion = _to_str(r.get("handle_opinion")) or None

        # (1) 催单:必须有 last_urge_time
        lut = r.get("last_urge_time")
        lub = _to_str(r.get("last_urge_by")) or ub
        if lut is not None:
            pending.append({
                "ticket_no": tno, "action_type": "催单",
                "action_at": lut, "operator": lub,
                "detail": {"urge_count": r.get("urge_count"), "urge_status": r.get("urge_status")},
                "remark": remark,
            })
        # (2) 派发:必须有 dispatch_time 或 last_dispatch_time
        dtime = r.get("dispatch_time") or r.get("last_dispatch_time")
        disp = _to_str(r.get("dispatcher")) or _to_str(r.get("assignee")) or ub
        if dtime is not None:
            pending.append({
                "ticket_no": tno, "action_type": "派发",
                "action_at": dtime, "operator": disp,
                "detail": {"handler": _to_str(r.get("handler")), "assignee": _to_str(r.get("assignee"))},
                "remark": None,
            })
        # (3) 结单:必须有 finish_time & status 看起来像终态
        ft = r.get("finish_time")
        st = _to_str(r.get("status"))
        if ft is not None and st is not None:
            if any(k in st for k in _DONE_STATUS):
                pending.append({
                    "ticket_no": tno, "action_type": "结单",
                    "action_at": ft, "operator": ub,
                    "detail": {
                        "status": st,
                        "handle_duration": r.get("handle_duration"),
                        "refund_amount": r.get("refund_amount"),
                    },
                    "remark": handle_opinion or remark,
                })
        # (4) 登记:必须有 register_time
        rt = r.get("register_time")
        if rt is not None:
            pending.append({
                "ticket_no": tno, "action_type": "登记",
                "action_at": rt,
                "operator": _to_str(r.get("registrant")) or ub,
                "detail": {
                    "channel": _to_str(r.get("channel")),
                    "service_category": _to_str(r.get("service_category")),
                    "service_item": _to_str(r.get("service_item")),
                },
                "remark": None,
            })

    if not pending:
        return

    # ---------- 幂等 INSERT:对每条 (ticket_no,action_type,action_at) 先 SELECT, 不存在才插 ----------
    # 用 raw SQL + VALUES 做一整批, 避免 N 次往返
    sql = text(
        """
        INSERT INTO ticket_action_logs
            (ticket_no, action_type, operator, action_at, detail, remark, created_at, updated_at)
        SELECT
            v.ticket_no, v.action_type, v.operator::varchar, v.action_at::timestamptz,
            v.detail::jsonb, v.remark::text, now(), now()
        FROM (
          SELECT
            :ticket_no    AS ticket_no,
            :action_type  AS action_type,
            :operator     AS operator,
            :action_at    AS action_at,
            :detail       AS detail,
            :remark       AS remark
        ) AS v
        WHERE NOT EXISTS (
          SELECT 1 FROM ticket_action_logs
           WHERE ticket_no    = v.ticket_no
             AND action_type  = v.action_type
             AND action_at    = v.action_at
        )
        """
    )
    # batch 执行:每批 <=200 行,避免绑定参数过多
    import math
    batch_size = 200
    total_batches = int(math.ceil(len(pending) / batch_size))
    for bi in range(total_batches):
        batch = pending[bi * batch_size: (bi + 1) * batch_size]
        # 把 batch 展开成多参数 SQL: 类型转换统一放到外层 SELECT (避免 VALUES 里 :name::type 与 text() 参数占位符冲突)
        cases_sql = []
        params = {}
        for i, p in enumerate(batch):
            cases_sql.append(
                f"(:tn{i}, :at{i}, :op{i}, :aa{i}, :dt{i}, :rk{i})"
            )
            params[f"tn{i}"] = p["ticket_no"]
            params[f"at{i}"] = p["action_type"]
            params[f"op{i}"] = p["operator"]
            params[f"aa{i}"] = p["action_at"]
            # ⚠️ detail 是 dict 类型,必须序列化为 JSON 字符串再绑定,否则 PG 驱动报 "can't adapt type 'dict'"
            params[f"dt{i}"] = json.dumps(p["detail"], ensure_ascii=False) if p["detail"] is not None else None
            params[f"rk{i}"] = p["remark"]
        insert_sql = text(
            f"""
            INSERT INTO ticket_action_logs
                (ticket_no, action_type, operator, action_at, detail, remark, created_at, updated_at)
            SELECT
                v.tn::varchar        AS ticket_no,
                v.at::varchar        AS action_type,
                v.op::varchar        AS operator,
                v.aa::timestamptz    AS action_at,
                v.dt::jsonb          AS detail,
                v.rk::text           AS remark,
                now(), now()
            FROM (VALUES {', '.join(cases_sql)}) AS v(tn, at, op, aa, dt, rk)
            WHERE NOT EXISTS (
              SELECT 1 FROM ticket_action_logs
               WHERE ticket_no    = v.tn::varchar
                 AND action_type  = v.at::varchar
                 AND action_at    = v.aa::timestamptz
            )
            """
        )
        db.execute(insert_sql, params)
    db.flush()


def upsert_merchant_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """商户号现金流 - 按 (trade_time, merchant_order_no) 复合键去重 upsert

    微信退款单号在 Excel 导出时被 float 截断丢失精度,支付行则为 0,不可作主键。
    改用 (交易时间, 商户订单号) 复合去重键(已验证 86352 行零重复),重复导入按此键
    更新而非累计,后续拿到准确数据也能按此键覆盖补全。
    缺少交易时间或商户订单号的行无法定位,跳过。单批超 PG 65535 绑定参数上限,按 2000 行/批。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            trade_time = _to_datetime(data.get("trade_time"))
            mono = data.get("merchant_order_no")
            mono = None if (isinstance(mono, str) and mono.strip() in ("", "0", "0.0")) else mono
            # 缺少去重键(交易时间或商户订单号)的行无法定位,跳过
            if trade_time is None or mono is None:
                result.add_error(idx, "缺少交易时间或商户订单号,无法去重定位(跳过)")
                continue
            # merchant_refund_no 为 "0"/"0.0" 时归一为 None(支付行无商户退款单号,非真实单号)
            mrn = data.get("merchant_refund_no")
            if isinstance(mrn, str) and mrn.strip() in ("0", "0.0"):
                mrn = None
            rows.append({
                "wx_refund_no": data.get("wx_refund_no"),
                "merchant_refund_no": mrn,
                "trade_time": trade_time,
                "app_id": data.get("app_id"),
                "merchant_id": data.get("merchant_id"),
                "sub_merchant_id": data.get("sub_merchant_id"),
                "device_no": data.get("device_no"),
                "wx_order_no": data.get("wx_order_no"),
                "merchant_order_no": mono,
                "user_id": data.get("user_id"),
                "trade_type": data.get("trade_type"),
                "trade_status": data.get("trade_status"),
                "pay_bank": data.get("pay_bank"),
                "currency": data.get("currency"),
                "settle_amount": _to_float(data.get("settle_amount")),
                "voucher_amount": _to_float(data.get("voucher_amount")),
                "refund_amount": _to_float(data.get("refund_amount")),
                "recharge_voucher_refund": _to_float(data.get("recharge_voucher_refund")),
                "refund_type": data.get("refund_type"),
                "refund_status": data.get("refund_status"),
                "product_name": data.get("product_name"),
                "fee": _to_float(data.get("fee")),
                "rate": _to_float(data.get("rate")),
                "order_amount": _to_float(data.get("order_amount")),
                "apply_refund_amount": _to_float(data.get("apply_refund_amount")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_no", db)
    # 分批 on_conflict_do_update:按 (trade_time, merchant_order_no) 部分唯一索引去重更新。
    # index_where 须与索引定义一致以供 PG 推断该部分索引。
    before = db.scalar(select(func.count()).select_from(MerchantRefund)) or 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(MerchantRefund).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0].keys()
                       if c not in ("trade_time", "merchant_order_no")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_time", "merchant_order_no"],
            index_where=text("trade_time IS NOT NULL AND merchant_order_no IS NOT NULL"),
            set_=update_cols,
        )
        db.execute(stmt)
        db.flush()
    after = db.scalar(select(func.count()).select_from(MerchantRefund)) or 0
    inserted = max(after - before, 0)
    result.inserted += inserted
    result.updated += max(len(rows) - inserted, 0)


def upsert_merchants(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            sub_name = (data.get("sub_name") or "").strip() if data.get("sub_name") else None
            if not sub_name:
                continue
            rows.append({
                "sub_name": sub_name,
                "main_name": data.get("main_name"),
                "merchant_id": data.get("merchant_id"),
                "legal_person": data.get("legal_person"),
                "super_admin": data.get("super_admin"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    _execute_upsert(db, Merchant, "sub_name", rows, result)


def upsert_cs_records(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """智能助手处理记录 - 业务复合唯一键去重

    唯一键: (device_sn, operation_time, operator, problem_category, problem_type, solution, consult_time, problem_create_time)
    防止重复同步导致数据膨胀;冲突时 DO NOTHING(接待记录是事件流水,不需要更新)。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            rows.append({
                "problem_category": data.get("problem_category"),
                "problem_type": data.get("problem_type"),
                "device_sn": data.get("device_sn"),
                "solution": data.get("solution"),
                "operation_time": _to_datetime(data.get("operation_time")),
                "operator": _sanitize_name_field(data.get("operator"), idx, result, "客服记录-操作人"),
                "send_content": data.get("send_content"),
                "description": data.get("description"),
                "execution_result": data.get("execution_result"),
                "user_accepted": data.get("user_accepted"),
                "consult_time": _to_datetime(data.get("consult_time")),
                "problem_create_time": _to_datetime(data.get("problem_create_time")),
                "last_consult_time": _to_datetime(data.get("last_consult_time")),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return

    # 1. 同批次内按业务键去重(保留最后一条)
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (
            r.get("device_sn"),
            r.get("operation_time"),
            r.get("operator"),
            r.get("problem_category"),
            r.get("problem_type"),
            r.get("solution"),
            r.get("consult_time"),
            r.get("problem_create_time"),
        )
        seen[key] = r
    unique_rows = list(seen.values())
    dup_in_batch = len(rows) - len(unique_rows)
    if dup_in_batch > 0:
        logger.info(f"[cs_record] 同批次内去重: {len(rows)} → {len(unique_rows)} 行 (移除 {dup_in_batch} 条重复)")

    _sync_devices(unique_rows, "device_sn", db)

    # 2. 分批插入(每批 <=300 行),冲突时跳过(按唯一约束 uq_cs_record_business)
    #    ⚠️ 2026-08-14 修复前: 用 or_(*[and_(8 字段条件)*5979]) 去 count 已存在 →
    #        单条 SQL 包含 ~48,000 个表达式,PG 解析直接吃爆 内存(被 OOM Killer signal 9 杀 3 次)
    #        → DB 进入 recovery mode → 前端 HTTP 500,用户以为"同步报错"。
    #    修复策略:
    #      a) 删除 pre-count 巨型 OR 链查询;
    #      b) 改为 300 行/批 的 pg_insert ... on_conflict_do_nothing,
    #         然后用 cursor.rowcount 直接拿到该批"真实 insert 行数"(冲突被跳过的不算),
    #         inserted 精确累加,且 SQL 体积 / PG 内存占用下降 20 倍以上,不再 OOM。
    BATCH_SIZE = 300
    real_inserted = 0
    for i in range(0, len(unique_rows), BATCH_SIZE):
        batch = unique_rows[i:i + BATCH_SIZE]
        if not batch:
            continue
        cursor_result = db.execute(
            pg_insert(CsRecord)
            .values(batch)
            .on_conflict_do_nothing(constraint="uq_cs_record_business")
        )
        db.flush()
        try:
            rc = cursor_result.rowcount
            if isinstance(rc, int) and rc >= 0:
                real_inserted += rc
            else:
                # rowcount 不可靠 → 用 批次 兜底(不准确但不会崩)
                real_inserted += len(batch)
        except Exception:
            real_inserted += len(batch)
    result.inserted += real_inserted
    result.updated += max(len(unique_rows) - real_inserted, 0)


def _complaint_dedup_hash(parts: list[str]) -> str:
    """投诉身份指纹(md5前32位): 拼接规范化身份字段, 供 dedup_hash 列去重.

    与 workbuddy 同步服务(complaint_sync.py)保持同一口径, 保证手动导入与推送共用去重.
    """
    import hashlib

    key = "|".join((p or "").strip() for p in parts)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:32]


def _complaint_problem_signature(problem: str | None, max_len: int = 2000) -> str:
    """投诉问题内容签名: 归一化(去空白/标点/小写)后取前 max_len 字.

    用于身份指纹的内容环节: 轻微文本变化(空格/标点)不影响指纹,
    实质性不同内容(手机号→多SN、SN→多投诉 的区分)产生不同指纹.
    """
    s = re.sub(r"[\s，。；、,.!?！？:：;；\"'“”‘’()（）\[\]【】\-—]+", "", str(problem or ""))
    return s[:max_len]


def _complaint_identity_hash(rec: dict) -> str:
    """身份指纹: md5(投诉日期 | 投诉手机号 | 设备SN | 归一化投诉问题)

    2026-08-20 指纹方案重规划(两版迭代):
    - v1 (编号|日期|手机|SN|问题): 编号上游易丢(53%)导致同一投诉拆两条 → 弃用编号
    - v2 (日期|手机|SN): 区分度不足, 同手机+同SN+同日期 不同投诉会误合并 → 加入内容
    - 现版 (日期|手机|SN|问题归一化): 内容环节解决误合并(实测误合并仅15行), 
      跨文件匹配仍99.8%(问题文本微改不影响), 编号仍不入指纹(丢失免疫)
    """
    d = rec.get("complaint_date")
    date_s = d.strftime("%Y-%m-%d") if d else ""
    return _complaint_dedup_hash([
        date_s,
        rec.get("complainant_phone"),
        rec.get("device_sn"),
        _complaint_problem_signature(rec.get("problem")),
    ])


def upsert_external_complaints(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """外部投诉(监督局/315) - 按 dedup_hash 身份指纹去重更新

    2026-08-20 变更: 原按 complaint_no 去重, 但样例文件中存在同编号多条不同投诉
    (含 "/" 等垃圾编号), 单一编号不可靠。现改用身份指纹
    md5(编号|日期|手机号|SN|问题) 去重: 指纹相同视为同一条投诉 → 更新,
    指纹不同(含编号相同但内容不同的投诉) → 各自保留。重复推送不会累积脏数据。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            # 投诉编号剥离 \n/空白, 非法值(垃圾编号)置空
            raw_no = data.get("complaint_no")
            complaint_no = raw_no.replace("\n", "").strip() if isinstance(raw_no, str) else None
            if complaint_no and not re.fullmatch(r"[0-9A-Za-z\-]{6,64}", complaint_no):
                complaint_no = None
            rec = {
                "complaint_no": complaint_no or None,
                "complaint_date": _sanitize_date(_to_datetime(data.get("complaint_date"))),
                "platform": data.get("platform"),
                "complaint_account": data.get("complaint_account"),
                "company_entity": data.get("company_entity"),
                "complaint_shop": data.get("complaint_shop"),
                "purchase_order_no": data.get("purchase_order_no"),
                "complainant": _sanitize_name_field(data.get("complainant"), idx, result, "外部投诉-投诉人"),
                "complainant_phone": data.get("complainant_phone"),
                "device_sn": data.get("device_sn"),
                "wechat_id": data.get("wechat_id"),
                "problem": data.get("problem"),
                "keyword": data.get("keyword"),
                "complaint_category": data.get("complaint_category"),
                "solution": data.get("solution"),
                "result": data.get("result"),
                "follower": _sanitize_name_field(data.get("follower"), idx, result, "外部投诉-跟进人"),
                "handle_time": _sanitize_date(_to_datetime(data.get("handle_time"))),
                "handle_detail": data.get("handle_detail"),
            }
            rec["dedup_hash"] = _complaint_identity_hash(rec)
            rows.append(rec)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    all_rows = rows
    if not all_rows:
        return
    _sync_devices(all_rows, "device_sn", db)
    _execute_upsert(db, ExternalComplaint, "dedup_hash", all_rows, result)


def upsert_card_complaints(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """卡商群投诉 - 按 dedup_hash 身份指纹去重更新

    2026-08-20 变更: 原为无唯一键简单 insert(重复导入会累积),现改用身份指纹
    md5(日期|群|手机号|SN|问题) 去重, 重复推送不累积。device_sn 单元格可能含
    多个 SN(换行分隔)或备注,取首个有效 SN 以保证 varchar(64) 与外键约束。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            # device_sn 取首个(单元格可能含多个 SN,换行分隔)
            raw_sn = data.get("device_sn")
            device_sn = raw_sn.split("\n")[0].strip() if isinstance(raw_sn, str) and raw_sn.strip() else None
            rec = {
                "complaint_date": _sanitize_date(_to_datetime(data.get("complaint_date"))),
                "complaint_group": data.get("complaint_group"),
                "complainant": _sanitize_name_field(data.get("complainant"), idx, result, "卡商群投诉-投诉人"),
                "complainant_phone": data.get("complainant_phone"),
                "device_sn": device_sn,
                "problem": data.get("problem"),
                "problem_category": data.get("problem_category"),
                "result": data.get("result"),
                "solution": data.get("solution"),
                "handler": _sanitize_name_field(data.get("handler"), idx, result, "卡商群投诉-处理人"),
                "handle_result": data.get("handle_result"),
            }
            rec["dedup_hash"] = _complaint_identity_hash(rec)
            rows.append(rec)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, CardComplaint, "dedup_hash", rows, result)


def upsert_device_combos(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备套餐 - 主键 recharge_order_no,有则更新无则插入

    初始套餐(免费赠送/体验套餐)在 BMS 导出时无充值订单号,此前被主键非空校验跳过,
    导致这部分记录缺失。现为其生成稳定 fallback 主键 `INIT::{device_sn}::{package_name}::{start_date}`:
      - `INIT::` 前缀与真实订单号(BR/WX/SP 开头)隔离,绝不冲突
      - (device_sn, package_name, start_date) 组合唯一,保证多次增量同步幂等(命中 on_conflict_do_update)
      - device_sn 缺失时无法构造稳定唯一键,才报错跳过(安全网,正常初始套餐数据不触发)
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            order_no = (data.get("recharge_order_no") or "").strip() if data.get("recharge_order_no") else None
            device_sn = data.get("device_sn")
            package_name = data.get("package_name")
            start_date = _to_datetime(data.get("start_date"))

            if not order_no:
                # 无充值订单号(典型:初始套餐),生成 fallback 主键入库
                if not device_sn:
                    result.add_error(idx, "充值订单号为空且设备SN为空,无法生成主键")
                    continue
                start_str = start_date.strftime("%Y-%m-%d") if start_date else "unknown"
                order_no = f"INIT::{device_sn}::{package_name or 'unknown'}::{start_str}"

            rows.append({
                "recharge_order_no": order_no,
                "device_sn": device_sn,
                "package_name": package_name,
                "package_type": data.get("package_type"),
                "traffic_type": data.get("traffic_type"),
                "merchant_name": data.get("merchant_name"),
                "actual_amount": _to_float(data.get("actual_amount")),
                "start_date": start_date,
                "end_date": _to_datetime(data.get("end_date")),
                "updated_at_raw": _to_datetime(data.get("updated_at_raw")),
                "created_at_raw": _to_datetime(data.get("created_at_raw")),
                "valid_days": _to_int(data.get("valid_days")),
                "creator": data.get("creator"),
                "available_traffic": _to_int(data.get("available_traffic")),
                "used_traffic": _to_int(data.get("used_traffic")),
                "remaining_traffic": _to_int(data.get("remaining_traffic")),
                "status": data.get("status"),
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, DeviceCombo, "recharge_order_no", rows, result)


def upsert_shanmao_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """闪猫退款(已退款申请) - 主键: refund_no

    实际 Excel 列(2026-08-03 样例,14列):
      退费单号,订单号,手机号码,套餐名称,实际支付金额,申请退款金额,退款金额,
      退款原因,退款状态,退款方式,审核人名称,审核时间,审核备注,申请备注
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            refund_no = (data.get("refund_no") or "").strip() if data.get("refund_no") else None
            if not refund_no:
                result.add_error(idx, "闪猫退款单号为空")
                continue

            # 姓名类字段清洗:数字/过长纯数字视为脏数据置空
            applicant = _sanitize_name_field(data.get("applicant"), idx, result, "闪猫退款-申请人")
            auditor = _sanitize_name_field(data.get("auditor"), idx, result, "闪猫退款-审核人")
            operator = _sanitize_name_field(data.get("operator"), idx, result, "闪猫退款-操作人")

            row_dict = {
                "refund_no": refund_no,
                "order_no": data.get("order_no"),
                "phone": data.get("phone"),
                "device_sn": data.get("device_sn"),
                "paid_amount": _to_float(data.get("paid_amount")),
                "refund_amount": _to_float(data.get("refund_amount")),
                "actual_amount": _to_float(data.get("actual_amount")),
                "package_name": data.get("package_name"),
                "merchant_name": data.get("merchant_name"),
                "refund_reason": data.get("refund_reason"),
                "refund_method": data.get("refund_method"),
                "status": data.get("status"),
                "applicant": applicant,
                "auditor": auditor,
                "operator": operator,
                "apply_time": _to_datetime(data.get("apply_time")),
                "audit_time": _sanitize_date(_to_datetime(data.get("audit_time"))),
                "refund_time": _to_datetime(data.get("refund_time")),
                "remark": data.get("remark"),
                "audit_remark": data.get("audit_remark"),
            }
            # 通用数据质量校验(L2)
            row_dict["quality_status"], row_dict["quality_note"] = check_shanmao_row(
                types.SimpleNamespace(**row_dict)
            )
            rows.append(row_dict)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    _execute_upsert(db, ShanmaoRefund, "refund_no", rows, result)


# ---------- 手动退款登记 数据质量校验(2026-08-23) ----------
# 业务规律: 登记日期应连续(基本同日/隔日), 不可能晚于今年或过早年份。
# 规则:
#   R1 示例模板行: 备注='示例' → 跳过导入
#   R2 登记日期超出合理范围[当前年-2 的1/1, 今天+3天]:
#      - 有操作时间佐证(在合理范围内) → 年份按 op_time 修正(保留月日), quality=auto_fixed
#      - 无佐证或修正后仍异常 → quality=pending(待人工确认)
#   R3 时间倒挂(op_time 早于 register_time 超过1天) → quality=pending
def _manual_refund_quality_check(
    register_time: datetime | None,
    op_time: datetime | None,
    row_idx: int,
    result: ImportResult,
) -> tuple[datetime | None, str | None, str | None]:
    """手动退款登记时间质量校验: 返回 (修正后register_time, quality_status, quality_note)

    - R2 年份/范围异常(有操作时间佐证 → auto_fixed 修正年份; 无佐证 → pending)
    - R3 时间倒挂(操作早于登记超过1天) → pending(覆盖 R2 结果)
    """
    if register_time is None:
        return register_time, None, None
    now = datetime.now(timezone.utc)
    low = datetime(now.year - 2, 1, 1, tzinfo=timezone.utc)
    high = now + timedelta(days=3)
    status: str | None = None
    note: str | None = None
    if not (low <= register_time <= high):
        # R2 年份/范围异常
        orig = register_time
        if op_time is not None and low <= op_time <= high:
            fixed = register_time.replace(year=op_time.year)
            if low <= fixed <= high:
                status, note = "auto_fixed", (
                    f"登记日期年份异常(原始 {orig:%Y-%m-%d}), "
                    f"按操作时间 {op_time:%Y-%m-%d} 佐证修正为 {fixed:%Y-%m-%d}")
                register_time = fixed
        if status is None:
            status, note = "pending", f"登记日期超出合理范围(原始 {orig:%Y-%m-%d}), 无可靠佐证, 待人工确认"
    # R3 时间倒挂(操作早于登记超过1天)
    if op_time is not None and register_time is not None and op_time < register_time - timedelta(days=1):
        status, note = "pending", (
            f"操作时间 {op_time:%Y-%m-%d} 早于登记日期 {register_time:%Y-%m-%d} 超过1天, 待人工确认")
    if status:
        result.add_warning(row_idx, note)
    return register_time, status, note


def upsert_manual_refunds(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """手动退款登记(商户号手机退款处理登记) - 按 (register_time, order_no, refund_amount) 复合键去重 upsert

    同一订单可分期多次退款(不同登记日期/退款金额),故去重键含登记日期+订单号+退款金额,
    重复导入按此键更新而非累计。is_refunded 归一: 非"是"一律"否"(含空 → 未退款,计入登记蓄水)。
    缺登记日期或订单号的行无法定位,跳过。导入时做时间质量校验(自动纠错/待人工确认, 见
    _manual_refund_quality_check), 结果写入 quality_status/quality_note 供数据质量审核。
    """
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            register_time = _to_datetime(data.get("register_time"))
            order_no = (data.get("order_no") or "").strip() if data.get("order_no") else None
            if register_time is None or not order_no:
                result.add_error(idx, "缺少登记日期或订单号,无法去重定位(跳过)")
                continue
            remark = (data.get("remark") or "").strip() if data.get("remark") is not None else ""
            # R1 示例模板行跳过
            if remark == "示例":
                result.add_warning(idx, "备注为'示例'的模板数据行,已跳过")
                continue
            op_time = _to_datetime(data.get("op_time"))
            # R2/R3 时间质量校验(含自动纠错/待确认标记)
            register_time, quality_status, quality_note = _manual_refund_quality_check(
                register_time, op_time, idx, result)
            registrant = _sanitize_name_field(data.get("registrant"), idx, result, "手动退款-登记人")
            refund_operator = _sanitize_name_field(data.get("refund_operator"), idx, result, "手动退款-退款操作人")
            raw_refunded = (data.get("is_refunded") or "").strip() if data.get("is_refunded") is not None else ""
            rows.append({
                "register_time": register_time,
                "device_sn": (data.get("device_sn") or "").strip() if data.get("device_sn") else None,
                "order_no": order_no,
                "merchant_id": (data.get("merchant_id") or "").strip() if data.get("merchant_id") else None,
                "order_amount": _to_float(data.get("order_amount")),
                "registrant": registrant,
                "is_refunded": "是" if raw_refunded == "是" else "否",
                "refund_amount": _to_float(data.get("refund_amount")),
                "refund_operator": refund_operator,
                "op_time": op_time,
                "remark": data.get("remark"),
                "quality_status": quality_status,
                "quality_note": quality_note,
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    _sync_devices(rows, "device_sn", db)
    # 同批次内复合键去重(Excel 中可能存在完全相同的 登记日期+订单号+退款金额 行,
    # PG ON CONFLICT DO UPDATE 不允许同一语句内二次命中,保留最后一条)
    seen: dict[tuple, dict] = {}
    for r in rows:
        amt_key = r["refund_amount"] if r["refund_amount"] is not None else 0.0
        seen[(r["register_time"], r["order_no"], amt_key)] = r
    rows = list(seen.values())
    # 分批 on_conflict_do_update:按 (register_time, order_no, COALESCE(refund_amount,0)) 表达式部分唯一索引去重更新
    # (refund_amount=NULL 的行由 COALESCE 归一为 0 参与去重, 避免同订单同日空金额行重复导入)
    before = db.scalar(select(func.count()).select_from(ManualRefund)) or 0
    BATCH = 2000
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        stmt = pg_insert(ManualRefund).values(batch)
        update_cols = {c: stmt.excluded[c] for c in batch[0].keys()
                       if c not in ("register_time", "order_no", "refund_amount")}
        stmt = stmt.on_conflict_do_update(
            # 列名用 Column 对象(渲染为标识符), 表达式索引 COALESCE 用 text() 原样渲染
            index_elements=[ManualRefund.register_time, ManualRefund.order_no, text("COALESCE(refund_amount, 0)")],
            index_where=text("order_no IS NOT NULL"),
            set_=update_cols,
        )
        db.execute(stmt)
        db.flush()
    after = db.scalar(select(func.count()).select_from(ManualRefund)) or 0
    inserted = max(after - before, 0)
    result.inserted += inserted
    result.updated += max(len(rows) - inserted, 0)


def upsert_device_tags(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备标签定义 - 主键 id,无 id 时按 tag_name upsert(避免重复名)

    BMS 导出的标签字典通常含:标签ID、标签名称、标签类型、颜色、描述等。
    若 BMS 未导出 id:优先用 tag_name 唯一键做 upsert。

    ⚠️ P0-2 修复: 早期迁移脚本可能导致 device_tags.id 列的 nextval(序列) 默认值
    未被正确设置, bulk insert 时若不带 id 字段会抛 NotNullViolation: "null value in
    column \"id\" of relation \"device_tags\" violates not-null constraint"。
    修复策略: 对缺 id 的批次,统一手动从 device_tags_id_seq 取 nextval 补齐,避免依赖 DB 默认值。
    """
    from sqlalchemy import text as sa_text
    rows: list[dict] = []
    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            tag_id = _to_int(data.get("id"))
            tag_name = _to_str(data.get("tag_name"))
            if not tag_name:
                result.add_error(idx, "标签名称为空")
                continue
            tag_name = tag_name.strip()
            tag_type = _to_str(data.get("tag_type")) or "preset"
            # tag_type 归一:中文(预设/自定义)→英文 preset/custom
            if tag_type in ("预设", "默认", "系统", "sys", "default"):
                tag_type = "preset"
            elif tag_type in ("自定义", "用户", "自定义标签", "custom", "user"):
                tag_type = "custom"
            r: dict = {
                "tag_name": tag_name,
                "tag_type": tag_type,
                "color": _to_str(data.get("color")),
                "description": _to_str(data.get("description")),
            }
            if tag_id is not None:
                r["id"] = tag_id
            rows.append(r)
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return

    # ── P0-2: 对缺 id 的行手动补 nextval(device_tags_id_seq),避免 DB 默认值缺失
    has_seq = True
    try:
        nextval_sql = sa_text("SELECT nextval(pg_get_serial_sequence('device_tags','id'))")
    except Exception:
        has_seq = False
    for r in rows:
        if "id" in r and r["id"] is not None:
            continue
        if has_seq:
            try:
                seq_val = db.scalar(nextval_sql)
                if seq_val is not None:
                    r["id"] = int(seq_val)
                    continue
            except Exception:
                # nextval 失败(例如序列名不对) → fallback 按 tag_name 查询避免重复 id
                pass
        # Fallback: query max(id) + 1(纯内存自增,有并发风险但 device_tags 导入很少,且 tag_name 有唯一索引兜底)
        try:
            from sqlalchemy import select as sa_sel, func as sa_fn
            max_id = db.scalar(sa_sel(sa_fn.max(DeviceTag.id))) or 0
            # 临时用 max_id+offset:tag_name 唯一索引会冲突时在 on_conflict 里 merge, 不影响正确性
            r["id"] = int(max_id) + len(rows) + rows.index(r) + 1  # 确保本批次内不重复
        except Exception:
            r["id"] = 10_000 + rows.index(r)

    # 统一使用 pg_insert on_conflict_do_update (tag_name 有唯一索引已覆盖所有冲突)
    stmt = pg_insert(DeviceTag).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tag_name"],
        set_={
            "tag_type": stmt.excluded.tag_type,
            "color": stmt.excluded.color,
            "description": stmt.excluded.description,
        },
    )
    res = db.execute(stmt)
    # Postgres on_conflict_do_update 不返回插入/更新计数,这里用近似值
    inserted_or_updated = res.rowcount or 0
    result.inserted = inserted_or_updated
    db.flush()


def upsert_device_tag_bindings(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """设备-标签绑定明细(打标记录) - 复合主键 (device_sn, tag_id)

    BMS 导出通常包含:设备SN、标签名称/标签ID、打标人、打标时间、设备所属商户、设备当前套餐等。
    步骤:
      1. 若导出的是 tag_name,先查 device_tags 表按 tag_name 解析出 tag_id
      2. 按 (device_sn, tag_id) 复合键 do_nothing upsert(重复绑定不报错)
      3. 顺带维护 devices 主表(出现新 SN 自动插入空壳 Device;并从导出列补 merchant_name/package_name 到 Device)
    """
    from sqlalchemy import select as sa_select

    rows: list[dict] = []
    tag_name_cache: dict[str, int] = {}
    # suggest_mapping 会返回 {target_field: source_col_str}, 所以这里取到的是字符串(源列名),需要统一包成 list
    _merchant_col: str | None = mapping.get("_merchant_name")  # type: ignore[assignment]
    _package_col: str | None = mapping.get("_package_name")  # type: ignore[assignment]
    alias_merchant: list[str] = [_merchant_col] if _merchant_col else []
    alias_package: list[str] = [_package_col] if _package_col else []

    def _resolve_tag_id(tag_name: str | None, tag_id_raw: object) -> int | None:
        if tag_id_raw is not None:
            tid = _to_int(tag_id_raw)
            if tid is not None:
                return tid
        if not tag_name:
            return None
        if tag_name in tag_name_cache:
            return tag_name_cache[tag_name]
        found = db.scalar(sa_select(DeviceTag).where(DeviceTag.tag_name == tag_name))
        if found:
            tag_name_cache[tag_name] = found.id
            return found.id
        # 标签不存在 → 临时插入一条 preset 标签(后续刷新 device_tags 数据源会覆盖)
        tmp = DeviceTag(tag_name=tag_name, tag_type="preset")
        db.add(tmp)
        db.flush()
        tag_name_cache[tag_name] = tmp.id
        return tmp.id

    for idx, row in enumerate(df.to_dict("records")):
        try:
            data = apply_mapping(row, mapping)
            device_sn = _to_str(data.get("device_sn"))
            if device_sn:
                device_sn = device_sn.strip()  # BMS 导出常带前后空白
            if not device_sn:
                result.add_error(idx, "设备SN为空")
                continue
            tag_name = _to_str(data.get("tag_name"))
            if tag_name:
                tag_name = tag_name.strip()
            tag_id = _resolve_tag_id(tag_name, data.get("tag_id"))
            if tag_id is None:
                result.add_error(idx, "标签ID和标签名称均为空,无法定位标签")
                continue
            created_by = _sanitize_name_field(data.get("created_by"), idx, result, "打标人")
            # 从原始 BMS 列名中取 merchant / package 补充给 Device
            merchant_name = package_name = None
            for ali in alias_merchant:
                v = _to_str(row.get(ali)) if isinstance(row, dict) else None
                if v:
                    merchant_name = v.strip()
                    break
            for ali in alias_package:
                v = _to_str(row.get(ali)) if isinstance(row, dict) else None
                if v:
                    package_name = v.strip()
                    break
            rows.append({
                "device_sn": device_sn,
                "tag_id": tag_id,
                "created_by": created_by,
                "merchant_name": merchant_name,
                "package_name": package_name,
            })
            result.total += 1
        except Exception as e:
            result.add_error(idx, f"行解析失败: {e}")
    if not rows:
        return
    # 维护 devices 主表(新 SN 先插入,避免外键报错;还补 merchant_name)
    # 注意: Device 表没有 package_name 字段,套餐名存在 device_combos,不往 Device 填
    _sync_devices(rows, "device_sn", db, fill_fields=["merchant_name"])
    # ⚠️ 复合主键 do_nothing 需要的 rows 里字段名必须与 DeviceTagBinding ORM 列完全一致
    # (注意: _sync_devices 会把 device_sn strip 了,但这里 rows 里的 device_sn 已经在上面 strip 过了 — 一致)
    bind_rows = [
        {
            "device_sn": r["device_sn"],
            "tag_id": r["tag_id"],
            "created_by": r.get("created_by"),
        }
        for r in rows
    ]
    stmt = pg_insert(DeviceTagBinding).values(bind_rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["device_sn", "tag_id"])
    res = db.execute(stmt)
    result.inserted = res.rowcount or 0
    db.flush()


UPSERT_DISPATCH = {
    "refund": upsert_refunds,
    "wallet": upsert_wallet,
    "ticket": upsert_tickets,
    "device_combo": upsert_device_combos,
    "merchant_refund": upsert_merchant_refunds,
    "merchant": upsert_merchants,
    "cs_record": upsert_cs_records,
    "external_complaint": upsert_external_complaints,
    "card_complaint": upsert_card_complaints,
    "shanmao_refund": upsert_shanmao_refunds,
    "manual_refund": upsert_manual_refunds,
    "device_tag": upsert_device_tags,
    "device_tag_binding": upsert_device_tag_bindings,
}


def run_upsert(
    df: pd.DataFrame,
    data_type: str,
    mapping: dict[str, str],
    db: Session,
) -> ImportResult:
    """执行 upsert 主入口"""
    result = ImportResult()
    # 统一清洗空值:
    # 1. NaN/NaT → None(iterrows 会把含空值的列推断为 float,产生 NaN/NaT)
    # 2. 文本 "NaN"/"nan"/"null"/"none"(含前后空白) → None
    #    (导出数据常用这些占位符表示空,直接插入会触发外键/类型错误)
    df = df.where(pd.notna(df), None)
    df = df.map(
        lambda v: None
        if (isinstance(v, str) and v.strip().lower() in ("nan", "null", "none", ""))
        else v
    )
    fn = UPSERT_DISPATCH.get(data_type)
    if not fn:
        raise ValueError(f"不支持的数据类型: {data_type}")
    fn(df, mapping, db, result)
    db.commit()
    return result
