"""人员别名提取 - 从业务表的人名字段提取候选别名

各业务表的 operator/auditor/handler/registrant 视为别名，映射到统一 Person。
sys/system 标记为系统自动操作（is_system=true），不建 person。
新别名只入待归并队列（person_id=None），等 HR 导入认领或管理员手工归并。

归一化(_normalize_alias)剥离「企微/客服/云客猫/随身WiFi/店铺」等系统/部门前缀,
使跨表同指向别名(企微-洪佳 / 云客猫-洪佳)归并到同一 normalized key。
自由文本字段(external_complaints.reception_cs)先取首段人名再归一化。
"""

import re

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.cs_record import CsRecord
from app.models.external_complaint import ExternalComplaint
from app.models.person import PersonAlias
from app.models.refund import Refund
from app.models.ticket import ServiceTicket
from app.models.wallet import WalletWithdrawal

# 系统自动操作标识（不归并到真人，入库标 is_system 供审计）
SYSTEM_ALIASES = {"sys", "system", "SYS", "System"}
# 无效占位符
INVALID_ALIASES = {"", "nan", "null", "none", "NaN", "NULL", "None"}
# 非人名脏值（归一化后命中即丢弃）
GARBAGE_DENYLIST = {
    "/", "wechat", "微信", "无责", "有责", "系统", "自动", "未知",
    "客服", "店铺", "企微", "云客猫", "随身wifi", "无", "是", "否",
    # 业务表 operator/registrant 字段常见非人值
    "客户", "班牛", "ai客服", "大秦wifi管家", "wifi服务订购",
    "深圳市云客猫科技有限公司",
}
# 时间戳特征（2026-06-03 17:44:30 这类列错位残留）
_DATETIME_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
# 公司名特征
_COMPANY_RE = re.compile(r"公司|有限|科技|集团")
# 中文字符
_CJK_RE = re.compile(r"[一-龥]")
# 连续 2+ 空白（两人名 / 名字+备注 分隔）
_MULTI_WS_RE = re.compile(r"\s{2,}")

# 人名前缀（系统标识/部门标识），归一化时剥离使跨表同名归并
# 注意: 店铺客服也是内部客服(另一部门),剥前缀后可与同名跨表归并,
# 但 alias 原值保留前缀供人工归并时辨别部门来源。
_NAME_PREFIXES = ("企微", "客服", "云客猫", "随身wifi", "店铺", "工厂")
# 前缀后随的分隔符
_PREFIX_SEPARATORS = "-·—－－_ \t"
# 角色头衔后缀(管理员/小二/负责人 等=角色非人名),命中即丢弃
_ROLE_TITLE_RE = re.compile(r"(管理员|小二|负责人|经手人)$")
# 纯拉丁代码型别名(员工工号/拼音缩写,如 YKM-XXX / YKM_LJF / ZHY):
# 无中文,无法 name-match HR 姓名,标 is_system 供审计但不入归并队列。
_CODE_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9]+$")  # 带分隔符 YKM-XXX
_INITIALS_RE = re.compile(r"^[A-Z]{2,6}$")  # 全大写缩写 ZHY/HYK

# (model, field_attr, source_table, source_field)
PERSON_FIELD_SOURCES = [
    (Refund, Refund.operator, "refunds", "operator"),
    (Refund, Refund.auditor, "refunds", "auditor"),
    (ServiceTicket, ServiceTicket.handler, "service_tickets", "handler"),
    (ServiceTicket, ServiceTicket.registrant, "service_tickets", "registrant"),
    (WalletWithdrawal, WalletWithdrawal.operator, "wallet_withdrawals", "operator"),
    (CsRecord, CsRecord.operator, "cs_records", "operator"),
    (ExternalComplaint, ExternalComplaint.follower, "external_complaints", "follower"),
    (ExternalComplaint, ExternalComplaint.reception_cs, "external_complaints", "reception_cs"),
    (CardComplaint, CardComplaint.handler, "card_complaints", "handler"),
]

# 自由文本字段: 源数据混了名字+投诉叙述,需先取首段人名
FREETEXT_FIELDS = {("external_complaints", "reception_cs")}

# 句子分隔符(取首段时截断)
_SENTENCE_SEPS = ("，", "。", "；", ";", ",", "、", "\n", "\r")


def _normalize_alias(raw: str) -> str:
    """归一化：剥前后空白；循环剥离「企微/客服/云客猫/随身WiFi/店铺」前缀及其分隔符；lower

    用于 alias_normalized 字段（不改 alias 原值），给归并建议分组用。
    样本: 企微-洪佳 / 云客猫-洪佳 / 随身WiFi-小罗 / 客服--张泽森 / 店铺-张文琳
        → 洪佳 / 洪佳 / 小罗 / 张泽森 / 张文琳
    """
    s = raw.strip()
    changed = True
    while changed:
        changed = False
        low = s.lower()
        for pfx in _NAME_PREFIXES:
            if low.startswith(pfx):
                s = s[len(pfx):]
                # 跳过紧随的分隔符(可能多个,如 客服-- )
                while s and s[0] in _PREFIX_SEPARATORS:
                    s = s[1:]
                s = s.strip()
                changed = True
                break
    return s.lower()


def _is_garbage(raw: str) -> bool:
    """判断是否为脏数据(非人名)。用于规整字段(operator/handler 等)。"""
    s = str(raw).strip()
    if not s or s.lower() in INVALID_ALIASES:
        return True
    if _DATETIME_RE.match(s):  # 时间戳残留
        return True
    if s in GARBAGE_DENYLIST or s.lower() in GARBAGE_DENYLIST:
        return True
    if len(s) <= 1:  # 单字符标点(如 /)
        return True
    if _ROLE_TITLE_RE.search(s):  # 角色头衔(管理员/小二/负责人),非人名
        return True
    if _is_placeholder(s):  # 占位符(XXX/YYY/test 等,剥公司前缀后纯重复)
        return True
    norm = _normalize_alias(s)
    if not norm:  # 剥前缀后为空(如裸前缀词"云客猫"/"企微")
        return True
    if norm in GARBAGE_DENYLIST:
        return True
    if _COMPANY_RE.search(s):  # 公司名(含 公司/有限/科技/集团)
        return True
    return False


def _is_code_alias(raw: str) -> bool:
    """纯拉丁代码型别名(无中文): 员工工号/拼音缩写,如 YKM-XXX / YKM_LJF / ZHY。
    无中文,无法 name-match HR 姓名。这类别名实际是「HR 人员姓名的拼音首字母」,
    保留为普通待归并别名(非 is_system),由 persons.py 的首字母匹配建议辅助人工归并。
    含中文的(如 cat_月白)不算代码别名,按人名处理走人工归并。
    """
    s = str(raw).strip()
    if not s or _CJK_RE.search(s):  # 含中文,不是纯代码
        return False
    return bool(_CODE_ALIAS_RE.match(s) or _INITIALS_RE.match(s))


# 公司缩写前缀(云客猫=YKM),剥后才是人名首字母(YKM_LJF → LJF)
_COMPANY_PREFIXES = ("YKM",)
# 常见占位符词
_PLACEHOLDER_WORDS = {"TEST", "TEST1", "XXX", "YYY", "ZZZ", "NULL", "NONE", "UNKNOWN", "N/A", "NA"}


def _is_placeholder(raw: str) -> bool:
    """占位符(XXX/YYY/test 等): 剥公司前缀后为纯重复同字符或常见占位词 → 丢弃。"""
    s = str(raw).strip().upper()
    for pfx in _COMPANY_PREFIXES:
        if s.startswith(pfx):
            s = s[len(pfx):].lstrip("-_ ")
            break
    if not s:
        return False
    if s in _PLACEHOLDER_WORDS:
        return True
    # 纯重复同字符(XXX/YY/AAA,长度≥2)
    if len(s) >= 2 and len(set(s)) == 1:
        return True
    return False


def _extract_name_token(raw: str) -> str | None:
    """从自由文本(如 reception_cs)提取首段人名。

    - 取首个句子分隔符(，。；,、换行)前的片段
    - 再截首个 2+ 空白前(两人名 / 名字+备注)
    - 剥前缀归一化后校验: 必须含中文,且长度 2~6(典型中文姓名),否则视为叙述丢弃
    返回截断后的片段(保留前缀,如 客服-张梦琪),供 alias 原值;归一化交给 _normalize_alias。
    """
    s = str(raw).strip()
    cut = len(s)
    for sep in _SENTENCE_SEPS:
        idx = s.find(sep)
        if 0 < idx < cut:
            cut = idx
    s = s[:cut].strip()
    m = _MULTI_WS_RE.search(s)
    if m:
        s = s[: m.start()].strip()
    if not s or s.lower() in INVALID_ALIASES:
        return None
    norm = _normalize_alias(s)
    if not _CJK_RE.search(norm):  # 无中文,不是中文人名
        return None
    if norm in GARBAGE_DENYLIST:
        return None
    if not (2 <= len(norm) <= 6):  # 过短/过长都不像人名
        return None
    return s


def _upsert_alias(
    db: Session,
    alias_val: str,
    src_table: str,
    src_field: str,
    last_seen,
    stats: dict,
) -> None:
    """单条别名入库(幂等): 已存在则更新 last_seen; sys/system 标 is_system; 否则新建待归并。

    纯拉丁首字母别名(ZHY/YKM-LJF)是真人(HR 人员拼音首字母),走普通待归并路径
    (非 is_system),由 persons.py 的首字母匹配建议辅助人工归并。
    """
    existing = db.scalar(select(PersonAlias).where(PersonAlias.alias == alias_val))
    if existing:
        if last_seen and (existing.last_seen_at is None or existing.last_seen_at < last_seen):
            existing.last_seen_at = last_seen
        return
    # 仅 sys/system 自动操作标 is_system(审计可追溯,不入归并队列)
    if alias_val in SYSTEM_ALIASES:
        db.add(PersonAlias(
            alias=alias_val,
            alias_normalized=alias_val.lower(),
            source_table=src_table,
            source_field=src_field,
            is_system=True,
            last_seen_at=last_seen,
        ))
        stats["system_aliases"] += 1
        stats["new_aliases"] += 1
        return
    db.add(PersonAlias(
        person_id=None,
        alias=alias_val,
        alias_normalized=_normalize_alias(alias_val),
        source_table=src_table,
        source_field=src_field,
        last_seen_at=last_seen,
    ))
    stats["new_aliases"] += 1


def extract_person_aliases(db: Session) -> dict:
    """从业务表的人名字段提取候选别名（幂等）

    - sys/system/空/nan 跳过（sys/system 入库标 is_system 供审计）
    - 时间戳/纯标点/非人名 denylist 跳过
    - 自由文本字段(reception_cs)先取首段人名
    - alias 已存在(含已归并到 HR 的)则只更新 last_seen_at
    - 新 alias 只入待归并队列(person_id=None),不建 Person
    - 先按 alias_val 跨源去重(同别名多源出现只入库一次,首次来源为准),
      避免 pending insert 未 flush 时 select 查不到导致重复插入违反唯一约束。
    """
    stats = {"new_aliases": 0, "new_persons": 0, "skipped": 0, "system_aliases": 0}

    # 1. 跨源收集唯一别名(同 alias_val 多源出现,首次来源为准,取 max last_seen)
    collected: dict[str, dict] = {}
    for model, field_attr, src_table, src_field in PERSON_FIELD_SOURCES:
        is_freetext = (src_table, src_field) in FREETEXT_FIELDS
        rows = db.execute(
            select(field_attr, func.max(model.created_at)).where(field_attr.isnot(None)).group_by(field_attr)
        ).all()
        for raw_value, last_seen in rows:
            if not raw_value or str(raw_value).strip() in INVALID_ALIASES:
                stats["skipped"] += 1
                continue
            if is_freetext:
                alias_val = _extract_name_token(str(raw_value))
                if alias_val is None:
                    stats["skipped"] += 1
                    continue
            else:
                alias_val = str(raw_value)
                if _is_garbage(alias_val):
                    stats["skipped"] += 1
                    continue
            entry = collected.get(alias_val)
            if entry is None:
                collected[alias_val] = {"table": src_table, "field": src_field, "last_seen": last_seen}
            elif last_seen and (entry["last_seen"] is None or entry["last_seen"] < last_seen):
                entry["last_seen"] = last_seen

    # 2. 每个唯一 alias_val 只 upsert 一次
    for alias_val, info in collected.items():
        _upsert_alias(db, alias_val, info["table"], info["field"], info["last_seen"], stats)
    db.commit()
    return stats


def rebuild_unclaimed_aliases(db: Session) -> dict:
    """全量重建未归并别名 + 重算所有别名的归一化值。

    步骤:
    1. 用新 _normalize_alias 重算全部别名的 alias_normalized(含已归并到 HR 的),
       使跨表同指向(企微-洪佳 / 云客猫-洪佳)归并到同一 key。
    2. 删除所有 person_id IS NULL 的待归并别名(含历史脏残留:时间戳/列错位旧值)。
    3. 用修好的逻辑从当前干净数据全量重新提取。
    已归并到 HR 的别名(person_id 非空)不动,仅更新其 normalized。
    """
    # 1. 重算全部归一化
    re_norm = 0
    for a in db.scalars(select(PersonAlias)).all():
        new_norm = _normalize_alias(a.alias) if a.alias not in SYSTEM_ALIASES else a.alias.lower()
        if new_norm != a.alias_normalized:
            a.alias_normalized = new_norm
            re_norm += 1

    # 2. 删除待归并别名(历史脏数据/列错位残留一并清掉)
    deleted = db.execute(delete(PersonAlias).where(PersonAlias.person_id.is_(None)))
    db.commit()

    # 3. 重新提取
    stats = extract_person_aliases(db)
    stats["re_normalized"] = re_norm
    stats["deleted_stale"] = deleted.rowcount
    return stats


def _escape_like(s: str) -> str:
    """转义 LIKE 通配符(%, _, \\),防别名含这些字符导致误匹配。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def count_alias_occurrences(db: Session, alias_vals: list[str]) -> dict[str, list[dict]]:
    """统计别名原值在各业务表人名字段的出现次数,回答「这个别名在哪些地方出现」。

    返回 {alias_val: [{table, field, count}]}。
    - 非自由文本字段(operator/auditor/handler 等):精确匹配 IN(alias_vals) 批量统计
    - 自由文本字段(reception_cs):别名是提取的人名 token,原文含 token → LIKE 匹配
    """
    if not alias_vals:
        return {}
    alias_set = list({str(v) for v in alias_vals})
    result: dict[str, list[dict]] = {v: [] for v in alias_set}
    for model, field_attr, src_table, src_field in PERSON_FIELD_SOURCES:
        is_freetext = (src_table, src_field) in FREETEXT_FIELDS
        if is_freetext:
            for av in alias_set:
                cnt = db.scalar(
                    select(func.count()).select_from(model).where(field_attr.like(f"%{_escape_like(av)}%", escape="\\"))
                ) or 0
                if cnt:
                    result[av].append({"table": src_table, "field": src_field, "count": int(cnt)})
        else:
            rows = db.execute(
                select(field_attr, func.count()).where(field_attr.in_(alias_set)).group_by(field_attr)
            ).all()
            for val, cnt in rows:
                key = str(val)
                if key in result:
                    result[key].append({"table": src_table, "field": src_field, "count": int(cnt)})
    return result
