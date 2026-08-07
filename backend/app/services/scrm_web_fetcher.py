"""独立的 SCRM 网页抓取服务 (Playwright)

作为 SCRM OpenAPI 同步失败时的兜底方式:
- 使用 Playwright 模拟浏览器访问 SCRM 网页版
- 自动识别 **数学计算式验证码** (7*0=?) → OCR → 计算 → 输入答案
- 登录后抓取 3 类数据:客服账号 / 会话 / 会话转接
- 产出结构与 scrm_client 解析函数保持一致 → scrm_sync_service.insert_* 可统一入库

使用方式:
  fetcher = ScrmWebFetcher(base_url, username, password, options, logger)
  try:
      fetcher.login()            # 自动登录(含自动重试验证码)
      operators = fetcher.fetch_operators()
      sessions = fetcher.fetch_sessions(start_date, end_date)
      transfers = fetcher.fetch_session_transfers(start_date, end_date)
  finally:
      fetcher.close()

与 BMS 抓取(BMSFetcher)的区别:
  - 验证码类型不同: BMS 用 "text" + 闪猫, SCRM 用 "math" + ddddocr OCR + AST 安全计算
  - 数据入口 / 解析函数不同: SCRM 复用 scrm_client.parse_*
"""

from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeoutError,
    sync_playwright,
)

# 跨服务: 复用数学验证码 OCR 逻辑 + 纯文本验证码 OCR 逻辑
from app.services.captcha_session import (
    try_ocr_math_captcha,
    try_ocr_captcha,
)
# 种子配置: 各 SCRM 源的 base_url / page_path / 默认 options 等
from app.services.scrm_client import SCRM_DATA_SOURCES


# ── 异常定义 ──────────────────────────────────────────────────────────
class ScrmWebFetcherError(Exception):
    """SCRM 网页抓取通用异常"""


class ScrmWebLoginError(ScrmWebFetcherError):
    """登录失败(凭证错 / 验证码失败次数超限 / 页面结构变了等)"""


class ScrmWebCaptchaFailError(ScrmWebLoginError):
    """验证码识别/重试超限"""


# ── 默认选择器 (种子 options 缺失时的兜底) ──────────────────────────────
_DEFAULT_SELECTORS: dict[str, str] = {
    # 登录页
    "login_url_path": "/login",
    "captcha_img_selector": "img.show-code-img",
    "username_placeholder": "请输入账号",
    "password_placeholder": "请输入密码",
    "captcha_input_placeholder": "计算结果",
    "submit_button_text": "登 录",
    # 数据入口 (与种子 SCRM_DATA_SOURCES.page_path 保持一致)
    "web_operator_entry_path": "/system/operator/index",
    "web_session_entry_path": "/monitor/index",
}


@dataclass
class ScrmWebFetcher:
    """Playwright 版 SCRM 抓取器: 自动登录(数学验证码) + 抓 3 类数据

    设计: 与 BMS 抓取框架解耦,专门负责 SCRM 的登录流程 + 数据抽取
    """
    base_url: str
    username: str
    password: str
    options: dict = field(default_factory=dict)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    captcha_max_retries: int = 5

    # ── 内部 Playwright 资源 ──
    _pw: Optional[Playwright] = None
    _browser: Optional[Browser] = None
    _context: Optional[BrowserContext] = None
    _page: Optional[Page] = None
    _logged_in: bool = False

    # ── 入口/工具方法 ─────────────────────────────────────────────────
    def _selector(self, key: str) -> str:
        """options 有就用 options; 否则回退 _DEFAULT_SELECTORS"""
        return (self.options or {}).get(key) or _DEFAULT_SELECTORS[key]

    def _full_url(self, path: str) -> str:
        base = (self.base_url.rstrip("/") if self.base_url else "")
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    def _ensure_playwright(self) -> None:
        """懒启 Playwright (首次调用 launch,后续复用)"""
        if self._pw is not None and self._browser is not None and self._context is not None:
            return
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36"
                ),
                viewport={"width": 1680, "height": 960},
                locale="zh-CN",
            )
            self._page = self._context.new_page()
            # Playwright 默认 30s; SCRM 偶尔慢,给 45s
            self._page.set_default_timeout(45 * 1000)
        except Exception as e:  # pragma: no cover - 依赖环境
            self.close()
            raise ScrmWebFetcherError(f"启动 Playwright 失败: {e}") from e

    def close(self) -> None:
        """显式释放资源(每次抓取后调用)"""
        for attr in ("_page", "_context", "_browser", "_pw"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass
            setattr(self, attr, None)
        self._logged_in = False

    # ── 上下文支持 ────────────────────────────────────────────────────
    def __enter__(self) -> "ScrmWebFetcher":
        self._ensure_playwright()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── 登录 (核心: 数学验证码自动识别 + 重试) ────────────────────────────
    def login(self, start_url: Optional[str] = None) -> None:
        """打开登录页 → 填账号密码 → 自动识别验证码 → 提交;最多重试 captcha_max_retries 次

        Args:
            start_url: 可选,直接访问某 URL;如果未登录会被 SCRM 自己重定向到 /login
        Raises:
            ScrmWebCaptchaFailError: 验证码重试次数用完
            ScrmWebLoginError: 其他登录失败(凭证错等)
        """
        self._ensure_playwright()
        assert self._page is not None

        target = start_url or self._full_url(self._selector("login_url_path"))
        self.logger.info(f"[ScrmWeb] 打开登录页: {target}")
        self._page.goto(target, wait_until="domcontentloaded")
        # 等 Vue 把 input/按钮渲染出来(有些 SPA 是 domcontentloaded 后才 mount 组件,input 此时还没出现)
        self._page.wait_for_timeout(1500)
        try:
            # 至少等到账号密码 input 任一可见才继续(否则后续 locator.count() 可能为 0)
            self._page.wait_for_load_state("networkidle", timeout=4000)
        except PWTimeoutError:
            self._page.wait_for_timeout(1000)

        # 极端情况: 已经登录过(复用 context 的 cookie 时) → 直接返回
        if self._am_i_logged_in():
            self._logged_in = True
            self.logger.info("[ScrmWeb] 已有登录态,跳过登录")
            return

        last_error: Optional[Exception] = None
        for attempt in range(1, self.captcha_max_retries + 1):
            try:
                self._single_login_attempt(attempt)
                if self._am_i_logged_in():
                    self._logged_in = True
                    self.logger.info(f"[ScrmWeb] 登录成功 (尝试 {attempt} 次)")
                    return
                # 没跳: 可能弹窗 / 提示错误 → 截一张图保存(debug)
                self.logger.warning(f"[ScrmWeb] 尝试 {attempt}: 提交后未跳转,继续刷新验证码重试")
            except ScrmWebCaptchaFailError as e:
                last_error = e
                self.logger.warning(f"[ScrmWeb] 尝试 {attempt}: 验证码识别失败 - {e}")
            except PWTimeoutError as e:
                last_error = ScrmWebLoginError(f"页面元素等待超时: {e}")
                self.logger.warning(f"[ScrmWeb] 尝试 {attempt}: {last_error}")
            except Exception as e:  # 兜底:不中断,把异常带上供最后 raise
                last_error = ScrmWebLoginError(f"登录流程异常: {e}")
                self.logger.exception(f"[ScrmWeb] 尝试 {attempt}: {last_error}")

            # 刷新验证码 → 下一轮
            try:
                self._refresh_captcha_image()
            except Exception:
                # 刷新失败: reload 整个页面
                self._page.reload(wait_until="domcontentloaded")

        # 重试次数用完
        raise ScrmWebCaptchaFailError(
            f"登录重试 {self.captcha_max_retries} 次仍失败, "
            f"最后错误: {last_error}"
        )

    # ── 辅助: 获取是否需要验证码 ───────────────────────────────────────
    def _needs_captcha(self) -> bool:
        """是否需要处理验证码(captcha_type != 'none' 且配置了验证码选择器)"""
        ct = (self.options or {}).get("captcha_type")
        if ct and str(ct).lower() == "none":
            return False
        cis = self._selector("captcha_img_selector")
        cap_ph = self._selector("captcha_input_placeholder")
        return bool(cis and cis.strip() and cap_ph and cap_ph.strip())

    # ── 辅助: 更稳健的 input 定位 ───────────────────────────────────────
    def _locate_input(self, field: str, placeholder_sel: str, preferred_name: str = ""):
        """定位 input: 优先按 name 属性(最稳定, 后端约定), 找不到再回退 placeholder 模糊匹配。

        注:SPA(Vue/Element UI) 可能在 domcontentloaded 之后异步挂载组件,
        所以对首选选择器会短时间轮询等待(最多 2.5s),找不到才 fallback。
        """
        page = self._page
        assert page is not None
        candidates = []
        if preferred_name:
            candidates.append((f"input[name='{preferred_name}']", f"name={preferred_name}", 2500))
        if placeholder_sel:
            candidates.append((f"input[placeholder*='{placeholder_sel}']",
                               f"placeholder~{placeholder_sel[:10]}", 800))
        all_inputs_sel = "input"
        for sel, _desc, wait_ms in candidates:
            try:
                loc = page.locator(sel).first
                # 先快速 count 检查,有就直接用
                if loc.count() > 0:
                    return loc
                # 没看到可能是异步渲染,短暂等待一下(不抛异常,失败就下一个候选)
                try:
                    loc.wait_for(state="visible", timeout=wait_ms)
                    if loc.count() > 0:
                        return loc
                except PWTimeoutError:
                    pass
            except Exception:
                continue
        # 兜底: 根据 field 序号取
        idx = {"user": 0, "pwd": 1, "cap": 2}.get(field, 0)
        self.logger.warning(f"[ScrmWeb] 未能按 name/placeholder 定位 {field} input, 兜底取全部 input 中第 #{idx} 个可见的")
        return page.locator(all_inputs_sel).nth(idx)

    # ── 单轮登录步骤 ───────────────────────────────────────────────────
    def _single_login_attempt(self, attempt_no: int) -> None:
        """填充账号 + 密码 (+ 可选验证码) → 点击登录。"""
        page = self._page
        assert page is not None

        needs_cap = self._needs_captcha()
        user_ph = self._selector("username_placeholder")
        pwd_ph = self._selector("password_placeholder")
        cap_ph = self._selector("captcha_input_placeholder")
        self.logger.info(
            f"[ScrmWeb] #{attempt_no}: 填充账号={self.username}, 需要验证码={needs_cap}"
        )

        user_input = self._locate_input("user", user_ph, "username")
        pwd_input = self._locate_input("pwd", pwd_ph, "password")
        user_input.wait_for(state="visible")
        pwd_input.wait_for(state="visible")

        # 清空旧值再填(避免 retry 时残留)
        user_input.fill("")
        user_input.fill(self.username)
        pwd_input.fill("")
        pwd_input.fill(self.password)

        if needs_cap:
            cap_input = self._locate_input("cap", cap_ph, "captcha")
            try:
                cap_input.wait_for(state="visible", timeout=5000)
                cap_input.fill("")
            except PWTimeoutError:
                self.logger.warning(f"[ScrmWeb] #{attempt_no}: 等待验证码输入框超时(可能页面没有), 跳过")
                needs_cap = False

        if needs_cap:
            # 抓验证码图片 → OCR 计算答案
            answer = self._crack_math_captcha(attempt_no)
            self.logger.debug(f"[ScrmWeb] #{attempt_no}: 验证码答案 = '{answer}'")
            cap_input.fill(answer)
            # 等 300ms: 让前端 JS 监听到 input 事件(有些框架会校验输入事件)
            page.wait_for_timeout(300)

        # 点登录按钮 (按文本匹配最通用,也可 fallback 到 placeholder 附近的 button)
        submit_text = self._selector("submit_button_text")
        submit_btn = page.get_by_role("button", name=submit_text).first
        try:
            submit_btn.wait_for(state="visible", timeout=5_000)
        except PWTimeoutError:
            # 兜底: 直接取页面最后一个 button (常见布局)
            submit_btn = page.locator("button").last
        submit_btn.click()
        # 等跳转(登录成功一般 1-3s 跳首页)
        page.wait_for_timeout(2_500)

    def _captcha_type(self) -> str:
        """返回当前配置的验证码类型: 'text' | 'math' | 'none'

        历史遗留: 早期配置未写 captcha_type 时默认 math,保持向后兼容。
        """
        ct = (self.options or {}).get("captcha_type")
        if ct is None:
            return "math"
        s = str(ct).strip().lower()
        if s in ("text", "plain", "alnum", "string"):
            return "text"
        if s in ("none", "off", "disable", "no"):
            return "none"
        return "math"

    def _crack_math_captcha(self, attempt_no: int) -> str:
        """抓 <img> → base64/png_bytes → 根据 captcha_type 选择对应 OCR 函数

        captcha_type='math': OCR 识别表达式 → AST 安全计算 → 返回答案 (7*0 → 0)
        captcha_type='text': 直接 ddddocr 识别 → 返回原文 (3t9a → 3T9A, 7+97 → 7+97)

        Returns: 验证码答案字符串
        Raises: ScrmWebCaptchaFailError: OCR 识别不出来
        """
        page = self._page
        assert page is not None

        img_sel = self._selector("captcha_img_selector")
        if not img_sel or not img_sel.strip():
            raise ScrmWebCaptchaFailError("未配置 captcha_img_selector, 无法识别验证码")
        img = page.locator(img_sel).first
        img.wait_for(state="visible")
        # wait_for: 让图片加载完成(有些页面是懒加载)
        page.wait_for_timeout(500)

        # 取 base64 (Playwright: screenshot 返回 bytes)
        png_bytes: bytes = img.screenshot(type="png")
        if not png_bytes:
            raise ScrmWebCaptchaFailError("验证码截图为空")

        ct = self._captcha_type()
        answer: Optional[str]
        if ct == "text":
            answer = try_ocr_captcha(png_bytes)
        else:
            answer = try_ocr_math_captcha(png_bytes)
        if not answer:
            # debug: 保存失败图片(存临时文件,方便后续人工查看提升 OCR)
            try:
                import tempfile
                with tempfile.NamedTemporaryFile(
                    prefix="scrm_captcha_fail_", suffix=".png", delete=False
                ) as f:
                    f.write(png_bytes)
                    self.logger.warning(f"[ScrmWeb] #{attempt_no}: captcha_type={ct} 验证码识别失败,图片保存到 {f.name}")
            except Exception:
                pass
            raise ScrmWebCaptchaFailError(
                f"验证码 OCR 失败(captcha_type={ct})"
            )
        return answer

    def _refresh_captcha_image(self) -> None:
        """点击验证码图片刷新(大多数 img 点击会换一张);无验证码时改为 reload 整页"""
        page = self._page
        assert page is not None
        if not self._needs_captcha():
            # 无验证码场景: 刷新失败直接 reload 整页即可
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            return
        img_sel = self._selector("captcha_img_selector")
        try:
            img = page.locator(img_sel).first
            img.click()
            page.wait_for_timeout(400)
        except Exception:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(800)

    def _am_i_logged_in(self) -> bool:
        """简单判登录态:
        - 当前 URL 不是 /login 结尾 → 大概率已登录
        - 或页面存在 "退出登录" / 用户名 文本 → 已登录
        """
        page = self._page
        if page is None:
            return False
        url = page.url or ""
        if "/login" not in url.split("?")[0] and not url.endswith("login"):
            # 已被重定向到其他页 → 算已登录
            return True
        # 在 login 页的情况下: 看是否有已登录 UI(罕见)
        try:
            logout = page.locator("text=退出登录").count()
            user_text = page.locator("text=" + self.username).count()
            return (logout > 0) or (user_text > 0)
        except Exception:
            return False

    # ── 数据抓取: 客服账号 ────────────────────────────────────────────────
    def fetch_operators(self) -> list[dict[str, Any]]:
        """进入客服账号页 → 点击查询 → 抓表格 → 产出与 OpenAPI 字段名尽量一致的记录列表"""
        self._ensure_logged_in()
        page = self._page
        assert page is not None
        # 默认入口从种子配置取,没有就回退默认
        entry = (
            (SCRM_DATA_SOURCES.get("scrm_operator") or {}).get("web_operator_entry_path")
            or self._selector("web_operator_entry_path")
        )
        target = self._full_url(entry)
        self.logger.info(f"[ScrmWeb] 抓取客服账号: {target}")
        page.goto(target, wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)
        # 通用套路: 点 "查询" → 等表格刷新 → 读 el-table
        self._click_query_if_present()
        records = self._read_el_table_rows()
        # 直接返回行记录(dict 列表),上层 upsert 兼容多种字段名写法
        return records or []

    # ── 数据抓取: 会话列表 ────────────────────────────────────────────────
    def fetch_sessions(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """抓会话主表(进入会话监控页 → 填时间 → 查询 → 读 el-table)"""
        self._ensure_logged_in()
        page = self._page
        assert page is not None
        entry = (
            (SCRM_DATA_SOURCES.get("scrm_sessions") or {}).get("web_session_entry_path")
            or self._selector("web_session_entry_path")
        )
        target = self._full_url(entry)
        self.logger.info(f"[ScrmWeb] 抓会话列表: {target} [{start_date} ~ {end_date}]")
        page.goto(target, wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)
        self._fill_el_datetime_range(start_date, end_date)
        self._click_query_if_present()
        records = self._read_el_table_rows()
        return records or []

    # ── 数据抓取: 会话转接 ────────────────────────────────────────────────
    def fetch_session_transfers(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """抓会话转接列表(同页 Tab / 另一个 page_path? 这里走同页面 + 点"转接记录"Tab,失败就 fallback 同入口)"""
        self._ensure_logged_in()
        page = self._page
        assert page is not None
        entry = (
            (SCRM_DATA_SOURCES.get("scrm_session_transfers") or {}).get("web_session_entry_path")
            or self._selector("web_session_entry_path")
        )
        target = self._full_url(entry)
        self.logger.info(f"[ScrmWeb] 抓会话转接: {target} [{start_date} ~ {end_date}]")
        page.goto(target, wait_until="domcontentloaded")
        page.wait_for_timeout(1_500)
        # 尝试切 "转接记录" / "会话转接" Tab
        for tab_name in ("转接记录", "会话转接", "转接流水"):
            try:
                tab = page.get_by_text(tab_name).first
                tab.wait_for(state="visible", timeout=2_000)
                tab.click()
                page.wait_for_timeout(800)
                break
            except Exception:
                continue
        self._fill_el_datetime_range(start_date, end_date)
        self._click_query_if_present()
        records = self._read_el_table_rows()
        return records or []

    # ── 抓取辅助: 通用 ElementUI/Vue 页面套路 ────────────────────────────
    def _ensure_logged_in(self) -> None:
        if not self._logged_in:
            self.login()
        assert self._page is not None

    def _click_query_if_present(self) -> None:
        """按文本点击查询按钮(最通用;找不到就再试 button 最后第二个)"""
        page = self._page
        assert page is not None
        for txt in ("查询", "搜索", "筛选", "Query", "Search"):
            try:
                btn = page.get_by_role("button", name=txt).first
                btn.wait_for(state="visible", timeout=2_000)
                btn.click()
                page.wait_for_timeout(1_500)
                return
            except Exception:
                continue
        # Fallback: 表格上方两个按钮一般是 [重置, 查询] → 点倒数第二个
        try:
            btn = page.locator(".el-form-item .el-button").nth(-2)
            btn.click()
            page.wait_for_timeout(1_500)
        except Exception:
            # 没找到就算了,页面默认可能已加载
            pass

    def _fill_el_datetime_range(self, start_date: date, end_date: date) -> None:
        """填写 ElementUI 日期区间选择器(常见 .el-date-editor 双 input 布局)

        策略: 直接 type 两个 YYYY-MM-DD 字符串(比点日期选择器稳很多)
        """
        page = self._page
        assert page is not None
        s_str = start_date.strftime("%Y-%m-%d")
        e_str = end_date.strftime("%Y-%m-%d")
        # 大多数 ElementUI 日期范围是两个 input(placeholder = 开始日期/结束日期)
        candidates = [
            ("input[placeholder*='开始日期']", "input[placeholder*='结束日期']"),
            ("input[placeholder*='起始']", "input[placeholder*='截止']"),
            ("input[placeholder*='Start']", "input[placeholder*='End']"),
        ]
        for s_sel, e_sel in candidates:
            s_inputs = page.locator(s_sel)
            e_inputs = page.locator(e_sel)
            if s_inputs.count() > 0 and e_inputs.count() > 0:
                s_inputs.first.click()
                page.wait_for_timeout(150)
                page.keyboard.press("Control+A")
                s_inputs.first.fill(s_str)
                e_inputs.first.click()
                page.wait_for_timeout(150)
                page.keyboard.press("Control+A")
                e_inputs.first.fill(e_str)
                return
        # Fallback: 两个连续的 .el-date-editor input
        editors = page.locator(".el-date-editor input")
        if editors.count() >= 2:
            editors.nth(0).click()
            page.keyboard.press("Control+A")
            editors.nth(0).fill(s_str)
            editors.nth(1).click()
            page.keyboard.press("Control+A")
            editors.nth(1).fill(e_str)

    def _read_el_table_rows(self) -> list[dict[str, Any]]:
        """读当前页面 el-table → list[dict](key = 列名中文/序号, value=单元格文本)

        注意: 只抓当前页(定时同步一般每天跑,数据量通常一页能装下;
        若超过一页,由调用方(同步服务)按日期分片或做翻页抓取)
        """
        page = self._page
        assert page is not None
        # 等 el-table 出来
        try:
            page.locator(".el-table").first.wait_for(state="visible", timeout=15_000)
        except PWTimeoutError:
            self.logger.warning("[ScrmWeb] 未找到 el-table,返回空列表")
            return []
        # 用 evaluate 一次性取 (比逐个 textContent 快 N 倍)
        rows_raw = page.evaluate(
            """() => {
                const table = document.querySelector('.el-table');
                if (!table) return [];
                const headers = Array.from(table.querySelectorAll('.el-table__header-wrapper th .cell'))
                    .map(t => (t.innerText || '').trim());
                const bodyRows = table.querySelectorAll('.el-table__body-wrapper tbody tr');
                const rows = [];
                bodyRows.forEach(tr => {
                    const cells = Array.from(tr.querySelectorAll('td .cell'))
                        .map(c => (c.innerText || '').trim());
                    const obj = {};
                    headers.forEach((h, i) => {
                        const key = h || ('col_' + i);
                        obj[key] = cells[i] ?? '';
                    });
                    rows.push(obj);
                });
                return rows;
            }"""
        )
        if not isinstance(rows_raw, list):
            return []
        # 转成 dict 并 strip
        cleaned: list[dict[str, Any]] = []
        for row in rows_raw:
            if not isinstance(row, dict):
                continue
            # 跳过全空行(分页分隔行/空占位)
            vals = [str(v).strip() for v in row.values() if str(v).strip()]
            if not vals:
                continue
            cleaned.append({str(k): (str(v).strip() if isinstance(v, str) else v) for k, v in row.items()})
        self.logger.info(f"[ScrmWeb] 读取 el-table 得 {len(cleaned)} 行")
        return cleaned


# ── 面向 scrm_sync_service 的高层简化 API ─────────────────────────────
def run_web_fetch(
    base_url: str,
    username: str,
    password: str,
    options: dict,
    data_types: list[str],
    date_range: tuple[date, date],
    logger: Optional[logging.Logger] = None,
) -> dict[str, list[dict[str, Any]]]:
    """高层包装: 创建 fetcher → 登录 → 按 data_types 抓对应数据 → close

    Args:
        data_types: 从 {"scrm_operator", "scrm_sessions", "scrm_session_transfers"} 里挑
        date_range: (start_date, end_date) 仅会话/转接用; 账号列表忽略
    Returns: {"scrm_operator": [...], "scrm_sessions": [...], ...} 不要求的 key 可能缺失
    Raises: 各种 ScrmWebFetcherError 子类(让上层决定是否 fall back 到 OpenAPI)
    """
    log = logger or logging.getLogger(__name__)
    fetcher = ScrmWebFetcher(
        base_url=base_url, username=username, password=password,
        options=options, logger=log,
    )
    result: dict[str, list[dict[str, Any]]] = {}
    try:
        fetcher.login()
        if "scrm_operator" in data_types:
            result["scrm_operator"] = fetcher.fetch_operators()
        start_date, end_date = date_range
        if "scrm_sessions" in data_types:
            result["scrm_sessions"] = fetcher.fetch_sessions(start_date, end_date)
        if "scrm_session_transfers" in data_types:
            result["scrm_session_transfers"] = fetcher.fetch_session_transfers(start_date, end_date)
        return result
    finally:
        fetcher.close()
