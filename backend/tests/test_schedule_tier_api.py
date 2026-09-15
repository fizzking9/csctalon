# -*- coding: utf-8 -*-
"""`GET /fetch/data-sources` 的 schedule_tier 档位字段测试（工单 #97）。

═══════════════════════════════════════════════════════════════════════════════
本测试锁定的契约（A-20260907-001 LOCKED §四「接口契约」预留项）:

    `GET /fetch/data-sources` 每源附 `schedule_tier: high/low/off`

为什么必须锁两层:

  ① **判据同源** —— schedule_tier 必须来自
     `sync_source_config_service.list_scheduled_bms_sources()`（调度器消费的同一个函数），
     **不得**在 API/service 层另立一份 `DEFAULT_SYNC_TYPES` 判断。
     否则重演 #31 事故：UI 显示的档位与调度器实际跑的档位是两份独立定义 → 界面骗人。

  ② **兜底方向** —— 未进入"应纳入定时"集合的源必须是 `off`（而不是 high/low）。
     前端「全选 / 恢复默认勾选」按该字段决定勾谁；若把未纳入定时的源误标为 high，
     使用者一次"全选"就会触发一次昂贵的大表导出（device_profile 7.5 万行级别）。
     **宁可少勾，不可误勾**。
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.sync_source_config import SyncSourceConfig
from app.services.bms_sync import SYNC_DATA_TYPES, get_sync_status
from app.services.sync_source_config_service import (
    get_schedule_tier_map,
    list_scheduled_bms_sources,
)

VALID_TIERS = {"high", "low", "off"}


@pytest.fixture()
def authed_fetch_client(_engine):
    """带假认证用户的 TestClient（复用 client 的依赖覆盖套路）。

    仅做**只读**接口断言：档位映射的正确性已由上面的 service 层用例覆盖，
    此处只验证 HTTP 层把字段如实暴露出去（含向后兼容）。
    """
    from fastapi.testclient import TestClient

    from app.api.deps import get_current_user
    from app.core.database import get_db
    from app.main import app
    from app.models.user import RoleEnum, User

    fake_user = User(id=1, username="tester", role=RoleEnum.AGENT.value, is_active=True)

    def _override_user():
        return fake_user

    def _override_db():
        from sqlalchemy.orm import Session

        session = Session(_engine, autocommit=False, autoflush=False)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _set_source(db, data_type: str, *, default_sync: bool, enabled: bool = True) -> None:
    """把某个源的 default_sync / enabled 设成指定值（不存在则创建最小行）。"""
    row = db.scalar(select(SyncSourceConfig).where(SyncSourceConfig.data_type == data_type))
    if row is None:
        row = SyncSourceConfig(
            data_type=data_type,
            name=f"测试源-{data_type}",
            base_url="https://wifi.cloud-cat.cn/bms",
            page_path="/index.html#/test",
            default_sync=default_sync,
            enabled=enabled,
        )
        db.add(row)
    else:
        row.default_sync = default_sync
        row.enabled = enabled
    db.flush()


# ───────────────────────── 正向：字段存在且取值合法 ─────────────────────────


def test_sync_status_exposes_schedule_tier_for_every_source(db):
    """正向: 每个 BMS 源都带 schedule_tier，取值 ∈ {high, low, off}。"""
    rows = get_sync_status(db)
    assert rows, "get_sync_status 不应为空"
    assert {r["data_type"] for r in rows} == set(SYNC_DATA_TYPES)
    for r in rows:
        assert r["schedule_tier"] in VALID_TIERS, f"{r['data_type']} → {r['schedule_tier']}"


def test_schedule_tier_map_matches_scheduler_helper(db):
    """正向(核心): 档位映射与调度器的分档判据**逐源一致**（判据同源，防 UI 漂移）。"""
    _set_source(db, "refund", default_sync=True)
    _set_source(db, "device_profile", default_sync=True)

    sched = list_scheduled_bms_sources(db)
    tier_map = get_schedule_tier_map(db)

    for dt in sched["high"]:
        assert tier_map[dt] == "high", f"{dt} 应在 high"
    for dt in sched["low"]:
        assert tier_map[dt] == "low", f"{dt} 应在 low"
    # 反向: 映射里的每个 key 都必须在 all 集合内（不允许有"幽灵档位"）
    assert set(tier_map.keys()) <= set(sched["all"])


def test_core_high_freq_sources_are_high_tier(db):
    """正向: 4 个种子核心高频源（refund/ticket/wallet/cs_record）归 high 档。"""
    for dt in ("refund", "ticket", "wallet", "cs_record"):
        _set_source(db, dt, default_sync=True)
    tier_map = get_schedule_tier_map(db)
    for dt in ("refund", "ticket", "wallet", "cs_record"):
        assert tier_map[dt] == "high", f"{dt} 应为 high，实际 {tier_map.get(dt)}"


# ───────────────────────── 边界：兜底为 off 的三个场景 ─────────────────────────


def test_default_sync_false_is_off_tier(db):
    """边界: default_sync=false（未纳入定时）→ off，绝不能落进 high/low。

    否则前端"全选"会把未纳入定时的源一起勾上并真跑一次。
    """
    _set_source(db, "device_combo", default_sync=False)
    rows = {r["data_type"]: r["schedule_tier"] for r in get_sync_status(db)}
    assert rows["device_combo"] == "off"


def test_enabled_false_is_off_tier(db):
    """边界: default_sync=true 但 enabled=false（停用）→ off。"""
    _set_source(db, "return_tracking", default_sync=True, enabled=False)
    rows = {r["data_type"]: r["schedule_tier"] for r in get_sync_status(db)}
    assert rows["return_tracking"] == "off"


def test_derived_source_device_tag_never_exposed_as_tier(db):
    """边界: 派生源 device_tag 既不出现在档位映射里，也不出现在源列表中。

    device_tag 不是独立抓取源（与 device_tag_binding 抓同一份明细 CSV，
    标签定义由明细导入时按 tag_name 自动补插），因此:

      ① 它不在 bms_fetcher.BMS_DATA_SOURCES 种子里 → 不进 SYNC_DATA_TYPES；
      ② 即便 DB 里被人手工改成 default_sync=true，也被
         list_scheduled_bms_sources 显式剔除（_DERIVED_NON_SCHEDULABLE）。

    对前端的效果 = 它压根不会出现在「手动同步」分组里，
    **因此不可能给出一个"勾了必炸"的选项**（#38 CardinalityViolation 先例）。
    本用例锁住这条兜底链路的**两个环节**，防止后人"顺手"把它加回清单。
    """
    _set_source(db, "device_tag", default_sync=True)
    # ① 不在种子清单 → 不被 get_sync_status 遍历
    assert "device_tag" not in SYNC_DATA_TYPES, "device_tag 是派生源，不应进 SYNC_DATA_TYPES"
    assert "device_tag" not in {r["data_type"] for r in get_sync_status(db)}
    # ② 即使在 DB 里被置为 default_sync=true，也不进任何档位
    assert "device_tag" not in get_schedule_tier_map(db)


def test_all_off_when_nothing_scheduled(db):
    """边界: 全部源都 default_sync=false 时，不存在任何 high/low 档。"""
    for dt in SYNC_DATA_TYPES:
        _set_source(db, dt, default_sync=False)
    tier_map = get_schedule_tier_map(db)
    assert tier_map == {}, f"应为空映射，实际 {tier_map}"
    rows = {r["data_type"]: r["schedule_tier"] for r in get_sync_status(db)}
    assert set(rows.values()) == {"off"}


# ───────────────────────── 接口层：响应结构不变（向后兼容） ─────────────────────────


def test_data_sources_api_returns_schedule_tier(authed_fetch_client):
    """正向(接口层): GET /fetch/data-sources 返回 schedule_tier 字段。

    同时断言既有字段仍在 —— 只做**加法**，不改动原有契约（前端已按这些字段渲染）。
    """
    resp = authed_fetch_client.get("/api/v1/fetch/data-sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert isinstance(data, list) and data, "数据源列表不应为空"

    for item in data:
        assert item["schedule_tier"] in VALID_TIERS
        # 既有字段未被破坏（向后兼容）
        for legacy in ("data_type", "name", "has_credentials", "default_sync", "enabled"):
            assert legacy in item, f"既有字段 {legacy} 丢失"


def test_schedule_status_returns_low_freq_hour(authed_fetch_client):
    """正向(接口层): GET /fetch/schedule/status 暴露 low_freq_hour。

    前端用它渲染低频档标题（"每天 06:00 跑一次"），避免在前端硬编码整点
    —— 该整点由 settings.LOW_FREQ_HOUR 控制，硬编码会与实际调度脱节。
    """
    resp = authed_fetch_client.get("/api/v1/fetch/schedule/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "low_freq_hour" in data
    assert isinstance(data["low_freq_hour"], int)
    assert data["low_freq_hour"] >= 0, "0 表示低频档禁用"
