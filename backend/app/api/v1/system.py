"""系统设置 API - 同步源配置管理(admin only)

提供 BMS 同步源运行配置(URL/账号/密码/开关)的查询与更新。
凭证加密存储,GET 接口仅返回是否已配置(has_credentials),不返回明文。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import AdminUser, DBSession
from app.schemas.common import R
from app.services.sync_source_config_service import (
    list_sync_source_configs,
    update_sync_source_config,
)

router = APIRouter(prefix="/system", tags=["系统设置"])


class SyncSourceOut(BaseModel):
    data_type: str
    name: str
    base_url: str
    page_path: str
    time_labels: list | None = None
    group_key: str | None = None
    has_credentials: bool
    # 2026-09-07 新增: 凭证来源 own/group/none + 单源与组账号是否不同(供前端提示)
    credential_source: str | None = None
    own_cred_differs_group: bool = False
    default_sync: bool
    enabled: bool
    sort_order: int
    fetch_mode: str | None = None
    captcha_type: str | None = None
    requires_captcha: bool | None = None
    options: dict | None = None


class SyncSourceUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    page_path: str | None = None
    time_labels: list | None = None
    username: str | None = None  # 留空表示不改; 显式 null/clear_credentials=true 表示清空(回退继承组凭证)
    password: str | None = None  # 同上
    default_sync: bool | None = None
    enabled: bool | None = None
    sync_to_group: bool = False  # D6: 设为 true 且填了凭证时, 把凭证同步到该源所属组(作用于同组所有源)
    clear_credentials: bool = False  # #28(2026-09-07): 设为 true 时清空本源单源凭证(置 NULL, 回退继承组凭证)


@router.get("/sync-sources", response_model=R[list[SyncSourceOut]], summary="同步源配置列表")
def list_sync_sources(db: DBSession, _: AdminUser):
    """获取全部同步源配置(凭证掩码,仅返回 has_credentials)"""
    rows = list_sync_source_configs(db)
    return R(data=[SyncSourceOut(**r) for r in rows])


@router.put("/sync-sources/{data_type}", response_model=R[dict], summary="更新同步源配置")
def update_sync_source(data_type: str, payload: SyncSourceUpdate, db: DBSession, _: AdminUser):
    """更新单个同步源配置;username/password 留空表示不改,非空则加密入库。

    D6(2026-09-05, #19): 若 payload.sync_to_group=true 且填了凭证,
    同时把凭证提升/同步到该源所属组(作用于同组所有源, 免逐源配置)。
    """
    body = payload.model_dump(exclude_unset=True)
    sync_to_group = bool(body.pop("sync_to_group", False))
    # #28(2026-09-07): 显式清空单源凭证(回退继承组凭证)——修复"UI 清空凭证无效"缺口
    clear_credentials = bool(body.pop("clear_credentials", False))
    if clear_credentials:
        body["username"] = None
        body["password"] = None
    # 记录本次是否"填写了单源凭证"(用于下方与组凭证一致性提示)
    filled_own_cred = bool(body.get("username") and body.get("password"))
    updated = update_sync_source_config(db, data_type, body)
    if not updated:
        raise HTTPException(status_code=404, detail=f"同步源不存在: {data_type}")
    # D6: 凭证同步到组(仅当填了账号密码且要求同步)
    if sync_to_group and body.get("username") and body.get("password"):
        from app.services.source_group_service import update_source_group
        from app.services.sync_source_config_service import get_sync_source_config

        cfg = get_sync_source_config(db, data_type)
        gkey = cfg.get("group_key") if cfg else None
        if gkey:
            update_source_group(db, gkey, {"username": body["username"], "password": body["password"]})
        else:
            raise HTTPException(status_code=400, detail="该源未归组,无法同步到组凭证")
        # 同步到组后,本源与组账号必然一致, 无需下方警告
        filled_own_cred = False

    # ── 2026-09-07 防复发: 单源凭证 ≠ 组凭证一致性提示 ──
    # 背景: device_combo 单源误填 tanzk(与组 YKM-TZK 不同) → 该源登录失败 → 曾连坐同组。
    # 这里不阻断保存(用户可能有合理场景需要单源独立凭证), 仅返回 warning 供前端二次确认。
    # ⚠️ SCRM 豁免: SCRM 源(username 语义=OpenAPI client_id, 非平台登录账号),
    #   不与组登录账号做一致性比较(前端 ownCredWarnVisible 已豁免, 后端保持一致)。
    _is_scrm = str(data_type).startswith("scrm_")
    warning: str | None = None
    if filled_own_cred:
        # 防呆: 用户误把 Fernet 密文(gAAAA...)当明文粘贴进表单(常见于从 DB/文档复制)。
        # 后端虽然 _safe_decrypt 会兜底识别并回退组凭证, 但用户会困惑"为什么没生效"。
        if body.get("username", "").startswith("gAAAA") or body.get("password", "").startswith("gAAAA"):
            warning = (
                "检测到填入的账号/密码疑似加密密文(gAAAA... 开头), 不是真实登录账号明文。"
                "请填入平台真实登录账号密码; 留空则保持原凭证不变。"
            )
        elif (
            not _is_scrm
            and updated.get("group_username") and updated.get("username")
            and updated["username"] != updated["group_username"]
        ):
            gk = updated.get("group_key") or ""
            warning = (
                f"该源账号与所属组({gk})的组凭证账号不一致, 将使用独立凭证登录; "
                f"若该凭证无效将导致本源登录失败(已按独立凭证处理, 不再连坐同组其它源)。"
                f"如需改用组账号, 可勾选「清空独立凭证」保存以继承组凭证。"
            )
    # 返回时剥除明文凭证
    updated.pop("username", None)
    updated.pop("password", None)
    if warning:
        return R(data=updated, message=warning)
    return R(data=updated)


# ── #299 device_combo 历史切片回补: admin 手动入口(调度器 01:00 自动档之外) ──

class ComboBackfillRunRequest(BaseModel):
    # 白天手动执行需显式 true(会与高频档争用同一 BMS 账号, 默认拒绝)
    allow_daytime: bool = False
    # 本次最多执行几片(None=跑到 04:45/3 小时预算或队列清空)
    max_slices: int | None = None


# 保留手动 task 强引用, 防事件循环弱引用 GC(与 bms_scheduler._combo_task 同理)
_manual_combo_backfill_task = None


@router.get("/combo-backfill/status", response_model=R[dict],
            summary="设备套餐历史回补进度(#299)")
def combo_backfill_status(db: DBSession, _: AdminUser):
    """台账汇总: 各状态切片数 / 剩余片数 / 最近 10 片明细。"""
    from app.services import combo_backfill

    return R(data=combo_backfill.backfill_status(db))


@router.post("/combo-backfill/run", response_model=R[dict],
             summary="手动启动设备套餐历史回补(#299)")
async def combo_backfill_run(payload: ComboBackfillRunRequest, _: AdminUser):
    """后台启动切片回补; 与调度器/同步任务互斥。夜间窗口外默认拒绝。"""
    import asyncio

    from app.services import combo_backfill
    from app.services.sync_task_manager import sync_task_manager

    global _manual_combo_backfill_task

    if combo_backfill.is_running():
        raise HTTPException(status_code=409, detail="device_combo 回补已在运行中")
    if not payload.allow_daytime and not combo_backfill.in_night_window():
        raise HTTPException(
            status_code=409,
            detail="当前不在夜间回补窗口(01:00~04:45); 确认空闲后可用 allow_daytime=true 白天手动执行",
        )
    if sync_task_manager.is_busy():
        raise HTTPException(status_code=409, detail="有同步任务正在执行, 请等待空闲后再启动")

    _manual_combo_backfill_task = asyncio.create_task(combo_backfill.run_backfill(
        triggered_by="manual",
        allow_daytime=payload.allow_daytime,
        max_slices=payload.max_slices,
    ))
    return R(data={"started": True}, message="device_combo 历史回补已后台启动, 进度见 GET /system/combo-backfill/status")
