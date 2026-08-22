"""共享文档(腾讯文档)同步 API 路由

- GET  /sync/docs/tencent/authorize-url : 生成腾讯文档授权链接(管理员)
- GET  /sync/docs/tencent/callback      : OAuth 回调(公开, 浏览器跳转), 换 token 存库
- POST /sync/docs/tencent/config        : 保存应用凭证 / 共享文档配置(管理员)
- GET  /sync/docs/tencent/status        : 接入状态(管理层)
- POST /sync/docs/tencent/sync-now      : 立即手动同步一次(管理员)
- POST /sync/docs/tencent/revoke        : 解除授权(管理员)

同步日志复用 /sync/complaints/logs(同一张 complaint_sync_logs 表)。
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.api.deps import AdminUser, DBSession, ManagerUser
from app.schemas.common import R
from app.services import doc_sync_service as dss
from app.services import tencent_doc_client as tdc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync/docs", tags=["数据同步-共享文档"])


def _resolve_redirect_uri(request: Request, db) -> str:
    """回调地址: 优先用户保存的, 其次从请求地址推导"""
    cfg = dss.get_doc_config(db)
    if cfg.get("redirect_uri"):
        return cfg["redirect_uri"]
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/sync/docs/tencent/callback"


@router.get("/tencent/authorize-url", response_model=R, summary="生成腾讯文档授权链接")
def tencent_authorize_url(request: Request, db: DBSession, _: AdminUser):
    cfg = dss.get_doc_config(db)
    if not cfg.get("app_id") or not cfg.get("app_secret"):
        raise HTTPException(status_code=400, detail="尚未配置腾讯文档开放平台应用凭证(ClientID/Secret)")
    redirect_uri = _resolve_redirect_uri(request, db)
    url = tdc.build_authorize_url(cfg["app_id"], redirect_uri, state="tencent")
    return R(data={"authorize_url": url, "redirect_uri": redirect_uri})


@router.get("/tencent/callback", response_class=HTMLResponse, summary="腾讯文档 OAuth 回调(公开)")
def tencent_callback(
    request: Request,
    db: DBSession,
    code: str | None = Query(None),
    state: str | None = Query(None),
):
    """用户授权后腾讯文档回跳: ?code=xxx&state=tencent → 换 token 存库"""
    platform = state if state and state in (dss.PLATFORM_TENCENT, dss.PLATFORM_WPS) else dss.PLATFORM_TENCENT
    if not code:
        return _callback_html(False, "授权回调缺少 code 参数")
    cfg = dss.get_doc_config(db, platform)
    if not cfg.get("app_id") or not cfg.get("app_secret"):
        return _callback_html(False, "尚未配置开放平台应用凭证, 无法完成授权")
    redirect_uri = _resolve_redirect_uri(request, db)
    try:
        data = tdc.exchange_token(cfg["app_id"], cfg["app_secret"], code, redirect_uri)
    except tdc.DocApiError as e:
        logger.error(f"[doc_sync] 授权回调换 token 失败: {e}")
        return _callback_html(False, f"授权失败: {e}")
    dss.save_oauth_tokens(
        db,
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_in=data.get("expires_in"),
        open_id=data.get("user_id", ""),
        platform=platform,
    )
    return _callback_html(True, "授权成功")


def _callback_html(ok: bool, message: str) -> str:
    status_icon = "✅" if ok else "❌"
    color = "#52c41a" if ok else "#f5222d"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>授权结果</title>
<style>
  body{{font-family:-apple-system,"PingFang SC",sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f7fa}}
  .card{{background:#fff;border-radius:12px;box-shadow:0 4px 16px rgba(0,0,0,.08);padding:40px 48px;text-align:center;max-width:420px}}
  .icon{{font-size:48px;margin-bottom:12px}}
  .msg{{font-size:18px;color:#333;margin-bottom:24px}}
  .btn{{display:inline-block;padding:10px 24px;border-radius:8px;color:#fff;background:{color};text-decoration:none;font-size:15px}}
</style></head>
<body><div class="card">
  <div class="icon">{status_icon}</div>
  <div class="msg">{message}</div>
  <a class="btn" href="/#/data/ingest?tab=doc-sync">返回系统 · 共享文档同步</a>
  <p style="margin-top:12px;font-size:12px;color:#999">页面可安全关闭, 授权已保存</p>
</div></body></html>"""


@router.post("/tencent/config", response_model=R, summary="保存共享文档同步配置")
def tencent_config(payload: dict, db: DBSession, _: AdminUser):
    """body: {app_id?, app_secret?, redirect_uri?, doc_url?, external_sheet_name?,
               card_sheet_name?, enabled?, sync_interval_minutes?}"""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    # 应用凭证(非空才覆盖)
    if payload.get("app_id") or payload.get("app_secret") or payload.get("redirect_uri"):
        dss.save_app_credentials(
            db,
            app_id=payload.get("app_id") or "",
            app_secret=payload.get("app_secret") or "",
            redirect_uri=payload.get("redirect_uri") or "",
        )
    # 文档配置(显式传 null 也会清空; 完全没传的 key 不动)
    dss.save_doc_config(
        db,
        doc_url=payload.get("doc_url") if "doc_url" in payload else None,
        external_sheet_name=payload.get("external_sheet_name") if "external_sheet_name" in payload else None,
        card_sheet_name=payload.get("card_sheet_name") if "card_sheet_name" in payload else None,
        enabled=payload.get("enabled") if "enabled" in payload else None,
        sync_interval_minutes=payload.get("sync_interval_minutes") if "sync_interval_minutes" in payload else None,
    )
    return R(data={"ok": True, "status": dss.get_doc_status(db)})


@router.get("/tencent/status", response_model=R, summary="腾讯文档接入状态")
def tencent_status(db: DBSession, _: ManagerUser):
    return R(data=dss.get_doc_status(db))


@router.post("/tencent/sync-now", response_model=R, summary="立即同步腾讯文档投诉数据")
def tencent_sync_now(db: DBSession, _: AdminUser):
    try:
        stats = dss.sync_tencent_doc(db, trigger="manual")
        return R(data={**stats, "status_detail": dss.get_doc_status(db)})
    except tdc.DocApiError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[doc_sync] 手动同步异常: {e}")
        raise HTTPException(status_code=500, detail=f"同步失败: {e}") from e


@router.post("/tencent/revoke", response_model=R, summary="解除腾讯文档授权")
def tencent_revoke(db: DBSession, _: AdminUser):
    dss.clear_oauth_tokens(db)
    return R(data={"ok": True})
