"""类型转换工具 + 通用执行器(纯函数零依赖 + 执行编排): _convert 原归属并入本模块消环, ImportResult / _execute_upsert / _sync_devices / apply_mapping / 手动退款 ok 保护与去重键。"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.manual_refund import ManualRefund

logger = logging.getLogger(__name__)


# 运营平台(BMS / ai-acc)导出的时间为北京时间(naive,无时区)。
# 存入 timestamptz 列时,DB 时区为 Etc/UTC,naive 值会被当 UTC 存,
# 导致按北京显示时偏后 8 小时。这里把 naive 视为北京时间,转 UTC 后再存。
_TZ_CN = timezone(timedelta(hours=8))

def _op_str(d: datetime) -> str:
    """wallet 时间窗口校验用: datetime → 可读字符串(UTC 统一打印)"""
    return d.strftime("%Y-%m-%d %H:%M:%S") if getattr(d, "tzinfo", None) else str(d)


# 工单 #168: 导入期文本异常留痕的每批上限（同 #40 validate_fail 的 200，防海量噪音）
_SUSPECT_LIMIT_PER_BATCH = 200

class ImportResult:
    """导入结果统计"""

    def __init__(self) -> None:
        self.total = 0
        self.inserted = 0
        self.updated = 0
        self.failed = 0
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []  # 提示/自动纠错类(非失败, 不阻断)
        # #42: 实际落拒收表(ingest_rejected_rows)的条数 —— 与 failed 不同义:
        #   failed=硬失败行数; rejected=其中"带原始行、真正按行可见"的条数
        #   (无 data 的失败行无法按行展示, 不计入 rejected)。
        self.rejected = 0
        # 工单 #168: 导入期文本异常留痕条数（截断/换行，**已入库但值可疑**，与 rejected 不同义）
        self.text_suspects = 0
        # 工单 #169: 导入期日期异常留痕条数（年份越界/未来日期，同义：已入库但值可疑）
        self.date_suspects = 0

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

def _ensure_afternoon_time(val: datetime | None) -> datetime | None:
    """若时间部分为 00:00:00(北京时区判定), 默认设为 15:00:00

    手动退款登记表原始数据只有日期没有时间, 统一用下午三点作为默认时间。
    """
    if val is None:
        return None
    try:
        cn_time = val.astimezone(_TZ_CN)
        if cn_time.hour == 0 and cn_time.minute == 0 and cn_time.second == 0:
            cn_time = cn_time.replace(hour=15, minute=0, second=0, microsecond=0)
            return cn_time.astimezone(timezone.utc)
    except (ValueError, OSError):
        pass
    return val



# Excel 序列日期(1899-12-30 起算的天数)合理区间: ≈1954-10-03 ~ 2119-01-05。
# 取此区间可避开日期串(20260614)、年份(2026)、金额等其他数字含义, 不会误判。
_EXCEL_EPOCH = datetime(1899, 12, 30)

_EXCEL_SERIAL_MIN = 20000

_EXCEL_SERIAL_MAX = 80000

_PURE_NUM_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")

# 紧凑日期串 YYYYMMDD(8位, 如 20260614)。上游若把日期列改成无分隔符导出,
# 它既不在 Excel 序列号区间(会被判 None), 也不是 pd.to_datetime 默认认识的格式。
# 不处理会导致整列时间静默丢失(2026-09-10 审查 WARNING)。
_COMPACT_DATE_RE = re.compile(r"^(19|20)\d{6}$")

def _try_compact_date(val: Any) -> datetime | None:
    """尝试把 8 位纯数字按 YYYYMMDD 解析(如 '20260614' → 2026-06-14); 不是则 None。

    仅当形如 (19|20)YYMMDD 且月/日合法时才认, 避免把 8 位编号误当日 期。
    """
    # numpy 标量归一(与 _try_excel_serial 同口径; 复审 N-1)
    if hasattr(val, "item") and not isinstance(val, (str, bytes)):
        try:
            val = val.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(val, bool):  # bool 是 int 子类, 必须排除(True→1 位数字)
        return None
    if isinstance(val, int):
        s = str(val)
    elif isinstance(val, str):
        s = val.strip()
    else:
        return None
    if not _COMPACT_DATE_RE.match(s):
        return None
    try:
        dt = datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None  # 月/日非法(如 20261340) → 不是日期
    return dt.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)

def _try_excel_serial(val: Any) -> datetime | None:
    """尝试把值按「Excel 序列日期」解析; 不是序列号则返回 None(绝不误判普通数字/字符串)。

    ⚠️ 关键: 必须**类型无关**。上游 xlsx 同一列可能是「文本型」也可能是「数字型」
    (WPS 导出习惯改格式):
      - 文本型 '46187'   → 原实现已覆盖;
      - 数字型 46187/46187.0 → 原实现漏判, 落入 pd.to_datetime(46187) 被当**纳秒**,
        解析成 1969-12-31 16:00:00.000046(实测 315 表 229 行坏值根因)。
    Excel 约定: 整数部分=天数, 小数部分=当天时刻。
    """
    if isinstance(val, bool):  # bool 是 int 子类, 必须显式排除(True→1 天)
        return None
    # numpy 标量(pd.read_excel/to_dict 可能产出 np.int64/np.float64) → 归一为 Python 标量。
    # 不归一的话 isinstance(val,(int,float)) 为 False, 会落入 pd.to_datetime(np.int64(46187))
    # 被当纳秒 → 原 bug(1969 伪日期)复现(2026-09-10 审查 WARNING)。
    if hasattr(val, "item") and not isinstance(val, (str, bytes)):
        try:
            val = val.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(val, bool):  # item() 后可能是 numpy.bool_
        return None
    num: float | None = None
    if isinstance(val, (int, float)):
        num = float(val)
    elif isinstance(val, str):
        s = val.strip()
        # 仅接受纯数字串(可带小数); 日期串/含单位文本交给 pd.to_datetime 处理
        if not _PURE_NUM_RE.match(s):
            return None
        num = float(s)
    if num is None:
        return None
    try:
        if pd.isna(num):
            return None
    except (TypeError, ValueError):
        return None
    # 浮点精度残渣(如 46187.000000000046 来自 float64 存储) → 归一到最近整数
    # 容差取 1e-6 天≈86ms 过宽会误伤合法小数时刻, 收紧到 1e-9 天≈86µs(2026-09-10 审查)
    if abs(num - round(num)) < 1e-9:
        num = float(round(num))
    if not (_EXCEL_SERIAL_MIN <= num <= _EXCEL_SERIAL_MAX):
        return None
    days = int(num)
    frac = num - days
    dt = _EXCEL_EPOCH + timedelta(days=days, seconds=round(frac * 86400))
    # 与其它路径一致: 视为北京时间 → 转 UTC 存 timestamptz
    return dt.replace(tzinfo=_TZ_CN).astimezone(timezone.utc)

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
    # 紧凑日期串 YYYYMMDD(8位): 必须在「区间外纯数字判 None」之前尝试
    _compact = _try_compact_date(val)
    if _compact is not None:
        return _compact
    # Excel 序列日期: 文本型与数字型都要走同一条归一(见 _try_excel_serial 说明)
    _serial = _try_excel_serial(val)
    if _serial is not None:
        return _serial
    # ⚠️ 纯数字但不在 Excel 序列号区间 → 不是合法日期语义(如 1 / 0 / 2026 / 12345 / 99999
    #    / 超长编号)。若继续交给 pd.to_datetime, 会被当成**纳秒**解释, 产出 1970 年前后的
    #    伪日期(实测 pd.to_datetime(1)→1969-12-31 16:00:00.000001), 即本函数要根除的坏值类型。
    #    故此类值一律判为「无法解析 → None」+ 告警(可见化), 由上层校验/拒收流程处理。
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)) or (isinstance(val, str) and _PURE_NUM_RE.match(val.strip())):
        logger.warning(
            f"[upsert._to_datetime] 纯数字但不在 Excel 序列号区间({_EXCEL_SERIAL_MIN}~"
            f"{_EXCEL_SERIAL_MAX}), 按 None 处理(避免造伪日期): {val!r}"
        )
        return None
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



# 时间戳/日期类字符串(如 "2026-04-26 16:00:37" / "2026/4/26"),用于识别写进人名字段的污染值
_DATETIME_LIKE_RE = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}")

# 人名字段不应出现的字符(核算/备注类污染)
_ILLEGAL_NAME_CHARS = "【】÷＝=＊"

_MAX_NAME_LEN = 24  # 仅作护栏,span人名/英文账号几乎不可能超过

def _is_manual_refund_name_polluted(val: Any) -> bool:
    """判定手动退款"人名字段"(登记人/退款操作人)是否被列错位污染。

    与 _sanitize_name_field 同一口径(数字/时间戳/非法字符/超长), 供清洗前预检:
    若污染 → sanitize 会置空该字段, 此时应补打 [人员污染] pending(而非仅日志 warning)。
    """
    s = _to_str(val)
    if s is None:
        return False
    return (_is_numeric_like(s) or _DATETIME_LIKE_RE.match(s)
            or any(c in s for c in _ILLEGAL_NAME_CHARS) or len(s) > _MAX_NAME_LEN)



# ── ok 保护: 人工确认后的质量状态不被后续同步覆盖 (#36, 2026-09-08) ──────
def _manual_refund_quality_kind(quality_note: str | None) -> str | None:
    """从 quality_note 提取问题类别标签(如 '[时间倒挂]')。

    #36 起新写入的 note 统一带 '[类别] ' 前缀(见 _manual_refund_quality_check /
    人员污染补打)。历史存量(无前缀)返回 None, 视为"未知类别":
      - 未知类别行被人工确认(ok)后, 不再被无标签的新问题打回(保守保留 ok)
      - 若源侧出现带标签的新问题且与存量 ok 类别未知 → 无法比对新旧, 保守保留 ok(不重复报)
    """
    if not quality_note:
        return None
    m = re.match(r"^\[([^\[\]]+)\]", quality_note.strip())
    return m.group(1) if m else None

def _freeze_ok_manual_refund_rows(db: Session, rows: list[dict]) -> list[dict]:
    """ok 保护: 过滤掉"库内已人工确认(ok)且本次问题类别相同"的待写行。

    语义(用户拍板, #36):
      - 库内 quality_status='ok' 且 本次 note 类别 == 库内 note 类别(同型问题)
        → 整行冻结: 不更新业务字段、不重报待办(源台账修正前不再烦人)
      - 库内 ok 但本次类别不同(新类型问题) → 保留更新并重报 pending
      - 其余(新增/非 ok) → 正常

    实现: 在 on_conflict 前用 Python 预查库内 ok 行(按去重键), 从 batch 移除冻结行。
    SQL 层无需复杂 CASE; 该 helper 同时供共享文档同步(manual_refund_sync)与本地导入
    (upsert_manual_refunds)两条路径复用, 口径一致。
    返回过滤后的 rows(冻结行已剔除)。
    """
    if not rows:
        return rows
    # 只考虑去重键完整的行(order_no 非空, 与部分唯一索引口径一致);
    # 若没有任何键完整的行, 冻结无从谈起(全部正常放行)
    if not any(_manual_dedup_key(r)[1] for r in rows):
        return rows

    # 候选行: 用 (order_no, device_sn, registrant) 粗查 + Python 侧精确匹配登记日期,
    # 避免 SQL 层时区表达式与 Python str(date) 的边界不一致
    # 粗查条件收集(按 order_no IN 分批)
    kept_all: list[dict] = []
    frozen = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i + 500]
        batch_keys = {_manual_dedup_key(r) for r in batch if _manual_dedup_key(r)[1]}
        if not batch_keys:
            kept_all.extend(batch)
            continue
        order_nos = [k[1] for k in batch_keys]
        db_rows = db.execute(
            select(ManualRefund)
            .where(ManualRefund.order_no.in_(order_nos))
        ).scalars().all()
        # 库内 ok 行按去重键索引
        ok_keys: set[tuple] = set()
        for cur in db_rows:
            if cur.quality_status == "ok":
                ok_keys.add(_manual_dedup_key({
                    "register_time": cur.register_time, "order_no": cur.order_no,
                    "device_sn": cur.device_sn, "registrant": cur.registrant,
                }))
        # 库内 ok 行的 note 类别(供同型判定)
        ok_kind: dict[tuple, str | None] = {}
        for cur in db_rows:
            if cur.quality_status == "ok":
                kk = _manual_dedup_key({
                    "register_time": cur.register_time, "order_no": cur.order_no,
                    "device_sn": cur.device_sn, "registrant": cur.registrant,
                })
                ok_kind[kk] = _manual_refund_quality_kind(cur.quality_note)
        for r in batch:
            k = _manual_dedup_key(r)
            if k not in ok_keys:
                kept_all.append(r)
                continue
            new_kind = _manual_refund_quality_kind(r.get("quality_note"))
            cur_kind = ok_kind.get(k)
            # 冻结条件(同型问题): 两边都无标签/未知 → 保守冻结(不重复烦人);
            # 或 两边标签相同 → 同型冻结。
            # 库内 ok 但无标签(历史 confirm), 本次出现带标签新问题 → 新型, 保留重报。
            if (new_kind is None and cur_kind is None) or (new_kind is not None and cur_kind == new_kind):
                frozen += 1
                continue
            kept_all.append(r)
    if frozen:
        logger.info(f"[manual_refund] ok 保护冻结 {frozen} 行(人工确认过, 同型问题不重复更新/重报)")
    return kept_all

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
    if _is_numeric_like(s) or _DATETIME_LIKE_RE.match(s) or any(c in s for c in _ILLEGAL_NAME_CHARS) or len(s) > _MAX_NAME_LEN:
        result.add_error(
            row_idx,
            f"{field_label}='{s}' 格式异常(疑似数字/时间戳/备注或超长,可能是 Excel 列映射错位或源数据污染),已置空请核对列映射",
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

def _manual_dedup_register_date(register_time: datetime | None) -> datetime | None:
    """登记日期(北京时间, 去时分秒) — 手动退款去重键用。

    兼容历史版本解析差异(同一日期可能存成北京 00:00 或 15:00), 统一按北京日期参与去重。
    """
    if register_time is None:
        return None
    try:
        return register_time.astimezone(_TZ_CN).date()
    except (ValueError, OSError):
        return register_time.date()

def _manual_dedup_key(r: dict) -> tuple:
    """手动退款去重键: (登记日期, 订单号, 设备号, 登记人)。

    这四列即可确定一笔退款单; 退款金额/状态被修改时按该键 UPDATE 而非新增,
    避免因金额变化产生新行导致历史残留累积。
    """
    return (
        str(_manual_dedup_register_date(r.get("register_time"))),
        r.get("order_no"),
        (r.get("device_sn") or "").strip(),
        (r.get("registrant") or "").strip(),
    )
