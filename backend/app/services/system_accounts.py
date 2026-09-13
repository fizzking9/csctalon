"""系统 / AI / 第三方账户的**唯一识别来源**（#78）

【设计说明（Design Rationale）】
- **目的**：替运营主管回答「AI 客服接管了多少接待」——但要先能**认出**哪些不是真人。
- **逻辑**：把散落各处的系统账户黑名单收敛到**单一来源**，避免「同一份黑名单两处并存、
  改一处漏一处」。本模块只做「是不是人」的判定 + 来源分类，不做任何统计。
- **口径**：精确名单（`SYSTEM_ACCOUNT_EXACT`）+ 词干提示（`VIRTUAL_HINT_WORDS`）+
  编号格式排除（`EMPLOYEE_ID_PATTERN`）三级。
- **取舍**：
  ① **不做**「AI 转人工」细分 —— 平台无稳定标记列（用户明确本期不做，见工单 #78）；
  ② **不删** AI 数据，只分类，供上层分层展示；
  ③ **不引入**新表新列，纯代码常量 + 派生函数。
- **工单**：#78；架构：`docs/arch/A-20260913-001-AI接待口径分层.md`

【背景 · 为什么必须收敛到一处】
2026-09-13 生产诊断（#78）发现：`analytics.py` 内同时存在两份系统账户清单，
且**互相不一致**，两份都漏掉 `WeChat`：

    L5192  SYS_OP_BLACKLIST     = sys/system/SYS/System/用户自助/客户自助/企微用户自助
    L5658  SYSTEM_VIRTUAL_EXACT = 用户自助/系统自动/客户自助/企微用户自助/企微系统自动/
                                  系统/AI客服/班牛/公司名/大秦WiFi管家/微信投诉

后果：`WeChat`（ai-acc 平台的 AI 全自动接待账户，9 月已 4 万条）被判为**真人**，
在生产人效榜上 **rank=1**（`is_virtual=false`，work_score 2764.6）。
根因在 `analytics._is_virtual_name()` 的 `_EMPLOYEE_ID_PATTERN`：
`^[A-Za-z][A-Za-z0-9_-]{1,11}$` 把 `WeChat` 当成「员工工号」→ 返回 False（真人）。

本模块用 `NON_PERSON_WORDS` 在「工号格式」判定**之前**做否决，堵住该类英文单词误判。
"""

from __future__ import annotations

import re
from enum import Enum

# ─────────────────────────────────────────────────────────────────────
# 一、系统 / AI / 第三方账户精确名单（唯一来源）
#
# ⚠️ 新增账户**只改这里**。任何其它模块不得再自行定义系统账户集合
#    （历史教训见模块 docstring：两处并存导致漏判 WeChat）。
# ─────────────────────────────────────────────────────────────────────
SYSTEM_ACCOUNT_EXACT: frozenset[str] = frozenset({
    # ---- 系统自动（大小写 4 种写法必须全列：PG NOT IN 大小写敏感）----
    "sys", "system", "SYS", "System",
    # ---- 虚拟/自助账户 ----
    "用户自助", "客户自助", "企微用户自助", "系统自动", "企微系统自动",
    "系统",            # 单字"系统"（BMS 系统账户，非操作人）
    # ---- AI 账户（#78 新增：本次问题的直接成因）----
    "WeChat", "wechat", "WECHAT",   # ai-acc 平台 AI 全自动接待账户
    "AI客服", "ai客服", "智能客服", "机器人客服",
    # ---- 第三方/系统归类账户 ----
    "班牛",            # 工单系统自动账户
    "微信投诉",        # 微信投诉工单的系统归类账户
    "客户",            # 工单 registrant 里的"客户"（客户自助，非客服）
    # ---- 设备供应商 / 管理账户 ----
    "深圳市云客猫科技有限公司",
    "大秦WiFi管家", "大秦随身管理员", "全球WiFi管理员",
})

# 非人词干（用于**否决**「工号格式」误判）
# 背景：WeChat/wechat 形如「英文单词」，会命中员工工号正则 ^[A-Za-z][A-Za-z0-9_-]{1,11}$
#       而真人员工工号是「拼音首字母/带部门前缀」形式（YKM-LN / hqq / ZHY）。
#       故在工号判定前，先否决这些含"平台/系统"语义的词。
NON_PERSON_WORDS: tuple[str, ...] = (
    "wechat", "weixin", "sys", "system", "admin", "root",
    "auto", "bot", "robot", "ai",
)

# 未登记到 Person 维表的第三方/系统/设备/公司账号关键字（关键字命中直接判非真人）
# ⚠️ 2026-08-11 修正记录（保留以防回退）：移除 "hqq" 和 "ykm-" ——
#    这两个是真人员工缩写/工号前缀（YKM-LN=卢宁 等），误判为虚拟。
VIRTUAL_HINT_WORDS: tuple[str, ...] = (
    "wifi", "管家", "科技", "公司", "设备", "云客猫", "闪猫",
    "sys_", "sys-", "畅享", "商米", "猫科", "科联", "运营",
    "管理员", "客服中心", "网络",  # "网络": 烁盟网络等公司名
)

# 工号/缩写格式（纯字母数字 + 横杠下划线，2-12 字）: 真人员工工号，如 YKM-XXX / hqq / DML
EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,11}$")
# 中文人名 2-4 字（黄展强 / 刘思敏 等漏登记的真人）
PERSON_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")


def is_system_account(name: str | None) -> bool:
    """精确判定：`name` 是否系统/AI/第三方账户（大小写不敏感）。

    只做精确名单匹配，不含启发式。用于「这个 operator 该不该排除」的硬判据。
    """
    if not name:
        return True  # 空值一律视为非真人（无操作人的记录）
    s = name.strip()
    return s in SYSTEM_ACCOUNT_EXACT or s.lower() in {a.lower() for a in SYSTEM_ACCOUNT_EXACT}


def is_non_person_word(name: str) -> bool:
    """词干否决：`name` 是否含"平台/系统"语义词（wechat/sys/ai/bot...）。

    用于在「工号格式」判定**之前**否决，避免 `WeChat` 被当成员工工号。
    """
    if not name:
        return True
    low = name.strip().lower()
    return any(w in low for w in NON_PERSON_WORDS)


# ─────────────────────────────────────────────────────────────────────
# 二、来源分类（#78 核心枚举）
# ─────────────────────────────────────────────────────────────────────
class SourceKind(str, Enum):
    """接待记录的来源类型（真人 / AI / 客户自助 / 系统）。

    对应工单 #78 用户描述的 6 类 AI 应用场景：
      - HUMAN         → 场景 1（人工写话术点击发送）、场景 2（人工点 AI 助手）、场景 4（AI 转人工）
      - AI_AUTO       → 场景 3（AI 识别需求后全自动响应，全程无人工）
      - CUSTOMER_SELF → 场景 5（客户公众号手动操作）
      - AI_SYSTEM     → 平台内部自动化流水（无人工痕迹的标记/执行）
    """

    HUMAN = "human"
    AI_AUTO = "ai_auto"
    AI_SYSTEM = "ai_system"
    CUSTOMER_SELF = "customer_self"


# AI-ACC 平台 AI 全自动接待账户（实测：operator='WeChat'，description='WeChat操作<方案>'）
AI_ACCOUNTS: frozenset[str] = frozenset({"WeChat", "wechat", "WECHAT"})

# openid 形态（客户在公众号自助操作时，operator 为空而 description 带 openid 前缀）
# 实测样本: olAFl6712SMJJHxyDyhCJqTVAE54 / ojb7F2PoSe4ydnxsHo-KOiRgVEDY
OPENID_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,}")
# 系统内部自动化流水前缀（标记用户接受 / 执行失败）
SYSTEM_AUTO_PREFIXES: tuple[str, ...] = ("系统标记", "系统执行")


def classify_cs_record(operator: str | None, description: str | None) -> SourceKind:
    """`cs_records` 单行 → SourceKind（判据顺序敏感，NULL 安全）。

    判定顺序（不可调换）：
      1. operator 非空且非系统账户  → HUMAN（含人工点 AI 助手，场景 1/2/4）
      2. operator ∈ AI 账户        → AI_AUTO（场景 3）
      3. operator 空 + openid 前缀  → CUSTOMER_SELF（场景 5）
      4. operator 空 + 系统前缀/其它 → AI_SYSTEM

    ⚠️ 为什么 HUMAN 排在 AI_AUTO 之前：
       `WeChat` 同时也是「系统账户」，若先判系统账户会把它归成 AI_SYSTEM。
       这里用 `AI_ACCOUNTS` 精确区分，确保 `WeChat` → AI_AUTO。
    """
    op = (operator or "").strip()
    desc = (description or "").strip()

    # 1. 有操作人 → 先看是不是 AI 账户
    if op:
        if op in AI_ACCOUNTS:
            return SourceKind.AI_AUTO
        # 非 AI 且非系统账户 → 真人（人工点 AI 助手也算人工，见工单 #78 场景 2）
        if not is_system_account(op):
            return SourceKind.HUMAN
        return SourceKind.AI_SYSTEM

    # 2. 无操作人（NULL / 空串）→ 按 description 细分
    if OPENID_PREFIX_PATTERN.match(desc):
        return SourceKind.CUSTOMER_SELF
    # "系统标记" / "系统执行" 及空描述，均归为系统内部自动化
    return SourceKind.AI_SYSTEM


def classify_ticket(registrant: str | None, handler: str | None) -> SourceKind:
    """`service_tickets` 单行 → SourceKind（第二套 AI：BMS 客服系统）。

    与 `classify_cs_record` 共用同一枚举，便于前端统一图例。
    判据：
      - registrant/handler 为 'AI客服' → AI_AUTO
      - registrant/handler 为 sys/system/SYS/System/系统 → AI_SYSTEM
      - registrant/handler 为 '客户' → CUSTOMER_SELF
      - 其他非空 → HUMAN
    """
    for v in (handler, registrant):
        s = (v or "").strip()
        if not s:
            continue
        if s in AI_ACCOUNTS or s in ("AI客服", "ai客服", "智能客服", "机器人客服"):
            return SourceKind.AI_AUTO
        if s.lower() in ("sys", "system") or s == "系统":
            return SourceKind.AI_SYSTEM
        if s == "客户":
            return SourceKind.CUSTOMER_SELF
        if is_non_person_word(s) or is_system_account(s):
            return SourceKind.AI_SYSTEM
        return SourceKind.HUMAN
    return SourceKind.AI_SYSTEM


# ─────────────────────────────────────────────────────────────────────
# 三、虚拟账户判定（today_staff_efficiency 用，替代原 _is_virtual_name）
# ─────────────────────────────────────────────────────────────────────
def is_virtual_name(
    name: str | None,
    dept: str | None = None,
    grp: str | None = None,
    pos: str | None = None,
    person_id: int | None = None,
) -> bool:
    """非真人虚拟账户判定（迁移自 `analytics._is_virtual_name`，逻辑等价 + 修 WeChat 误判）。

    ① 名字精确匹配 `SYSTEM_ACCOUNT_EXACT` → True
    ② 已通过 PersonAlias 匹配到 Person（person_id 非空）且不在黑名单 → 一定真人，False
       （覆盖「财务-徐依婷」「生产-刘思敏」等 display_name 带前缀但确属真人的情况）
    ③ 未匹配 Person 时按名字特征判定：
       - **词干否决**（wechat/sys/ai/bot...）→ True  ← ★ #78 修复点
       - 关键字命中（wifi/管家/科技/公司/管理员/畅享 等）→ True
       - 工号格式（YKM-XXX/hqq/DML 等）→ False（真人）
       - 中文人名 2-4 字 → False（真人，只是 Person 维表漏登记）
       - 长名（≥5 字）→ True；短名 → False（保守判真人）

    ★ 与旧实现的差异：新增「词干否决」且在**工号格式判定之前**，
      否则 `WeChat` 会命中 `EMPLOYEE_ID_PATTERN` 被判成真人（#78 根因）。
    """
    if not name:
        return True
    if name in SYSTEM_ACCOUNT_EXACT:
        return True
    # 已匹配到 Person → 一定不是虚拟（虚拟账户的名字已在上面被拦截）
    if person_id is not None:
        return False

    low = name.lower()
    # ★ 词干否决：必须在工号格式判定之前（WeChat 的根因）
    if any(w in low for w in NON_PERSON_WORDS):
        return True
    if any(k.lower() in low for k in VIRTUAL_HINT_WORDS):
        return True
    # 工号/缩写格式 → 真人（YKM-XXX / hqq / DML / LJY）
    if EMPLOYEE_ID_PATTERN.match(name):
        return False
    # 中文人名 2-4 字 → 真人
    if PERSON_NAME_PATTERN.match(name):
        return False
    # 其他未登记且不像人名/工号 → 长名判虚拟，短名保守判真人
    return len(name) >= 5
