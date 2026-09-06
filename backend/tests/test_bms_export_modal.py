# -*- coding: utf-8 -*-
"""bms_fetcher 导出按钮可靠性测试(2026-09-06 修复)

真根因(生产 app.log 铁证): BMS 页面(尤其大窗口查询后)会弹 el-message-box 模态「提示」框,
  它 `<div role="dialog" aria-label="提示" class="el-message-box__wrapper">` intercepts
  pointer events → 挡住导出/查询按钮的真实点击 → 15s 盲目重试后**误报「未找到导出按钮」**
  (全日志 12 次拦截, 按钮文本其实存在)。

修复:
  1. _dismiss_modal_dialogs(page): 识别并关闭 el-message-box / el-dialog 模态框
     (优先点确定/OK/知道了 → ESC → JS 移除容器)。
  2. _click_export_button(page): 点击前 dismiss 模态框; 候选扩到伪按钮(_EXPORT_ANY_CLICKABLE);
     若页面只剩「登录」按钮 → 抛 BmsSessionExpired(而非误导性「未找到导出按钮」)。
  3. _download_with_retry: BmsSessionExpired 不重试直接冒泡(否则白等 180~600s×N)。

本测试用 FakePage 模拟 Playwright page(不走真实浏览器), 锁定关闭逻辑与报错语义。
"""

from __future__ import annotations

import pytest

from app.services.bms_fetcher import (
    BmsSessionExpired,
    _click_export_button,
    _dismiss_modal_dialogs,
)


class _FakeElem:
    """极简 ElementHandle 替身: 记录文本/点击, 可触发点击异常。"""

    def __init__(self, text: str = "", click_raises: Exception | None = None):
        self._text = text
        self._click_raises = click_raises
        self.clicked = 0

    async def inner_text(self) -> str:
        return self._text

    async def get_attribute(self, name: str):
        return None

    async def click(self, timeout: int | None = None, force: bool = False):
        if self._click_raises:
            raise self._click_raises
        self.clicked += 1


class FakePage:
    """模拟 Playwright Page: 记录 evaluate/click/query_selector_all 等调用。"""

    def __init__(self):
        self.url_value = "https://wifi.cloud-cat.cn/bms/index.html#/settle/refund-record-report"
        self.modal_count = 0          # evaluate 返回的可见模态框数
        self.modal_removed = 0        # JS 移除数量
        self.keyboard_esc_calls = 0
        self.all_buttons: list[_FakeElem] = []   # query_selector_all(_EXPORT_ANY_CLICKABLE) 返回
        self.login_btn = None                    # query_selector(登录) 返回
        self._qs_all_history: list[str] = []
        self.evaluated_js: list[str] = []

    # ---- Page 接口 ----
    @property
    def url(self) -> str:
        return self.url_value

    async def evaluate(self, js: str, *args):
        self.evaluated_js.append(js[:60])
        # 模态框计数
        if "offsetParent !== null" in js:
            return self.modal_count
        # JS 移除模态容器
        if "removeChild" in js and "el-message-box__wrapper" in js:
            n = self.modal_count
            self.modal_count = 0
            return n
        return 0

    @property
    def keyboard(self):
        """Playwright 的 page.keyboard 是属性, 不是方法(返回 Keyboard 对象)。"""
        return self

    async def press(self, key: str):
        if key == "Escape":
            self.keyboard_esc_calls += 1
            # ESC 后假设模态框消失
            self.modal_count = 0

    async def query_selector(self, selector: str):
        if "登录" in selector:
            return self.login_btn
        return None

    async def query_selector_all(self, selector: str):
        self._qs_all_history.append(selector)
        if "el-message-box__btns" in selector or "el-dialog" in selector:
            # 模态框内部按钮: 返回一个「确定」按钮(点击后清掉模态框 → 模拟真实关闭)
            if self.modal_count:
                btn = _FakeElem("确定")

                async def _click_ok(timeout=None, force=False):
                    self.modal_count = 0
                btn.click = _click_ok
                return [btn]
            return []
        # _EXPORT_ANY_CLICKABLE / button:visible → 页面按钮
        return self.all_buttons

    async def click(self, selector: str, timeout: int | None = None, force: bool = False):
        if "导出" in selector and ">> nth=0" in selector:
            # 标准 selector 点击: 若存在模态框或页面无"导出"按钮则模拟失败
            has_export = any("导出" in (getattr(b, "_text", "") or "") for b in self.all_buttons)
            if self.modal_count > 0:
                raise TimeoutError("Timeout 8000ms exceeded ... el-message-box__wrapper intercepts pointer events")
            if not has_export:
                raise TimeoutError("Timeout 8000ms exceeded ... no export button")
            return None
        raise TimeoutError("no button")


# ─────────────────────────────────────────────────────────────
# _dismiss_modal_dialogs
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_dismiss_modal_via_button():
    """有模态框 + 有「确定」按钮 → 点按钮关闭, 返回关闭数。"""
    page = FakePage()
    page.modal_count = 1
    n = await _dismiss_modal_dialogs(page, max_wait_sec=1.0)
    assert n >= 1  # 至少关了一个


@pytest.mark.asyncio
async def test_dismiss_modal_no_button_esc():
    """模态框无按钮可点 → ESC 关闭。"""
    page = FakePage()
    page.modal_count = 1

    async def qsa(sel):
        # 模态框内无按钮
        if "el-message-box__btns" in sel or "el-dialog" in sel:
            return []
        return page.all_buttons
    page.query_selector_all = qsa

    await _dismiss_modal_dialogs(page, max_wait_sec=1.0)
    assert page.keyboard_esc_calls >= 1


@pytest.mark.asyncio
async def test_dismiss_no_modal_returns_zero():
    """无模态框 → 立即返回 0, 不做多余动作。"""
    page = FakePage()
    page.modal_count = 0
    assert await _dismiss_modal_dialogs(page, max_wait_sec=1.0) == 0


# ─────────────────────────────────────────────────────────────
# _click_export_button
# ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_click_export_normal_success():
    """无模态框、导出按钮存在 → 标准 selector 命中成功, 不报错。"""
    page = FakePage()
    page.modal_count = 0
    page.all_buttons = [_FakeElem("导出")]
    # 标准 selector 分支: page.click(selector 含 '导出' 与 >> nth=0)
    await _click_export_button(page)  # 不应抛异常


@pytest.mark.asyncio
async def test_click_export_modal_dismissed_then_success():
    """存在模态框 → _click_export_button 内部 dismiss 后再点导出 → 成功不误报。"""
    page = FakePage()
    page.modal_count = 1
    page.all_buttons = [_FakeElem("导出")]

    async def click(selector, timeout=None, force=False):
        if ">> nth=0" in selector and "导出" in selector:
            # 第一次被模态框拦截, dismiss 后成功
            if page.modal_count > 0:
                page.modal_count = 0  # 模拟 dismiss 清掉
            return None
        raise TimeoutError("no")

    async def _scenario():
        page.click = click
        return await _click_export_button(page)

    await _scenario()  # 不应抛"未找到导出按钮"


@pytest.mark.asyncio
async def test_click_export_login_only_raises_session_expired():
    """页面只剩「登录」按钮(会话被踢落登录页) → 抛 BmsSessionExpired, 而非误导性报错。"""
    page = FakePage()
    page.url_value = "https://wifi.cloud-cat.cn/bms/index.html#/login"
    page.modal_count = 0
    login_btn = _FakeElem("登录")
    page.login_btn = login_btn
    page.all_buttons = [_FakeElem("登录")]
    with pytest.raises(BmsSessionExpired):
        await _click_export_button(page)


@pytest.mark.asyncio
async def test_click_export_no_button_raises_original():
    """无模态框、无登录页、真找不到导出按钮 → 仍抛「未找到导出按钮」(语义不变)。"""
    page = FakePage()
    page.modal_count = 0
    page.url_value = "https://wifi.cloud-cat.cn/bms/index.html#/settle/refund-record-report"
    page.all_buttons = [_FakeElem("搜索"), _FakeElem("重置")]
    with pytest.raises(RuntimeError, match="未找到导出按钮"):
        await _click_export_button(page)
