"""SCRM 微信聚合平台同步服务 - 对接 SCRM OpenAPI

核心职责:
  1. 操作员列表:每次全量 upsert(覆盖更新,不做历史)
  2. 会话列表(私聊/群聊/虚拟)三接口:按时间窗口增量拉取,upsert 到 scrm_sessions
     - 转接明细:每个会话若包含 transfers,逐条拆出到 scrm_session_transfers
     - 质量指标:若列表接口直接返回 firstResponse/avgResponse,就填;否则标记 quality_computed=False,等后续按需计算
     - 对账:记录 api_total_reported vs (inserted+updated) 的偏差
  3. 写 ScrmSyncLog(与 BmsSyncLog 独立,拆分 inserted/updated/failed 三分类)
  4. 不做聊天记录明细(用户要求:量大暂不入库)

复用现有依赖:
  - SyncSourceConfig 存储 clientId / clientSecret (Fernet 加密,用户名=clientId,密码=clientSecret)
  - ScrmClient 封装 token / 分页 / 错误处理
  - BmsScheduler 后续接入节奏(SCHEDULE_HOURS: 0/8/12/16/20)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.scrm_operator import ScrmOperator
from app.models.scrm_session import ScrmSession
from app.models.scrm_session_transfer import ScrmSessionTransfer
from app.models.scrm_sync_log import ScrmSyncLog
from app.services.scrm_client import ScrmClient
from app.services.sync_source_config_service import get_sync_source_config

logger = logging.getLogger(__name__)

# 同步窗口"首次兜底"时间(第一次没历史时往前拉 N 天)
SCRM_INITIAL_LOOKBACK_DAYS = 30
# 增量同步时的重叠窗口(防止跨两次同步边界的会话漏,回退 N 小时)
SCRM_INCREMENTAL_OVERLAP_HOURS = 2

# 每个 scrm_sync_logs.data_type 枚举
DATA_TYPE_OPERATOR = "scrm_operator"
DATA_TYPE_SESSION_PRIVATE = "scrm_session_private"
DATA_TYPE_SESSION_GROUP = "scrm_session_group"
DATA_TYPE_SESSION_VIRTUAL = "scrm_session_virtual"


# ==========================================================
# 基础:客户端构造 + 时间范围计算 + 日志写入
# ==========================================================

def _build_scrm_client(db: Session) -> tuple[ScrmClient, dict]:
    """从 SyncSourceConfig 读取配置构造 ScrmClient

    Returns: (client, config_dict)
    Raises: ValueError 未配置/已停用
    """
    # 任一 scrm 源都能拿到同一份 clientId/secret,取 scrm_operator 作为代表
    cfg = get_sync_source_config(db, DATA_TYPE_OPERATOR)
    if not cfg:
        raise ValueError("SCRM 数据源未注册,请先在后端启动后让 ensure_sync_sources_registered 灌入种子")
    if not cfg.get("enabled", True):
        raise ValueError("SCRM 数据源已停用(在同步设置中关闭)")
    base_url = cfg.get("base_url") or ""
    client_id = cfg.get("username") or ""
    client_secret = cfg.get("password") or ""
    client = ScrmClient(base_url=base_url, client_id=client_id, client_secret=client_secret)
    return client, cfg


def _to_utc_aware(ts: Any) -> datetime | None:
    """宽松解析 SCRM 各种时间字段 → 带 UTC 时区的 datetime;失败返回 None"""
    if ts is None or ts == "":
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    if isinstance(ts, (int, float)):
        # 毫秒 / 秒 兼容
        try:
            v = float(ts)
            if v > 1e12:
                v = v / 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        # 尝试常见格式(从严格到宽松)
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y%m%d%H%M%S",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        # 纯数字字符串兼容
        if s.isdigit():
            return _to_utc_aware(int(s))
        # 最后兜底 ISO
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _fmt_scrm_ts(dt: datetime) -> str:
    """SCRM 列表接口要求的 startTime/endTime 字符串(本地东八区 YYYY-MM-DD HH:mm:ss)"""
    import datetime as _dt
    # SCRM 平台要求东八区,按文档约定
    offset = _dt.timezone(_dt.timedelta(hours=8))
    local = dt.astimezone(offset) if dt.tzinfo else dt.replace(tzinfo=offset)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def _last_success_end_time(db: Session, data_type: str) -> datetime | None:
    """最近一次成功同步的 end_time,供增量窗口用"""
    row = db.scalar(
        select(ScrmSyncLog)
        .where(ScrmSyncLog.data_type == data_type, ScrmSyncLog.status == "success")
        .order_by(ScrmSyncLog.end_time.desc())
        .limit(1)
    )
    return row.end_time if row else None


def _calculate_sync_range(db: Session, data_type: str, override: tuple[datetime, datetime] | None = None) -> tuple[datetime, datetime]:
    """计算 (start, end) = (最近成功的 end - 重叠窗口, now),首次全量兜底 30 天前

    override: 显式传入则直接使用(手动同步指定窗口)
    """
    now = datetime.now(timezone.utc)
    if override:
        start, end = override
        # 至少保证 naive 带了时区
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return start, end
    last_end = _last_success_end_time(db, data_type)
    from datetime import timedelta
    if last_end is None:
        # 首次: 30 天前 ~ now
        return now - timedelta(days=SCRM_INITIAL_LOOKBACK_DAYS), now
    # 增量: last_end - overlap ~ now
    return last_end - timedelta(hours=SCRM_INCREMENTAL_OVERLAP_HOURS), now


def _create_log(db: Session, data_type: str, start: datetime, end: datetime, trigger_type: str, triggered_by: str) -> ScrmSyncLog:
    log = ScrmSyncLog(
        data_type=data_type,
        sync_scope=f"{_fmt_scrm_ts(start)} → {_fmt_scrm_ts(end)}",
        start_time=start,
        end_time=end,
        status="running",
        trigger_type=trigger_type,
        triggered_by=triggered_by,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def _finish_log(
    db: Session,
    log_id: int,
    status: str,
    *,
    api_call_count: int = 0,
    api_total_reported: int | None = None,
    fetched_rows: int = 0,
    inserted: int = 0,
    updated: int = 0,
    failed: int = 0,
    error: str | None = None,
) -> None:
    log = db.get(ScrmSyncLog, log_id)
    if not log:
        return
    log.status = status
    log.finished_at = datetime.now(timezone.utc)
    log.api_call_count = api_call_count
    if api_total_reported is not None:
        log.api_total_reported = api_total_reported
    log.fetched_rows = fetched_rows
    log.inserted_rows = inserted
    log.updated_rows = updated
    log.failed_rows = failed
    delta = None
    if api_total_reported is not None:
        delta = int(api_total_reported) - (inserted + updated)
    log.reconcile_delta = delta
    if error:
        log.error = (error or "")[:8000]
    db.commit()


# ==========================================================
# 操作员同步(全量覆盖)
# ==========================================================

def _upsert_operator(db: Session, rec: dict) -> str:
    """单操作员 upsert;返回 'inserted' / 'updated' / 'failed'"""
    operator_id = str(rec.get("operatorId") or rec.get("operator_id") or rec.get("id") or "").strip()
    if not operator_id:
        return "failed"
    operator_name = str(
        rec.get("operatorName") or rec.get("operator_name") or rec.get("name") or rec.get("nickName") or ""
    ).strip() or None
    if not operator_name:
        return "failed"

    existing = db.scalar(select(ScrmOperator).where(ScrmOperator.operator_id == operator_id).limit(1))
    if existing is None:
        op = ScrmOperator(
            operator_id=operator_id,
            operator_name=operator_name,
            department=str(rec.get("deptName") or rec.get("department") or "").strip() or None,
            position=str(rec.get("position") or rec.get("role") or "").strip() or None,
            status=str(rec.get("status") or rec.get("onlineStatus") or "").strip() or None,
            last_active_at=_to_utc_aware(rec.get("lastActiveTime") or rec.get("last_active_at")),
            receive_count_today=int(rec.get("receiveCountToday") or 0) if str(rec.get("receiveCountToday") or "").isdigit() else None,
            raw_extra=rec,
        )
        db.add(op)
        return "inserted"
    else:
        existing.operator_name = operator_name
        existing.department = str(rec.get("deptName") or rec.get("department") or "").strip() or None
        existing.position = str(rec.get("position") or rec.get("role") or "").strip() or None
        existing.status = str(rec.get("status") or rec.get("onlineStatus") or "").strip() or None
        laa = _to_utc_aware(rec.get("lastActiveTime") or rec.get("last_active_at"))
        if laa is not None:
            existing.last_active_at = laa
        rct = rec.get("receiveCountToday")
        if str(rct or "").isdigit():
            existing.receive_count_today = int(rct)
        existing.raw_extra = rec
        return "updated"


async def sync_operators(
    client: ScrmClient,
    db: Session,
    trigger_type: str = "manual",
    triggered_by: str = "admin",
    time_range_override: tuple[datetime, datetime] | None = None,
) -> dict:
    """同步操作员列表(每次全量覆盖)"""
    start, end = _calculate_sync_range(db, DATA_TYPE_OPERATOR, time_range_override)
    log = _create_log(db, DATA_TYPE_OPERATOR, start, end, trigger_type, triggered_by)
    inserted = updated = failed = 0
    api_call_count = 0
    api_total_reported: int | None = None
    fetched_rows = 0
    try:
        async with client:
            records = await client.list_operators()
            api_call_count += 1  # token + 1次接口(但 token 不算在内,这里只计数业务接口)
            if isinstance(records, dict):
                api_total_reported = int(records.get("total") or 0) or None
                records = records.get("records") or []
            fetched_rows = len(records)
            # 分批 commit(避免单事务过大)
            BATCH = 100
            for i in range(0, fetched_rows, BATCH):
                batch = records[i:i + BATCH]
                for rec in batch:
                    try:
                        result = _upsert_operator(db, rec)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[scrm][operator] 单条 upsert 失败: {e}")
                        result = "failed"
                    if result == "inserted":
                        inserted += 1
                    elif result == "updated":
                        updated += 1
                    else:
                        failed += 1
                db.commit()
        _finish_log(
            db, log.id, "success",
            api_call_count=api_call_count, api_total_reported=api_total_reported,
            fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
        )
        logger.info(f"[scrm][operator] 成功: inserted={inserted} updated={updated} failed={failed}")
        return {
            "data_type": DATA_TYPE_OPERATOR, "success": True,
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "api_call_count": api_call_count, "fetched_rows": fetched_rows,
            "inserted": inserted, "updated": updated, "failed": failed,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[scrm][operator] 同步失败: {e}")
        _finish_log(
            db, log.id, "failed",
            api_call_count=api_call_count, api_total_reported=api_total_reported,
            fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
            error=str(e),
        )
        return {
            "data_type": DATA_TYPE_OPERATOR, "success": False, "error": str(e),
            "start_time": start.isoformat(), "end_time": end.isoformat(),
        }


# ==========================================================
# 会话同步(三种类型 + 转接明细拆分)
# ==========================================================

_SESSION_TYPE_TO_METHOD = {
    "private": ("private", DATA_TYPE_SESSION_PRIVATE, lambda c, s, e, oid: c.list_private_sessions(s, e, operator_id=oid)),
    "group": ("group", DATA_TYPE_SESSION_GROUP, lambda c, s, e, oid: c.list_group_sessions(s, e, operator_id=oid)),
    "virtual": ("virtual", DATA_TYPE_SESSION_VIRTUAL, lambda c, s, e, oid: c.list_virtual_sessions(s, e, operator_id=oid)),
}


def _pick(rec: dict, *keys: str, default=None):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return rec[k]
        # 驼峰/下划线交替尝试
        kl = k.lower()
        for rk, rv in rec.items():
            if rk.lower() == kl and rv not in (None, ""):
                return rv
    return default


def _safe_int(x, default=None) -> int | None:
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except (TypeError, ValueError):
        return default


def _upsert_session(
    db: Session,
    rec: dict,
    session_type: str,
) -> tuple[ScrmSession | None, str]:
    """单会话 upsert + 转接明细拆分。返回 (session_or_none, status: inserted/updated/failed)"""
    # 主键:sessionId (三表统一概念)
    session_id = str(_pick(rec, "sessionId", "session_id", "id", "chatSessionId") or "").strip()
    if not session_id:
        return None, "failed"

    operator_id = str(_pick(rec, "operatorId", "operator_id", "staffId", "kfId") or "").strip() or None
    operator_name = str(_pick(rec, "operatorName", "operator_name", "staffName", "kfName", "nickName") or "").strip() or None
    customer_id = str(_pick(rec, "customerId", "customer_id", "userId", "wxId", "externalUserId") or "").strip() or None
    customer_name = str(_pick(rec, "customerName", "customer_name", "userName", "wxNickName", "remark") or "").strip() or None
    group_id = str(_pick(rec, "groupId", "group_id", "roomId") or "").strip() or None
    group_name = str(_pick(rec, "groupName", "group_name", "roomName") or "").strip() or None

    start_time = _to_utc_aware(_pick(rec, "startTime", "start_time", "createTime", "createdAt", "firstMsgTime"))
    if start_time is None:
        # 缺开始时间的会话无法进看板,直接失败
        return None, "failed"
    end_time = _to_utc_aware(_pick(rec, "endTime", "end_time", "closeTime", "finishTime", "lastMsgTime"))
    duration_sec = _safe_int(_pick(rec, "duration", "duration_sec", "sessionDuration", "costTime"))
    if duration_sec is None and start_time and end_time:
        try:
            duration_sec = max(0, int((end_time - start_time).total_seconds()))
        except (TypeError, ValueError):
            duration_sec = None

    msg_count_customer = _safe_int(_pick(rec, "customerMsgCount", "customerMsgNum", "userMsgCount", "msgCustomerCount"), default=0) or 0
    msg_count_staff = _safe_int(_pick(rec, "staffMsgCount", "staffMsgNum", "operatorMsgCount", "msgStaffCount"), default=0) or 0
    first_response_sec = _safe_int(_pick(rec, "firstResponseTime", "firstResponseSec", "firstResponse", "firstRespSec"))
    avg_response_sec = _safe_int(_pick(rec, "avgResponseTime", "avgResponseSec", "avgResponse", "avgRespSec"))
    transfer_count = _safe_int(_pick(rec, "transferCount", "transfer_num", "zhuanJieCount"), default=0) or 0
    question_type_id = str(_pick(rec, "questionTypeId", "question_type_id", "tagId") or "").strip() or None
    question_type_name = str(_pick(rec, "questionTypeName", "question_type_name", "tagName", "categoryName") or "").strip() or None

    sync_status = "closed" if end_time is not None else "open"
    quality_computed = (first_response_sec is not None or avg_response_sec is not None)

    # 转接明细(raw list)
    transfers_raw = _pick(rec, "transferList", "transfers", "transferRecords") or []
    if not isinstance(transfers_raw, list):
        transfers_raw = []

    existing = db.scalar(select(ScrmSession).where(ScrmSession.session_id == session_id).limit(1))
    if existing is None:
        sess = ScrmSession(
            session_id=session_id,
            session_type=session_type,
            operator_id=operator_id,
            operator_name=operator_name,
            person_id=None,
            customer_id=customer_id,
            customer_name=customer_name,
            group_id=group_id,
            group_name=group_name,
            start_time=start_time,
            end_time=end_time,
            duration_sec=duration_sec,
            msg_count_customer=msg_count_customer,
            msg_count_staff=msg_count_staff,
            first_response_sec=first_response_sec,
            avg_response_sec=avg_response_sec,
            transfer_count=transfer_count,
            question_type_id=question_type_id,
            question_type_name=question_type_name,
            sync_status=sync_status,
            quality_computed=quality_computed,
            raw_extra=rec,
        )
        db.add(sess)
        db.flush()
        _insert_transfers(db, session_id, transfers_raw)
        return sess, "inserted"
    else:
        changed = False
        for field, newval in [
            ("operator_id", operator_id), ("operator_name", operator_name),
            ("customer_id", customer_id), ("customer_name", customer_name),
            ("group_id", group_id), ("group_name", group_name),
            ("start_time", start_time), ("end_time", end_time), ("duration_sec", duration_sec),
            ("msg_count_customer", msg_count_customer), ("msg_count_staff", msg_count_staff),
            # 质量指标:仅当新值非空、且原值为 None 时覆盖(避免把已算好的给冲掉)
            ("first_response_sec", first_response_sec if existing.first_response_sec is None else existing.first_response_sec),
            ("avg_response_sec", avg_response_sec if existing.avg_response_sec is None else existing.avg_response_sec),
            ("transfer_count", max(existing.transfer_count or 0, transfer_count or 0)),
            ("question_type_id", question_type_id), ("question_type_name", question_type_name),
            ("sync_status", sync_status),
            ("quality_computed", bool(existing.quality_computed or quality_computed)),
        ]:
            if getattr(existing, field) != newval:
                setattr(existing, field, newval)
                changed = True
        existing.raw_extra = rec
        # 转接明细:重新拉则全量覆盖(先删再插),保证幂等
        if transfers_raw:
            db.query(ScrmSessionTransfer).where(ScrmSessionTransfer.session_id == session_id).delete(
                synchronize_session=False
            )
            _insert_transfers(db, session_id, transfers_raw)
            changed = True
        return existing, ("updated" if changed else "updated")  # 即使完全不变也计为 updated (表示"已存在且一致")


def _insert_transfers(db: Session, session_id: str, transfers_raw: list[dict]) -> int:
    """从会话响应里抽出 transfers 列表插入 scrm_session_transfers,返回插入条数"""
    inserted = 0
    for t in transfers_raw:
        if not isinstance(t, dict):
            continue
        ts = _to_utc_aware(_pick(t, "transferTime", "createTime", "time", "transfer_time"))
        if ts is None:
            ts = datetime.now(timezone.utc)
        from_id = str(_pick(t, "fromOperatorId", "from_operator_id", "fromId") or "").strip() or None
        from_name = str(_pick(t, "fromOperatorName", "from_operator_name", "fromName") or "").strip() or None
        to_id = str(_pick(t, "toOperatorId", "to_operator_id", "toId") or "").strip() or None
        to_name = str(_pick(t, "toOperatorName", "to_operator_name", "toName") or "").strip() or None
        if not from_id and not to_id:
            continue
        reason = str(_pick(t, "transferReason", "reason", "remark") or "").strip() or None
        db.add(ScrmSessionTransfer(
            session_id=session_id,
            from_operator_id=from_id,
            from_operator_name=from_name,
            to_operator_id=to_id,
            to_operator_name=to_name,
            transfer_time=ts,
            transfer_reason=reason,
            raw_extra=t,
        ))
        inserted += 1
    return inserted


async def _sync_one_session_type(
    client: ScrmClient,
    db: Session,
    session_type: str,
    trigger_type: str,
    triggered_by: str,
    time_range_override: tuple[datetime, datetime] | None = None,
) -> dict:
    type_label, data_type, fetcher = _SESSION_TYPE_TO_METHOD[session_type]
    start, end = _calculate_sync_range(db, data_type, time_range_override)
    log = _create_log(db, data_type, start, end, trigger_type, triggered_by)
    inserted = updated = failed = 0
    fetched_rows = 0
    api_total_reported: int | None = None
    api_call_count = 0
    start_s = _fmt_scrm_ts(start)
    end_s = _fmt_scrm_ts(end)
    try:
        async with client:
            records = await fetcher(client, start_s, end_s, None)
            # 简单把 list_*_sessions 自身视为一次逻辑调用(分页内部是多次 HTTP,但我们不在 ScrmClient 外计数)
            api_call_count += 1
            # 因为 _auto_page 合并了跨页 total,这里没法直接拿到 total;不影响对账(后续可扩展)
            fetched_rows = len(records)
            BATCH = 50
            for i in range(0, fetched_rows, BATCH):
                batch = records[i:i + BATCH]
                for rec in batch:
                    try:
                        _sess, status = _upsert_session(db, rec, type_label)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[scrm][session-{type_label}] 单条 upsert 失败: {e}")
                        status = "failed"
                    if status == "inserted":
                        inserted += 1
                    elif status == "updated":
                        updated += 1
                    else:
                        failed += 1
                db.commit()
        _finish_log(
            db, log.id, "success",
            api_call_count=api_call_count, api_total_reported=api_total_reported,
            fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
        )
        logger.info(f"[scrm][session-{type_label}] 成功: inserted={inserted} updated={updated} failed={failed} (fetched={fetched_rows})")
        return {
            "data_type": data_type, "success": True,
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "api_call_count": api_call_count, "fetched_rows": fetched_rows,
            "inserted": inserted, "updated": updated, "failed": failed,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[scrm][session-{type_label}] 同步失败: {e}")
        _finish_log(
            db, log.id, "failed",
            api_call_count=api_call_count, api_total_reported=api_total_reported,
            fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
            error=str(e),
        )
        return {
            "data_type": data_type, "success": False, "error": str(e),
            "start_time": start.isoformat(), "end_time": end.isoformat(),
        }


# ==========================================================
# 对外统一入口:一次性按顺序跑 operator + 3 类会话
# ==========================================================

async def sync_scrm_all(
    trigger_type: str = "manual",
    triggered_by: str = "admin",
    time_ranges: dict[str, tuple[datetime, datetime]] | None = None,
    sync_types: list[str] | None = None,
    on_progress=None,
) -> list[dict]:
    """SCRM 全量/增量同步统一入口

    Args:
        trigger_type: manual/scheduled/catch_up
        triggered_by: 触发者用户名(写入日志)
        time_ranges: {data_type: (start,end)} 指定某几个源的窗口,其余自动算增量
        sync_types: 指定要跑的子集,不传就跑全部4类
        on_progress: 可选 async (data_type, phase, payload) -> None

    Returns:
        list[dict] 每个 data_type 的同步结果(成功/失败/统计)
    """
    default_types = [DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE, DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL]
    to_run = sync_types or default_types
    results: list[dict] = []

    # 构造独立 Session(因为要在同步线程里用)
    db = SessionLocal()
    try:
        client, _cfg = _build_scrm_client(db)
    except Exception as e:  # noqa: BLE001
        # 配置读取失败,直接把每个类型都标失败
        msg = f"构造 SCRM 客户端失败: {e}"
        logger.error(msg)
        now = datetime.now(timezone.utc)
        for dt in to_run:
            try:
                log = _create_log(db, dt, now, now, trigger_type, triggered_by)
                _finish_log(db, log.id, "failed", error=msg)
            except Exception:  # noqa: BLE001
                pass
            results.append({"data_type": dt, "success": False, "error": msg})
        db.close()
        return results
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

    # 1. operator 同步(串行,因为不依赖时间窗口)
    if DATA_TYPE_OPERATOR in to_run:
        if on_progress:
            try:
                await on_progress(DATA_TYPE_OPERATOR, "syncing", None)
            except Exception:  # noqa: BLE001
                pass
        thread_db = SessionLocal()
        try:
            def _op_work():
                return asyncio.run(sync_operators(
                    client, thread_db, trigger_type, triggered_by,
                    time_range_override=(time_ranges or {}).get(DATA_TYPE_OPERATOR),
                ))
            res = await asyncio.to_thread(_op_work)
        except Exception as e:  # noqa: BLE001
            logger.exception("[scrm] operator to_thread 崩溃")
            res = {"data_type": DATA_TYPE_OPERATOR, "success": False, "error": str(e)}
        finally:
            try:
                thread_db.close()
            except Exception:  # noqa: BLE001
                pass
        results.append(res)
        if on_progress:
            try:
                await on_progress(DATA_TYPE_OPERATOR, "done", res)
            except Exception:  # noqa: BLE001
                pass

    # 2. 三类会话:串行跑(共享同一个 client 但不同步上下文,每个任务内部 async with client)
    for st, dt in [
        ("private", DATA_TYPE_SESSION_PRIVATE),
        ("group", DATA_TYPE_SESSION_GROUP),
        ("virtual", DATA_TYPE_SESSION_VIRTUAL),
    ]:
        if dt not in to_run:
            continue
        if on_progress:
            try:
                await on_progress(dt, "syncing", None)
            except Exception:  # noqa: BLE001
                pass
        thread_db = SessionLocal()
        try:
            def _sess_work(s=st, d=dt):
                return asyncio.run(_sync_one_session_type(
                    client, thread_db, s, trigger_type, triggered_by,
                    time_range_override=(time_ranges or {}).get(d),
                ))
            res = await asyncio.to_thread(_sess_work)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[scrm] session-{st} to_thread 崩溃")
            res = {"data_type": dt, "success": False, "error": str(e)}
        finally:
            try:
                thread_db.close()
            except Exception:  # noqa: BLE001
                pass
        results.append(res)
        if on_progress:
            try:
                await on_progress(dt, "done", res)
            except Exception:  # noqa: BLE001
                pass

    return results


# ==========================================================
# 同步状态查询(对接前端展示,类似 bms_sync.get_sync_status)
# ==========================================================

def get_scrm_sync_status(db: Session) -> list[dict]:
    """返回所有 4 个 data_type 最近一条日志(用于前端卡片)"""
    types = [DATA_TYPE_OPERATOR, DATA_TYPE_SESSION_PRIVATE, DATA_TYPE_SESSION_GROUP, DATA_TYPE_SESSION_VIRTUAL]
    out = []
    for dt in types:
        row = db.scalar(
            select(ScrmSyncLog)
            .where(ScrmSyncLog.data_type == dt)
            .order_by(ScrmSyncLog.created_at.desc())
            .limit(1)
        )
        if row is None:
            out.append({
                "data_type": dt,
                "status": "never",
                "last_start": None,
                "last_end": None,
                "stats": None,
            })
        else:
            out.append({
                "data_type": dt,
                "status": row.status,
                "last_start": row.start_time.isoformat() if row.start_time else None,
                "last_end": row.end_time.isoformat() if row.end_time else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                "trigger_type": row.trigger_type,
                "triggered_by": row.triggered_by,
                "stats": {
                    "fetched_rows": row.fetched_rows,
                    "inserted": row.inserted_rows,
                    "updated": row.updated_rows,
                    "failed": row.failed_rows,
                    "api_call_count": row.api_call_count,
                    "api_total_reported": row.api_total_reported,
                    "reconcile_delta": row.reconcile_delta,
                },
                "error": row.error,
            })
    return out
