"""分析口径真身 + 纯工具层。

所有「全站唯一口径」的 CASE/过滤条件与纯函数集中于此(零 analytics 内部依赖):
- 投诉三源分类 `_complaint_source_type_case` / `_ticket_category_case` / `_complaint_root_case`
- 经营流水路由过滤 `_route_a_online_refund_filter` / `_route_b_withdraw_filter` / `_route_e_cashflow_filter`
- 时间窗口 `_resolve_windows` / `_today_windows` / `_parse_date` / `_parse_minute_end`
- 岗位归类 `_person_position_map`、退款判定 `_normalize_refund_reason` / `_is_refund_staff` 等

本模块不得 import 同包其它业务子模块(避免循环依赖)。
"""

import datetime as _dt
import re as _re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import and_, case, exists, func, literal, or_, select, text
from sqlalchemy.orm import Session

from app.models.group import PersonGroupMembership
from app.models.ods_clean import CsRecordClean
from app.models.person import Person, PersonAlias

# #332: 退款五路统计切换标准表(std_*) + ods 原始 Refund(全状态诊断专用, std 只含已退款采纳行)
from app.models.refund import Refund
from app.models.refund_standard import StdRefundRecord, StdWalletFlow
from app.models.specialty import PersonSpecialtyAssignment, Specialty
from app.models.staff_assignment import StaffAssignment
from app.models.ticket import ServiceTicket
from app.services.complaint_scope import (
    BUCKET_OTHER,  # noqa: F401
    complaint_bucket_case,
    is_complaint_ticket_cond,
)

# ═══════════════════════════════════════════════════════════════════
# 2026-08-14: 投诉登记节点下的外部投诉/卡商群投诉两个文件曾"暂不纳入"
#   系统汇总分析。设置为 False 后, 部分 analytics 聚合将跳过
#   外部投诉 / 卡商群投诉两封信源表, 仅使用 service_tickets
#   中的投诉分类作为统计口径。
#
# ⚠️⚠️ 2026-09-10 (#47) 改为 True —— 用户拍板 ⚠️⚠️
#   原因(实测):
#     · 用户明确要求「(外)卡商 这个分类在各个统计口都单列出来」;
#       但 False 时来源桶只查 service_tickets → 卡商恒为 0, 需求无法实现。
#     · 造成**同一响应内自相矛盾**: 来源桶说"(外)卡商=0",
#       而 `complaint_card_handlers`(处理人表, 直查三表不受本开关影响)
#       却报"卡商 4 人/69 笔" —— 两个数字同源不同口径。
#   量化影响(窗口 2026-07-01~09-11):
#     外部投诉 343 → 849 (+506, 并入315);
#     (外)卡商   0 →  67 (+67, 单列);
#     合计 11,030 → 11,603 (+573)。
#
#   ⚠️ 本变更与原 LOCKED A-20260902-016 §问题4 记载的
#      "`_INCLUDE_COMPLAINT_FILES = False`(analytics.py:17) 影响
#       complaint_sub/roots 聚合, 但不影响 complaint_pending 的三表直接查询"
#      **相冲突** → 需走人工审核修订该 LOCKED 文档。
#      修订申请见 docs/arch/_pending/A-20260910-005。
#
#   如需回退(不建议): 改回 False 即可, 但卡商单列将失效。
# ═══════════════════════════════════════════════════════════════════
_INCLUDE_COMPLAINT_FILES = True


# ============================================================
# 金额定点化（#222 方案 C′：元单位 + Decimal，禁止 float 进入金额计算）
# ============================================================
def D(val) -> Decimal:
    """金额读取定点化：DB(Numeric)→Decimal 原样；None→Decimal('0')；
    万一混入 float/str 经 str() 取最短十进制表示，不带二进制尾数。

    用法边界：计算路径（累加/比较/聚合）一律用 D()；
    纯出参边界（JSON/Excel dict）允许 float(...) 一次性表示层转换。
    """
    if val is None:
        return Decimal("0")
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))



# ============================================================
# 通用人员筛选 (按部门/分组/专项)
# ============================================================
def _person_filter_conds(department: str | None = None,
                         group_id: int | None = None,
                         specialty_id: int | None = None):
    """返回对Person表的筛选条件列表,用于JOIN后WHERE"""
    conds = []
    if department:
        conds.append(Person.department == department)
    if group_id is not None:
        # 通过PersonGroupMembership关联 (使用EXISTS子查询)
        pass  # group_id/specialty_id通过JOIN处理,这里返回None表示需要额外JOIN
    if specialty_id is not None:
        pass
    return conds

def _apply_person_filters(stmt, person_alias,
                          department: str | None = None,
                          group_id: int | None = None,
                          specialty_id: int | None = None):
    """在已有查询语句上应用人员筛选条件 (person_alias是Person表或其别名)

    通过JOIN/EXISTS方式添加分组和专项筛选,返回修改后的stmt
    """
    if department:
        stmt = stmt.where(person_alias.department == department)
    if group_id is not None:
        # EXISTS子查询检查是否在指定分组
        exists_g = select(1).where(
            PersonGroupMembership.person_id == person_alias.id,
            PersonGroupMembership.group_id == group_id,
            PersonGroupMembership.is_current.is_(True),
        )
        stmt = stmt.where(exists_g.exists())
    if specialty_id is not None:
        # EXISTS子查询检查是否分配了指定专项
        exists_s = select(1).where(
            PersonSpecialtyAssignment.person_id == person_alias.id,
            PersonSpecialtyAssignment.specialty_id == specialty_id,
            PersonSpecialtyAssignment.is_current.is_(True),
        )
        stmt = stmt.where(exists_s.exists())
    return stmt



# ============================================================
# 「有效接待记录(cs_record)」单一谓词 (#326 口径归一)
# ============================================================
# 背景: analytics 各文件此前各自拼装 cs_record 过滤条件, 存在三种
# 写法 —— ① IS NOT NULL + >= + <=  ② 裸 >= + <=  ③ BETWEEN(闭区间)。
# 三者对 NULL 行在 PostgreSQL 中同为 false(NULL 比较结果为 NULL、
# NULL BETWEEN 结果为 NULL, WHERE 中均不通过), 行选择结果完全一致
# → 属等价重复, 统一收敛到本谓词, 消除口径分裂隐患。
# ⚠️ 本谓词只含「时间窗」与「操作人非空」两个最小公共判据;
#    各处更强的专用判据(系统账户黑名单/自动关键词/退款大类等)
#    不在内、不得混用, 保留在各调用点原样。
def _cs_time_cond(start, end):
    """有效接待时间窗谓词(ORM): operation_time 非空且 ∈ [start, end] 闭区间。

    #326: 归一 analytics 内约 29 处手拼写法(①/②/③三种写法语义等价);
    仅收敛时间窗本身, 不含 operator/problem_category 等领域条件。
    """
    return and_(
        CsRecordClean.operation_time.isnot(None),
        CsRecordClean.operation_time >= start,
        CsRecordClean.operation_time <= end,
    )


def _cs_operator_cond():
    """有效操作人谓词(ORM, 弱判据): operator 非空。

    ⚠️ 仅表示「有操作人」; 排除系统/AI 账户的强判据在
    today/_staff 的 _sys_auto_filter 与 refund 模块的 SYS_OP_BLACKLIST_RR
    (裸 SQL 侧), 两者语义更强, 禁止用本弱判据替代。
    """
    return CsRecordClean.operator.isnot(None)


def _cs_time_sql(te_param: str = ":te") -> str:
    """有效接待时间窗谓词(裸 SQL 片段, 供 text() 内嵌入)。

    生成: c.operation_time IS NOT NULL
              AND c.operation_time >= :ts AND c.operation_time <= {te_param}
    te_param 仅兼容各查询截止参数名差异(:te / :te7); 起始参数名统一 :ts
    (现存全部裸 SQL 均如此)。表别名固定 c(现存裸 SQL 均 FROM cs_record_clean c)。
    ⚠️ 拼接的只是代码内常量参数名/别名, 值一律走绑定参数, 不违反参数化铁律。
    """
    return (
        "c.operation_time IS NOT NULL "
        "AND c.operation_time >= :ts "
        f"AND c.operation_time <= {te_param}"
    )


# ============================================================
# 通用工具: 手机号清洗归一化 (_clean_phone)
# ============================================================
def _clean_phone(raw: str | None) -> str | None:
    """清洗并归一化手机号,用于 COUNT(DISTINCT) / TOP 去重 / JOIN

    规则 (严格保持与规范一致):
      1. 空/纯空白 → None
      2. 去掉前缀 +86 / 86 / 0086 / 0 (仅首字符为0时) 及 ( ) - 空格 等分隔符
      3. 含掩码字符(**** / **** / XXXX) → None (不进去重计数,避免归并)
      4. 清洗后长度 != 11 或不是 1 开头纯数字 → None
      5. 其余 → 11 位纯数字字符串
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # 掩码: 任意含 4 位连续 * 或 X 的直接丢
    if "****" in s or "XXXX" in s or "****" in s:
        return None
    # 去所有非数字字符
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    # 掉 0086 / +86 → 等价于去数字前导 86 (仅当总长 13 时)
    if digits.startswith("0086") and len(digits) == 15:
        digits = digits[4:]
    elif digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 12:
        digits = digits[1:]
    if len(digits) != 11 or not digits.startswith("1"):
        return None
    return digits

def _clean_col_expr(col):
    """SQLAlchemy 表达式版本的 _clean_phone (生成 CASE WHEN 表达式返回归一化后的手机号或 NULL)

    用于 COUNT(DISTINCT) 聚合,避免先拉回 Python 再去重。
    """
    # 简化版本: 先 trim, 如果含 ****/XXXX 直接 NULL; 长度 11 且 1 开头的才保留; 其它 NULL。
    # 去前缀 86/+86 的部分: 去掉所有非数字字符后再判断
    raw = func.coalesce(col, literal(""))
    # 去掉非数字字符 (regexp_replace only available in PG, which we're on)
    digits_ = func.regexp_replace(raw, r"\D", "", "g")
    digits = case(
        (func.substring(digits_, 1, 4) == "0086", func.substring(digits_, 5)),
        (func.substring(digits_, 1, 2) == "86", func.substring(digits_, 3)),
        else_=digits_,
    )
    mask_hit = or_(raw.ilike("%****%"), raw.ilike("%XXXX%"))
    ok = and_(func.length(digits) == 11, func.substring(digits, 1, 1) == "1")
    return case(
        (mask_hit, None),
        (ok, digits),
        else_=None,
    ).label("phone_clean")

def _date_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """时间窗: 缺省=最近30天(UTC aware); naive 入参统一按北京时间解释转 UTC aware。

    ⚠️ 2026-09-06 审查修复(#20): API 层 _parse_dt 产出 naive(前端传北京 YYYY-MM-DD),
    若直接与 timestamptz 列比较, PG 按会话时区(UTC)解释 naive → 北京 00:00-08:00 数据
    落在窗外(窗口整体偏 8h)。这里把 naive 视为北京时间 → UTC aware, 消除偏移。
    """
    if end is None:
        end = _dt.datetime.now(_dt.timezone.utc)
    if start is None:
        start = end - timedelta(days=30)

    def _as_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ).astimezone(_dt.timezone.utc)  # naive=北京 → UTC
        return dt.astimezone(_dt.timezone.utc)  # aware 统一转 UTC

    return _as_utc(start), _as_utc(end)



# ---------- 看板2:现金流分析 [全域统一退款核算规范 v3] ----------
#
# 本文件所有看板/接口/指标(退款笔数/金额/真实现金流)必须严格遵守
# 以下"四路经营出款 + 一路实际现金流 + 对账偏差"的统一算法,禁止局部私自改写:
#
# ┌─────────────────── 四路经营侧出款 (经营报表导出,data_source business类型) ────┐
# │ A. 退费单-线上直退  refunds 表 WHERE refund_method = '线上'                    │
# │   (严格按用户要求: 退费单管理里只有这条算退款路线)                              │
# │   sys/system 操作(钱包内部转)仍按 refund_method 字段保留,不做人工剔除           │
# │ B. 钱包-用户取现    wallet_withdrawals 表 WHERE op_type = '用户提现'           │
# │   (钱从钱包真实提出到用户银行卡,构成现金流)                                    │
# │ C. 闪猫退款         shanmao_refunds 独立业务表 (data_sources.code=shanmao_refund)│
# │   已建表有数据,按 status='已退款' + audit_time 聚合                            │
# │ D. 手动退款         manual_refunds 独立业务表 (data_sources.code=manual_refund)│
# │   客服商户号手动退款登记台账,按 is_refunded='是' + COALESCE(op_time,register_time)│
# ├─────────────────── 实际现金流 (财务侧,微信商户号真实扣款,对账基准) ────────────┤
# │ E. 商户号现金流     refund_record_reports 表 WHERE refund_status IN            │
# │                      ('退款成功','成功','SUCCESS')   (2026-09-05 v1.1 起切换) │
# │   不区分 refund_type 字段 (ORIGINAL+BALANCE 全部合计,不分桶C/D)               │
# │   (data_sources.code=refund_record_report, name_cn="退款记录报表" = 对账基准) │
# ├─────────────────── 合计口径 ───────────────────────────────────────────────────┤
# │ 经营估算 (经营侧四路总登记出款) = A + B + C + D                           │
# │ 实际现金流 (财务侧商户号真扣) = E  (refund_record_reports 退款成功合计)       │
# │ 对账偏差 = 经营估算 − 实际现金流 = (A+B+C+D) − E                               │
# │                                                                                │
# │ 术语对齐:                                                                      │
# │   "今日退款笔数/金额" = 路线 A  (退费单线上直退, 业务退款口径)                 │
# │   "真实现金流出 / 现金流出" = A+B+C+D (经营侧四路出款估算)                     │
# │   "商户号实际现金流" = E (refund_record_reports 退款成功,对账基准)            │
# │   "对账偏差" = 经营估算 − 实际现金流 = (A+B+C+D) − E                          │
# └────────────────────────────────────────────────────────────────────────────────┘
#
# 【v2 → v3 改动】(2026-08-02,用户明确纠正):
#   v2错误: 把 C/D 当成 merchant_refunds.refund_type 分桶(ORIGINAL/BALANCE),
#           把 C+D 当成"财务实际"与 A+B 当成"经营估算",导致商户号流水被重复使用
#   v3纠正: C/D 是独立的两张业务表 (闪猫退款 / 手动退款, data_source已注册),
#           E 只作对账基准独立展示, 经营估算 = A+B+C+D, 实际 = E, 对账偏差 = 经营 − 实际
# 【v3 → v4 改动】(2026-08-23): D 路(手动退款)物理表 manual_refunds 已建并导入
#   商户号手机退款处理登记.xlsx, 不再依赖占位表返回 0; 决策概览移除"商户号直退=E"
#   的错误累加, E 只作对账基准独立展示, 并新增登记退款水位(已登记未退款=蓄水)。
# 【v4 → v1.1 改动】(2026-09-05,#20): E 路数据源由 merchant_refunds(一次性 Excel
#   快照) 切换为 refund_record_reports(BMS 退款记录报表,每天采集+导入补历史);
#   旧表 merchant_refunds 保留供 matcher/wide 关联, 不再作 E 统计取数。

# ---------- 路线A 退费单线上直退 ----------
# ⚠️ 2026-09-08 (#41/A-20260908-004): A 路经营口径收紧为「线上 + 已退款」。
# ⚠️ #332 (2026-10-18): A 路取数切换为标准表 std_refund_online —— 采纳作业
#   (refund_adoption, r20261016_v1) 已在表层固化「线上(+空方式捞回) + 已退款 +
#   质量排除」，统计入口不再散落 WHERE。全状态分析(状态分布 / 今日退款挽回
#   工作量)用 _route_a_online_any_status_filter() 读 ods 原始层 Refund
#   (std 表只含已退款采纳行，不含申请中/驳回/转入提现账户)。
#   口径权威: docs/standards/退款五路数据标准与采纳规则.md
def _route_a_online_refund_filter():
    """【路线A经营口径】std_refund_online 已在表层固化采纳口径，恒返回空元组。

    #332 切换后行集由采纳作业固化(status='已退款' AND (refund_method='线上' OR
    refund_method IS NULL)，含空方式捞回 530 行)。调用方保留调用点以维持
    「走统一口径入口」的形态；原 method/status/quality 三段 WHERE 删除。
    """
    return ()

def _route_a_online_any_status_filter():
    """【路线A全状态口径】仅 refund_method='线上'(不看 status)，读 ods 原始层。

    用于需要看全状态的分析(如 operation_status_dist 状态分布 / 今日退款挽回
    工作量)，不用于经营出款统计。std_refund_online 只含已退款采纳行，
    全状态诊断必须读 ods.refunds 原始表(clean 薄视图同投影，#332 起直读原表)。
    """
    return (Refund.refund_method == "线上",)


# ---------- 路线B 钱包用户取现 ----------
# 历史口径(2026-08-28): BMS 提现记录中备注含"提现申请-冻结/退款失败"等的行
#   并非成功退款, 同一笔提现会出现"冻结记录+成功记录"成对; 统一口径(用户确认):
#   所有失败/冻结备注的提现记录均不计入成功退款统计。
# ⚠️ #332 (2026-10-18): B 路取数切换为 std_wallet_flow —— 采纳作业已固化
#   op_type='用户提现' 且备注非失败/冻结(失败/冻结/空 op_type 不进表)。
#   原 _WITHDRAW_FAIL_REMARK_KEYWORDS 关键词机制随之删除(quality_check 与
#   cash_flow_wide_builder 各有独立副本, 不受影响)。
def _route_b_withdraw_filter():
    """【路线B】钱包-用户取现: op_type='用户提现' (成功提现已在表层固化)。

    std_wallet_flow 仅收成功提现(outflow): 提现申请-冻结/退款失败等备注行
    与空 op_type 行不进表(采纳规则 r20261016_v1), 备注排除不再散落于查询。
    """
    return (StdWalletFlow.op_type == "用户提现",)



# ---------- 实际现金流 E: 商户号退款水单表 (独立对账基准) ----------
# ⚠️ 2026-09-05 (#20): E 路对账数据源从 merchant_refunds(旧 Excel 快照) 切换到
#   refund_record_reports(BMS 退款记录报表, 每天采集, 用户补录历史)。旧表保留
#   (供 matcher/wide 关联等), 但不再作为 E 路对账基准。
#   - 时间列: trade_time → refund_time
#   - 状态:   'SUCCESS' → '退款成功'(兼容 '成功'/'SUCCESS' 变体)
_E_ROUTE_SUCCESS_STATUSES = ("退款成功", "成功", "SUCCESS")

def _route_e_cashflow_filter():
    """【实际现金流 E】std_refund_record: refund_status 为退款成功 全部(防御性保留)。

    #332: E 路为标准数据源, 采纳作业按原始数据全量进表(不做状态过滤),
    此处保留 refund_status 过滤作为防御, 防未来采纳规则放宽导致口径漂移。
    """
    return (StdRefundRecord.refund_status.in_(_E_ROUTE_SUCCESS_STATUSES),)



# ---------- 兼容旧接口保留: 直接退款 = 路线A (现在与 _route_a_online_refund_filter 一致) ----------
def _direct_refund_filter():
    """兼容旧名: 直接退款 = 路线A (退费单 refund_method='线上', 严格按用户要求)"""
    return _route_a_online_refund_filter()



# ---------- 看板3:退款分类分析 ----------

# 工单类别兜底 CASE:
#   service_category 为空 → 100%是投诉(已验证7.30-8.1共883条空类别,投诉占比100%)
#   service_category 含"投诉"字样 → 识别为投诉(如 SCRM/平台同步过来的 "业务投诉""用户投诉"等)
#   service_item 含外部投诉关键词(12315/工信部/消协/公共服务平台等) → 识别为投诉
#   其它保持原值(退款/套餐疑问/其他/换货/设备故障/网络故障)
# 用于所有按 service_category 聚合的场景,避免"空"类别在报表里散成孤立点,
# 同时兼容 SCRM 平台把"业务投诉"写进 service_category 的新同步口径。
_EXTERNAL_ITEM_COMPLAINT_KW = (
    "12315", "工信部", "消协", "公共服务平台", "消费者协会",
    "监督局", "市场监管", "市监", "315平台", "12345", "政务平台",
)

def _ticket_category_case(
    col=ServiceTicket.service_category,
    item_col=ServiceTicket.service_item,
):
    """工单「是否属于投诉」判定 → 归一为 '投诉' 或原 category 值

    ⚠️ 口径来源统一到 `services/complaint_scope.is_complaint_ticket_cond`。

    ★★ 2026-09-11 (#56) 判据收敛 ★★
      用户指正: 「服务类别里为空的 默认都回填为 投诉; 你现在每次都去乱查」

      · **移除 item 兜底** —— 原实现叠加 `item ILIKE '%投诉%'`(宽松模式),
        属于**冗余扫描**。实测 `category` 空白 32,058 + 显式'投诉' 5 = 32,063,
        与 #53 四类完备数**完全吻合** → 空白本身就是完整判据。
      · `item_col` 参数**保留但已废弃**(仅为兼容既有调用签名), 内部不再使用。

      业务口径(用户 2026-09-10 澄清): **`service_category` 空白 = 投诉**
      —— 客服登记投诉时不选类别(底层的 BMS 导出漏洞)。
    """
    complaint_cond = is_complaint_ticket_cond(col)
    return case(
        (complaint_cond, "投诉"),
        else_=col,
    ).label("ticket_category")



# 投诉子类(从 service_tickets.service_item 归一化):
#   商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台 → 保留原名
#   其余 → "其它投诉"
def _complaint_subitem_case():
    """投诉子类归一（从 service_tickets.service_item）

    ⚠️ 匹配顺序即优先级 —— **外部平台关键词必须排在「商品/服务」之前**，
       否则 `公共服务平台` 会被 `%服务%` 抢走（实测 87 行被错标为"服务投诉"）。
       这与 `_complaint_source_type_case` 的"外部平台关键词优先"约定保持一致
       （2026-08-25 固化）。修复于 2026-09-10 (#47)。
    """
    si = ServiceTicket.service_item
    return case(
        (si is None, "未标注"),
        # ── 外部平台关键词优先(先于"商品/服务", 见 docstring) ──
        (si.ilike("%12315%"), "全国12315平台"),
        (si.ilike("%工信部%"), "工信部投诉"),
        (si.ilike("%公共%"), "公共服务平台"),
        (si.ilike("%消费者协会%"), "消费者协会"),
        (si.ilike("%消协%"), "消费者协会"),
        # ── 内部/商户号(后置, 避免吃掉外部平台名) ──
        (si.ilike("%商品%"), "商品投诉"),
        (si.ilike("%服务%"), "服务投诉"),
        (si.ilike("%微信%"), "微信投诉"),
        else_=si,
    ).label("complaint_sub")



# ═══════════════════════════════════════════════════════════════════
# 投诉分桶约定(2026-08-25 固化 → 2026-09-10 #47 重构)
# ───────────────────────────────────────────────────────────────────
#   商户号投诉 = service_item 含「微信」(微信投诉, 本质是外部投诉但独立分组)
#   内部投诉   = service_item = 商品投诉 / 服务投诉 (工单直接登记, 未走外部渠道)
#   外部投诉   = service_item 含外部监管/平台关键词(12315/工信部/公共服务平台/
#                消费者协会/消协/监督局/市场监管/市监/315平台/12345/政务平台)
#   其他       = 其余(service_item 为空 / 套餐扣费申诉 等) —— **不再兜底归外部**
#
# 判定优先级: 外部平台关键词 > 微信 > 商品/服务投诉 > 其他
#   ⚠️ 外部平台关键词优先: 若 service_item 同时含外部关键词与微信(如"12315微信投诉"),
#      归「外部投诉」(监管渠道更重)。
#
# ⚠️⚠️ 2026-09-10 (#47) 变更说明 ⚠️⚠️
#   1. **判据修正**: 工单"投诉"判据由 `service_category ILIKE '%投诉%'`
#      改为 **`service_category` 为空白**(用户业务澄清, 实测交叉验证 100% 成立)。
#   2. **取消外部兜底**: 旧版 `else_="外部投诉"` → 新版 `else_="其他"`。
#      实测空白 category 下 item 为空(81) + 套餐扣费申诉(14) = 95 行,
#      混入外部投诉会使其虚高。
#   3. **卡商独立**: `card_complaints` 在**所有统计口单列**为 `(外)卡商` 桶,
#      不再并入「外部投诉」(需修订 LOCKED A-20260902-016, 见 #47 设计文档)。
#   4. **去重**: 按 `(SN, 手机)` + 30 天窗合并同一笔多渠道投诉。
#   5. **状态合并**: 任一渠道为完成态即视为已处理。
#   6. **★★ #65 软纠正下沉 (#58)**: 工信部工单若 SN 在 315 文档属监所/市监局类
#      → 统计时**纠正为外部投诉**。原仅对账侧生效 → 看板少 160 条真实监所投诉。
#
# 实现单一来源: `app/services/complaint_scope.py`(常量/判据/CASE 均出自该模块),
#   本文件的 `_ticket_category_case` / `_complaint_source_type_case` 仅作薄封装,
#   避免多处关键词判断漂移。
# 全域统一: 所有投诉统计/明细/看板/涉诉 均复用本函数, 不得另写关键词判断
#   (涉诉 SQL 硬编码处见 analytics.py 6628/7110, 与本法一致, 仅原生SQL场景)。
# ═══════════════════════════════════════════════════════════════════
# ⚠️⚠️ #56/#57 已废弃: 以下三个常量**不再被任何逻辑使用** ——
#    口径已全部下沉到 `complaint_scope.py`(`_EXTERNAL_PLATFORM_KEYWORDS` 等)。
#    保留仅为**历史存档/人工排查时对照**, 切勿在任何新代码中引用。
#    尤其注意 `_EXTERNAL_PLATFORM_KEYWORDS` 仍含「工信部」—— 那是 #57 **之前**的旧口径。
#    使用前请先确认 `complaint_scope` 同名常量的**当前**值。
_EXTERNAL_PLATFORM_KEYWORDS = (  # ⚠️ DEPRECATED — 含已移出的「工信部」
    "工信部", "12315", "公共服务平台", "消费者协会", "消协",
    "监督局", "市场监管", "市监", "315平台", "12345", "政务平台",
)

_MERCHANT_COMPLAINT_KEYWORDS = ("微信",)   # ⚠️ DEPRECATED

_INTERNAL_COMPLAINT_KEYWORDS = ("商品投诉", "服务投诉")  # ⚠️ DEPRECATED

def _complaint_source_type_case(
    col=ServiceTicket.service_item,
    channel_col=ServiceTicket.channel,
    sn_col=ServiceTicket.device_sn,
):
    """工单投诉分桶(CASE WHEN) → 内部投诉 / 商户号投诉 / 外部投诉（**三类**）

    ⚠️ 口径来源统一到 `services/complaint_scope.complaint_bucket_case`。
       全域唯一来源，不得另写关键词判断。

    ★★ 2026-09-11 (#53) 口径二次重构 ★★
      · **(外)卡商 移出本函数** —— 它是**独立统计体系**(`CARD_SCOPE`),
        来源=补充表 `card_complaints`, **不参与投诉总量**。
        用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
        实证: 工单 `service_item`/`service_category` 含「卡商」均为 **0 行**。
      · **移除「其他」桶** —— 改 `else_=BUCKET_INTERNAL`(内部投诉)。
        channel 回退可救回 78 行「item 空 + channel=微信投诉」→ 商户号。
        用户: "还有部分 似乎应该归类为 内部投诉"。
      · **新增 `channel` 回退** —— item 无法判定时用渠道兜底(实测 81 行空 item)。

    优先级: 外部平台词 > 微信 > 商品/服务 > 套餐扣费申诉 > channel微信 > 内部(兜底)
      ⚠️ 外部平台关键词优先: "12315微信投诉"归「外部投诉」(监管渠道更重, 2026-08-25 固化)。

    ★★ 2026-09-11 (#65) 软纠正接入 ★★
      `sn_col` 默认 `ServiceTicket.device_sn` → **默认启用 #58 软纠正**。
      实测影响(近3月): 工信部桶 188 条中 **160 条** SN 属监所类 → 改判外部投诉。
      看板「外部投诉」由 211 → **371**(与对账接口一致)。
      同时修复了原先 188 条工信部工单在统计层**两边都不算**的黑洞。
    """
    return complaint_bucket_case(col, channel_col, sn_col)


# ★ #85: 315 补充表「已办结」结果枚举 —— 用于趋势图「处理」序列。
#   ⚠️ 与 `app/api/v1/complaints._EXTERNAL_DONE_RESULTS` **必须逐字一致**。
#     之所以本地再定义一份而不 import：`complaints.py` 模块级
#     `from app.services.analytics import ...`，此处反向 import 会形成循环依赖。
#     （同类处理见本文件对 `_sources_reg_handled` 的惰性导入）
#   语义: 315 推送的办结结果（和解结案/撤销结案等）应视为已处理；
#         「未达成一致方案/暂行办理/需跟进/空」不算办结（可能仍需跟进）。
_EXTERNAL_DONE_RESULTS_LOCAL = {
    "和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案",
    "投诉无效", "重复投诉", "终止调节", "退回消委会",
}



# 工单「真实完成」阈值（秒）—— **单一来源** (#87)
# 系统建单时会把 finish_time 默认填成 register_time（或差几秒），故 finish−register
# ≥ 180s 才认可是真实完成。`api/v1/complaints._REAL_FINISH_EPOCH_SECS` 从本常量导入，
# 避免两处各写一个 180 而漂移（#83 的退款笔数就是这么漂的）。
REAL_FINISH_EPOCH_SECS = 180



# 投诉工单根因分类 CASE (用关联 cs_record.problem_category/problem_type 归并):
#   网络质量类: 网络问题/网卡/慢/信号差/无网络/离线/无WiFi/激活网卡
#   套餐争议类: 套餐问题/涨价/套餐升级/流量统计差异/360天套餐/连续包月 等
#   退款失败类: 退款大类 (cs_record.problem_category='退款' 表示客服在推退款方案仍升级投诉)
#   降成争议类: 降成问题/限速涨价
#   其它(注销/发票/退换货等): 其他问题/激活实名
def _complaint_root_case(pcat, ptype):
    net_kw = ("网", "卡", "信号", "离线", "WiFi", "wifi")
    pkg_kw = ("套餐", "涨价", "流量统计", "360天", "包月", "加油包", "返现", "预存", "切网")
    downgrade_kw = ("限速", "降成", "钻石VIP", "VIP", "赠送")
    other_kw = ("注销", "发票", "退换货")
    # 先判断 problem_type 关键词 (更细)
    pt_type = func.coalesce(ptype, literal(""))
    pc_cat = func.coalesce(pcat, literal(""))
    return case(
        # 网络质量: 类型含网/卡/信号/离线 或 大类是网络问题
        (
            or_(
                pc_cat == "网络问题",
                *[pt_type.ilike(f"%{k}%") for k in net_kw],
            ),
            "网络质量类",
        ),
        # 套餐争议: 大类=套餐问题 或 类型含套餐/涨价 等关键词
        (
            or_(
                pc_cat == "套餐问题",
                *[pt_type.ilike(f"%{k}%") for k in pkg_kw],
            ),
            "套餐争议类",
        ),
        # 退款方案仍升级成投诉 (客服在推退款但用户不满升级投诉)
        (pc_cat == "退款", "退款协商失败类"),
        # 降成/限速争议
        (
            or_(
                pc_cat == "降成问题",
                *[pt_type.ilike(f"%{k}%") for k in downgrade_kw],
            ),
            "降成争议类",
        ),
        # 其他问题 子项 (注销/发票/退换货)
        (
            or_(
                pc_cat == "其他问题",
                pc_cat == "激活实名",
                *[pt_type.ilike(f"%{k}%") for k in other_kw],
            ),
            "其它(注销/发票/退换)",
        ),
        else_="待标注",
    ).label("complaint_root")



# ---------- 看板4:人员操作分析 ----------


# ══════════════════════════════════════════════════════════════════════
# ★ 投诉专项合并（2026-09-14 用户决策）—— **必须在 _PERSON_CAT_NAMES 之前定义**
# ══════════════════════════════════════════════════════════════════════
# 背景: 用户将「投诉组」的行政与专项合一 —— 内/外投诉不再分线，
#   统一为一个「投诉专项」，由洪佳任专项负责人。
#
# ★★ 合并策略:「只增不改」(关键设计决策) ★★
#   · **新建** `投诉专项`(系数 5.0 = 原外部投诉值, 用户拍板)
#   · **保留** `内部投诉`/`外部投诉` 两条旧专项记录（不删不改名）
#     → 历史任职仍指向旧专项，**历史绩效可追溯、不重算**
#   · 代码只**新增**新专项映射，**保留**旧映射作为**历史归因**通道
#     → 生效日之前的任职仍能被正确映射
#
# ⚠️⚠️ 绝不可把旧映射改成指向新专项 —— 那会把**历史区间的内部投诉工作
#   按 5.0 系数重算**（原为 3.0），= 静默篡改历史绩效。
#
# ⚠️ 与「投诉来源桶」的**同名陷阱**: 专项名「内部投诉/外部投诉」与
#   `complaint_scope.BUCKET_INTERNAL/EXTERNAL`（投诉**来源**分桶）
#   **字符串相同但语义完全不同**。本常量只约束**人员专项**，
#   **不得**用于来源分桶，反之亦然。
LEGACY_COMPLAINT_SPECIALTIES = ("内部投诉", "外部投诉")

COMPLAINT_SPECIALTY = "投诉专项"

COMPLAINT_SPECIALTY_WEIGHT = 5.0

# 全量投诉类专项（新旧合并）—— 用于「是否投诉专员」判定
COMPLAINT_SPECIALTIES_ALL = (COMPLAINT_SPECIALTY, *LEGACY_COMPLAINT_SPECIALTIES)

def _strip_qw_prefix(x: str | None) -> str | None:
    """终极兜底: Python端去掉人名前的部门/系统前缀(企微-/店铺-/生产-/财务-/云客猫-/全球wifi- 等),
    避免维表漏登记或 display_name 带前缀导致同一人分两桶。仅作为 display_name 兜底用。
    2026-08-11 扩展: 复用 person_extractor._NAME_PREFIXES 全量前缀, 覆盖生产/财务/测试等独立部门。"""
    if not x or not isinstance(x, str):
        return x
    from app.services.person_extractor import _NAME_PREFIXES, _PREFIX_SEPARATORS
    s = x
    changed = True
    while changed:
        changed = False
        low = s.lower()
        for pfx in _NAME_PREFIXES:
            if low.startswith(pfx.lower()):
                s = s[len(pfx):]
                while s and s[0] in _PREFIX_SEPARATORS:
                    s = s[1:]
                changed = True
                break
    return s if s else x  # 剥光(纯前缀词)则返回原值

_TIME_BINS = [
    ("<0异常", -1e9, 0),
    ("=0天(秒级)", 0, 0),
    ("0~1天", 0, 1),
    ("1~3天", 1, 3),
    ("3~7天", 3, 7),
    ("7~15天", 7, 15),
    ("15~30天", 15, 30),
    ("30天+", 30, 1e9),
]

def _bin_label(days: float) -> str:
    if days < 0:
        return "<0异常"
    if days == 0:
        return "=0天(秒级)"
    for lbl, lo, hi in _TIME_BINS[2:]:
        if lo < days <= hi:
            return lbl
    return "30天+"

def _naive(dt: datetime | None) -> datetime | None:
    """把 timestamptz 感知时间统一转成 naive(去掉 tzinfo), 用于和 fromisoformat 得到的 naive 日期进行比较."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        # 取 DB 层存储的"墙上时间"直接剥离 tz, 不做 UTC 换算
        # (数据库写入和本地查询都在同一个 TZ 语境下做日粒度筛选, 这是最稳定的口径)
        return dt.replace(tzinfo=None)
    return dt



# 有效退款方式: A = 转入提现账户 (退款到钱包, 后续用户提现构成现金流出)
#                  B = 直接线上退款 (严格按统一规范: 只认 refund_method='线上' 才算一条退款路线)
#                  其他方式(线下/未填写/含"微信/支付宝/银联/银行卡/直退"关键词但不含"线上"二字) → 无效剔除
# 全域对齐规范: 统一核算的路线A = 这里的路径B (退费单线上直退), 路线B = 这里的路径A (钱包用户提现)
_B_METHOD_KEYS = ("线上",)

def _classify_method(m: str | None) -> str | None:
    """返回 'A' / 'B' / None(无效方法, 从看板剔除)."""
    if not m:
        return None
    if "转入" in m:
        return "A"
    if any(k in m for k in _B_METHOD_KEYS):
        return "B"
    return None  # 线下 / 未填写 / 其他 → 非实际现金退款路径, 剔除

def _pct(v: float, tot: float) -> float:
    return round(100.0 * v / tot, 2) if tot else 0.0

_REFUND_REMARK_COMPLAINT_MARKERS = ("涉投诉", "客诉", "投诉", "315", "争议", "催退")


# 涉诉分类 → 用户风险等级 (2026-08-26 用户口径): 外部/商户号投诉=高风险, 内部投诉及备注涉诉=中风险, 普通退款=低风险
_RISK_LEVEL_BY_COMPLAINT = {
    "外部投诉": "高风险", "商户号投诉": "高风险",
    "内部投诉": "中风险", "备注涉诉": "中风险",
    "普通退款": "低风险",
}

def _is_meta_junk_operator(s) -> bool:
    """识别被同步时间戳/占位符污染的 operator 字段, 供筛选候选项清洗(不影响明细行展示)。"""
    s = (s or "").strip()
    if not s:
        return True
    if s in ("系统", "SYS", "系统自动", "SYS/系统自动", "未填写", "-", "/"):
        return True
    if _re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return True
    # 纯数字/符号的杂串(如 "37.7÷2=18"), 姓名不可能仅含这些
    if _re.fullmatch(r"[\d\s÷×=+\-*/%.<>()\[\]]+", s):
        return True
    return False

def _chain_alias_map(db: Session) -> dict[str, str]:
    """PersonAlias→display_name 归一映射, 与决策概览/人员统计展示口径一致。"""
    alias_map: dict[str, str] = {}
    for a, dn in db.execute(
        select(PersonAlias.alias, func.coalesce(Person.display_name, PersonAlias.alias))
        .select_from(PersonAlias).outerjoin(Person, PersonAlias.person_id == Person.id)
    ):
        if a:
            alias_map[str(a)] = str(dn)
    return alias_map

def _norm_chain_operator(op, alias_map: dict[str, str]) -> str:
    if not op:
        return ""
    raw = str(op)
    if raw in alias_map:
        return _strip_qw_prefix(alias_map[raw])
    stripped = _strip_qw_prefix(raw)
    if stripped != raw and stripped in alias_map:
        return _strip_qw_prefix(alias_map[stripped])
    return stripped or raw

def _chain_sn_complaint_map(db: Session, sns) -> dict[str, str]:
    """按设备SN关联工单的三源投诉分类映射 {sn: 涉诉分类}, 优先级 外部投诉>商户号投诉>内部投诉。

    关键词与 decision_overview 涉诉 SQL / _complaint_source_type_case 约定一致(2026-08-25 固化)。
    无关联工单 / 关联工单无投诉分类的 SN 不返回(由调用方兜底为普通退款)。
    """
    sns = list({s for s in sns if s})
    if not sns:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(sns), 500):
        chunk = sns[i:i + 500]
        ph = ",".join(f":sn{j}" for j in range(len(chunk)))
        rows = db.execute(text(f"""
            SELECT s.device_sn AS sn,
                bool_or(s.service_item ILIKE '%工信部%' OR s.service_item ILIKE '%12315%'
                    OR s.service_item ILIKE '%公共服务平台%' OR s.service_item ILIKE '%消费者协会%'
                    OR s.service_item ILIKE '%消协%' OR s.service_item ILIKE '%监督局%'
                    OR s.service_item ILIKE '%市场监管%' OR s.service_item ILIKE '%市监%'
                    OR s.service_item ILIKE '%315平台%' OR s.service_item ILIKE '%12345%'
                    OR s.service_item ILIKE '%政务平台%') AS has_ext,
                bool_or(s.service_item ILIKE '%微信%') AS has_merchant,
                bool_or(s.service_item ILIKE '%商品投诉%' OR s.service_item ILIKE '%服务投诉%') AS has_int
            FROM service_tickets s
            WHERE s.device_sn IN ({ph})
            GROUP BY s.device_sn
        """), {f"sn{j}": sn for j, sn in enumerate(chunk)}).fetchall()
        for r in rows:
            if r.has_ext:
                out[r.sn] = "外部投诉"
            elif r.has_merchant:
                out[r.sn] = "商户号投诉"
            elif r.has_int:
                out[r.sn] = "内部投诉"
    return out

def _annotate_chain_litigation(db: Session, rows):
    """为全链路明细行计算涉诉分类 complaint_type (普通退款/内部投诉/商户号投诉/外部投诉/备注涉诉)。

    - 优先按设备SN关联投诉工单判断(外部>商户号>内部);
    - 无单关联且路径为 A/B(线上直退)时, 回退到退款记录审核备注/备注含投诉类标识 → 备注涉诉;
    - 其余 → 普通退款。
    """
    comp_map = _chain_sn_complaint_map(db, [r.get("device_sn") for r in rows])
    ab_nos = [r["refund_no"] for r in rows if r["path"] in ("A", "B")]
    remark_map: dict[str, str] = {}
    for i in range(0, len(ab_nos), 500):
        chunk = ab_nos[i:i + 500]
        for no, ar, rm in db.execute(
            # 读 ods 原始层: 链路行含转入提现账户(被 std A 路排除), 反查备注不能读 std
            select(Refund.refund_no, Refund.audit_remark, Refund.remark).where(Refund.refund_no.in_(chunk))
        ):
            remark_map[str(no)] = ((ar or "") + (rm or ""))
    for r in rows:
        ctype = comp_map.get(r.get("device_sn"))
        if not ctype and r["path"] in ("A", "B"):
            txt = remark_map.get(str(r["refund_no"]), "")
            if any(m in txt for m in _REFUND_REMARK_COMPLAINT_MARKERS):
                ctype = "备注涉诉"
        r["complaint_type"] = ctype or "普通退款"
        r["risk_level"] = _RISK_LEVEL_BY_COMPLAINT.get(r["complaint_type"], "低风险")
    return rows

def _chain_complaint_filter(ctype: str, comp: str) -> bool:
    if comp in (None, "", "all"):
        return True
    if comp == "any":
        return ctype != "普通退款"
    label = {
        "internal": "内部投诉", "merchant": "商户号投诉",
        "external": "外部投诉", "remark": "备注涉诉", "normal": "普通退款",
    }.get(comp)
    return label == ctype



# ---------- 看板7: 今日看板 (单日切面 + 实时节奏) ----------

# 用于"昨日同时段"对比: 比较 00:00 ~ 当前时间(而不是整天), 避免白天看跌90%的误导.
# 用法: _today_window(None) -> (今天00:00, 现在), _yesterday_same_window(None) -> (昨天00:00, 昨天此时)
# 当 date 显式传入(如选了昨天或某历史日期), 窗口就是整天 00:00 ~ 23:59:59.
TZ = __import__("zoneinfo").ZoneInfo("Asia/Shanghai")

def _resolve_windows(
    date: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[_dt.datetime, _dt.datetime, _dt.datetime, _dt.datetime, bool, tuple[str, str] | None]:
    """统一时间窗口解析: 优先 start/end 范围, 否则退回 date 单日.

    Returns:
        (cur_start, cur_end, prev_start, prev_end, is_realtime, (display_start_iso, display_end_iso) or None)
        - prev_* 是用于同比/环比的前一个完整周期:
          * 单日 → 前一天平移 (和 _today_windows 完全一致)
          * 周/月/季度 → 向前平移 range_length 天 (保证周期长度一致)
        - is_realtime: 是否实时(今天未结束)窗口
        - display_*: Asia/Shanghai 时区 ISO 字符串 (前端友好显示/水印), 单日模式下为 None 让 today_* 自行沿用 date 字段
    """
    _utc = _dt.timezone.utc

    def _sh_to_naive_utc(dt_sh: _dt.datetime) -> _dt.datetime:
        return dt_sh.astimezone(_utc).replace(tzinfo=None)

    # ── 优先 start/end 模式 ──
    if start and end:
        s_d = _parse_date(start)
        e_d = _parse_date(end)
        if s_d is None or e_d is None:
            # 解析失败, 退回今日实时
            return _today_windows(None) + (True, None)
        if s_d > e_d:
            s_d, e_d = e_d, s_d
        now_sh = _dt.datetime.now(TZ)
        today_d = now_sh.date()
        span_days = (e_d - s_d).days + 1

        # 周期结束日 = end 当天; 若 end == 今天 → 实时窗口截止 now; 否则整天 23:59:59
        if e_d == today_d:
            cur_end_sh = now_sh
            is_realtime = True
        else:
            cur_end_sh = _dt.datetime.combine(e_d, _dt.time(23, 59, 59, 999999), tzinfo=TZ)
            is_realtime = False
        cur_start_sh = _dt.datetime.combine(s_d, _dt.time(0, 0, 0), tzinfo=TZ)

        # 上一个周期: 向前平移 span_days 天 (保证两个周期长度严格一致)
        prev_start_sh = cur_start_sh - timedelta(days=span_days)
        prev_end_sh = cur_end_sh - timedelta(days=span_days)

        display = (cur_start_sh.isoformat(), cur_end_sh.isoformat())
        return (
            _sh_to_naive_utc(cur_start_sh),
            _sh_to_naive_utc(cur_end_sh),
            _sh_to_naive_utc(prev_start_sh),
            _sh_to_naive_utc(prev_end_sh),
            is_realtime,
            display,
        )

    # ── 退回 date 单日模式 (和原 _today_windows 完全一致) ──
    # 2026-09-11 (#66): 新增分钟级单日窗口 —— date='YYYY-MM-DDTHH:MM' 时,
    # 窗口为「该日 00:00 ~ 该分钟」, 同比窗口为「前一天同刻」。下游据此做同刻日比对。
    minute_end = _parse_minute_end(date)
    if minute_end is not None:
        d = minute_end.date()
        now_sh = _dt.datetime.now(TZ)
        t_start = _dt.datetime.combine(d, _dt.time(0, 0, 0), tzinfo=TZ)
        t_end = minute_end.replace(tzinfo=TZ)
        # 同刻平移一天(与前一日 00:00~同刻 严格对齐, 保证两个周期边界一致)
        y_start = t_start - timedelta(days=1)
        y_end = t_end - timedelta(days=1)
        display = (t_start.isoformat(), t_end.isoformat())
        return (
            _sh_to_naive_utc(t_start),
            _sh_to_naive_utc(t_end),
            _sh_to_naive_utc(y_start),
            _sh_to_naive_utc(y_end),
            # 分钟窗口已终结(护栏保证不晚于当前时刻) → 非实时, 可长缓存
            False,
            display,
        )

    d = _parse_date(date)
    now = _dt.datetime.now(TZ)
    is_today = d is None or d == now.date()
    if is_today:
        t_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        t_end = now
        y_start = t_start - timedelta(days=1)
        y_end = t_end - timedelta(days=1)
    else:
        t_start = _dt.datetime.combine(d, _dt.time(0, 0, 0), tzinfo=TZ)
        t_end = t_start + timedelta(days=1) - timedelta(microseconds=1)
        y_start = t_start - timedelta(days=1)
        y_end = t_end - timedelta(days=1)
    return (
        _sh_to_naive_utc(t_start),
        _sh_to_naive_utc(t_end),
        _sh_to_naive_utc(y_start),
        _sh_to_naive_utc(y_end),
        is_today,
        None,
    )

def _today_windows(date: _dt.date | None) -> tuple[_dt.datetime, _dt.datetime, _dt.datetime, _dt.datetime]:
    """返回 (today_start, today_end, yoy_start, yoy_end) —— 向后兼容, 直接调 _resolve_windows()."""
    date_str = date.isoformat() if date else None
    ts, te, ys, ye, _, _ = _resolve_windows(date=date_str)
    return ts, te, ys, ye

def _parse_date(s: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(s) if s else None

def _parse_minute_end(s: str | None) -> _dt.datetime | None:
    """解析分钟级单日窗口 `YYYY-MM-DDTHH:MM` → naive datetime(北京时间); 非该形态返回 None。

    2026-09-11 (#66): 只读 API 单日放开到分钟, 便于下游做同刻日比对。
    仅认长度 16 且第 11 位为 'T' 的串; `YYYY-MM-DD` / 带秒 / 带时区偏移一律返回 None,
    由调用方走原有纯日期路径(保持纯日期行为与既有缓存 key 完全不变)。
    """
    if not s or not isinstance(s, str):
        return None
    raw = s.strip()
    if len(raw) != 16 or raw[10] != "T":
        return None
    try:
        return _dt.datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None

def _pct_delta(cur, prev) -> float | None:
    """环比百分比: (cur-prev)/prev*100, prev为0返回None."""
    if prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100.0, 1)

_COMPLAINT_LEAD_POSITIONS = ("组长", "副主管")


# ══════════════════════════════════════════════════════════════
# 岗位值「投诉专员」（2026-09-14 用户决策）
# ══════════════════════════════════════════════════════════════
# 背景: 原 `position` 枚举只有 客服/组长/副主管/主管，但投诉团队实际存在
#   专职处理投诉的角色，需要与「客服」区分（否则岗位负荷表把投诉专职
#   和普通接待混在一起，看不出专职 vs 兼办）。
#
# ⚠️ 两个不同概念、勿混（本仓库已因此差点搞错）:
#   · `position`(岗位)  = 客服 / 组长 / 副主管 / 主管 / **投诉专员** → 驱动岗位负荷分桶
#   · `role`(角色/权限) = agent / specialist / specialty_lead / supervisor / admin
#     → 「专员」在 `role` 里早就有(specialist)，与本次 position 新增不是一回事。
#
# ⚠️ 副作用（有意为之）: 值中含「专员」→ 命中 `EXEMPT_POSITION_PATTERNS`
#   → 该人获得**跨类全豁免**（所有专项按各自默认系数计权重，而非转接 0.2）。
#   这是正确的 —— 投诉专员处理的高难度投诉不应按「转接接待」贬值。
#   但**会抬高其工作量得分**，属预期内变化。
COMPLAINT_SPECIALIST_POSITION = "投诉专员"

def _person_position_map(db: Session) -> dict[str, dict[str, Any]]:
    """返回 {别名: {person_id, display_name, position, specialty, bucket}}

    `bucket` ∈ {投诉专员, 组长, 客服, 其他} —— 用于「岗位负荷」分组。
    ⚠️ 以 `PersonAlias` 为入口（工单 handler 就是别名原文），
       岗位/专项取自 **镜像字段**（`Person.position`/`specialty`，已与 SCD 源同步）。
    """
    rows = db.execute(
        select(
            PersonAlias.alias,
            Person.id,
            Person.display_name,
            Person.position,
            func.coalesce(Person.specialty, Person.primary_specialty).label("specialty"),
        ).outerjoin(Person, PersonAlias.person_id == Person.id)
    ).all()

    # 专项 → 别名集合（SCD 真身，用于交叉校验镜像缺失的人）
    scd_specialists: dict[int, str] = {}
    for pid, sp_name in db.execute(
        select(PersonSpecialtyAssignment.person_id, Specialty.name)
        .join(Specialty, PersonSpecialtyAssignment.specialty_id == Specialty.id)
        .where(
            PersonSpecialtyAssignment.is_current.is_(True),
            # 含合并后的新专项 + 历史两条（历史区间仍能正确归因）
            Specialty.name.in_(COMPLAINT_SPECIALTIES_ALL),
        )
    ).all():
        scd_specialists[int(pid)] = str(sp_name)

    # SCD 真身岗位（镜像缺失时兜底，且与镜像做交叉；两者 position 实测不一致 = 0）
    scd_positions: dict[int, str] = {}
    for pid, pos in db.execute(
        select(StaffAssignment.person_id, StaffAssignment.position).where(
            StaffAssignment.is_current.is_(True),
            StaffAssignment.position.isnot(None),
        )
    ).all():
        scd_positions[int(pid)] = str(pos)

    def _bucket(pos: str | None, spec: str | None, pid: int | None) -> str:
        p = (pos or scd_positions.get(pid or -1, "") or "").strip()
        s = (spec or scd_specialists.get(pid or -1, "") or "").strip()
        # ① 岗位直判「投诉专员」优先于专项 ——
        #    用户明确任命为专职的，不因未指派专项而落入「客服」。
        if p == COMPLAINT_SPECIALIST_POSITION:
            return "投诉专员"
        # ② 否则看专项：所有投诉类专项（含合并后新专项 + 历史两条）
        if s in COMPLAINT_SPECIALTIES_ALL:
            return "投诉专员"
        if p in _COMPLAINT_LEAD_POSITIONS:
            return "组长"
        if p == "客服":
            return "客服"
        return "其他"

    out: dict[str, dict[str, Any]] = {}
    for alias, pid, dname, pos, spec in rows:
        if not alias:
            continue
        out[str(alias)] = {
            "person_id": int(pid) if pid else None,
            "display_name": dname,
            "position": pos,
            "specialty": spec,
            "bucket": _bucket(pos, spec, int(pid) if pid else None),
        }
    return out

def _derive_service_item_for_refund(
    remark: str | None,
    audit_remark: str | None,
    package_name: str | None,
    refund_reason: str | None,
) -> tuple[str, str | None]:
    """根据退款单 remark/audit_remark/package 猜 service_category + service_item

    返回: (service_category, service_item). 命中明确关键词优先:
      「超期」→ 退款 / 退款-超期纠纷
      「充值」+ 退款 → 退款 / 充值错误
      网慢/网卡/无网络 → 投诉 / 网络问题
      不想用 / 要退 → 退款 / 不想用了
      设备坏 → 投诉 / 设备故障
    兜底 scat=退款 sitm=退款管理-未分类
    """
    blob = " ".join(x or "" for x in [remark, audit_remark, package_name, refund_reason])
    b = blob.lower()
    if any(k in b for k in ["超期", "超期订单不允许"]):
        return "退款", "退款-超期纠纷"
    if any(k in blob for k in ["充值错误", "充错", "充错了", "冲错"]):
        return "退款", "充值错误"
    if any(k in b for k in ["网慢", "网卡", "无网络", "没网", "断网", "信号差", "网速"]):
        return "投诉", "网络问题"
    if any(k in b for k in ["不想用", "不要了", "用不上", "不想续费", "不需要了", "不用了", "退订"]):
        return "退款", "不想用了"
    if any(k in b for k in ["设备坏", "坏了", "设备故障", "硬件", "无法开机", "坏机"]):
        return "投诉", "设备故障"
    # WiFi钱包充值
    if any(k in (package_name or "") for k in ["WiFi钱包", "钱包"]):
        return "退款", "充值错误"
    return "退款", "退款管理-未分类"



# ==============================================================================
# 退款挽回专项分析 (独立视图, 不冲抵接待效率)
#   业务逻辑:
#     1. 分母 = 退款专项组人员, 在周期内通过 cs_records 接待的退款类设备SN去重集合
#     2. 挽留失败 = 分母SN中, 出现在 refunds(线上退款单) 或 service_tickets(退款类工单) 中的SN
#     3. 挽留成功 = 分母SN - 挽留失败SN
#     4. 成功率 = 挽留成功 / 分母
#     5. 原因分布: 基于挽留失败SN对应的 refund_reason / service_item 聚合
#     6. 周/日趋势: 按周/日 拦截订单 + 成功率 走势
# ==============================================================================

# 注意: 口径原则 —— 分组(group_name)=行政/管理维度, 专项(specialty/hr_cat)=业务内容维度.
# 退款挽回专项只按"专项内容"判定, 不看管理分组 (避免同一管理组内不同专项被误圈).
# 之前的 REFUND_RECOVERY_GROUPS (基于组名) 已废弃, 统一只看 REFUND_RECOVERY_HR_CATS.
REFUND_RECOVERY_HR_CATS = ("退款挽回",)             # 判定条件: HR专项(=Person.specialty 映射)为"退款挽回"

SYS_OP_BLACKLIST_RR = ("sys", "system", "SYS", "System", "用户自助", "客户自助", "企微用户自助")


# 退款原因归一化映射 (兼容 refund_reason 文本 和 service_item 文本)
_REASON_NORMALIZE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("不想用", "不想要", "没用了", "不需要", "退订", "取消"), "不想用了"),
    (("充错", "充值错", "充多", "误充", "错充"), "充值错误"),
    (("设备", "坏了", "故障", "损坏", "不能用", "用不了", "死机", "卡机"), "设备故障"),
    (("网卡", "卡慢", "网速", "网络慢", "网慢", "没网", "无网", "没信号", "信号差", "掉线"), "网卡/卡无网络"),
    (("套餐", "流量", "升级", "涨价", "资费", "太贵"), "套餐问题(流量虚、涨价、升级)"),
    (("卡商", "渠道", "代理", "上游"), "卡商问题"),
]

def _normalize_refund_reason(raw: str | None) -> str:
    """把 refund_reason / service_item 原始文本 归一化到 7 大标准原因桶."""
    if not raw:
        return "其他(填写备注)"
    s = raw.strip()
    if not s:
        return "其他(填写备注)"
    low = s.lower()
    for kws, bucket in _REASON_NORMALIZE_RULES:
        if any(k.lower() in low for k in kws):
            return bucket
    return "其他(填写备注)"

def _is_refund_staff(grp: str | None, hr_cat: str | None) -> bool:
    """判定是否属于退款挽回专项组人员.

    口径原则: 分组(group_name)=行政/管理维度, 专项(hr_cat/specialty)=业务内容维度.
    只按"专项内容"判定, 不看管理分组 (避免同一管理组内不同专项被误圈).
    同时保留"套餐专项"优先排除, 防止其它分支误判.
    """
    if hr_cat == "套餐专项":
        return False
    if hr_cat and hr_cat in REFUND_RECOVERY_HR_CATS:
        return True
    return False


# ============================================================
# 通用设备维度筛选 (#273/#292 口径修正): 退款/投诉明细共用同一口径
#   · 设备类型 = bms_orders 最新订单(create_date DESC) 的 device_type_name
#   · 套餐     = bms_orders 最新订单的 combo_name; 工厂质检测试套餐(3 变体)不计入
#   · 商户号   = devices.merchant_name(设备归属商户, #271 links/merchants 表全空)
#   · 激活时间 = bms_orders MIN(active_date) 最早激活日, 无则 devices.active_time 兜底
#               落在 [active_start, active_end] 北京日期闭区间(并集口径)
# ============================================================
def _device_dim_matched_sns(
    db: Session,
    device_type: str | None = None,
    combo: str | None = None,
    merchant: str | None = None,
    active_start: str | None = None,
    active_end: str | None = None,
) -> set[str] | None:
    """返回命中设备维度条件的 SN 集合; 全部条件为空时返回 None(不筛选, 避免全表扫描)。

    #292 口径修正(2026-10-09): 设备类型/套餐改查 bms_orders 最新订单字段,
    激活时间改查 bms_orders MIN(active_date) 或 devices.active_time 兜底(并集)。
    不同条件查不同源, 各自取 SN 集合后取交集(AND 语义)。
    """
    from app.models.device import Device
    from app.models.ods_clean import BmsOrderClean

    sn_sets: list[set[str]] = []

    # ── 设备类型 / 套餐: bms_orders 最新订单(create_date DESC 取首条)字段 ──
    # 修复(#293): ① distinct_on() 不是 Select 的方法(PG DISTINCT ON 应写 .distinct(expr)),
    #   自 #292 引入以来投诉积压/退款链条页选「设备类型/套餐」筛选即 500;
    # ② 类型/套餐筛选必须作用在「每台设备最新订单」**之后**(外层 where)——若放在同一
    #   select 里, WHERE 先于 DISTINCT ON 执行, 语义变成"曾有该类型订单即命中",
    #   与 #292 口径「最新订单的类型」不符(命中集偏大);
    # ③ #293: 与 _device_dim_attrs 回落口径对齐 —— bms_orders 无订单的 SN(14位SN 68%)
    #   回落 devices.device_type/current_combo_name, 筛选命中集 = 两源并集,
    #   否则图表桶(回落值)点击下钻查不到。
    if device_type or combo:
        latest_sub = (
            select(BmsOrderClean.device_sn, BmsOrderClean.device_type_name, BmsOrderClean.combo_name)
            .where(BmsOrderClean.device_sn.isnot(None))
            .distinct(BmsOrderClean.device_sn)
            .order_by(BmsOrderClean.device_sn, BmsOrderClean.create_date.desc().nullslast())
        ).subquery()
        latest_sq = select(latest_sub.c.device_sn)
        if device_type:
            latest_sq = latest_sq.where(latest_sub.c.device_type_name == device_type)
        if combo:
            latest_sq = latest_sq.where(latest_sub.c.combo_name == combo)
        matched = {s for s in db.scalars(latest_sq).all() if s}
        # devices 回落并集(仅当 bms_orders 无该 SN 订单时生效, 权威序与 _device_dim_attrs 一致;
        # NOT EXISTS 反连接下推 SQL, 避免把几十万 bms_orders SN 拉进 Python)
        dev_conds = []
        if device_type:
            dev_conds.append(Device.device_type == device_type)
        if combo:
            dev_conds.append(Device.current_combo_name == combo)
        dev_sq = (
            select(Device.sn)
            .where(Device.sn.isnot(None), or_(*dev_conds))
            .where(~exists(select(1).where(BmsOrderClean.device_sn == Device.sn)))
        )
        for s in db.scalars(dev_sq).all():
            if s:
                matched.add(s)
        sn_sets.append(matched)

    # ── 商户号: devices.merchant_name(设备归属) ──
    if merchant:
        sn_sets.append({s for s in db.scalars(
            select(Device.sn).where(Device.merchant_name == merchant)
        ).all() if s})

    # ── 激活时间: bms_orders MIN(active_date) 或 devices.active_time 兜底(并集) ──
    #   naive=北京时间 → UTC naive(与库 timestamptz 列 replace(tzinfo=None) 比较, 同 _parse_dt 口径)
    #   非法日期串静默忽略该条件(与 _parse_dt 失败→None 的容错风格一致);
    #   两端都解析失败 → 整个条件视为未传, 不追加 SN 集(否则退化为"有激活记录即命中")
    if active_start or active_end:
        try:
            d0 = _parse_date(active_start) if active_start else None
        except (ValueError, TypeError):
            d0 = None
        try:
            d1 = _parse_date(active_end) if active_end else None
        except (ValueError, TypeError):
            d1 = None
        if d0 or d1:
            # 北京日期 → UTC 边界 naive(与库 timestamptz 列 replace(tzinfo=None) 比较)
            lo = datetime.combine(d0, _dt.time.min, tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None) if d0 else None
            hi = datetime.combine(d1, _dt.time.max, tzinfo=TZ).astimezone(_dt.timezone.utc).replace(tzinfo=None) if d1 else None
            active_matched: set[str] = set()
            # 1) bms_orders 有 MIN(active_date) 落在区间
            bms_sq = select(BmsOrderClean.device_sn).where(BmsOrderClean.active_date.isnot(None)).group_by(BmsOrderClean.device_sn)
            if lo:
                bms_sq = bms_sq.having(func.min(BmsOrderClean.active_date) >= lo)
            if hi:
                bms_sq = bms_sq.having(func.min(BmsOrderClean.active_date) <= hi)
            active_matched.update(s for s in db.scalars(bms_sq).all() if s)
            # 2) devices.active_time 落在区间 且 该 SN 无 bms_orders 激活记录(兜底, 不重复)
            has_bms_active = select(BmsOrderClean.device_sn).where(BmsOrderClean.active_date.isnot(None))
            dev_conds = [Device.active_time.isnot(None), ~Device.sn.in_(has_bms_active)]
            if lo:
                dev_conds.append(Device.active_time >= lo)
            if hi:
                dev_conds.append(Device.active_time <= hi)
            active_matched.update(s for s in db.scalars(select(Device.sn).where(*dev_conds)).all() if s)
            sn_sets.append(active_matched)

    if not sn_sets:
        return None
    result = sn_sets[0]
    for s in sn_sets[1:]:
        result &= s
    return result


def _device_dim_options(db: Session) -> dict[str, list[str]]:
    """设备维度筛选候选项(#292 口径修正): 型号/套餐改取 bms_orders。

    · 设备类型 = bms_orders.device_type_name distinct(原文, 非归一码)
    · 套餐     = bms_orders.combo_name distinct(排除工厂质检 3 变体)
    · 商户号   = devices.merchant_name(设备归属)
    """
    from app.models.device import Device
    from app.models.ods_clean import BmsOrderClean

    # 设备类型: bms_orders.device_type_name(原文), distinct 取后排序
    type_names = sorted({
        t for t in db.scalars(
            select(BmsOrderClean.device_type_name)
            .where(BmsOrderClean.device_type_name.isnot(None))
            .distinct()
        ).all() if t
    })
    device_types = [{"value": t, "label": t} for t in type_names]
    # 套餐: bms_orders.combo_name, 排除工厂质检测试套餐(3 变体不计入分析)
    combos = sorted({
        c for c in db.scalars(
            select(BmsOrderClean.combo_name)
            .where(BmsOrderClean.combo_name.isnot(None))
            .distinct()
        ).all() if c and "工厂质检" not in c
    })
    # 商户号: devices.merchant_name(设备归属, 维持)
    merchants = sorted({
        m for m in db.scalars(
            select(Device.merchant_name)
            .where(Device.merchant_name.isnot(None))
            .distinct()
        ).all() if m
    })
    return {"device_types": device_types, "combos": combos, "merchants": merchants}


# ============================================================
# 设备维度汇总(#291 建, #295 改桶): 退款/投诉按 设备类型/套餐/商户号/激活时长 分桶共用
#   · 激活时长(用户拍板口径): 事件日 − bms_orders 最早激活日 MIN(active_date)
#     （#251「按销售天数核算」同源单一基准; 原 #252 用 devices.active_time 已收敛）
#     2026-10-11 #295 统一改五桶(退款/投诉同名同序, 消除原退款六桶 vs 投诉四桶分裂):
#     0-15天 / 16-30天 / 31-180天 / 181-365天 / >365天 / (无激活记录)
#   · 边界(用户拍板): 切点=上界 —— 30 归 16-30、180 归 31-180、365 归 181-365;
#     负数(激活晚于事件, 补录/时点差)自然落首桶, 与 SQL case 行为一致。
# ============================================================
ACTIVE_AGE_BUCKETS = ("0-15天", "16-30天", "31-180天", "181-365天", ">365天", "(无SN)", "(无激活记录)")

# active_age 筛选参数(URL 安全) → 桶名(明细下钻用, #291/#295)
_AGE_PARAM_TO_BUCKET = {
    "le15": "0-15天", "16-30": "16-30天", "31-180": "31-180天",
    "181-365": "181-365天", "gt365": ">365天",
    "nosn": "(无SN)", "none": "(无激活记录)",
}


def _active_age_bucket(days: int | None) -> str:
    """激活后天数 → 分桶名(见 ACTIVE_AGE_BUCKETS)。days=None 表示无激活记录。

    #295 切点=上界: 15/30/180/365 各归其左侧桶, >365 落末桶; 负数自然落首桶。
    #311 方案D: "(无SN)" 单独桶由调用方判定(sn 为 None 时直接归该桶, 不进本函数),
    本函数仅处理 days=None(有 SN 但查不到 active_date)→"(无激活记录)"。
    """
    if days is None:
        return "(无激活记录)"
    if days <= 15:
        return "0-15天"
    if days <= 30:
        return "16-30天"
    if days <= 180:
        return "31-180天"
    if days <= 365:
        return "181-365天"
    return ">365天"


def _device_dim_attrs(db: Session, sns: set[str]) -> dict[str, dict[str, Any]]:
    """SN → 设备属性映射(#292 口径修正: 型号/套餐改取 bms_orders 最新订单, 激活并集兜底)。

    口径(用户拍板 2026-10-09 #292; #293 回落扩展 2026-10-10; #312 口径扩展 2026-10-13):
      · type_name  = bms_orders 最新订单(create_date DESC) 的 device_type_name;
                     无订单时回落 devices.device_type(#293 反填后覆盖 93%+,
                     90天退款 SN 68% 在 bms_orders 无订单 → 原全落「(未填)」)
      · combo      = bms_orders **最新非质检订单**(create_date DESC 首条 combo_name 非空
                     且不含"工厂质检")的 combo_name(#312: 设备退款/提现后回厂质检,
                     质检单顶到最新, 会把历史正常套餐掩埋 → 跳过质检单);
                     回落 devices.current_combo_name;
                     再回落 device_combos 最新 package_name(#312: bms_orders 无订单
                     但充值流水有套餐的设备, 90天实测 256 SN 因此白归「(未填)」);
                     三源统一过滤工厂质检 → None
      · merchant   = devices.merchant_name(设备归属商户, 退款/投诉分析更准)
      · active_date = bms_orders MIN(active_date) 最早激活日; 无则 devices.active_time 兜底
                     （并集, 覆盖率 78.8% → 83.3%; #293 反填后 90%+）
    不在 devices 表的 SN 不出现在结果中(调用方归「(未建档)」桶, 暴露覆盖缺口)。
    """
    if not sns:
        return {}
    from app.models.device import Device
    from app.models.ods_clean import BmsOrderClean, DeviceComboClean

    # 1) devices 表: merchant_name + active_time(激活兜底) + device_type/current_combo_name(#293 回落)
    dev_rows = db.execute(
        select(Device.sn, Device.merchant_name, Device.active_time,
               Device.device_type, Device.current_combo_name)
        .where(Device.sn.in_(sns))
    ).all()

    # 2) bms_orders: 最新订单(create_date DESC 取首条)的 device_type_name;
    #    combo 取最新**非质检**订单(#312: 跳过质检单, 防历史正常套餐被顶掉)
    order_rows = db.execute(
        select(BmsOrderClean.device_sn, BmsOrderClean.device_type_name,
               BmsOrderClean.combo_name, BmsOrderClean.create_date)
        .where(BmsOrderClean.device_sn.in_(sns))
        .order_by(BmsOrderClean.device_sn, BmsOrderClean.create_date.desc().nullslast())
    ).all()
    latest: dict[str, tuple[str | None, str | None]] = {}
    latest_nonqc_combo: dict[str, str] = {}
    for sn, type_name, combo_name, _c in order_rows:
        sn_s = str(sn)
        if sn_s not in latest:
            latest[sn_s] = (type_name, combo_name)
        # 首个(=最新)非质检且非空 combo 即为该 SN 的真实套餐(#312)
        if sn_s not in latest_nonqc_combo and combo_name and "工厂质检" not in combo_name:
            latest_nonqc_combo[sn_s] = combo_name

    # 2b) device_combos 最新充值流水 package_name(#312 第三回落源, 非质检)
    dc_combo: dict[str, str] = {}
    for sn, pkg, _c in db.execute(
        select(DeviceComboClean.device_sn, DeviceComboClean.package_name, DeviceComboClean.created_at_raw)
        .where(DeviceComboClean.device_sn.in_(sns), DeviceComboClean.package_name.isnot(None))
        .order_by(DeviceComboClean.device_sn, DeviceComboClean.created_at_raw.desc().nullslast())
    ).all():
        sn_s = str(sn)
        if sn_s not in dc_combo and pkg and "工厂质检" not in pkg:
            dc_combo[sn_s] = pkg

    # 3) 最早激活日: bms_orders MIN(active_date)(#251 同源)
    first_active: dict[str, Any] = {
        sn: fa for sn, fa in db.execute(
            select(BmsOrderClean.device_sn, func.min(BmsOrderClean.active_date))
            .where(BmsOrderClean.device_sn.in_(sns), BmsOrderClean.active_date.isnot(None))
            .group_by(BmsOrderClean.device_sn)
        ).all()
    }

    out: dict[str, dict[str, Any]] = {}
    for sn, merchant, dev_active, dev_type, dev_combo in dev_rows:
        sn_s = str(sn)
        # type_name: bms_orders 最新订单权威优先, 空则回落 devices(#293 反填数据)
        type_name, _latest_combo = latest.get(sn_s, (None, None))
        if not type_name and dev_type:
            type_name = dev_type
        # combo 回落链(#312): 最新非质检订单 → devices.current_combo_name → device_combos 最新
        combo_name = latest_nonqc_combo.get(sn_s)
        if not combo_name and dev_combo:
            combo_name = dev_combo
        if not combo_name:
            combo_name = dc_combo.get(sn_s)
        # 工厂质检测试套餐(3 变体)不计入分析, 三源统一过滤
        if combo_name and "工厂质检" in combo_name:
            combo_name = None
        fa = first_active.get(sn_s)
        if fa is None and dev_active is not None:
            fa = dev_active  # devices.active_time 兜底(无 bms_orders 激活记录时)
        if fa is not None:
            fa = fa if fa.tzinfo else fa.replace(tzinfo=_dt.timezone.utc)
            fa = fa.astimezone(TZ).date()
        out[sn_s] = {
            "type_name": type_name,
            "combo": combo_name,
            "merchant": merchant,
            "active_date": fa,
        }
    return out


def _age_days_between(event_date, active_date) -> int | None:
    """事件日 − 激活日 天数; active_date 为空 → None(无激活记录)。"""
    if event_date is None or active_date is None:
        return None
    return (event_date - active_date).days


def lookup_point_in_time_combo(
    db: Session,
    sn_ev_pairs: Iterable[tuple[str, datetime]],
) -> dict[tuple[str, datetime], str | None]:
    """#320: 按 (设备 SN, 事件时点) 从 dim_device_combo 取当时套餐名。

    返回 dict[(sn, ev)] = package_name; 无匹配区间的返回 None(由调用方回落)。
    实现: 按 SN 批量取区间(valid_from, valid_to, package_name),
    对每个 ev 做包含判断 valid_from <= ev < valid_to(valid_to=NULL 视为无穷大)。
    """
    from app.models.dim_device_combo import DimDeviceCombo

    pairs = list(sn_ev_pairs)
    if not pairs:
        return {}
    sns = {sn for sn, _ in pairs}
    rows = db.execute(
        select(
            DimDeviceCombo.device_sn,
            DimDeviceCombo.valid_from,
            DimDeviceCombo.valid_to,
            DimDeviceCombo.package_name,
        )
        .where(DimDeviceCombo.device_sn.in_(sns))
        .order_by(DimDeviceCombo.device_sn, DimDeviceCombo.valid_from)
    ).all()
    # sn → list[(valid_from, valid_to, package)]
    intervals: dict[str, list[tuple[datetime, datetime | None, str]]] = {}
    for sn, vf, vt, pkg in rows:
        intervals.setdefault(str(sn), []).append((vf, vt, pkg))

    out: dict[tuple[str, datetime], str | None] = {}
    for sn, ev in pairs:
        ivs = intervals.get(str(sn), [])
        found: str | None = None
        for vf, vt, pkg in ivs:
            if ev >= vf and (vt is None or ev < vt):
                found = pkg
                break
        out[(str(sn), ev)] = found
    return out
