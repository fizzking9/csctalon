"""SCRM 微信聚合平台同步服务 - 双模式抓取(OpenAPI + Playwright 网页兜底)

核心职责:
  1. 操作员列表:每次全量 upsert(覆盖更新,不做历史)
  2. 会话列表(私聊/群聊/虚拟)三接口:按时间窗口增量拉取,upsert 到 scrm_sessions
     - 转接明细:每个会话若包含 transfers,逐条拆出到 scrm_session_transfers
     - 质量指标:若列表接口直接返回 firstResponse/avgResponse,就填;否则标记 quality_computed=False,等后续按需计算
     - 对账:记录 api_total_reported vs (inserted+updated) 的偏差
  3. 写 ScrmSyncLog(与 BmsSyncLog 独立,拆分 inserted/updated/failed 三分类)
  4. 不做聊天记录明细(用户要求:量大暂不入库)

两种抓取模式(从 SyncSourceConfig.options.fetch_mode 读取):
  - openapi (默认): 原 ScrmClient HTTP OpenAPI (clientId/clientSecret)
  - playwright: 纯网页版抓取(Playwright + 数学计算式验证码自动识别)
  - dual: 先尝试 OpenAPI,失败自动回退 Playwright (最稳妥的兜底模式)

复用现有依赖:
  - SyncSourceConfig 存储 clientId / clientSecret 或 网页账号密码 (Fernet 加密,用户名=clientId/登录账号,密码=clientSecret/登录密码)
  - ScrmClient 封装 token / 分页 / 错误处理
  - ScrmWebFetcher 独立服务,Playwright + 数学验证码 OCR
  - BmsScheduler 后续接入节奏(SCHEDULE_HOURS: 8~23+0 每小时 + 5:00 补跑 + 8:00 全量兜底)
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

# ── P0-1: 单个 data_type 同步超时(单 operator / 单会话类型) 超过直接取消,防止僵死 ──
SCRM_SYNC_PER_SOURCE_TIMEOUT_SEC = 300  # 5 分钟

# ── P2-2: 进程级 SCRM Playwright 登录失败全局缓存(带 TTL) ──
# 解决: 两个任务并发(例如 scheduler + manual 快速点两次) 各自一份缓存,
#   会分别启浏览器 + OCR + 登录尝试 2 次。升级为进程级全局缓存,带 10 分钟 TTL。
# key: (base_url, username) → (expire_monotonic_sec, error_message)
_PW_LOGIN_FAIL_CACHE_GLOBAL: dict[tuple[str, str], tuple[float, str]] = {}
_PW_LOGIN_FAIL_CACHE_TTL_SEC = 600  # 10 分钟
import asyncio as _asyncio_global

_PW_LOGIN_FAIL_CACHE_LOCK = _asyncio_global.Lock()


async def _pw_global_cache_get(key: tuple[str, str]) -> str | None:
    """读取进程级全局缓存,过期自动清理;None=未命中"""
    import time as _time
    async with _PW_LOGIN_FAIL_CACHE_LOCK:
        item = _PW_LOGIN_FAIL_CACHE_GLOBAL.get(key)
        if item is None:
            return None
        expire_at, err = item
        if _time.monotonic() > expire_at:
            _PW_LOGIN_FAIL_CACHE_GLOBAL.pop(key, None)
            return None
        return err


async def _pw_global_cache_put(key: tuple[str, str], err: str) -> None:
    """写入进程级全局缓存(带 TTL)"""
    import time as _time
    async with _PW_LOGIN_FAIL_CACHE_LOCK:
        _PW_LOGIN_FAIL_CACHE_GLOBAL[key] = (_time.monotonic() + _PW_LOGIN_FAIL_CACHE_TTL_SEC, err)

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

    注意:
      - fetch_mode=playwright 时, username/password 是**网页登录账号密码**,不是 OpenAPI ClientID/ClientSecret,
        所以 client_id/client_secret 会传空字符串。ScrmClient 允许空值(仅作为 cfg 载体),
        真正调用 OpenAPI 方法时才会在 _ensure_openapi_ready() 里报错。
      - fetch_mode=openapi 且缺 ClientID/Secret → 直接抛明确错误(提示用户切换模式或填写凭证)。
      - fetch_mode=dual 且缺 ClientID/Secret → 警告日志但不报错(后续同步会自动回退到 playwright)。

    Returns: (client, config_dict)
    Raises: ValueError 未配置/已停用 / openapi 模式缺凭证且未启用网页回退
    """
    # 任一 scrm 源都能拿到同一份 clientId/secret,取 scrm_operator 作为代表
    cfg = get_sync_source_config(db, DATA_TYPE_OPERATOR)
    if not cfg:
        raise ValueError("SCRM 数据源未注册,请先在后端启动后让 ensure_sync_sources_registered 灌入种子")
    if not cfg.get("enabled", True):
        raise ValueError("SCRM 数据源已停用(在同步设置中关闭)")
    base_url = cfg.get("base_url") or ""
    # ⚠️ 2026-08-06: 配置拆分后,username/password 的含义随 fetch_mode 变化:
    #   - openapi 模式: username/password = ClientID / ClientSecret
    #   - playwright 模式: username/password = SCRM 网页登录账号/密码(真实手机号/邮箱)
    #   - dual 模式:
    #        * username/password = OpenAPI ClientID / ClientSecret (主字段, 供 _build_scrm_client 用)
    #        * options.web_username / options.web_password = 独立的网页登录凭证
    fetch_mode = str(cfg.get("fetch_mode") or "openapi")
    if fetch_mode in ("openapi", "dual"):
        client_id = (cfg.get("username") or "").strip()
        client_secret = (cfg.get("password") or "").strip()
    else:
        # playwright 模式: username/password 是网页登录账号, 不是 OpenAPI 凭证 → 传空
        client_id = ""
        client_secret = ""

    # ---------- 前置:按模式给更精准的提示(让用户一眼看出缺啥) ----------
    has_web_cred = _has_playwright_credentials(cfg)
    if fetch_mode == "openapi" and (not client_id or not client_secret):
        raise ValueError(
            "当前抓取模式=OpenAPI(默认稳定模式),但未配置 ClientID/ClientSecret。"
            "请在「同步设置」中填写 OpenAPI 凭证;"
            "或把抓取模式切换为「Playwright 网页」(仅需网页登录账号密码,数学验证码一次性登录)。"
        )
    if fetch_mode == "dual" and (not client_id or not client_secret):
        logger.warning(
            "[scrm][build_client] 抓取模式=双模式,但 OpenAPI ClientID/ClientSecret 未配置,"
            " 后续同步将直接跳过 OpenAPI 第一轮,只用 Playwright 网页抓取兜底。"
            " 如需稳定优先请补填 ClientID/ClientSecret。"
        )
    if fetch_mode == "playwright" and not has_web_cred:
        raise ValueError(
            "当前抓取模式=Playwright 网页,但未配置网页登录账号密码。"
            "请在「同步设置」中填写 SCRM 管理后台的登录账号/密码(手机号/邮箱 + 登录密码)。"
        )

    client = ScrmClient(base_url=base_url, client_id=client_id, client_secret=client_secret)
    # 把 cfg 附到 client 上, 后续 sync_operators 等函数读取 fetch_mode/options.web_* 用,
    # 避免每个子函数再各自 get_sync_source_config 重新查一次(浪费 SQL 且结果不一致风险)
    try:
        setattr(client, "_attached_cfg", cfg)
    except Exception:
        pass
    return client, cfg


def _has_playwright_credentials(cfg: dict) -> bool:
    """判断当前配置是否已具备 Playwright 网页登录所需的账号密码。

    读取优先级:
      1) options.web_username / options.web_password (dual 模式下独立配置的网页凭证)
      2) username / password (playwright 模式下直接用作网页凭证)
    """
    opts = cfg.get("options")
    opts = opts if isinstance(opts, dict) else {}
    web_user = (opts.get("web_username") or "").strip()
    web_pwd = (opts.get("web_password") or "").strip()
    if web_user and web_pwd:
        return True
    if str(cfg.get("fetch_mode") or "openapi") == "playwright":
        user = (cfg.get("username") or "").strip()
        pwd = (cfg.get("password") or "").strip()
        if user and pwd:
            return True
    return False


def _to_utc_aware(ts: Any) -> datetime | None:
    """宽松解析 SCRM 各种时间字段 → 带 UTC 时区的 datetime;失败返回 None

    ⚠️ 时区假设待核实(2026-08-24 备注, SCRM 数据暂未同步无实证):
       - 当前把 naive 时间一律视为 UTC 存储;
       - 但查询参数是按东八区发送的(_fmt_scrm_ts, "SCRM 平台要求东八区")。
       若上游接口返回的其实是北京时间 naive, 这里会偏 8 小时(读回北京后 +8h)。
       待 SCRM 首次同步有数据后, 需用实际数据验证(startTime 换算北京是否落在
       客服上班时段 08:00~23:59), 确认后再决定 naive 分支是否改为按北京解释。
    """
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


def _looks_like_openapi_clientid(val: str) -> bool:
    """
    启发式判断 SCRM username/password 是否更像 OpenAPI 的 ClientID/ClientSecret,
    而不是网页登录用的账号密码。

    网页登录凭证通常是:手机号(11 位数字) / 邮箱 / 员工号(3-16 位常见);
    OpenAPI 凭证通常是:UUID 风格(含大量「-」+hex) / 长度>24 的高熵随机串 / 明显的 Bearer Token 前缀。
    """
    if not val:
        return False
    v = val.strip()
    if not v:
        return False
    # UUID / GUID 含 4 个以上横杠(8-4-4-4-12)
    if v.count("-") >= 3:
        return True
    # 明显的 Bearer Token 前缀
    low = v.lower()
    for prefix in ("bearer ", "sk_", "sk-", "client_", "api_", "secret_", "secret-", "access_", "token:"):
        if low.startswith(prefix):
            return True
    # 长度 > 24 的高熵随机串(网页账号很少这么长且含大量数字字母混合符号)
    if len(v) > 24:
        import re
        # 包含 2 种以上字符类别且长度偏长
        classes = 0
        if re.search(r"[a-z]", v): classes += 1
        if re.search(r"[A-Z]", v): classes += 1
        if re.search(r"\d", v): classes += 1
        if re.search(r"[^A-Za-z0-9]", v): classes += 1
        if classes >= 3:
            return True
    return False


def _resolve_pw_credentials(cfg: dict) -> tuple[str, str, str | None]:
    """
    解析 SCRM Playwright 模式的网页登录凭证。

    ⚠️ 2026-08-06: 与前端表单模型(SyncDashboard.vue + views/system/SyncSettings.vue)完全对齐:
      - fetch_mode = playwright:
          主字段 username/password 就是 SCRM 网页登录凭证(真实手机号/邮箱 + 登录密码)
          → 直接取, 不再做 OpenAPI 启发式判断(避免把真实手机号误判为"看起来像 ClientID"报错)
      - fetch_mode = dual (OpenAPI+Playwright):
          主字段 username/password = OpenAPI ClientID/ClientSecret
          options.web_username / options.web_password = 独立的网页登录凭证
          → 1) 优先 options.web_*; 2) 没有就 fallback 主字段但要做 OpenAPI 启发式过滤
      - fetch_mode = openapi:
          理论上不会调本函数(Playwright 分支才用); 若真进来了, 走 dual 模式的保守策略

    返回: (web_username, web_password, error_or_None)
      error_or_None 不为 None 时,表示凭证不可用,调用方应跳过 Playwright 启动并使用该错误消息。
    """
    fetch_mode = str(cfg.get("fetch_mode") or "openapi")
    options = cfg.get("options") or {}
    web_user = (options.get("web_username") or "").strip()
    web_pwd = (options.get("web_password") or "").strip()
    fallback_user = (cfg.get("username") or "").strip()
    fallback_pwd = (cfg.get("password") or "").strip()

    if fetch_mode == "playwright":
        # Playwright 模式: 主字段 username/password 直接就是网页登录凭证(用户最直观的选择)
        if fallback_user and fallback_pwd:
            return fallback_user, fallback_pwd, None
        # 兼容过渡: 老版本的 playwright 配置可能是把 web 凭证写在 options.web_* 里, 但主字段没动
        if web_user and web_pwd:
            return web_user, web_pwd, None
        return "", "", (
            "Playwright 模式缺少 SCRM 网页登录账号密码(SCRM 网页账号 + SCRM 网页密码)。"
            " 请在「同步设置 / 数据同步页的编辑同步源」中把抓取模式改为 Playwright, 并在"
            " 主字段的 SCRM 网页账号 / SCRM 网页密码中填入真实的 SCRM 后台登录账号密码(手机号/邮箱 + 登录密码)。"
            " 注意: 留空表示不修改原值, 但如果你之前从未保存过, 需要真的填一次。"
        )

    # dual / openapi 策略一致: 优先 options.web_* (显式声明独立的网页凭证), 再 fallback 主字段并做 OpenAPI 启发式
    if web_user and web_pwd:
        return web_user, web_pwd, None
    if fallback_user and fallback_pwd:
        if _looks_like_openapi_clientid(fallback_user) or _looks_like_openapi_clientid(fallback_pwd):
            return "", "", (
                "当前 SCRM fetch_mode=" + fetch_mode + ", 主字段 username/password 看起来是 OpenAPI 凭证(ClientID/ClientSecret),"
                " 不能直接用于 Playwright 网页登录。请在「同步设置 / 数据同步页」的「编辑同步源」中,"
                " 选择抓取模式为「双模式(Dual)」, 并在下方独立的「Playwright 网页登录凭证」区块填写"
                " 真实的 SCRM 后台登录账号密码(手机号/邮箱 + 登录密码); 或把抓取模式改为 Playwright 后直接把网页账号密码填到主字段。"
                " 示例错误: username 是 UUID 格式 / bearer/sk_/client_ 前缀 / 长度>24 且含多种字符。"
            )
        return fallback_user, fallback_pwd, None
    return "", "", (
        "缺少 SCRM 网页登录账号密码。请在「同步设置 / 数据同步页的编辑同步源」中填写:"
        " * Playwright 模式: 把主字段改为 SCRM 网页账号 / SCRM 网页密码(手机号/邮箱 + 登录密码); 或"
        " * Dual/OpenAPI 模式: 在下方独立的「Playwright 网页登录凭证」区块填写 options.web_username + options.web_password,"
        " 并保证主字段 username/password 是真实的 OpenAPI ClientID/ClientSecret。"
    )


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
    remark: str | None = None,
) -> None:
    """收尾日志(带事务回滚兜底,避免前面入库异常导致 session aborted 无法 commit)"""
    try:
        log = db.get(ScrmSyncLog, log_id)
    except Exception:  # noqa: BLE001
        db.rollback()
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
    if remark:
        log.remark = (remark or "")[:1000]
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
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
    pw_login_fail_cache: dict[tuple[str, str], str] | None = None,
) -> dict:
    """同步操作员列表(每次全量覆盖)

    双模式: cfg.fetch_mode = openapi(走 client), playwright(走 web), dual(先 API 失败回退 web)

    Args:
        pw_login_fail_cache: 任务级别 (base_url, username) → 错误消息 的记忆字典。
            同一个任务内如果某 (base_url, username) 已因"登录类错误"失败过,
            后续所有同源 (base_url+username 相同) 的 Playwright 抓取直接返回同一个失败,
            不再反复启动浏览器、OCR、登录,避免日志反复弹同一种登录失败。
            None 表示不启用缓存(兼容旧调用方)。
    """
    start, end = _calculate_sync_range(db, DATA_TYPE_OPERATOR, time_range_override)
    log = _create_log(db, DATA_TYPE_OPERATOR, start, end, trigger_type, triggered_by)
    log_id = log.id
    log_closed = False
    cfg: dict = getattr(client, "_attached_cfg", None) or get_sync_source_config(db, DATA_TYPE_OPERATOR) or {}
    fetch_mode = cfg.get("fetch_mode") or "openapi"

    inserted = updated = failed = 0
    api_call_count = 0
    api_total_reported: int | None = None
    fetched_rows = 0
    error_msg: str | None = None
    success = False
    fetch_source = "openapi"

    # 登录失败缓存用的 key: base_url + username
    cache_key: tuple[str, str] | None = None
    if pw_login_fail_cache is not None:
        cache_key = (cfg.get("base_url") or "", cfg.get("username") or "")

    try:
        # ── 第 1 轮: OpenAPI (仅 openapi / dual 模式) ──
        try_openapi = fetch_mode in ("openapi", "dual")
        records: list[dict] | None = None
        if try_openapi:
            try:
                async with client:
                    raw = await client.list_operators()
                    api_call_count += 1
                    if isinstance(raw, dict):
                        api_total_reported = int(raw.get("total") or 0) or None
                        raw = raw.get("records") or []
                    records = list(raw) if raw else []
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[scrm][operator] OpenAPI 同步失败(模式={fetch_mode}): {e}")
                error_msg = f"OpenAPI: {e}"
                records = None  # 触发回退
        # ── 第 2 轮: Playwright 网页抓取 (playwright 直接; dual 在 API 失败时) ──
        fetch_source = "openapi"
        if records is None and fetch_mode in ("playwright", "dual"):
            logger.info(f"[scrm][operator] 回退到 Playwright 网页抓取(触发原因:{'API失败' if try_openapi else '指定playwright模式'})")
            fetch_source = "playwright"

            # ⚠️ 熔断检测: 如果当前 (base_url,username) 之前已经在同任务内因登录类错误失败过,
            #    直接返回同一个失败原因,不要反复启浏览器 + OCR + 登录 → 日志无限弹相同错误。
            if cache_key is not None and cache_key in pw_login_fail_cache:
                err_cached = pw_login_fail_cache[cache_key]
                logger.warning(
                    "[scrm][operator] Playwright 同 (base_url=%r,user=%r) 已在同任务内记录登录类失败: %r;跳过本次抓取以避免反复弹日志",
                    cache_key[0], cache_key[1], err_cached,
                )
                error_msg = (error_msg + " | " if error_msg else "") + f"Playwright: [同域登录熔断复用] {err_cached}"
                records = None
            else:
                # ── P2-2: 先查进程级全局缓存(跨任务复用,避免快速点两次同步两次启浏览器) ──
                global_cached_err: str | None = None
                if cache_key is not None:
                    global_cached_err = await _pw_global_cache_get(cache_key)
                    if global_cached_err:
                        logger.warning(
                            "[scrm][operator] Playwright (base_url=%r,user=%r) 命中进程级全局登录失败缓存(10min TTL): %r",
                            cache_key[0], cache_key[1], global_cached_err,
                        )
                if global_cached_err:
                    error_msg = (error_msg + " | " if error_msg else "") + f"Playwright: [全局登录熔断复用] {global_cached_err}"
                    pw_login_fail_cache[cache_key] = global_cached_err  # 同步写回任务级,减少下一个源的查锁开销
                    records = None
                else:
                    try:
                        from app.services.scrm_web_fetcher import run_web_fetch
                        # ⚠️ 先解析网页登录凭证:避免拿 ClientID/ClientSecret 填网页登录框 → 100% 失败反复弹日志
                        pw_user, pw_pwd, pw_cred_err = _resolve_pw_credentials(cfg)
                        if pw_cred_err is not None:
                            raise RuntimeError(f"Playwright 凭证校验失败: {pw_cred_err}")
                        # SCRM 时间格式: start/end datetime → date(网页一般按日期查)
                        start_date = start.astimezone(timezone.utc).date() if start.tzinfo else start.date()
                        end_date = end.astimezone(timezone.utc).date() if end.tzinfo else end.date()
                        # ⚠️ P0-3: run_web_fetch 已经是 async def,直接 await,不再嵌套 asyncio.run
                        web_res = await run_web_fetch(
                            base_url=cfg.get("base_url") or "",
                            username=pw_user,
                            password=pw_pwd,
                            options=cfg.get("options") or {},
                            data_types=[DATA_TYPE_OPERATOR],
                            date_range=(start_date, end_date),
                            logger=logger,
                        )
                        records = list(web_res.get(DATA_TYPE_OPERATOR) or [])
                        api_total_reported = None  # web 抓取没有官方 total 字段
                    except Exception as e:  # noqa: BLE001
                        logger.exception(f"[scrm][operator] Playwright 抓取也失败: {e}")
                        err_txt = f"Playwright: {e}"
                        error_msg = (error_msg + " | " if error_msg else "") + err_txt
                        records = None
                        # ⚠️ 记忆化: 登录/凭证/验证码/启动浏览器类错误,都写入缓存(下一个源直接跳过)
                        _low_err = str(e).lower()
                        _cred_match = "凭证校验失败" in str(e) or "凭据校验失败" in str(e)
                        should_cache_login_fail = (
                            _cred_match
                            or any(k in _low_err for k in ("登录", "账号", "密码", "验证码", "启动 playwright", "playwright: 启动"))
                            or (isinstance(e, RuntimeError) and any(k in str(e) for k in ("登录", "用户名或密码", "验证码", "凭证")))
                        )
                        if pw_login_fail_cache is not None and should_cache_login_fail:
                            if cache_key is not None:
                                pw_login_fail_cache[cache_key] = str(e)
                                # ── P2-2: 同步写进程级全局缓存(带 10min TTL) ──
                                await _pw_global_cache_put(cache_key, str(e))

        # ── 共用入库逻辑 ──
        success = False
        if records is not None:
            try:
                fetched_rows = len(records)
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
                success = True
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[scrm][operator] 入库失败: {e}")
                error_msg = (error_msg + " | " if error_msg else "") + f"Upsert: {e}"

        if success:
            _finish_log(
                db, log_id, "success",
                api_call_count=api_call_count, api_total_reported=api_total_reported,
                fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
                remark=f"fetch_source={fetch_source}",
            )
            log_closed = True
            logger.info(f"[scrm][operator] 成功(source={fetch_source}): inserted={inserted} updated={updated} failed={failed}")
            return {
                "data_type": DATA_TYPE_OPERATOR, "success": True,
                "start_time": start.isoformat(), "end_time": end.isoformat(),
                "api_call_count": api_call_count, "fetched_rows": fetched_rows,
                "inserted": inserted, "updated": updated, "failed": failed,
                "fetch_source": fetch_source,
            }
        else:
            _finish_log(
                db, log_id, "failed",
                api_call_count=api_call_count, api_total_reported=api_total_reported,
                fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
                error=str(error_msg or "两种抓取模式均失败"),
                remark=f"fetch_source={fetch_source}, 双模式均失败",
            )
            log_closed = True
            logger.error(f"[scrm][operator] 同步失败: {error_msg}")
            return {
                "data_type": DATA_TYPE_OPERATOR, "success": False, "error": str(error_msg or "两种抓取模式均失败"),
                "start_time": start.isoformat(), "end_time": end.isoformat(),
                "fetch_source": fetch_source,
            }
    finally:
        if not log_closed:
            try:
                _finish_log(
                    db, log_id, "failed",
                    api_call_count=api_call_count, api_total_reported=api_total_reported,
                    fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
                    error=str(error_msg or "同步过程异常中断(兜底)"),
                    remark=f"[finally 兜底] fetch_source={fetch_source}",
                )
            except Exception:  # noqa: BLE001
                pass


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
    pw_login_fail_cache: dict[tuple[str, str], str] | None = None,
) -> dict:
    type_label, data_type, fetcher = _SESSION_TYPE_TO_METHOD[session_type]
    start, end = _calculate_sync_range(db, data_type, time_range_override)
    log = _create_log(db, data_type, start, end, trigger_type, triggered_by)
    log_id = log.id
    log_closed = False
    cfg: dict = getattr(client, "_attached_cfg", None) or get_sync_source_config(db, DATA_TYPE_OPERATOR) or {}
    fetch_mode = cfg.get("fetch_mode") or "openapi"

    inserted = updated = failed = 0
    fetched_rows = 0
    api_total_reported: int | None = None
    api_call_count = 0
    start_s = _fmt_scrm_ts(start)
    end_s = _fmt_scrm_ts(end)
    error_msg: str | None = None
    fetch_source = "openapi"
    success = False

    # 登录失败缓存用的 key: base_url + username
    cache_key: tuple[str, str] | None = None
    if pw_login_fail_cache is not None:
        cache_key = (cfg.get("base_url") or "", cfg.get("username") or "")

    try:
        # ── 第 1 轮: OpenAPI ──
        try_openapi = fetch_mode in ("openapi", "dual")
        records: list[dict] | None = None
        transfers_records: list[dict] | None = None  # 仅 web 模式下 scrm_session_transfers 会用到
        if try_openapi:
            try:
                async with client:
                    raw = await fetcher(client, start_s, end_s, None)
                    api_call_count += 1
                    records = list(raw) if raw else []
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[scrm][session-{type_label}] OpenAPI 失败(模式={fetch_mode}): {e}")
                error_msg = f"OpenAPI: {e}"
                records = None
        # ── 第 2 轮: Playwright 网页抓取 ──
        fetch_source = "openapi"
        if records is None and fetch_mode in ("playwright", "dual"):
            logger.info(
                f"[scrm][session-{type_label}] 回退到 Playwright 网页抓取"
                f"(触发原因:{'API失败' if try_openapi else '指定playwright模式'})"
            )
            fetch_source = "playwright"

            # ⚠️ 熔断检测: 同 (base_url,user) 已经有登录类失败记录 → 直接跳过,不再反复启浏览器
            if cache_key is not None and cache_key in pw_login_fail_cache:
                err_cached = pw_login_fail_cache[cache_key]
                logger.warning(
                    "[scrm][session-%s] Playwright 同 (base_url=%r,user=%r) 已在同任务内记录登录类失败: %r;跳过本次抓取",
                    type_label, cache_key[0], cache_key[1], err_cached,
                )
                error_msg = (error_msg + " | " if error_msg else "") + f"Playwright: [同域登录熔断复用] {err_cached}"
                records = None
            else:
                # ── P2-2: 先查进程级全局缓存(跨任务复用) ──
                global_cached_err: str | None = None
                if cache_key is not None:
                    global_cached_err = await _pw_global_cache_get(cache_key)
                    if global_cached_err:
                        logger.warning(
                            "[scrm][session-%s] Playwright (base_url=%r,user=%r) 命中进程级全局登录失败缓存(10min TTL): %r",
                            type_label, cache_key[0], cache_key[1], global_cached_err,
                        )
                if global_cached_err:
                    error_msg = (error_msg + " | " if error_msg else "") + f"Playwright: [全局登录熔断复用] {global_cached_err}"
                    if cache_key is not None:
                        pw_login_fail_cache[cache_key] = global_cached_err
                    records = None
                else:
                    try:
                        from app.services.scrm_web_fetcher import run_web_fetch
                        # ⚠️ 先解析网页登录凭证:避免拿 ClientID/ClientSecret 填网页登录框 → 100% 失败反复弹
                        pw_user, pw_pwd, pw_cred_err = _resolve_pw_credentials(cfg)
                        if pw_cred_err is not None:
                            raise RuntimeError(f"Playwright 凭证校验失败: {pw_cred_err}")
                        start_date = start.astimezone(timezone.utc).date() if start.tzinfo else start.date()
                        end_date = end.astimezone(timezone.utc).date() if end.tzinfo else end.date()
                        # web_sessions / web_transfers 两个都抓(因为同步 scrm_sessions 时若 transfers_list 内嵌可能不全)
                        web_to_fetch = ["scrm_sessions"]
                        # SCRM 会话 3 类型(private/group/virtual) 在网页版可能是同一个 Tab,用通用抓取名 scrm_sessions
                        # ⚠️ P0-3: run_web_fetch 现在是 async def,直接 await
                        web_res = await run_web_fetch(
                            base_url=cfg.get("base_url") or "",
                            username=pw_user,
                            password=pw_pwd,
                            options=cfg.get("options") or {},
                            data_types=web_to_fetch,
                            date_range=(start_date, end_date),
                            logger=logger,
                        )
                        raw_sessions = list(web_res.get("scrm_sessions") or [])
                        # 网页版抓取可能不区分 private/group/virtual;
                        # 按 record 里的 sessionType/会话类型 字段筛选,没找到就默认全量给当前类型兜底(后续同步类型时会覆盖更新,不重复创建)
                        filtered: list[dict] = []
                        for rec in raw_sessions:
                            rtype = (
                                str(rec.get("sessionType") or rec.get("会话类型") or rec.get("type") or "").strip().lower()
                            )
                            if not rtype:
                                # 无类型字段:交给当前同步类型(3 个类型都同步一次总数据的话,会覆盖更新同一条 session)
                                filtered.append(rec)
                            elif type_label in rtype or rtype in type_label:
                                # 类型匹配
                                filtered.append(rec)
                            elif (type_label == "private" and ("私" in rtype or "1v1" in rtype or "p2p" in rtype)) or (
                                type_label == "group" and ("群" in rtype or "group" in rtype)
                            ) or (type_label == "virtual" and ("虚拟" in rtype or "virtual" in rtype)):
                                filtered.append(rec)
                            else:
                                # 兜底:3 种类型都要走一遍时,全量给,让主键重复时 upsert 成一样
                                filtered.append(rec)
                        records = filtered
                    except Exception as e:  # noqa: BLE001
                        logger.exception(f"[scrm][session-{type_label}] Playwright 抓取也失败: {e}")
                        err_txt = f"Playwright: {e}"
                        error_msg = (error_msg + " | " if error_msg else "") + err_txt
                        records = None
                        # ⚠️ 记忆化: 登录/凭证/验证码/启动浏览器类错误都记到缓存
                        _low_err = str(e).lower()
                        _cred_match = "凭证校验失败" in str(e) or "凭据校验失败" in str(e)
                        should_cache_login_fail = (
                            _cred_match
                            or any(k in _low_err for k in ("登录", "账号", "密码", "验证码", "启动 playwright", "playwright: 启动"))
                            or (isinstance(e, RuntimeError) and any(k in str(e) for k in ("登录", "用户名或密码", "验证码", "凭证")))
                        )
                        if pw_login_fail_cache is not None and should_cache_login_fail:
                            if cache_key is not None:
                                pw_login_fail_cache[cache_key] = str(e)
                                # ── P2-2: 同步写进程级全局缓存(带 10min TTL) ──
                                await _pw_global_cache_put(cache_key, str(e))

        # ── 共用入库逻辑 ──
        success = False
        if records is not None:
            try:
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
                success = True
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[scrm][session-{type_label}] 入库失败: {e}")
                error_msg = (error_msg + " | " if error_msg else "") + f"Upsert: {e}"

        if success:
            _finish_log(
                db, log_id, "success",
                api_call_count=api_call_count, api_total_reported=api_total_reported,
                fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
                remark=f"fetch_source={fetch_source},type={type_label}",
            )
            log_closed = True
            logger.info(
                f"[scrm][session-{type_label}] 成功(source={fetch_source}): "
                f"inserted={inserted} updated={updated} failed={failed} (fetched={fetched_rows})"
            )
            return {
                "data_type": data_type, "success": True,
                "start_time": start.isoformat(), "end_time": end.isoformat(),
                "api_call_count": api_call_count, "fetched_rows": fetched_rows,
                "inserted": inserted, "updated": updated, "failed": failed,
                "fetch_source": fetch_source,
            }
        else:
            _finish_log(
                db, log_id, "failed",
                api_call_count=api_call_count, api_total_reported=api_total_reported,
                fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
                error=str(error_msg or "两种抓取模式均失败"),
                remark=f"fetch_source={fetch_source},type={type_label},双模式均失败",
            )
            log_closed = True
            logger.error(f"[scrm][session-{type_label}] 同步失败: {error_msg}")
            return {
                "data_type": data_type, "success": False, "error": str(error_msg or "两种抓取模式均失败"),
                "start_time": start.isoformat(), "end_time": end.isoformat(),
                "fetch_source": fetch_source,
            }
    finally:
        if not log_closed:
            try:
                _finish_log(
                    db, log_id, "failed",
                    api_call_count=api_call_count, api_total_reported=api_total_reported,
                    fetched_rows=fetched_rows, inserted=inserted, updated=updated, failed=failed,
                    error=str(error_msg or "同步过程异常中断(兜底)"),
                    remark=f"[finally 兜底] fetch_source={fetch_source},type={type_label}",
                )
            except Exception:  # noqa: BLE001
                pass


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

    # ── 任务级别 SCRM Playwright 登录失败熔断缓存 ──
    # 4 个 SCRM 源(operator/session_p/session_g/session_v)默认共用同一个 base_url+username 配置,
    # 一旦某个源 Playwright 模式因"登录类错误"失败(账号/密码/验证码/启浏览器失败),
    # 其余源直接复用同一个失败结果,避免 4 次独立启动 Playwright + OCR + 登录尝试 → 日志反复弹 4 条完全相同的登录失败。
    pw_login_fail_cache: dict[tuple[str, str], str] = {}

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
            # P0-3: sync_operators 已经是 async def,直接 await,不再 to_thread + asyncio.run 嵌套
            # P0-1: 包一层 asyncio.wait_for 单源 300s(5 分钟)超时,防止 Playwright 僵死 running 不结束
            res: dict
            try:
                res = await asyncio.wait_for(
                    sync_operators(
                        client, thread_db, trigger_type, triggered_by,
                        time_range_override=(time_ranges or {}).get(DATA_TYPE_OPERATOR),
                        pw_login_fail_cache=pw_login_fail_cache,
                    ),
                    timeout=SCRM_SYNC_PER_SOURCE_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                err = (f"[P0-1 超时熔断] SCRM 操作员同步单源超过 "
                       f"{SCRM_SYNC_PER_SOURCE_TIMEOUT_SEC}s,已强制取消,防止僵死。")
                logger.error(f"[scrm][operator] {err}")
                res = {"data_type": DATA_TYPE_OPERATOR, "success": False, "error": err}
        except Exception as e:  # noqa: BLE001
            logger.exception("[scrm][operator] 顶层崩溃(should be rare,超时已被 wait_for 兜底)")
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
            # P0-3: 不再 to_thread + asyncio.run 嵌套,直接 await
            # P0-1: 每个会话类型都单独 5 分钟超时
            sess_res: dict
            try:
                sess_res = await asyncio.wait_for(
                    _sync_one_session_type(
                        client, thread_db, st, trigger_type, triggered_by,
                        time_range_override=(time_ranges or {}).get(dt),
                        pw_login_fail_cache=pw_login_fail_cache,
                    ),
                    timeout=SCRM_SYNC_PER_SOURCE_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                err = (f"[P0-1 超时熔断] SCRM 会话-{st} 同步单源超过 "
                       f"{SCRM_SYNC_PER_SOURCE_TIMEOUT_SEC}s,已强制取消,防止僵死。")
                logger.error(f"[scrm][session-{st}] {err}")
                sess_res = {"data_type": dt, "success": False, "error": err}
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[scrm] session-{st} 顶层崩溃(should be rare)")
            sess_res = {"data_type": dt, "success": False, "error": str(e)}
        finally:
            try:
                thread_db.close()
            except Exception:  # noqa: BLE001
                pass
        results.append(sess_res)
        if on_progress:
            try:
                await on_progress(dt, "done", sess_res)
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
        # 从 SyncSourceConfig 读取配置(与 BMS get_sync_status 保持一致)
        config = get_sync_source_config(db, dt) or {}
        row = db.scalar(
            select(ScrmSyncLog)
            .where(ScrmSyncLog.data_type == dt)
            .order_by(ScrmSyncLog.created_at.desc())
            .limit(1)
        )
        if row is None:
            out.append({
                "data_type": dt,
                "name": config.get("name", dt),
                "default_sync": config.get("default_sync", True),
                "has_credentials": bool(config.get("username") and config.get("password")),
                "enabled": config.get("enabled", True),
                "status": "never",
                "last_start": None,
                "last_end": None,
                "stats": None,
            })
        else:
            out.append({
                "data_type": dt,
                "name": config.get("name", dt),
                "default_sync": config.get("default_sync", True),
                "has_credentials": bool(config.get("username") and config.get("password")),
                "enabled": config.get("enabled", True),
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
