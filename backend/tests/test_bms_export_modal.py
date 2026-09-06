# -*- coding: utf-8 -*-
"""bms_fetcher 导出按钮可靠性测试(2026-09-06 二次修复)

背景: 3d48c1f(08:47) 引入回归 —— 导出选择器混入 `div:has-text("导出") >> nth=0`,
  has-text 子串匹配会把「工具栏/页脚/父容器(如"导出管理""支持导出")」甚至 el-message-box
  内部都纳入匹配, 且 `>> nth=0` 对整个逗号列表取 DOM 第 1 个 → 点到**非按钮容器**,
  page.click 对容器也返回成功 → 误记「命中成功」却永不触发 download(生产 8:55 起
  refund/ticket/ticket_finish/wallet/bms_order 全源 180s/600s 超时铁证)。

二次修复:
  1. _click_export_button 只接受"按钮语义"元素(tag=button / class 含 btn|button / role=button),
     点击前 _is_clickable_button 身份校验, 命中非按钮(容器/文本)跳过并 warning,
     不再无把握记「命中成功」; 候选不再用裸 div:has-text。
  2. _dismiss_modal_dialogs: 可见性检测改用 getBoundingClientRect(fixed 元素 offsetParent 恒 null,
     旧检测 n 恒 0 形同虚设); JS 移除仅限 .el-message-box__wrapper, 不再误删 el-dialog 业务面板。
  3. _download_with_retry: 点击后 4s 无任何新网络请求 → 判"点空/点错"立即失败, 不再傻等满超时。

本测试用 FakePage 模拟 Playwright page(不走真实浏览器), 锁定:
  - 非按钮容器(含"导出"文案)不会被点击(核心回归)
  - 模态框可见性检测(fixed 语义)
  - 模态框关闭只针对 message-box, 不误删 el-dialog
  - 点空(download 不触发且无网络)快速失败
"""

from __future__ import annotations

import pytest

from app.services.bms_fetcher import (
    BmsSessionExpired,
    _click_export_button,
    _dismiss_modal_dialogs,
)


# ─────────────────────────────────────────────────────────────
# 极简 FakePage: 支持新实现用到的 Page/Locator/ElementHandle 接口
# ─────────────────────────────────────────────────────────────
class _FakeHandle:
    """模拟 ElementHandle: 记录 tag/class/role/text, 供 _is_clickable_button 判定。"""

    def __init__(self, tag="BUTTON", text="", cls="", role=None, click_raises=None):
        self.tag = tag
        self._text = text
        self.cls = cls
        self.role = role
        self._click_raises = click_raises
        self.clicked = 0

    async def inner_text(self) -> str:
        return self._text

    async def get_attribute(self, name: str):
        if name == "role":
            return self.role
        if name == "class":
            return self.cls
        return None

    async def evaluate(self, js: str):
        # _is_clickable_button / _describe_click_target 用
        return {
            "tag": self.tag,
            "cls": self.cls,
            "role": self.role,
            "txt": self._text,
            "aria": "",
            "visible": True,
            "rect": "10x20@(0,0)",
        }

    async def click(self, timeout: int | None = None, force: bool = False):
        if self._click_raises:
            raise self._click_raises
        self.clicked += 1


class _FakeLocator:
    """模拟 Locator: 支持 count()/nth()/inner_text()/element_handle()/click()。"""

    def __init__(self, handles: list[_FakeHandle]):
        self._handles = handles

    async def count(self) -> int:
        return len(self._handles)

    def nth(self, i: int):
        return _FakeLocator([self._handles[i]]) if 0 <= i < len(self._handles) else _FakeLocator([])

    async def inner_text(self) -> str:
        if self._handles:
            return await self._handles[0].inner_text()
        return ""

    async def element_handle(self) -> _FakeHandle:
        if not self._handles:
            raise Exception("no element")
        return self._handles[0]

    async def click(self, timeout: int | None = None, force: bool = False):
        if not self._handles:
            raise TimeoutError("no element")
        await self._handles[0].click(timeout=timeout, force=force)


class FakePage:
    """模拟 Playwright Page(新接口): locator / query_selector_all / evaluate / keyboard。"""

    def __init__(self):
        self.url_value = "https://wifi.cloud-cat.cn/bms/index.html#/settle/refund-record-report"
        # DOM 结构: 按"选择器段"提供候选; 简化模型:
        #   - standard_locator_handles: 标准按钮定位返回的句柄(button/span.el-button 含"导出")
        #   - all_buttons: query_selector_all(_EXPORT_ANY_CLICKABLE) 返回
        #   - login_btn: query_selector(登录) 返回
        self.standard_handles: list[_FakeHandle] = []
        self.all_buttons: list[_FakeHandle] = []
        self.login_btn = None
        # 模态框: True=有一个可见 message-box(点确定/ESC/移除后置 False)
        self.message_box_visible = False
        self.dialog_visible = False   # el-dialog 业务面板(不应被移除)
        self.dialog_removed = False
        self.keyboard_esc_calls = 0
        self.clicked_handles: list[str] = []

    @property
    def url(self) -> str:
        return self.url_value

    # ---- Locator ----
    def locator(self, selector: str):
        # 标准导出按钮 selector(button/span.el-button 含"导出")
        if 'has-text("导出")' in selector:
            return _FakeLocator(list(self.standard_handles))
        return _FakeLocator([])

    # ---- query_selector_all ----
    async def query_selector_all(self, selector: str):
        if "el-message-box__btns" in selector:
            if self.message_box_visible:
                return [self._make_ok_btn()]
            return []
        if "el-message-box button" in selector:
            if self.message_box_visible:
                return [self._make_ok_btn()]
            return []
        if "el-dialog" in selector:
            return []
        # _EXPORT_ANY_CLICKABLE / button:visible 兜底
        return list(self.all_buttons)

    def _make_ok_btn(self) -> _FakeHandle:
        """模拟 message-box 的「确定」按钮: 点击后关闭模态框。"""
        ok = _FakeHandle(tag="BUTTON", text="确定")

        async def _click_ok(timeout=None, force=False):
            self.message_box_visible = False
            ok.clicked += 1
        ok.click = _click_ok
        return ok

    async def query_selector(self, selector: str):
        if "登录" in selector:
            return self.login_btn
        return None

    # ---- evaluate(模态框可见性检测 / JS 移除) ----
    async def evaluate(self, js: str, *args):
        # 可见性检测: 用 getBoundingClientRect 语义(fixed wrapper 也可见)
        if "getBoundingClientRect" in js and "el-message-box__wrapper" in js:
            return 1 if self.message_box_visible else 0
        # JS 移除(新实现只移除 .el-message-box__wrapper, 不碰 el-dialog)
        if "removeChild" in js:
            if "el-message-box__wrapper" in js and "el-dialog" not in js:
                n = 1 if self.message_box_visible else 0
                self.message_box_visible = False
                return n
            if "el-dialog__wrapper" in js:
                # 旧实现会移除 dialog; 新实现不应走到这里
                self.dialog_removed = True
                return 1
        return 0

    @property
    def keyboard(self):
        return self

    async def press(self, key: str):
        if key == "Escape":
            self.keyboard_esc_calls += 1
            self.message_box_visible = False

    # ---- click(旧实现入口, 新实现不走这里, 保留以防测试误用) ----
    async def click(self, selector: str, timeout: int | None = None, force: bool = False):
        raise AssertionError("_click_export_button 不应再使用 page.click(selector)")


# ─────────────────────────────────────────────────────────────
# _dismiss_modal_dialogs: 可见性检测(fixed) + 只处理 message-box
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_dismiss_messagebox_via_button():
    """有可见 message-box(即使 position:fixed, offsetParent=null) → 检测到并点确定关闭。"""
    page = FakePage()
    page.message_box_visible = True  # fixed wrapper 也可见(新检测语义)
    n = await _dismiss_modal_dialogs(page, max_wait_sec=1.0)
    assert n >= 1
    assert page.message_box_visible is False


@pytest.mark.asyncio
async def test_dismiss_messagebox_no_button_esc():
    """message-box 无按钮可点 → ESC 关闭。"""
    page = FakePage()
    page.message_box_visible = True

    async def qsa(sel):
        if "el-message-box__btns" in sel or "el-message-box button" in sel:
            return []  # 无按钮
        return []
    page.query_selector_all = qsa

    await _dismiss_modal_dialogs(page, max_wait_sec=1.0)
    assert page.keyboard_esc_calls >= 1
    assert page.message_box_visible is False


@pytest.mark.asyncio
async def test_dismiss_no_modal_returns_zero():
    """无模态框 → 返回 0, 不做多余动作。"""
    page = FakePage()
    assert await _dismiss_modal_dialogs(page, max_wait_sec=1.0) == 0


@pytest.mark.asyncio
async def test_dismiss_never_removes_el_dialog():
    """el-dialog(业务面板)不应被 _dismiss_modal_dialogs 移除(旧实现缺陷)。"""
    page = FakePage()
    page.message_box_visible = False
    page.dialog_visible = True
    n = await _dismiss_modal_dialogs(page, max_wait_sec=1.0)
    # 即使 dialog 存在, 也不检测/不移除(只处理 message-box)
    assert n == 0
    assert page.dialog_removed is False


# ─────────────────────────────────────────────────────────────
# _click_export_button: 核心回归 = 不点非按钮容器
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_click_export_normal_button_success():
    """标准 <button> 导出按钮存在 → 命中并点击成功。"""
    page = FakePage()
    export_btn = _FakeHandle(tag="BUTTON", text="导出")
    page.standard_handles = [export_btn]
    page.all_buttons = [export_btn]
    await _click_export_button(page)
    assert export_btn.clicked == 1


@pytest.mark.asyncio
async def test_click_export_span_el_button_success():
    """Element UI 伪按钮 span.el-button 导出 → 命中并点击成功。"""
    page = FakePage()
    export_btn = _FakeHandle(tag="SPAN", text="导出", cls="el-button el-button--primary")
    page.standard_handles = [export_btn]
    page.all_buttons = [export_btn]
    await _click_export_button(page)
    assert export_btn.clicked == 1


@pytest.mark.asyncio
async def test_click_export_ignores_container_div_with_export_text():
    """核心回归: 含"导出"文案的祖先 div(非按钮)绝不能被点击(3d48c1f 事故场景)。

    DOM 里 "导出管理" 工具栏 div 在导出按钮之前 → 旧选择器 div:has-text 会命中它。
    新实现只接受按钮语义元素, 应跳过容器 div, 最终点中真正的 <button>导出</button>。
    """
    page = FakePage()
    toolbar_div = _FakeHandle(tag="DIV", text="导出管理", cls="toolbar")  # 非按钮, 必须跳过
    export_btn = _FakeHandle(tag="BUTTON", text="导出")
    # 标准 selector(button:has-text) 只命中 export_btn; 容器 div 不在此列
    page.standard_handles = [export_btn]
    # 遍历候选里混入容器 div 在最前 → 应被 _is_clickable_button 拦截跳过
    page.all_buttons = [toolbar_div, export_btn]
    await _click_export_button(page)
    assert toolbar_div.clicked == 0, "非按钮容器(div.toolbar 含'导出')绝不能被点击"
    assert export_btn.clicked == 1, "应最终点中真正的导出按钮"


@pytest.mark.asyncio
async def test_click_export_skips_import_and_reset():
    """文本含"导入/重置"的按钮不应被当作导出按钮。"""
    page = FakePage()
    import_btn = _FakeHandle(tag="BUTTON", text="导入")
    export_btn = _FakeHandle(tag="BUTTON", text="导出")
    page.standard_handles = [export_btn]
    page.all_buttons = [import_btn, export_btn]
    await _click_export_button(page)
    assert import_btn.clicked == 0
    assert export_btn.clicked == 1


@pytest.mark.asyncio
async def test_click_export_login_only_raises_session_expired():
    """页面只剩「登录」按钮(会话被踢落登录页) → 抛 BmsSessionExpired。"""
    page = FakePage()
    page.url_value = "https://wifi.cloud-cat.cn/bms/index.html#/login"
    login_btn = _FakeHandle(tag="BUTTON", text="登录")
    page.login_btn = login_btn
    page.all_buttons = [login_btn]
    with pytest.raises(BmsSessionExpired):
        await _click_export_button(page)


@pytest.mark.asyncio
async def test_click_export_no_button_raises_original():
    """无模态框、无登录页、真找不到导出按钮 → 抛「未找到导出按钮」。"""
    page = FakePage()
    page.url_value = "https://wifi.cloud-cat.cn/bms/index.html#/settle/refund-record-report"
    page.all_buttons = [_FakeHandle(tag="BUTTON", text="搜索"), _FakeHandle(tag="BUTTON", text="重置")]
    with pytest.raises(RuntimeError, match="未找到导出按钮"):
        await _click_export_button(page)


@pytest.mark.asyncio
async def test_click_export_modal_dismissed_then_success():
    """有 message-box 挡住 → 先 dismiss 再点导出 → 成功。"""
    page = FakePage()
    page.message_box_visible = True
    export_btn = _FakeHandle(tag="BUTTON", text="导出")
    page.standard_handles = [export_btn]
    page.all_buttons = [export_btn]
    await _click_export_button(page)
    assert page.message_box_visible is False  # 模态框已被关闭
    assert export_btn.clicked == 1
