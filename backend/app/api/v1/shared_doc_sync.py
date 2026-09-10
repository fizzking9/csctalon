"""共享文档在线采集源同步 API 路由 (#33, A-20260907-002)

- GET    /sync/shared-docs              : 列出所有共享文档采集源配置(Manager)
- POST   /sync/shared-docs              : 新建采集源配置(Admin)
- PUT    /sync/shared-docs/{id}         : 更新采集源配置(Admin)
- DELETE /sync/shared-docs/{id}         : 删除采集源配置(Admin)
- POST   /sync/shared-docs/{id}/toggle  : 启停采集源(Admin)
- POST   /sync/shared-docs/sync-now     : 手动触发同步某源(Admin)

同步日志复用既有 complaint_sync_logs / manual_refund_sync_logs 表
(与 workbuddy push 同表, sync_source='shared_doc' 区分来源)。
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, DBSession, ManagerUser
from app.core.config import settings
from app.schemas.common import R
from app.services import shared_doc_sync_service as sdss

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/shared-docs", tags=["数据同步-共享文档"])


def _validate_source_id(source_id) -> int:
    """source_id 必须是正整数"""
    if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0:
        raise HTTPException(status_code=400, detail="source_id 必须是正整数")
    return source_id


@router.get("", response_model=R, summary="列出所有共享文档采集源配置")
def list_shared_docs(db: DBSession, _: ManagerUser):
    """列出全部 doc_sync_sources(含启用状态与最近同步结果)"""
    return R(data=sdss.list_doc_sources(db))


@router.post("", response_model=R, summary="新建共享文档采集源配置")
def create_shared_doc(payload: dict, db: DBSession, _: AdminUser):
    """body: {platform, doc_url, doc_name?, target_type, sheet_rules?, state_file?,
             enabled?, sync_interval_minutes?}

    返回新建的配置行(含 id)。
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    try:
        row = sdss.save_doc_source(db, payload, source_id=None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return R(data=sdss.row_to_dict(row))


@router.put("/{source_id}", response_model=R, summary="更新共享文档采集源配置")
def update_shared_doc(source_id: int, payload: dict, db: DBSession, _: AdminUser):
    """body: 同新建(可只传需修改字段; platform/doc_url 若传则需合法且不与他行冲突)"""
    source_id = _validate_source_id(source_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    try:
        row = sdss.save_doc_source(db, payload, source_id=source_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return R(data=sdss.row_to_dict(row))


@router.delete("/{source_id}", response_model=R, summary="删除共享文档采集源配置")
def delete_shared_doc(source_id: int, db: DBSession, _: AdminUser):
    """删除采集源(同步日志/历史数据保留在业务表)"""
    source_id = _validate_source_id(source_id)
    try:
        sdss.delete_doc_source(db, source_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return R(data={"deleted": source_id})


@router.post("/{source_id}/toggle", response_model=R, summary="启停共享文档采集源")
def toggle_shared_doc(source_id: int, payload: dict, db: DBSession, _: AdminUser):
    """body: {"enabled": bool} — 启/停定时采集(仅接受真布尔, 拒绝字符串"false")"""
    source_id = _validate_source_id(source_id)
    row = sdss.get_doc_source(db, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"采集源不存在: id={source_id}")
    if not isinstance(payload, dict) or "enabled" not in payload:
        raise HTTPException(status_code=400, detail="body 需包含 enabled(bool)")
    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled 必须是布尔值 true/false")
    row.enabled = enabled
    db.commit()
    db.refresh(row)
    return R(data=sdss.row_to_dict(row))


@router.post("/sync-now", response_model=R, summary="手动触发同步某共享文档源")
def shared_doc_sync_now(payload: dict, db: DBSession, _: AdminUser):
    """body: {"source_id": int}

    同步结果(成功/失败)会写回采集源配置行的 last_sync_* 字段;
    同步详情日志复用 complaint_sync_logs / manual_refund_sync_logs。
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    source_id = _validate_source_id(payload.get("source_id"))

    row = sdss.get_doc_source(db, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"采集源不存在: id={source_id}")
    if not row.enabled:
        raise HTTPException(status_code=400, detail=f"采集源已停用, 请先启用: id={source_id}")

    try:
        stats = sdss.sync_one_source(db, row, trigger="manual")
        return R(data={**stats, "source": sdss.row_to_dict(row)})
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[shared_doc] 手动同步源 #{source_id} 异常: {e}")
        raise HTTPException(status_code=500, detail=f"同步失败: {e}") from e


# ─────────────────────────────────────────────────────────────────────
# 页面内登录态管理 (#45, A-20260910-001 LOCKED v1.0)
#   替代「SSH 上服务器跑 scripts/tencent_doc_login.py」的唯一路径。
# ─────────────────────────────────────────────────────────────────────
@router.get("/states", response_model=R, summary="各平台登录态健康度")
def shared_doc_states(_: AdminUser):
    """列出腾讯/金山登录态文件的 mtime / 大小 / 是否超期。

    超期仅提示, 不阻断采集(各平台 token 实际有效期不同, 无法准确判定)。
    """
    from app.services import shared_doc_login_service as sdls

    items = [
        sdls.state_health(p) for p in (sdss.PLATFORM_TENCENT, sdss.PLATFORM_KDOCS)
    ]
    return R(data={
        "items": items,
        "stale_threshold_days": sdls.STATE_STALE_DAYS,
        "display_available": sdls.login_manager.check_display_available(),
    })


@router.post("/{source_id}/login/start", response_model=R, summary="启动页面内登录会话(扫码)")
def shared_doc_login_start(source_id: int, db: DBSession, _: AdminUser):
    """起有头浏览器打开目标文档 → 截图登录页/二维码 → 返回会话。

    错误码:
      404 采集源不存在 / 409 同平台已有活跃会话 / 501 无显示服务 / 502 浏览器启动失败
    """
    from app.services import shared_doc_login_service as sdls

    source_id = _validate_source_id(source_id)
    row = sdss.get_doc_source(db, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"采集源不存在: id={source_id}")

    state_filename = row.state_file or sdls.DEFAULT_STATE_FILE.get(
        row.platform, f"{row.platform}.json"
    )
    state_path = Path(settings.BROWSER_STATE_DIR) / state_filename

    try:
        sess = sdls.login_manager.start(
            platform=row.platform,
            doc_url=row.doc_url,
            state_path=state_path,
            source_id=source_id,
        )
    except sdls.DisplayUnavailableError:
        # 降级: 给出可执行的 CLI 命令, 不抛 500
        cmd = sdls.login_manager.cli_fallback_command(row.platform, row.doc_url)
        raise HTTPException(
            status_code=501,
            detail=f"服务器未安装虚拟显示服务(Xvfb), 无法在容器内启动浏览器。请用命令行登录: {cmd}",
        ) from None
    except RuntimeError as e:
        # 同平台已有活跃会话 → 409; 浏览器启动失败 → 502
        if "已有正在进行的登录会话" in str(e):
            raise HTTPException(status_code=409, detail=str(e)) from e
        raise HTTPException(status_code=502, detail=str(e)) from e

    return R(data={
        "session_id": sess.session_id,
        "platform": sess.platform,
        "screenshot": sdls.login_manager.screenshot_b64(sess),
        "current_url": sess.current_url,
        "expires_in": sdls.SESSION_TTL,
        "hint": "请用微信/QQ 扫码登录, 完成后点击「我已扫码完成」",
    })


@router.get("/login/{session_id}/status", response_model=R, summary="查询登录会话状态(轮询)")
def shared_doc_login_status(session_id: str, _: AdminUser):
    """前端 2 秒轮询: pending / scanned / authorized / expired / closed。"""
    from app.services import shared_doc_login_service as sdls

    sess = sdls.login_manager.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期, 请重新发起登录")
    status = sdls.login_manager.refresh_status(sess)
    return R(data={
        "status": status,
        "current_url": sess.current_url,
        "elapsed": sess.elapsed,
        "expires_in": max(0, sdls.SESSION_TTL - sess.elapsed),
    })


@router.post("/login/{session_id}/confirm", response_model=R, summary="确认扫码完成并保存登录态")
def shared_doc_login_confirm(session_id: str, _: AdminUser):
    """用户点「我已扫码完成」→ **轻量校验** → 保存 storage_state(覆盖前备份)。

    校验不通过时**不覆盖**旧登录态(A-20260910-001 §五.4): 防止用户误点导致
    原本可用的登录态被无效 state 顶掉。
    """
    from app.services import shared_doc_login_service as sdls

    sess = sdls.login_manager.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期, 请重新发起登录")

    ok, msg = sdls.login_manager.verify_logged_in(sess)
    if not ok:
        return R(data={"ok": False, "message": msg, "state_saved": False})

    saved = sdls.login_manager.save_state(sess)
    sdls.login_manager.finish(session_id)
    return R(data={"ok": True, "platform": sess.platform, **saved})


@router.delete("/login/{session_id}", response_model=R, summary="取消登录会话")
def shared_doc_login_cancel(session_id: str, _: AdminUser):
    """主动取消(关浏览器 + 移除会话)。幂等。"""
    from app.services import shared_doc_login_service as sdls

    sdls.login_manager.finish(session_id)
    return R(data={"ok": True})
