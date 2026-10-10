# -*- coding: utf-8 -*-
"""共享文档导出测试 #148: 访客检测 + 新选择器 + 防退化守卫

覆盖:
- _check_tencent_guest: body 含 skeleton-guest-mode → True(访客)
- _check_kdocs_guest: __WPSENV__.user.id 以 fake_ 开头 → True(访客)
- _export_tencent_xlsx: 访客身份时 raise PermissionError, 不应继续 hover 30s 超时
- _export_kdocs_xlsx: 访客身份时 raise PermissionError
- _export_kdocs_xlsx: 非访客时点击 .app-header-more-btn 选择器(验证新选择器路径)
- _save_debug_screenshot: 失败时被调用, 落盘到 BROWSER_STATE_DIR/.export_debug_*.png

设计: monkeypatch playwright.sync_playwright 返回桩 context/page, 不打开真浏览器。
     所有 page.evaluate 调用按用例预设返回值, 验证业务逻辑而非浏览器交互。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import shared_doc_sync_service as sdss


# ── 桩对象: 模拟 playwright Page ────────────────────────────────────
class _FakeDownload:
    def __init__(self, name: str = "fake.xlsx"):
        self._name = name
        self.saved_to: str | None = None

    @property
    def suggested_filename(self):
        return self._name

    def save_as(self, path: str) -> None:
        self.saved_to = path


class _FakePage:
    """模拟 playwright Page: 可预设 evaluate 返回值 / hover / click 行为"""

    def __init__(self, *, eval_returns: dict[str, object] | None = None,
                 click_raises: dict[str, Exception] | None = None,
                 hover_raises: dict[str, Exception] | None = None,
                 click_side_effects: dict[str, object] | None = None,
                 locator_click_raises: bool = False):
        # eval_returns: JS 代码片段 → 返回值
        self._eval_returns = eval_returns or {}
        self._click_raises = click_raises or {}
        self._hover_raises = hover_raises or {}
        self._click_side_effects = click_side_effects or {}
        self._locator_click_raises = locator_click_raises
        self.screenshot_called: list[str] = []
        self.hover_called: list[str] = []
        self.click_called: list[str] = []
        self.locator_called: list[str] = []
        self._download = _FakeDownload()

    def goto(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, ms: int):
        pass

    def evaluate(self, js: str, *args, **kwargs):
        # 找预设返回值: 精确匹配 / 子串匹配
        for key, val in self._eval_returns.items():
            if key in js:
                return val
        return None

    def screenshot(self, path: str = "", **kwargs):
        self.screenshot_called.append(path)

    def hover(self, sel: str, **kwargs):
        self.hover_called.append(sel)
        for key, exc in self._hover_raises.items():
            if key in sel:
                raise exc

    def click(self, sel: str, **kwargs):
        self.click_called.append(sel)
        for key, exc in self._click_raises.items():
            if key in sel:
                raise exc
        # 副作用: 触发 download
        if "Excel" in sel:
            return self._download

    def expect_download(self, **kwargs):
        # 与 with 语句配合: 返回一个有 .value 的对象
        m = MagicMock()
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: None
        m.value = self._download
        return m

    @property
    def mouse(self):
        m = MagicMock()
        m.click = MagicMock()
        return m

    def locator(self, sel: str):
        self.locator_called.append(sel)
        loc = MagicMock()
        loc.first = loc
        if self._locator_click_raises:
            loc.click.side_effect = TimeoutError(f"simulated timeout clicking {sel}")
        else:
            loc.click = MagicMock()
        return loc


class _FakeContext:
    def __init__(self, page: _FakePage):
        self._page = page

    def new_page(self):
        return self._page


class _FakeBrowser:
    def __init__(self, page: _FakePage):
        self._ctx = _FakeContext(page)

    def new_context(self, **kwargs):
        return self._ctx

    def close(self):
        pass


class _FakePlaywright:
    def __init__(self, page: _FakePage):
        self._page = page

    @property
    def chromium(self):
        b = _FakeBrowser(self._page)
        m = MagicMock()
        m.launch = MagicMock(return_value=b)
        return m

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _patch_playwright(monkeypatch, page: _FakePage):
    """桩 playwright.sync_api.sync_playwright: 返回 _FakePlaywright"""
    monkeypatch.setattr(
        "app.services.shared_doc_sync_service.sync_playwright",
        lambda: _FakePlaywright(page),
        raising=False,
    )
    # service 内部用 `from playwright.sync_api import sync_playwright` 形式
    # monkeypatch 该 import 路径
    import sys
    fake_mod = MagicMock()
    fake_mod.sync_playwright = lambda: _FakePlaywright(page)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)


def _make_state_file(tmp_path: Path, name: str = "tencent_doc.json") -> Path:
    """生成一个假的登录态文件(让 FileNotFoundError 不触发)"""
    p = tmp_path / name
    p.write_text(json.dumps({"cookies": [], "origins": []}), encoding="utf-8")
    return p


# ── 访客检测单元测试(直接调 _check_*_guest) ────────────────────────
class TestGuestChecks:
    def test_tencent_guest_mode_detected(self, monkeypatch):
        """body 含 skeleton-guest-mode → 访客"""
        page = _FakePage(eval_returns={"document.body": "web-pc skeleton-guest-mode other"})
        assert sdss._check_tencent_guest(page) is True

    def test_tencent_non_guest(self, monkeypatch):
        """body 不含 skeleton-guest-mode → 非访客"""
        page = _FakePage(eval_returns={"document.body": "web-pc normal-mode"})
        assert sdss._check_tencent_guest(page) is False

    def test_tencent_eval_exception_treated_as_guest(self, monkeypatch):
        """evaluate 异常 → 按访客处理(保守)"""
        page = _FakePage()
        # 让 evaluate 抛异常
        page.evaluate = MagicMock(side_effect=RuntimeError("page detached"))
        assert sdss._check_tencent_guest(page) is True

    def test_kdocs_guest_fake_user(self, monkeypatch):
        """user.id=fake_xxx → 访客"""
        page = _FakePage(eval_returns={"u.id": "fake_520110566"})
        assert sdss._check_kdocs_guest(page) is True

    def test_kdocs_real_user(self, monkeypatch):
        """user.id=纯数字 → 真用户"""
        page = _FakePage(eval_returns={"u.id": "1245496884"})
        assert sdss._check_kdocs_guest(page) is False

    def test_kdocs_empty_user(self, monkeypatch):
        """user.id 空 → 访客"""
        page = _FakePage(eval_returns={"u.id": ""})
        assert sdss._check_kdocs_guest(page) is True


# ── _save_debug_screenshot 测试 ─────────────────────────────────────
class TestDebugScreenshot:
    def test_screenshot_saved_on_failure(self, monkeypatch, tmp_path):
        """失败时落盘到 BROWSER_STATE_DIR/.export_debug_*.png"""
        monkeypatch.setattr(sdss.settings, "BROWSER_STATE_DIR", str(tmp_path))
        page = _FakePage()
        sdss._save_debug_screenshot(page, "test_key")
        assert len(page.screenshot_called) == 1
        assert ".export_debug_test_key_" in page.screenshot_called[0]
        # 路径在 tmp_path 下
        from pathlib import Path as P
        p = P(page.screenshot_called[0])
        assert p.parent == tmp_path

    def test_screenshot_failure_does_not_raise(self, monkeypatch, tmp_path):
        """page.screenshot 自身抛异常时不传播(防退化守卫失败不阻断主流程)"""
        monkeypatch.setattr(sdss.settings, "BROWSER_STATE_DIR", str(tmp_path))
        page = _FakePage()
        page.screenshot = MagicMock(side_effect=RuntimeError("page gone"))
        # 不应抛
        sdss._save_debug_screenshot(page, "ignored")


# ── _export_*_xlsx 访客身份检测测试 ─────────────────────────────────
class TestExportGuestDetection:
    def test_tencent_export_raises_on_guest(self, monkeypatch, tmp_path):
        """腾讯访客身份 → 抛 PermissionError 而非继续 hover"""
        _make_state_file(tmp_path, "tencent_doc.json")
        monkeypatch.setattr(sdss.settings, "BROWSER_STATE_DIR", str(tmp_path))
        page = _FakePage(eval_returns={
            "document.body": "web-pc skeleton-guest-mode",
        })
        _patch_playwright(monkeypatch, page)

        with pytest.raises(PermissionError) as ei:
            sdss._export_tencent_xlsx(
                "https://docs.qq.com/sheet/TEST",
                str(tmp_path / "out.xlsx"),
            )
        # 错误信息含"访客"提示
        assert "访客" in str(ei.value)
        # 落盘了访客截图
        assert any("tencent_guest" in s for s in page.screenshot_called)
        # 没调用 hover(原 bug 是 hover 30s 超时)
        assert page.hover_called == []

    def test_kdocs_export_raises_on_guest(self, monkeypatch, tmp_path):
        """金山访客身份 → 抛 PermissionError 而非继续 locator.click"""
        _make_state_file(tmp_path, "kdocs.json")
        monkeypatch.setattr(sdss.settings, "BROWSER_STATE_DIR", str(tmp_path))
        page = _FakePage(eval_returns={
            "u.id": "fake_520110566",
        })
        _patch_playwright(monkeypatch, page)

        with pytest.raises(PermissionError) as ei:
            sdss._export_kdocs_xlsx(
                "https://www.kdocs.cn/l/TEST",
                str(tmp_path / "out.xlsx"),
            )
        assert "访客" in str(ei.value)
        # 落盘访客截图
        assert any("kdocs_guest" in s for s in page.screenshot_called)
        # 没调用 locator(没继续往下点菜单按钮)
        assert page.locator_called == []


# ── _export_kdocs_xlsx 新选择器路径测试(非访客) ────────────────────
class TestKdocsNewSelectorPath:
    def test_kdocs_uses_app_header_more_btn_selector(self, monkeypatch, tmp_path):
        """非访客时, 用 .app-header-more-btn 选择器打开菜单(代替坐标 (95,15))"""
        _make_state_file(tmp_path, "kdocs.json")
        monkeypatch.setattr(sdss.settings, "BROWSER_STATE_DIR", str(tmp_path))
        page = _FakePage(eval_returns={
            "u.id": "1245496884",  # 真用户
            # evaluate 找"导出为"叶子节点 → 返回 True(模拟成功点击)
            "all.filter": True,
        })
        _patch_playwright(monkeypatch, page)

        # 执行导出 - 应能完成(因为 locator 不抛 + evaluate 返回 True + click 不抛)
        result = sdss._export_kdocs_xlsx(
            "https://www.kdocs.cn/l/TEST",
            str(tmp_path / "out.xlsx"),
        )
        # 验证用了 .app-header-more-btn 选择器(新选择器)
        assert ".app-header-more-btn" in page.locator_called
        # 没用 mouse.click(老坐标路径)
        # (注: _FakePage.mouse() 返回 MagicMock, 不实际跟踪 click 调用)
        assert result == "fake.xlsx"

    def test_kdocs_export_failure_drops_screenshot(self, monkeypatch, tmp_path):
        """菜单按钮点击失败 → 落盘 .export_debug_kdocs_open_menu_*.png"""
        _make_state_file(tmp_path, "kdocs.json")
        monkeypatch.setattr(sdss.settings, "BROWSER_STATE_DIR", str(tmp_path))
        page = _FakePage(eval_returns={
            "u.id": "1245496884",  # 非访客
        })
        page._locator_click_raises = True  # locator.click 抛 TimeoutError
        _patch_playwright(monkeypatch, page)

        with pytest.raises(RuntimeError) as ei:
            sdss._export_kdocs_xlsx(
                "https://www.kdocs.cn/l/TEST",
                str(tmp_path / "out.xlsx"),
            )
        # 错误信息含"app-header-more-btn"
        assert "app-header-more-btn" in str(ei.value)
        # 落盘失败截图
        assert any("kdocs_open_menu" in s for s in page.screenshot_called)


# ── _export_tencent_xlsx hover/click 失败守卫测试 ──────────────────
class TestTencentFailureGuards:
    def test_tencent_hover_failure_drops_screenshot(self, monkeypatch, tmp_path):
        """腾讯非访客 + hover 失败 → 落盘 tencent_hover_export 截图"""
        _make_state_file(tmp_path, "tencent_doc.json")
        monkeypatch.setattr(sdss.settings, "BROWSER_STATE_DIR", str(tmp_path))
        page = _FakePage(
            eval_returns={"document.body": "web-pc normal-mode"},  # 非访客
            hover_raises={"导出为": TimeoutError("hover timeout")},
        )
        _patch_playwright(monkeypatch, page)

        with pytest.raises(RuntimeError) as ei:
            sdss._export_tencent_xlsx(
                "https://docs.qq.com/sheet/TEST",
                str(tmp_path / "out.xlsx"),
            )
        assert "hover" in str(ei.value).lower() or "导出为" in str(ei.value)
        assert any("tencent_hover_export" in s for s in page.screenshot_called)
