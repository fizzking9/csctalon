"""工单域 upsert: tickets / cs_records + 操作日志触达。"""

import json
import logging
import types

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.cs_record import CsRecord
from app.models.ticket import ServiceTicket
from app.services.quality_check import (
    check_ticket_row,
)

logger = logging.getLogger(__name__)

# 跨子模块引用(拆分自 upsert.py, 原同文件内直接调用)
from app.services.upsert._core import (
    ImportResult,
    _execute_upsert,
    _sanitize_name_field,
    _sync_devices,
    _to_datetime,
    _to_decimal,
    _to_int,
    _to_str,
    _truncate_str,
    apply_mapping,
)


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
                "refund_amount": _to_decimal(data.get("refund_amount")),
                "estimated_amount": _to_decimal(data.get("estimated_amount")),
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
    text(
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

def upsert_cs_records(df: pd.DataFrame, mapping: dict[str, str], db: Session, result: ImportResult) -> None:
    """智能助手处理记录 - 业务复合唯一键去重

    唯一键: (device_sn, operation_time, operator, problem_category, problem_type, solution, consult_time, problem_create_time)
    防止重复同步导致数据膨胀;冲突时 DO NOTHING(接待记录是事件流水,不需要更新)。

    ⚠️ #77 (2026-09-13): 该唯一键在数据库层是 `UNIQUE NULLS NOT DISTINCT`
       (见迁移 a1c2e3f4d5b6)。原因: problem_create_time 大量为 NULL,而 PG 唯一约束
       默认 NULLS DISTINCT 会让含 NULL 的行不参与去重 → 重复行无限插入
       (实测 2026-09 重复率 47%)。下面的 on_conflict_do_nothing(constraint=...) 依赖
       该约束名生效;若约束被改回默认语义,本函数的去重会**静默失效**。
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
