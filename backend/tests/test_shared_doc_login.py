# -*- coding: utf-8 -*-
"""共享文档页面内登录态管理 - 测试 (#45, A-20260910-001 LOCKED v1.0)

覆盖:
- 会话生命周期: start → status → confirm → 关闭
- 同平台单例锁(409) —— 防并发登录互相覆盖 state
- confirm 校验不通过**不覆盖**旧登录态(安全回归)
- 覆盖前备份 .json.bak
- 无显示服务 → 501 + CLI 降级命令(不是 500)
- 健康度: 文件不存在 / 存在 / 超期
- 会话 TTL 到期回收
- 浏览器层全部 monkeypatch 打桩, 不真起 chromium
"""

import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.user import RoleEnum, User
from app.services import shared_doc_login_service as sdls
from app.services.shared_doc_login_service import (
    DisplayUnavailableError,
    LoginSession,
    SharedDocLoginManager,
)


def _uniq_url(tag: str = "x") -> str:
    """唯一共享文档链接。

    必要性: `save_doc_source` 内部 `db.commit()` 会绕过 conftest 的 rollback 隔离,
    固定 doc_url 在第二次运行时撞「同平台同链接」唯一校验 → 用唯一后缀规避。
    """
    return f"https://docs.qq.com/sheet/{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def admin_client(db):
    fake = User(id=1, username="admin", role=RoleEnum.ADMIN.value, is_active=True)

    def _override_user():
        return fake

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def mgr():
    """独立 manager 实例(避免污染全局单例/跨用例串状态)。"""
    m = SharedDocLoginManager()
    yield m
    m.close_all()


# ── 假 Playwright 对象(打桩, 不真起浏览器) ──
class _FakePage:
    def __init__(self, url="https://docs.qq.com/sheet/abc"):
        self.url = url
        self._goto_url = url

    def goto(self, url, **kwargs):
        self._goto_url = url
        self.url = url  # 模拟：能正常打开文档(即已登录)

    def screenshot(self, **kwargs):
        return b"\x89PNG\r\n\x1a\nFAKE"


class _FakeCtx:
    def __init__(self, cookies=None):
        self._cookies = cookies if cookies is not None else [{"name": "uid", "value": "1"}]
        self.saved_path = None

    def cookies(self):
        return self._cookies

    def storage_state(self, path):
        Path(path).write_text(json.dumps({"cookies": self._cookies}), encoding="utf-8")
        self.saved_path = path

    def close(self):
        pass


class _FakeBrowser:
    def close(self):
        pass


class _FakePW:
    def stop(self):
        pass


def _make_session(tmp_path, platform="tencent", cookies=None, url="https://docs.qq.com/sheet/abc"):
    """构造一个已"启动"的会话(绕过真实浏览器)。"""
    sess = LoginSession(
        session_id="sess123",
        platform=platform,
        source_id=1,
        doc_url="https://docs.qq.com/sheet/abc",
        state_path=tmp_path / sdls.DEFAULT_STATE_FILE[platform],
    )
    sess._pw = _FakePW()
    sess._browser = _FakeBrowser()
    sess._ctx = _FakeCtx(cookies=cookies)
    sess._page = _FakePage(url=url)
    sess.current_url = url
    return sess


# ─────────────────────────────────────────────────────────────────────
# 显示环境探测
# ─────────────────────────────────────────────────────────────────────
def test_display_unavailable_raises(monkeypatch, tmp_path, mgr):
    """边界: 无 DISPLAY 且无 xvfb-run → DisplayUnavailableError(路由层转 501)。"""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(sdls.shutil, "which", lambda _: None)
    with pytest.raises(DisplayUnavailableError):
        mgr.start("tencent", "https://docs.qq.com/sheet/x", tmp_path / "s.json")


def test_display_available_with_display_env(monkeypatch, tmp_path, mgr):
    """正向: 有 DISPLAY 时探测通过(不再走 501 分支)。

    之后浏览器启动会失败(测试环境无 chromium), 但那属 502 分支 —— 与本节断言无关。
    """
    monkeypatch.setenv("DISPLAY", ":0")
    assert mgr.check_display_available() is True


def test_cli_fallback_command_contains_script_and_url(mgr):
    """正向: 降级命令含正确脚本名与文档地址。"""
    cmd = mgr.cli_fallback_command("tencent", "https://docs.qq.com/sheet/AAA")
    assert "tencent_doc_login.py" in cmd
    assert "https://docs.qq.com/sheet/AAA" in cmd
    cmd2 = mgr.cli_fallback_command("kdocs", "https://www.kdocs.cn/l/BBB")
    assert "kdocs_login.py" in cmd2


def test_login_start_returns_501_when_no_display(admin_client, db, monkeypatch):
    """边界(HTTP): 无显示服务 → 501 且 detail 含可执行命令(不是 500)。"""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(sdls.shutil, "which", lambda _: None)

    from app.services import shared_doc_sync_service as sdss
    row = sdss.save_doc_source(db, {
        "platform": "tencent", "doc_url": _uniq_url("nodisp"),
        "doc_name": "t", "target_type": "complaint",
    }, source_id=None)

    r = admin_client.post(f"/api/v1/sync/shared-docs/{row.id}/login/start")
    assert r.status_code == 501
    assert "tencent_doc_login.py" in r.json()["detail"]
    db.delete(row)
    db.commit()


def test_login_start_404_for_unknown_source(admin_client, monkeypatch):
    """边界: 采集源不存在 → 404。"""
    monkeypatch.setenv("DISPLAY", ":0")
    r = admin_client.post("/api/v1/sync/shared-docs/999999/login/start")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────
# 会话生命周期
# ─────────────────────────────────────────────────────────────────────
def test_status_recognizes_authorized(tmp_path, mgr):
    """正向: 已脱离登录页 + 有会话 cookie → authorized。"""
    sess = _make_session(tmp_path, url="https://docs.qq.com/sheet/abc",
                         cookies=[{"name": "uid", "value": "1"}])
    assert mgr.refresh_status(sess) == "authorized"


def test_status_pending_when_on_login_page(tmp_path, mgr):
    """边界: 仍在登录页且无会话 cookie → pending。"""
    sess = _make_session(tmp_path, url="https://docs.qq.com/login?x=1", cookies=[])
    assert mgr.refresh_status(sess) == "pending"


def test_status_scanned_when_on_login_page_with_cookie(tmp_path, mgr):
    """边界: 仍在登录页但已有部分 cookie → scanned(已扫码待确认)。"""
    sess = _make_session(tmp_path, url="https://docs.qq.com/login", cookies=[{"name": "uid", "value": "1"}])
    assert mgr.refresh_status(sess) == "scanned"


def test_screenshot_returns_data_uri(tmp_path, mgr):
    """正向: 截图返回 data URI。"""
    sess = _make_session(tmp_path)
    assert mgr.screenshot_b64(sess).startswith("data:image/png;base64,")


def test_screenshot_empty_when_closed(tmp_path, mgr):
    """边界: 页面已关闭 → 空串(不抛错)。"""
    sess = _make_session(tmp_path)
    sess._page = None
    assert mgr.screenshot_b64(sess) == ""


def test_confirm_saves_state_and_creates_backup(tmp_path, mgr):
    """正向: confirm 落盘, 且旧 state 被备份为 .json.bak。"""
    sess = _make_session(tmp_path)
    sess.state_path.write_text('{"cookies":[]}', encoding="utf-8")  # 旧登录态

    ok, msg = mgr.verify_logged_in(sess)
    assert ok, msg
    saved = mgr.save_state(sess)
    assert saved["backup_created"] is True
    assert saved["cookies"] == 1
    assert sess.state_path.with_suffix(".json.bak").exists()
    assert "cookies" in sess.state_path.read_text(encoding="utf-8")


def test_verify_rejects_when_still_on_login_page(tmp_path, mgr):
    """🔒 安全回归: 仍停留在登录页 → 校验失败(防止误点确认写入无效 state)。"""
    sess = _make_session(tmp_path, url="https://docs.qq.com/sheet/abc")

    # 让 goto 后仍落在登录页(模拟未真正登录)
    class _StuckPage(_FakePage):
        def goto(self, url, **kwargs):
            self.url = "https://docs.qq.com/login?redirect=x"

    sess._page = _StuckPage()
    ok, msg = mgr.verify_logged_in(sess)
    assert ok is False
    assert "登录" in msg


def test_verify_rejects_when_no_cookies(tmp_path, mgr):
    """🔒 安全回归: 无 cookie → 校验失败。"""
    sess = _make_session(tmp_path, cookies=[])
    ok, msg = mgr.verify_logged_in(sess)
    assert ok is False
    assert "Cookie" in msg


def test_confirm_http_does_not_overwrite_on_failed_verify(admin_client, db, monkeypatch):
    """🔒 安全回归(HTTP): 校验失败时**不覆盖**旧登录态文件。"""
    from app.core.config import settings
    from app.services import shared_doc_sync_service as sdss

    state_file = "tencent_doc.json"
    state_path = Path(settings.BROWSER_STATE_DIR) / state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    original = '{"cookies":[{"name":"uid","value":"ORIGINAL"}]}'
    state_path.write_text(original, encoding="utf-8")

    doc_url = _uniq_url("confirm")
    row = sdss.save_doc_source(db, {
        "platform": "tencent", "doc_url": doc_url,
        "doc_name": "t", "target_type": "complaint",
    }, source_id=None)

    # 注入一个"校验不通过"的会话
    sess = LoginSession(
        session_id="bad1", platform="tencent", source_id=row.id,
        doc_url=doc_url, state_path=state_path,
    )
    sess._pw, sess._browser = _FakePW(), _FakeBrowser()
    sess._ctx = _FakeCtx(cookies=[])  # 无 cookie → 校验必失败
    sess._page = _FakePage()

    from app.services.shared_doc_login_service import login_manager
    login_manager._sessions["bad1"] = sess
    try:
        r = admin_client.post("/api/v1/sync/shared-docs/login/bad1/confirm")
        assert r.status_code == 200
        assert r.json()["data"]["ok"] is False
        assert r.json()["data"]["state_saved"] is False
        # 关键: 旧登录态未被覆盖
        assert state_path.read_text(encoding="utf-8") == original
    finally:
        login_manager.finish("bad1")
        db.delete(row)
        db.commit()


def test_confirm_404_for_unknown_session(admin_client):
    """边界: 会话不存在 → 404。"""
    assert admin_client.post("/api/v1/sync/shared-docs/login/nope/confirm").status_code == 404
    assert admin_client.get("/api/v1/sync/shared-docs/login/nope/status").status_code == 404


def test_cancel_is_idempotent(admin_client):
    """边界: 取消幂等(即便会话不存在也返回 200)。"""
    assert admin_client.delete("/api/v1/sync/shared-docs/login/nope").status_code == 200


# ─────────────────────────────────────────────────────────────────────
# 单例锁(防并发覆盖)
# ─────────────────────────────────────────────────────────────────────
def test_concurrent_session_blocked(mgr, tmp_path):
    """🔒 边界: 同平台已有活跃会话 → 再次 start 抛冲突(防并发覆盖 state)。"""
    sess = _make_session(tmp_path)
    mgr._sessions[sess.session_id] = sess

    from app.services.shared_doc_login_service import login_manager as _lm  # noqa: F401
    with pytest.raises(RuntimeError) as exc:
        # check_display_available 通过后才会走到锁检查, 这里直接设 DISPLAY
        import os
        os.environ["DISPLAY"] = ":0"
        try:
            mgr.start("tencent", "https://docs.qq.com/sheet/y", tmp_path / "s2.json")
        finally:
            os.environ.pop("DISPLAY", None)
    assert "已有正在进行的登录会话" in str(exc.value)


def test_different_platform_not_blocked(mgr, tmp_path):
    """正向: 不同平台互不阻塞(腾讯/金山可各有一个会话)。

    这里只验证锁判定逻辑, 不走真实浏览器启动。
    """
    sess = _make_session(tmp_path, platform="tencent")
    mgr._sessions[sess.session_id] = sess
    assert mgr._active_for_platform("tencent") is not None
    assert mgr._active_for_platform("kdocs") is None


# ─────────────────────────────────────────────────────────────────────
# TTL 回收
# ─────────────────────────────────────────────────────────────────────
def test_expired_session_reaped(mgr, tmp_path):
    """边界: 超 TTL 的会话被回收并标记 expired, 浏览器被关闭。"""
    sess = _make_session(tmp_path)
    sess.created_at = time.time() - (sdls.SESSION_TTL + 10)
    mgr._sessions[sess.session_id] = sess

    mgr._reap()
    assert sess.session_id not in mgr._sessions
    assert sess.status == "expired"
    assert sess._page is None  # close() 已置空


def test_get_returns_none_after_expiry(mgr, tmp_path):
    """边界: 过期后 get 返回 None(路由层转 404)。"""
    sess = _make_session(tmp_path)
    sess.created_at = time.time() - (sdls.SESSION_TTL + 10)
    mgr._sessions[sess.session_id] = sess
    assert mgr.get(sess.session_id) is None


# ─────────────────────────────────────────────────────────────────────
# 健康度
# ─────────────────────────────────────────────────────────────────────
def test_state_health_missing_file(tmp_path, monkeypatch):
    """边界: 登录态文件不存在 → exists=false + 未登录提示。"""
    monkeypatch.setattr(sdls.settings, "BROWSER_STATE_DIR", str(tmp_path))
    h = sdls.state_health("tencent")
    assert h["exists"] is False
    assert h["stale"] is True
    assert "未登录" in h["advice"]


def test_state_health_fresh_file(tmp_path, monkeypatch):
    """正向: 刚写入的登录态 → exists=true 且不超期。"""
    monkeypatch.setattr(sdls.settings, "BROWSER_STATE_DIR", str(tmp_path))
    (tmp_path / "tencent_doc.json").write_text('{"cookies":[]}', encoding="utf-8")
    h = sdls.state_health("tencent")
    assert h["exists"] is True
    assert h["stale"] is False
    assert h["size_bytes"] > 0


def test_state_health_stale_detected(tmp_path, monkeypatch):
    """边界: mtime 超过阈值 → stale=true 且给出建议。"""
    import os
    monkeypatch.setattr(sdls.settings, "BROWSER_STATE_DIR", str(tmp_path))
    p = tmp_path / "tencent_doc.json"
    p.write_text('{"cookies":[]}', encoding="utf-8")
    old = time.time() - (sdls.STATE_STALE_DAYS + 3) * 86400
    os.utime(p, (old, old))

    h = sdls.state_health("tencent")
    assert h["stale"] is True
    assert "建议重新登录" in h["advice"]


def test_states_endpoint_lists_both_platforms(admin_client, monkeypatch, tmp_path):
    """正向: 健康度端点返回腾讯+金山两项 + 显示服务可用性。"""
    monkeypatch.setattr(sdls.settings, "BROWSER_STATE_DIR", str(tmp_path))
    r = admin_client.get("/api/v1/sync/shared-docs/states")
    assert r.status_code == 200
    data = r.json()["data"]
    platforms = {i["platform"] for i in data["items"]}
    assert platforms == {"tencent", "kdocs"}
    assert "display_available" in data
