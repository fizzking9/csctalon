# -*- coding: utf-8 -*-
"""退款五路「采纳」幂等作业 (#332 退款五路数据标准层, T2)

权威口径: docs/standards/退款五路数据标准与采纳规则.md (META v1.0)

作业两步, 每路均为集合化 SQL (全量重算, 可重复执行, 结果一致):
  1. UPDATE ods 五路原始表四列 adopt_state/adopt_reason/adopt_rule_ver/adopted_at:
     机器规则以白名单 CASE 分类(adopted/excluded/pending), 未知枚举一律 pending;
  2. TRUNCATE + INSERT…SELECT 重建对应 std_* 物理表(仅 adopted 行)。

人工覆盖约定 (FR-6, 落法见 tasks.md T2):
  - ods.adopt_state  : LEFT JOIN adoption_overrides, 命中人工覆盖时以 target_state 为准;
  - ods.adopt_reason : **始终记机器规则码**, 便于和机器结论对照(不写 manual: 前缀);
  - 消费方(对账/统计/前端)若需展示「有效归因」, 命中覆盖时按
    ``f"manual:{override.target_state}"`` 表达, 人工理由在 adoption_overrides.reason。

边界:
  - 金额全程 Decimal(Numeric 定点), 不做任何浮点换算;
  - 不做时区转换写库(沿用原 timestamptz), 对账展示层再 AT TIME ZONE 'Asia/Shanghai';
  - SQL 全部 text() + 绑定参数; 表名/列名来自本模块常量配置(代码侧白名单), 无外部输入。
  - 并发互斥锁(pg_advisory_lock)与调度接入在 T6, 本模块只做单会话作业。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── 规则版本(权威口径文档变更时必须升版) ─────────────────────────────────────
ADOPT_RULE_VER = "r20261016_v1"

# ── 三态 ────────────────────────────────────────────────────────────────────
ST_ADOPTED = "adopted"
ST_EXCLUDED = "excluded"
ST_PENDING = "pending"

# ── 五路机器规则码(枚举来源: workdata/census_refunds_1016.py + census_adopt_counts_1016.py
#    2026-10-16 生产值普查; 与口径文档 §二 表格逐条一致) ─────────────────────
# A 路 ods.refunds(跨 status + refund_method 两列, 优先级即下列判定顺序)
A_ADOPT_ONLINE = "A_ADOPT_ONLINE"          # 已退款 且 方式=线上/NULL
A_EX_METHOD = "A_EX_METHOD"                # 方式∈{转入提现账户,线下} 非线上直退
A_EX_GARBAGE = "A_EX_GARBAGE"              # 错位值(方式∈{已退款,申请中,无需退款,作废})/状态长文本
A_EX_STATUS = "A_EX_STATUS"                # 状态∈{无需退款,驳回,申请中}
A_EX_STATUS_NULL = "A_EX_STATUS_NULL"      # 状态 IS NULL(且非已退款)
# B 路 ods.wallet_withdrawals
B_ADOPT_INFLOW = "B_ADOPT_INFLOW"          # 订单退费转入(蓄水池进水)
B_ADOPT_OUTFLOW = "B_ADOPT_OUTFLOW"        # 系统扣费 / 成功用户提现(出水)
B_EX_FAIL_FROZEN = "B_EX_FAIL_FROZEN"      # 用户提现 且 remark/quality_note 含 失败/冻结
B_EX_NULL_TYPE = "B_EX_NULL_TYPE"          # op_type 为空
# C 路 ods.shanmao_refunds
C_ADOPT = "C_ADOPT"                        # status=已退款
C_EX_NOT_REFUNDED = "C_EX_NOT_REFUNDED"    # 审核中/退款中/失败/驳回/无需退款
# D 路 ods.manual_refunds(单一优先级分因, dup/sn 优先于 is_refunded=否)
D_ADOPT = "D_ADOPT"                        # is_refunded=是 且 非 dup_residual/sn_unresolvable
D_EX_DUP = "D_EX_DUP"                      # quality_status=dup_residual 重复残留
D_EX_NO_SN = "D_EX_NO_SN"                  # quality_status=sn_unresolvable SN不可解析
D_EX_NOT_REFUNDED = "D_EX_NOT_REFUNDED"    # is_refunded=否
D_EX_QUOTE_SN = "D_EX_QUOTE_SN"            # device_sn 带 Excel 文本前缀引号, 同去重键干净行已采纳
# E 路 ods.refund_record_reports
E_ADOPT_ALL = "E_ADOPT_ALL"                # 全量采纳(refund_status 原样保留)

# 未知枚举(不在任何白名单内)→ pending; 非业务规则码, 仅标记「机器无法归类」
PENDING_UNKNOWN = "PENDING_UNKNOWN"

# ── 规则码中文释义(#333) ─────────────────────────────────────────────────────
# 单一来源: 与上方规则码定义同处维护(改规则码必须同步改这里)。
# 消费方: data_source_registry 经 ADOPT_REASON_ENUM 下发前端, 数据查看树
# adopt_reason 列 tooltip 展示; 供业务看懂「这行为什么进/没进统计」。
ADOPT_REASON_CN: dict[str, str] = {
    A_ADOPT_ONLINE: "采纳: 已退款且方式为线上(含空方式)",
    A_EX_METHOD: "排除: 方式为转入提现账户/线下, 非线上直退",
    A_EX_GARBAGE: "排除: 方式或状态错位值/长文本垃圾",
    A_EX_STATUS: "排除: 状态为无需退款/驳回/申请中(未完结)",
    A_EX_STATUS_NULL: "排除: 状态为空(且非已退款)",
    B_ADOPT_INFLOW: "采纳: 订单退费转入(蓄水池进水)",
    B_ADOPT_OUTFLOW: "采纳: 系统扣费/成功用户提现(出水)",
    B_EX_FAIL_FROZEN: "排除: 用户提现备注含失败/冻结关键词",
    B_EX_NULL_TYPE: "排除: 操作类型为空",
    C_ADOPT: "采纳: 闪猫退款状态为已退款",
    C_EX_NOT_REFUNDED: "排除: 审核中/退款中/失败/驳回/无需退款",
    D_ADOPT: "采纳: 手动登记已退款(非重复/SN可解析)",
    D_EX_DUP: "排除: 重复残留(dup_residual)",
    D_EX_NO_SN: "排除: SN不可解析(sn_unresolvable)",
    D_EX_NOT_REFUNDED: "排除: 登记为未退款(蓄水, 非出款)",
    D_EX_QUOTE_SN: "排除: SN带Excel文本前缀引号(同去重键干净行已采纳)",
    E_ADOPT_ALL: "采纳: E路全量(水单对账基准, 状态原样保留)",
    PENDING_UNKNOWN: "待定: 规则白名单外的未知枚举, 待业务确认",
}

# 前端下发格式: "code=中文" 列表(经 DataSourceField.enum_values 走既有通道)
ADOPT_REASON_ENUM: list[str] = [f"{k}={v}" for k, v in ADOPT_REASON_CN.items()]

# adopt_state 三态枚举(含 NULL 语义说明见 AdoptionColumnsMixin 注释)
ADOPT_STATE_ENUM: list[str] = ["adopted", "excluded", "pending"]

ROUTE_A = "A"
ROUTE_B = "B"
ROUTE_C = "C"
ROUTE_D = "D"
ROUTE_E = "E"
ROUTES = (ROUTE_A, ROUTE_B, ROUTE_C, ROUTE_D, ROUTE_E)

# A 路「状态长文本垃圾」长度阈值: 全部合法状态均 ≤4 个中文字符,
# 普查发现约 10 行 status 为整段错位长文本; >16 视为垃圾, 其余未知短值走 pending。
_A_GARBAGE_STATUS_LEN = 16

# 盖章 UPDATE 的 keyset 分批大小: 单条语句只扫描/锁定一批行, 控制单语句时长与
# 锁突发规模。注意行锁在事务提交前不跨批释放 —— 与同步任务的真正互斥由 T6 的
# pg_advisory_lock 保证(单 worker 串行调度), 分批只负责语句级可控。
UPDATE_BATCH = 5000


@dataclass(frozen=True)
class _Route:
    """单路作业配置: 表名(代码常量, 非外部输入)、主键列、std 列清单与两段 SQL。"""

    route: str
    ods_table: str
    std_table: str
    update_sql: str
    std_insert_sql: str
    amount_expr: str
    pk_col: str
    std_count: int = 0
    std_amount: Decimal = Decimal("0")
    elapsed_ms: int = 0
    counts: dict = field(default_factory=dict)


# 公共绑定参数(状态三态), 各 UPDATE 复用
_STATE_PARAMS = {
    "st_adopted": ST_ADOPTED,
    "st_excluded": ST_EXCLUDED,
    "st_pending": ST_PENDING,
    "rule_ver": ADOPT_RULE_VER,
}


# ── A 路 ods.refunds ─────────────────────────────────────────────────────────
# 优先级: 采纳 → 非线上方式 → 错位方式 → 长文本垃圾状态 → 未完成状态 → 状态空 → 其余 pending
_A_UPDATE = """
UPDATE ods.refunds AS t SET
    adopt_state = COALESCE(o.target_state, c.state),
    adopt_reason = c.reason,
    adopt_rule_ver = :rule_ver,
    adopted_at = now()
FROM (
    SELECT refund_no,
        CASE
            WHEN status = '已退款' AND (refund_method = '线上' OR refund_method IS NULL)
                THEN :st_adopted
            WHEN refund_method IN ('转入提现账户', '线下')
                THEN :st_excluded
            WHEN refund_method IN ('已退款', '申请中', '无需退款', '作废')
                THEN :st_excluded
            WHEN char_length(status) > :garbage_len
                THEN :st_excluded
            WHEN status IN ('无需退款', '驳回', '申请中')
                THEN :st_excluded
            WHEN status IS NULL
                THEN :st_excluded
            ELSE :st_pending
        END AS state,
        CASE
            WHEN status = '已退款' AND (refund_method = '线上' OR refund_method IS NULL)
                THEN :r_adopt
            WHEN refund_method IN ('转入提现账户', '线下')
                THEN :r_method
            WHEN refund_method IN ('已退款', '申请中', '无需退款', '作废')
                THEN :r_garbage
            WHEN char_length(status) > :garbage_len
                THEN :r_garbage
            WHEN status IN ('无需退款', '驳回', '申请中')
                THEN :r_status
            WHEN status IS NULL
                THEN :r_status_null
            ELSE :r_pending
        END AS reason
    FROM (
        SELECT * FROM ods.refunds
        WHERE :cursor IS NULL OR refund_no > :cursor
        ORDER BY refund_no LIMIT :batch_size
    ) b
) c
LEFT JOIN adoption_overrides o
    ON o.route = 'A' AND o.src_pk = c.refund_no
WHERE t.refund_no = c.refund_no
"""

_A_STD_INSERT = """
INSERT INTO std_refund_online (
    refund_no, platform_order_no, third_order_no, device_sn, refund_reason,
    package_name, merchant_name, refund_amount, actual_amount, remark, status,
    refund_method, audit_remark, auditor, operator, created_at_raw, updated_at_raw,
    actual_refund_time, quality_status, created_at, updated_at,
    adopt_rule_ver, adopted_at
)
SELECT
    refund_no, platform_order_no, third_order_no,
    ltrim(device_sn, chr(39)) AS device_sn,  -- 采纳层值清洗: 去 Excel 文本格式前导单引号
    refund_reason,
    package_name, merchant_name, refund_amount, actual_amount, remark, status,
    refund_method, audit_remark, auditor, operator, created_at_raw, updated_at_raw,
    actual_refund_time, quality_status, created_at, updated_at,
    :rule_ver, now()
FROM ods.refunds
WHERE adopt_state = :st_adopted
"""


# ── B 路 ods.wallet_withdrawals ─────────────────────────────────────────────
# 失败/冻结判据: remark 或 quality_note 任一含「失败」「冻结」(普查口径)
_B_FAIL_FROZEN = """
(COALESCE(remark, '') LIKE '%失败%' OR COALESCE(quality_note, '') LIKE '%失败%'
 OR COALESCE(remark, '') LIKE '%冻结%' OR COALESCE(quality_note, '') LIKE '%冻结%')
"""

_B_UPDATE = f"""
UPDATE ods.wallet_withdrawals AS t SET
    adopt_state = COALESCE(o.target_state, c.state),
    adopt_reason = c.reason,
    adopt_rule_ver = :rule_ver,
    adopted_at = now()
FROM (
    SELECT id,
        CASE
            WHEN op_type = '订单退费转入' THEN :st_adopted
            WHEN op_type = '系统扣费' THEN :st_adopted
            WHEN op_type = '用户提现' AND {_B_FAIL_FROZEN} THEN :st_excluded
            WHEN op_type = '用户提现' THEN :st_adopted
            WHEN op_type IS NULL OR op_type = '' THEN :st_excluded
            ELSE :st_pending
        END AS state,
        CASE
            WHEN op_type = '订单退费转入' THEN :r_inflow
            WHEN op_type = '系统扣费' THEN :r_outflow
            WHEN op_type = '用户提现' AND {_B_FAIL_FROZEN} THEN :r_fail
            WHEN op_type = '用户提现' THEN :r_outflow
            WHEN op_type IS NULL OR op_type = '' THEN :r_null_type
            ELSE :r_pending
        END AS reason
    FROM (
        SELECT * FROM ods.wallet_withdrawals
        WHERE :cursor IS NULL OR id > :cursor
        ORDER BY id LIMIT :batch_size
    ) b
) c
LEFT JOIN adoption_overrides o
    ON o.route = 'B' AND o.src_pk = c.id::text
WHERE t.id = c.id
"""

_B_STD_INSERT = """
INSERT INTO std_wallet_flow (
    id, account_id, wallet_balance, device_sn, op_type, op_amount, remark,
    op_time, operator, actual_refund_time, quality_status, quality_note,
    created_at, updated_at, flow_dir, adopt_rule_ver, adopted_at
)
SELECT
    id, account_id, wallet_balance,
    ltrim(device_sn, chr(39)) AS device_sn,  -- 采纳层值清洗: 去 Excel 文本格式前导单引号
    op_type, op_amount, remark,
    op_time, operator, actual_refund_time, quality_status, quality_note,
    created_at, updated_at,
    CASE WHEN op_type = '订单退费转入' THEN 'inflow' ELSE 'outflow' END,
    :rule_ver, now()
FROM ods.wallet_withdrawals
WHERE adopt_state = :st_adopted
"""


# ── C 路 ods.shanmao_refunds ────────────────────────────────────────────────
_C_UPDATE = """
UPDATE ods.shanmao_refunds AS t SET
    adopt_state = COALESCE(o.target_state, c.state),
    adopt_reason = c.reason,
    adopt_rule_ver = :rule_ver,
    adopted_at = now()
FROM (
    SELECT refund_no,
        CASE
            WHEN status = '已退款' THEN :st_adopted
            WHEN status IN ('申请中', '退款中', '退款失败', '已驳回', '无需退款')
                THEN :st_excluded
            ELSE :st_pending
        END AS state,
        CASE
            WHEN status = '已退款' THEN :r_adopt
            WHEN status IN ('申请中', '退款中', '退款失败', '已驳回', '无需退款')
                THEN :r_not_refunded
            ELSE :r_pending
        END AS reason
    FROM (
        SELECT * FROM ods.shanmao_refunds
        WHERE :cursor IS NULL OR refund_no > :cursor
        ORDER BY refund_no LIMIT :batch_size
    ) b
) c
LEFT JOIN adoption_overrides o
    ON o.route = 'C' AND o.src_pk = c.refund_no
WHERE t.refund_no = c.refund_no
"""

_C_STD_INSERT = """
INSERT INTO std_refund_shanmao (
    refund_no, order_no, device_sn, phone, paid_amount, refund_amount,
    actual_amount, package_name, merchant_name, refund_reason, refund_method,
    status, applicant, auditor, operator, apply_time, audit_time, refund_time,
    remark, audit_remark, quality_status, quality_note, created_at, updated_at,
    adopt_rule_ver, adopted_at
)
SELECT
    refund_no, order_no,
    ltrim(device_sn, chr(39)) AS device_sn,  -- 采纳层值清洗: 去 Excel 文本格式前导单引号
    phone, paid_amount, refund_amount,
    actual_amount, package_name, merchant_name, refund_reason, refund_method,
    status, applicant, auditor, operator, apply_time, audit_time, refund_time,
    remark, audit_remark, quality_status, quality_note, created_at, updated_at,
    :rule_ver, now()
FROM ods.shanmao_refunds
WHERE adopt_state = :st_adopted
"""


# ── D 路 ods.manual_refunds ─────────────────────────────────────────────────
# 单一优先级分因: 引号SN → dup_residual → sn_unresolvable → 已退款采纳 → 未退款 → 其余 pending
# 引号SN判据: device_sn 带 Excel 文本格式前导单引号(chr(39)), 同去重键干净行已存在并采纳,
#             脏行本身是 Excel 格式 artifact, 不应进入标准表。
_D_UPDATE = """
UPDATE ods.manual_refunds AS t SET
    adopt_state = COALESCE(o.target_state, c.state),
    adopt_reason = c.reason,
    adopt_rule_ver = :rule_ver,
    adopted_at = now()
FROM (
    SELECT id,
        CASE
            WHEN device_sn LIKE chr(39)||'%' THEN :st_excluded
            WHEN quality_status = 'dup_residual' THEN :st_excluded
            WHEN quality_status = 'sn_unresolvable' THEN :st_excluded
            WHEN is_refunded = '是' THEN :st_adopted
            WHEN is_refunded = '否' THEN :st_excluded
            ELSE :st_pending
        END AS state,
        CASE
            WHEN device_sn LIKE chr(39)||'%' THEN :r_quote_sn
            WHEN quality_status = 'dup_residual' THEN :r_dup
            WHEN quality_status = 'sn_unresolvable' THEN :r_no_sn
            WHEN is_refunded = '是' THEN :r_adopt
            WHEN is_refunded = '否' THEN :r_not_refunded
            ELSE :r_pending
        END AS reason
    FROM (
        SELECT * FROM ods.manual_refunds
        WHERE :cursor IS NULL OR id > :cursor
        ORDER BY id LIMIT :batch_size
    ) b
) c
LEFT JOIN adoption_overrides o
    ON o.route = 'D' AND o.src_pk = c.id::text
WHERE t.id = c.id
"""

_D_STD_INSERT = """
INSERT INTO std_refund_manual (
    id, register_time, device_sn, order_no, merchant_id, order_amount,
    registrant, is_refunded, refund_amount, refund_operator, op_time, remark,
    actual_refund_time, quality_status, quality_note, register_time_raw,
    backfill_refund_record_report_id, created_at, updated_at,
    source_grade, adopt_rule_ver, adopted_at
)
SELECT
    id, register_time,
    ltrim(device_sn, chr(39)) AS device_sn,  -- 采纳层值清洗: 去 Excel 文本格式前导单引号
    order_no, merchant_id, order_amount,
    registrant, is_refunded, refund_amount, refund_operator, op_time, remark,
    actual_refund_time, quality_status, quality_note, register_time_raw,
    backfill_refund_record_report_id, created_at, updated_at,
    'manual', :rule_ver, now()
FROM ods.manual_refunds
WHERE adopt_state = :st_adopted
"""


# ── E 路 ods.refund_record_reports(全量采纳, 无 pending/excluded) ───────────
_E_UPDATE = """
UPDATE ods.refund_record_reports AS t SET
    adopt_state = COALESCE(o.target_state, :st_adopted),
    adopt_reason = :r_adopt,
    adopt_rule_ver = :rule_ver,
    adopted_at = now()
FROM (
    SELECT * FROM ods.refund_record_reports
    WHERE :cursor IS NULL OR id > :cursor
    ORDER BY id LIMIT :batch_size
) s
LEFT JOIN adoption_overrides o
    ON o.route = 'E' AND o.src_pk = s.id::text
WHERE t.id = s.id
"""

_E_STD_INSERT = """
INSERT INTO std_refund_record (
    id, platform_order_no, wx_order_no, merchant_id, merchant_name, device_sn,
    product_name, refund_time, apply_refund_amount, refund_amount, refund_status,
    backfill_op_time, backfill_manual_refund_id, created_at, updated_at,
    adopt_rule_ver, adopted_at
)
SELECT
    id, platform_order_no, wx_order_no, merchant_id, merchant_name,
    ltrim(device_sn, chr(39)) AS device_sn,  -- 采纳层值清洗: 去 Excel 文本格式前导单引号
    product_name, refund_time, apply_refund_amount, refund_amount, refund_status,
    backfill_op_time, backfill_manual_refund_id, created_at, updated_at,
    :rule_ver, now()
FROM ods.refund_record_reports
WHERE adopt_state = :st_adopted
"""


_ROUTES: dict[str, _Route] = {
    ROUTE_A: _Route(
        route=ROUTE_A, ods_table="ods.refunds", std_table="std_refund_online",
        update_sql=_A_UPDATE, std_insert_sql=_A_STD_INSERT,
        amount_expr="COALESCE(sum(refund_amount), 0)", pk_col="refund_no",
    ),
    ROUTE_B: _Route(
        route=ROUTE_B, ods_table="ods.wallet_withdrawals", std_table="std_wallet_flow",
        update_sql=_B_UPDATE, std_insert_sql=_B_STD_INSERT,
        amount_expr="COALESCE(sum(op_amount), 0)", pk_col="id",
    ),
    ROUTE_C: _Route(
        route=ROUTE_C, ods_table="ods.shanmao_refunds", std_table="std_refund_shanmao",
        update_sql=_C_UPDATE, std_insert_sql=_C_STD_INSERT,
        amount_expr="COALESCE(sum(COALESCE(actual_amount, refund_amount)), 0)", pk_col="refund_no",
    ),
    ROUTE_D: _Route(
        route=ROUTE_D, ods_table="ods.manual_refunds", std_table="std_refund_manual",
        update_sql=_D_UPDATE, std_insert_sql=_D_STD_INSERT,
        amount_expr="COALESCE(sum(refund_amount), 0)", pk_col="id",
    ),
    ROUTE_E: _Route(
        route=ROUTE_E, ods_table="ods.refund_record_reports", std_table="std_refund_record",
        update_sql=_E_UPDATE, std_insert_sql=_E_STD_INSERT,
        amount_expr="COALESCE(sum(refund_amount), 0)", pk_col="id",
    ),
}

# 各路 UPDATE SQL 绑定的规则码参数(值全是上方代码常量, 无外部输入)
_REASON_PARAMS: dict[str, dict[str, str]] = {
    ROUTE_A: {
        "r_adopt": A_ADOPT_ONLINE, "r_method": A_EX_METHOD, "r_garbage": A_EX_GARBAGE,
        "r_status": A_EX_STATUS, "r_status_null": A_EX_STATUS_NULL,
        "r_pending": PENDING_UNKNOWN, "garbage_len": _A_GARBAGE_STATUS_LEN,
    },
    ROUTE_B: {
        "r_inflow": B_ADOPT_INFLOW, "r_outflow": B_ADOPT_OUTFLOW,
        "r_fail": B_EX_FAIL_FROZEN, "r_null_type": B_EX_NULL_TYPE,
        "r_pending": PENDING_UNKNOWN,
    },
    ROUTE_C: {"r_adopt": C_ADOPT, "r_not_refunded": C_EX_NOT_REFUNDED, "r_pending": PENDING_UNKNOWN},
    ROUTE_D: {
        "r_dup": D_EX_DUP, "r_no_sn": D_EX_NO_SN, "r_adopt": D_ADOPT,
        "r_not_refunded": D_EX_NOT_REFUNDED, "r_quote_sn": D_EX_QUOTE_SN,
        "r_pending": PENDING_UNKNOWN,
    },
    ROUTE_E: {"r_adopt": E_ADOPT_ALL},
}


def _run_one_route(db: Session, cfg: _Route) -> dict:
    """执行单路: 分批盖章四列 → 重建 std → 汇总计数/金额/耗时。"""
    t0 = time.monotonic()
    params = {**_STATE_PARAMS, **_REASON_PARAMS[cfg.route]}

    # 1) 分批全量重算 ods 四列(keyset 分页, 控制单语句规模; 与同步互斥由 T6
    #    advisory lock 保证; RETURNING 主键回传游标, 无需额外查询)。
    #    人工覆盖只改 state, reason 永远记机器码。
    updated = 0
    cursor = None
    update_stmt = text(cfg.update_sql + f"\nRETURNING t.{cfg.pk_col}")  # t = 被更新目标表别名
    while True:
        pks = [r[0] for r in db.execute(
            update_stmt, {**params, "cursor": cursor, "batch_size": UPDATE_BATCH}
        ).all()]
        updated += len(pks)
        if len(pks) < UPDATE_BATCH:
            break
        cursor = max(pks)
    db.flush()

    # 2) 重建 std: 先清空再按 adopted 集合全量插入(事务内, 回滚可恢复)
    db.execute(text(f"TRUNCATE TABLE {cfg.std_table}"))
    db.execute(text(cfg.std_insert_sql), params)
    db.flush()

    # 3) 汇总: ods 侧三态计数(含被人工覆盖改写后的最终 state)
    state_rows = db.execute(text(
        f"SELECT COALESCE(adopt_state, :st_pending) AS st, count(*) AS c "
        f"FROM {cfg.ods_table} GROUP BY 1"
    ), {"st_pending": ST_PENDING}).all()
    counts = {r.st: int(r.c) for r in state_rows}

    std_row = db.execute(text(
        f"SELECT count(*) AS c, {cfg.amount_expr} AS amt FROM {cfg.std_table}"
    )).one()
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    stats = {
        "route": cfg.route,
        "total": int(updated),
        "adopted": counts.get(ST_ADOPTED, 0),
        "excluded": counts.get(ST_EXCLUDED, 0),
        "pending": counts.get(ST_PENDING, 0),
        "std_rows": int(std_row.c),
        "std_amount": Decimal(std_row.amt or 0),
        "elapsed_ms": elapsed_ms,
    }
    logger.info(
        "refund_adoption route=%s total=%d adopted=%d excluded=%d pending=%d "
        "std_rows=%d std_amount=%s elapsed_ms=%d",
        cfg.route, stats["total"], stats["adopted"], stats["excluded"],
        stats["pending"], stats["std_rows"], f"{stats['std_amount']:.2f}", elapsed_ms,
    )
    return stats


def run_refund_adoption(
    db: Session,
    routes: str | tuple[str, ...] | list[str] | None = None,
    *,
    commit: bool = True,
) -> dict:
    """运行退款五路采纳作业(全量幂等)。

    参数:
        db: 已建立的 SQLAlchemy Session;
        routes: 单路代码('A')或路别序列; None 表示五路全跑;
        commit: True 时作业末尾统一提交(调度/API 入口用法);
                False 时仅 flush 不提交(单测用法, 由外层 fixture 回滚隔离)。

    返回: ``{"rule_ver": ..., "routes": {route: stats}}``, stats 含
        total/adopted/excluded/pending/std_rows/std_amount(Decimal)/elapsed_ms。
    """
    if routes is None:
        selected = ROUTES
    elif isinstance(routes, str):
        selected = (routes,)
    else:
        selected = tuple(routes)
    bad = [r for r in selected if r not in _ROUTES]
    if bad:
        raise ValueError(f"未知采纳路别: {bad}; 合法值: {ROUTES}")

    logger.info("refund_adoption start rule_ver=%s routes=%s", ADOPT_RULE_VER, selected)
    t0 = time.monotonic()
    result: dict = {"rule_ver": ADOPT_RULE_VER, "routes": {}}
    for r in selected:
        result["routes"][r] = _run_one_route(db, _ROUTES[r])
    if commit:
        db.commit()
    else:
        db.flush()
    logger.info("refund_adoption done routes=%s elapsed_ms=%d",
                selected, int((time.monotonic() - t0) * 1000))
    return result
