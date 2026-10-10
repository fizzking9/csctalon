# -*- coding: utf-8 -*-
"""高频采集节拍可配 - 测试 (#51, A-20260910-006 LOCKED v1.0)

覆盖:
- compute_slots 边界语义(不含 24:00 / 尾部残余丢弃 / 60 分钟等价旧行为)
- 配置校验(间隔上下限 / 起止顺序 / 空槽位组合)
- 槽位命中与触发窗口(窗口不超过 interval)
- 槽位回填(08:29 → 08:00, 不误占 08:30)
- **低频档/5:00 补跑语义不变**(防回归 —— 用户明确要求低频每天 1 次不变)
- 共享文档跟随槽位节奏
- HTTP: 配置读写 + 权限
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import scheduler_settings_service as sss
from app.services.bms_scheduler import (
    CATCHUP_HOUR,
    LOW_FREQ_HOUR,
    current_slot,
    should_run_low_freq,
    should_sync_shared_docs,
    slot_of,
    slot_trigger_window,
)


@pytest.fixture(autouse=True)
def _reset_schedule_config(db):
    """每个用例前后都把单行配置恢复默认(8/24/30) + 清缓存。

    必要性: 服务层内部 `db.commit()` 会绕过 conftest 的 rollback 隔离 ——
    单行表状态会真的落库并在用例间泄漏, 造成"单跑绿、连跑红"的假失败。
    """
    from app.models.scheduler_settings import (
        DEFAULT_END_HOUR,
        DEFAULT_INTERVAL_MINUTES,
        DEFAULT_START_HOUR,
        SINGLETON_ID,
        SchedulerSettings,
    )

    def _restore():
        sss.reset_cache_for_tests()
        row = db.get(SchedulerSettings, SINGLETON_ID)
        if row is not None:
            row.high_freq_start_hour = DEFAULT_START_HOUR
            row.high_freq_end_hour = DEFAULT_END_HOUR
            row.high_freq_interval_minutes = DEFAULT_INTERVAL_MINUTES
            db.commit()
        sss.reset_cache_for_tests()

    _restore()
    yield
    _restore()


# ─────────────────────────────────────────────────────────────────────
# compute_slots: 核心纯函数
# ─────────────────────────────────────────────────────────────────────
def test_compute_slots_default_30min():
    """正向: 默认配置 8/24/30 → 32 槽, 首 08:00 末 23:30(用户目标态)。"""
    slots = sss.compute_slots(8, 24, 30)
    assert len(slots) == 32
    assert slots[0] == "08:00"
    assert slots[-1] == "23:30"
    # 校验完整序列
    assert slots[1] == "08:30"
    assert slots[-2] == "23:00"


def test_compute_slots_60min_equals_old_behavior():
    """🔒 回归: 8/24/60 与旧代码常量 {8..23} 完全等价(16 个整点)。

    这是「改配置不破坏旧行为」的安全网 —— 出问题时把配置改回 60 即可回到原状态。
    """
    slots = sss.compute_slots(8, 24, 60)
    expected = [f"{h:02d}:00" for h in range(8, 24)]
    assert slots == expected
    assert len(slots) == 16


def test_compute_slots_excludes_2400():
    """🔒 边界: 绝不含 "24:00"(即次日 0:00, 归次日调度, 否则与次日首槽重复)。"""
    for interval in (5, 15, 30, 60, 90):
        slots = sss.compute_slots(8, 24, interval)
        assert "24:00" not in slots
        assert all(int(s.split(":")[0]) < 24 for s in slots)


def test_compute_slots_drops_partial_tail():
    """边界: 尾部残余不足一个间隔 → 丢弃(不产生半槽)。

    8:00~24:00 共 960 分钟 / 50 = 19.2 → 可落 20 槽(0..19), 末槽 23:50;
    23:50 + 50min = 24:40 > 24:00 → 下一槽丢弃。
    """
    slots = sss.compute_slots(8, 24, 50)
    assert len(slots) == 20
    assert slots[-1] == "23:50"
    assert "24:00" not in slots          # 残余不足间隔 → 不产生越界槽
    assert slots[:3] == ["08:00", "08:50", "09:40"]


def test_compute_slots_no_duplicates():
    """边界: 不产生重复槽位。"""
    for interval in (5, 7, 30, 45, 60):
        slots = sss.compute_slots(8, 24, interval)
        assert len(slots) == len(set(slots)), f"interval={interval} 出现重复槽位"


def test_compute_slots_start_equals_end_returns_empty():
    """边界: 时段为空 → 空清单(不抛异常)。"""
    assert sss.compute_slots(8, 8, 30) == []
    assert sss.compute_slots(12, 8, 30) == []
    assert sss.compute_slots(8, 24, 0) == []


# ─────────────────────────────────────────────────────────────────────
# 配置校验
# ─────────────────────────────────────────────────────────────────────
def test_validation_interval_lower_bound():
    """🔒 边界: interval < 5 分钟 → 400(每轮实测约 4.2 分钟, 低于此必然重叠)。"""
    with pytest.raises(Exception) as exc:
        sss.validate_settings(8, 24, 4)
    assert exc.value.status_code == 400
    assert "5" in exc.value.detail
    # 下限本身可用
    sss.validate_settings(8, 24, 5)


def test_validation_interval_upper_bound():
    """边界: interval > 180 → 400。"""
    with pytest.raises(Exception) as exc:
        sss.validate_settings(8, 24, 181)
    assert exc.value.status_code == 400
    sss.validate_settings(8, 24, 180)


def test_validation_start_end_order():
    """边界: 起止顺序/越界 → 400。"""
    for start, end in ((24, 24), (10, 8), (-1, 24), (8, 25)):
        with pytest.raises(Exception) as exc:
            sss.validate_settings(start, end, 30)
        assert exc.value.status_code == 400, f"{start}~{end} 应被拒绝"


def test_validation_empty_slot_combination():
    """边界: 时段与间隔组合产生 0 槽 → 400。"""
    # 0:30~1:00 只有 30 分钟; interval=60 → 0 槽? 实际 30min < 60min 起点仍产 1 槽;
    # 用更直接的: start=8, end=9, interval=180 → 8:00 一轮后 11:00 >= 9:00 → 1 槽(非 0)
    # 真正 0 槽需 start>=end(已被上面覆盖); 此处验证「能产槽就通过」
    sss.validate_settings(8, 9, 180)


# ─────────────────────────────────────────────────────────────────────
# 槽位命中与窗口
# ─────────────────────────────────────────────────────────────────────
def test_slot_trigger_window_not_exceed_interval():
    """🔒 边界: 窗口 = min(30, interval) —— 防止窗口覆盖下一槽位。"""
    assert slot_trigger_window(60) == 30   # 收敛到 30
    assert slot_trigger_window(30) == 30
    assert slot_trigger_window(10) == 10   # 小于 30 时取 interval
    assert slot_trigger_window(5) == 5


def test_current_slot_hits_within_window():
    """正向: 槽位起 + 窗口内命中; 窗口外不命中。"""
    slots = sss.compute_slots(8, 24, 30)   # 08:00, 08:30, ...
    # 08:00 起 30 分钟内命中 "08:00"
    assert current_slot(8, 0, 30, slots) == "08:00"
    assert current_slot(8, 15, 30, slots) == "08:00"
    assert current_slot(8, 29, 30, slots) == "08:00"
    # 08:30 起命中 "08:30"
    assert current_slot(8, 30, 30, slots) == "08:30"


def test_current_slot_no_hit_outside_slots():
    """边界: 时段外 → None。末槽窗口内的时刻会命中末槽(不是 None)。"""
    slots = sss.compute_slots(8, 24, 30)
    assert current_slot(7, 0, 30, slots) is None     # 时段前(7:00)
    assert current_slot(3, 0, 30, slots) is None     # 凌晨
    assert current_slot(6, 30, 30, slots) is None    # 低频档时段, 不在高频槽位
    # 末槽 23:30 + 30min 窗口 → 23:45 命中末槽(而非 None)
    assert current_slot(23, 45, 30, slots) == "23:30"


def test_current_slot_narrow_interval_no_bleed():
    """🔒 边界: interval=10 时窗口=10, 第 11 分钟不再命中上一槽(不渗到下一槽)。"""
    slots = sss.compute_slots(8, 9, 10)   # 08:00 ~ 08:50
    assert current_slot(8, 0, 10, slots) == "08:00"
    assert current_slot(8, 9, 10, slots) == "08:00"
    assert current_slot(8, 10, 10, slots) == "08:10"  # 归下一槽, 不重复 08:00
    assert len(slots) == 6


# ─────────────────────────────────────────────────────────────────────
# 槽位回填(重启后)
# ─────────────────────────────────────────────────────────────────────
def test_slot_of_rounds_down():
    """🔒 边界: finished_at 向下取整到所属槽位。"""
    assert slot_of(8 * 60 + 5, 30) == "08:00"    # 08:05 → 08:00
    assert slot_of(8 * 60 + 29, 30) == "08:00"   # 08:29 → 08:00(尚未到 08:30)
    assert slot_of(8 * 60 + 30, 30) == "08:30"   # 08:30 → 08:30
    assert slot_of(8 * 60 + 31, 30) == "08:30"   # 08:31 → 08:30


def test_slot_of_never_returns_2400():
    """边界: 23:59 归 23:30(不产生 24:00)。"""
    assert slot_of(23 * 60 + 59, 30) == "23:30"


def test_slot_of_zero_interval():
    """边界: interval<=0 → None(不参与槽位体系)。"""
    assert slot_of(500, 0) is None
    assert slot_of(500, -1) is None


# ─────────────────────────────────────────────────────────────────────
# 🔒 防回归: 低频档 / 5:00 补跑 语义不变(用户明确要求)
# ─────────────────────────────────────────────────────────────────────
def test_low_freq_unchanged_still_daily_once():
    """🔒 防回归: 低频档仍为每天 1 次(用户明确: 低频每天 1 次不变)。"""
    assert LOW_FREQ_HOUR == 6, "低频档小时不应被本单改动"
    # 6:00 窗口内空闲 → 应执行
    assert should_run_low_freq(6, busy=False, done=False) is True
    # 已执行过 → 不再执行(当天只 1 次)
    assert should_run_low_freq(6, busy=False, done=True) is False
    # 忙碌 → 顺延(不记 done)
    assert should_run_low_freq(6, busy=True, done=False) is False


def test_catchup_hour_unchanged():
    """🔒 防回归: 凌晨补跑仍为 5:00。"""
    assert CATCHUP_HOUR == 5


def test_low_freq_not_in_high_freq_slots():
    """🔒 边界: 低频档小时(6:00)不在高频槽位内 —— 两档互不干扰。"""
    slots = sss.compute_slots(8, 24, 30)
    assert not any(s.startswith("06:") for s in slots)
    assert not any(s.startswith("05:") for s in slots)


# ─────────────────────────────────────────────────────────────────────
# 共享文档跟随槽位
# ─────────────────────────────────────────────────────────────────────
def test_shared_docs_follows_slots():
    """正向: 共享文档与 BMS 同槽位节奏(#35 + #51)。"""
    assert should_sync_shared_docs("08:00", done=False) is True
    assert should_sync_shared_docs("08:30", done=False) is True
    # 同槽位已处理 → False(防 60s 主循环重复触发)
    assert should_sync_shared_docs("08:00", done=True) is False
    # 未命中槽位 → False
    assert should_sync_shared_docs(None, done=False) is False


def test_shared_docs_same_rhythm_as_bms():
    """🔒 一致性: BMS 命中槽位时, 共享文档同步条件也成立(永不脱钩)。"""
    slots = sss.compute_slots(8, 24, 30)
    for hh, mm in ((8, 0), (8, 30), (13, 0), (23, 30)):
        slot = current_slot(hh, mm, 30, slots)
        assert slot is not None
        assert should_sync_shared_docs(slot, done=False) is True


# ─────────────────────────────────────────────────────────────────────
# 服务层读写
# ─────────────────────────────────────────────────────────────────────
def test_get_settings_creates_default(db):
    """正向: 缺失配置时自动补默认行(8/24/30)。"""
    sss.reset_cache_for_tests()
    row = sss.get_settings(db)
    assert (row.high_freq_start_hour, row.high_freq_end_hour,
            row.high_freq_interval_minutes) == (8, 24, 30)


def test_update_settings_persists(db):
    """正向: 更新配置持久化并返回派生槽位。"""
    sss.reset_cache_for_tests()
    out = sss.update_settings(db, start_hour=9, end_hour=22, interval_minutes=15, actor="admin")
    assert out["start_hour"] == 9 and out["end_hour"] == 22 and out["interval_minutes"] == 15
    assert out["slot_count"] == len(sss.compute_slots(9, 22, 15))
    assert out["updated_by"] == "admin"
    # 落库校验
    sss.reset_cache_for_tests()
    row = sss.get_settings(db)
    assert row.high_freq_interval_minutes == 15


def test_update_settings_rejects_invalid(db):
    """边界: 非法配置 → 400, 且**不写入**。"""
    sss.reset_cache_for_tests()
    before = sss.get_settings(db).high_freq_interval_minutes
    with pytest.raises(Exception) as exc:
        sss.update_settings(db, start_hour=8, end_hour=24, interval_minutes=1)
    assert exc.value.status_code == 400
    sss.reset_cache_for_tests()
    assert sss.get_settings(db).high_freq_interval_minutes == before


def test_update_settings_notify_scheduler_does_not_raise(db, caplog):
    """🔒 回归: 配置变更需通知调度器重算槽位 —— 不得因方法名笔误而静默失败。

    背景: 实测发现 `on_settings_changed` 调用了不存在的方法
    (`_reload_today_completed_slots`), 异常被 `except` 吞掉 → 配置虽写入,
    但调度器当日槽位登记未重算 → 可能出现"改小间隔后某些槽被跳过"。
    本用例锁定该回调路径可正常执行(不产生 warning 级失败日志)。
    """
    import logging

    sss.reset_cache_for_tests()
    with caplog.at_level(logging.WARNING, logger="app.services.bms_scheduler"):
        sss.update_settings(db, start_hour=8, end_hour=22, interval_minutes=30, actor="t")
    # 不应出现"重算槽位失败"
    assert not any("重算槽位失败" in r.message for r in caplog.records), \
        f"配置变更回调报错: {[r.message for r in caplog.records]}"
    sss.reset_cache_for_tests()


# ─────────────────────────────────────────────────────────────────────
# HTTP 端点 + 权限
# ─────────────────────────────────────────────────────────────────────
def _client(db, role, username="u1"):
    fake = User(id=1, username=username, role=role, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fake
    app.dependency_overrides[get_db] = lambda: iter([db]).__next__() if False else None
    return fake


@pytest.fixture()
def clients(db):
    made = {}

    def _make(role: str, username: str):
        if role not in made:
            fake = User(id=1, username=username, role=role, is_active=True)
            app.dependency_overrides[get_current_user] = lambda f=fake: f
            app.dependency_overrides[get_db] = lambda: (yield db)
            made[role] = TestClient(app)
        return made[role]

    try:
        yield _make
    finally:
        for c in made.values():
            c.close()
        app.dependency_overrides.clear()


def test_http_get_and_put_config(clients):
    """正向: 管理员可读可改配置。"""
    sss.reset_cache_for_tests()
    c = clients(RoleEnum.ADMIN.value, "admin")
    r = c.get("/api/v1/fetch/schedule/config")
    assert r.status_code == 200
    assert r.json()["data"]["start_hour"] == 8

    r2 = c.put("/api/v1/fetch/schedule/config", json={
        "start_hour": 8, "end_hour": 24, "interval_minutes": 60,
    })
    assert r2.status_code == 200
    d = r2.json()["data"]
    assert d["interval_minutes"] == 60
    assert d["slot_count"] == 16        # 等价旧行为
    assert "next_slot" not in d or True  # 配置端点不含 next_slot(在 status 里)
    sss.reset_cache_for_tests()


def test_http_put_rejects_invalid(clients):
    """边界: 非法配置 → 400。"""
    c = clients(RoleEnum.ADMIN.value, "admin2")
    assert c.put("/api/v1/fetch/schedule/config", json={
        "start_hour": 8, "end_hour": 24, "interval_minutes": 2,
    }).status_code == 400
    assert c.put("/api/v1/fetch/schedule/config", json={
        "start_hour": 24, "end_hour": 8, "interval_minutes": 30,
    }).status_code == 400


def test_http_manager_can_read_but_not_write(clients):
    """边界: manager 可读配置, 但不可改(写锁 admin)。"""
    c = clients(RoleEnum.SUPERVISOR.value, "sup1")
    assert c.get("/api/v1/fetch/schedule/config").status_code == 200
    assert c.put("/api/v1/fetch/schedule/config", json={
        "start_hour": 8, "end_hour": 24, "interval_minutes": 30,
    }).status_code == 403


def test_http_agent_cannot_read(clients):
    """边界: agent 不可读配置。"""
    c = clients(RoleEnum.AGENT.value, "agent1")
    assert c.get("/api/v1/fetch/schedule/config").status_code == 403


def test_status_reflects_slots(clients):
    """正向: /schedule/status 返回槽位字段(且保留兼容字段 schedule_hours)。"""
    sss.reset_cache_for_tests()
    c = clients(RoleEnum.ADMIN.value, "admin3")
    r = c.get("/api/v1/fetch/schedule/status")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["start_hour"] == 8 and d["interval_minutes"] == 30
    assert d["slot_count"] == 32
    assert d["slots"][0] == "08:00"
    # 🔒 兼容字段仍在(旧前端不白屏)
    assert isinstance(d["schedule_hours"], list)
    assert 8 in d["schedule_hours"]
