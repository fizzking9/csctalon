"""BMS 平台数据抓取服务

支持 6 个数据源的抓取:
  - refund (退费单): https://wifi.cloud-cat.cn/bms/index.html#/pay/refund-manage
  - ticket (服务请求): https://support.cloud-cat.cn/bms/index.html#/service-request/service-request-manage
  - wallet (提现操作): https://support.cloud-cat.cn/bms/index.html#/settle/customerWalletOperationRecord
  - device_combo (设备套餐): https://support.cloud-cat.cn/bms/index.html#/device/device-combo
  - cs_record (智能助手处理): http://ai-acc.cloud-cat.cn (HTTP API 导出)
  - shanmao_refund (闪猫退款): https://fast.cloud-cat.cn/sc_brm/#/order-alrrefundapply-alrrefundapply

注意:
  - 账号密码从 sync_source_configs 表读取(加密存储),按源独立配置
  - 两个子域名(wifi.cloud-cat.cn / support.cloud-cat.cn)需要分别登录,可能用不同账号
  - BMS 系统限制:1分钟内不能连续做查询导出下载动作
  - BMS_DATA_SOURCES 现仅作为默认种子(首次启动写入 DB),运行时配置以 DB 为准
  - shanmao_refund (闪猫) 登录需验证码,采用 storage_state 复用机制,缺失时走前端交互验证码登录
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# BMS 数据源默认配置(种子,首次启动写入 sync_source_configs;default_sync 控制是否纳入定时/默认勾选)
BMS_DATA_SOURCES: dict[str, dict] = {
    "refund": {
        "name": "退费单管理",
        "base_url": "https://wifi.cloud-cat.cn/bms",
        "page_path": "/index.html#/pay/refund-manage",
        "default_sync": True,
        # 退费单默认按创建时间筛选,无需选时间字段
    },
    "ticket": {
        "name": "服务请求管理",
        "base_url": "https://support.cloud-cat.cn/bms",
        "page_path": "/index.html#/service-request/service-request-manage",
        "default_sync": True,
        # 服务请求管理有多个时间筛选框,只填"起始完成时间"和"结束完成时间"
        "time_labels": ["起始完成时间", "结束完成时间"],
    },
    "wallet": {
        "name": "提现操作记录",
        "base_url": "https://support.cloud-cat.cn/bms",
        "page_path": "/index.html#/settle/customerWalletOperationRecord",
        "default_sync": True,
    },
    "device_combo": {
        "name": "设备套餐",
        "base_url": "https://support.cloud-cat.cn/bms",
        "page_path": "/index.html#/device/device-combo",
        "default_sync": False,  # 默认不同步,可在手动同步时勾选
    },
    # ai-acc 平台(智能助手处理记录)— Naive UI,走 HTTP API 非 Playwright
    # fetch_mode="http" 标记 bms_sync.sync_all 分流到 ai_acc_fetcher
    "cs_record": {
        "name": "智能助手处理记录",
        "base_url": "http://ai-acc.cloud-cat.cn",
        "page_path": "/ai-acc/cs/v1/assistant/handleRecord/export",  # 导出 API 路径
        "default_sync": True,
        "fetch_mode": "http",
    },
    # 闪猫加速平台(已退款申请) - 独立域名 fast.cloud-cat.cn,登录需验证码
    # requires_captcha=True 标记: 抓取时优先复用 storage_state,缺失时走交互验证码登录流程
    "shanmao_refund": {
        "name": "闪猫退款管理",
        "base_url": "https://fast.cloud-cat.cn/sc_brm",
        "page_path": "/index.html#/order-alrrefundapply-alrrefundapply",
        "default_sync": False,  # 首次上线默认不同步,验证通过后管理员手动开启
        "requires_captcha": True,
    },
}

# 每次操作之间的最小间隔(秒),BMS 系统限制1分钟
OPERATION_INTERVAL = 65

# 浏览器登录态保存目录(复用 storage_state 跳过验证码登录)
# 目录结构: settings.UPLOAD_DIR/.browser_states/{data_type}.json
STORAGE_STATE_DIR = ".browser_states"


def _storage_state_path(data_type: str) -> Path:
    """获取某数据源的 storage_state 文件路径(随 UPLOAD_DIR 按月归档不必,登录态单独存)"""
    base = Path(settings.UPLOAD_DIR) / STORAGE_STATE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{data_type}.json"


class FetchResult:
    """单次抓取结果"""

    def __init__(
        self,
        data_type: str = "",
        success: bool = False,
        file_path: Optional[str] = None,
        filename: Optional[str] = None,
        total_rows: Optional[int] = None,
        error: Optional[str] = None,
    ):
        self.data_type = data_type
        self.success = success
        self.file_path = file_path
        self.filename = filename
        self.total_rows = total_rows
        self.error = error


class BmsFetcher:
    """BMS 平台数据抓取器 - 支持多数据源"""

    def __init__(self):
        self._browser = None
        self._playwright = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._cleanup()

    async def _cleanup(self):
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def _init_browser(self):
        """初始化 Playwright 浏览器(整个抓取过程复用一个浏览器实例)"""
        if self._browser:
            return
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--window-size=1920,1080",
                ],
            )
            logger.info("Playwright Chromium 启动成功")
        except Exception as e:
            raise RuntimeError(f"浏览器启动失败: {e}") from e

    async def fetch_one(
        self,
        data_type: str,
        start_date: datetime,
        end_date: datetime,
        config: dict,
        timeout: int = 180,
    ) -> FetchResult:
        """抓取单个数据源

        Args:
            data_type: refund/ticket/wallet/device_combo
            start_date: 开始时间
            end_date: 结束时间
            config: 该源的运行配置(base_url/page_path/time_labels/username/password/name,来自 sync_source_configs)
            timeout: 超时时间(秒)
        """
        result = FetchResult()
        result.data_type = data_type

        if not config or not config.get("base_url"):
            result.error = f"数据源未配置或缺少 base_url: {data_type}"
            return result
        if not config.get("username") or not config.get("password"):
            result.error = f"数据源未配置账号密码: {data_type}(请在系统管理-同步设置中配置)"
            return result

        try:
            await asyncio.wait_for(
                self._do_fetch_one(data_type, start_date, end_date, config, result),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            result.error = f"抓取超时({timeout}秒): {config.get('name', data_type)}"
            logger.error(result.error)
        except Exception as e:
            result.error = f"抓取失败: {e}"
            logger.error(result.error, exc_info=True)

        return result

    async def _set_time_by_labels(self, page, start_label: str, end_label: str, start_str: str, end_str: str):
        """按表单标签精确查找日期输入框并设置时间

        适用于页面上有多个时间筛选框的情况(如服务请求管理):
          起始完成时间 [输入框]
          结束完成时间 [输入框]
          登记时间   [输入框]
          催单时间   [输入框]

        策略: 找包含目标标签文本的最近表单项(.el-form-item),操作其中的可见 input
        """
        logger.info(f"按标签查找时间输入框: '{start_label}' / '{end_label}'")

        # 查找起始时间输入框
        start_filled = await self._fill_input_by_label(page, start_label, start_str)
        # 查找结束时间输入框
        end_filled = await self._fill_input_by_label(page, end_label, end_str)

        if not start_filled or not end_filled:
            # 标签查找失败,回退到通用策略
            logger.warning("按标签查找失败,回退到通用时间范围策略")
            await self._set_time_range(page, start_str, end_str)

    async def _fill_input_by_label(self, page, label: str, value: str) -> bool:
        """在包含指定标签文本的表单项中找到可见 input 并填值

        Returns: True 表示成功
        """
        # 策略1: 找 .el-form-item 中包含该 label 文本的,取里面的可见 input
        # 用 has_text 过滤,取最内层匹配的(用 .first 避免父元素干扰)
        form_item = page.locator(f".el-form-item:has-text('{label}')").first
        try:
            if await form_item.count() > 0:
                # 找 form-item 内的可见 input(排除纯展示用的)
                inputs = form_item.locator("input:visible")
                count = await inputs.count()
                if count > 0:
                    logger.info(f"在 '{label}' 表单项中找到 {count} 个可见 input")
                    await self._fill_el_input(page, inputs.first, value)
                    return True
        except Exception as e:
            logger.warning(f"在表单项中查找 '{label}' 失败: {e}")

        # 策略2: 找 label 元素文本匹配,然后找紧邻的 input
        # Element UI 的 label 可能是 .el-form-item__label 或普通 label
        try:
            # 找文本完全匹配的 label 元素,然后找同一 form-item 内的 input
            label_el = page.locator(f".el-form-item__label:has-text('{label}'), label:has-text('{label}')").first
            if await label_el.count() > 0:
                # 向上找 form-item,再找 input
                input_el = label_el.locator("xpath=ancestor::div[contains(@class,'el-form-item')]//input").first
                if await input_el.count() > 0 and await input_el.is_visible():
                    logger.info(f"通过 label '{label}' 找到 input")
                    await self._fill_el_input(page, input_el, value)
                    return True
        except Exception as e:
            logger.warning(f"通过 label 元素查找 '{label}' 失败: {e}")

        # 策略3: 用 JS 精确查找(遍历所有 label 文本,找匹配的,定位到同 form-item 的 input)
        try:
            found = await page.evaluate(f"""
                (labelText) => {{
                    const labels = document.querySelectorAll('.el-form-item__label, label, .form-label');
                    for (const lab of labels) {{
                        if (lab.textContent.trim() === labelText || lab.textContent.trim().includes(labelText)) {{
                            // 找最近的 form-item 祖先
                            let formItem = lab.closest('.el-form-item') || lab.parentElement;
                            if (!formItem) continue;
                            // 找里面的可见 input
                            const inputs = formItem.querySelectorAll('input');
                            for (const inp of inputs) {{
                                const rect = inp.getBoundingClientRect();
                                const style = window.getComputedStyle(inp);
                                if (rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none') {{
                                    return true;
                                }}
                            }}
                        }}
                    }}
                    return false;
                }}
            """, label)
            if found:
                # 用 JS 找到了,重新用 locator 定位填充
                logger.info(f"JS 确认 '{label}' 的 input 存在,用 locator 填充")
                # 重新用更精确的 xpath 定位
                input_locator = page.locator(f"xpath=//div[contains(@class,'el-form-item')][.//text()[contains(.,'{label}')]]//input").first
                if await input_locator.count() > 0:
                    await self._fill_el_input(page, input_locator, value)
                    return True
        except Exception as e:
            logger.warning(f"JS 查找 '{label}' 失败: {e}")

        logger.warning(f"未找到 '{label}' 对应的输入框")
        return False

    async def _set_time_range(self, page, start_str: str, end_str: str):
        """设置时间范围 - 适配 Element UI 日期选择器

        策略:
          1. 优先找 datetimerange 范围选择器(单个组件两个 input)
          2. 其次找可见的日期 input(过滤隐藏的)
          3. 兜底用 placeholder 匹配

        Element UI datetimerange 结构:
          .el-date-editor--datetimerange
            input.el-range-input (开始)
            input.el-range-input (结束)
        """
        # 策略1: Element UI datetimerange 范围选择器(只取第一个,避免多时间字段干扰)
        range_editor = await page.query_selector(".el-date-editor--datetimerange")
        if range_editor:
            inputs = await range_editor.query_selector_all(".el-range-input")
            if len(inputs) >= 2:
                logger.info(f"使用 datetimerange 选择器,找到 {len(inputs)} 个 input")
                await self._fill_el_input(page, inputs[0], start_str)
                await self._fill_el_input(page, inputs[1], end_str)
                return

        # 策略2: 找所有可见的日期相关 input,取前两个
        visible_time_inputs = await page.evaluate("""
            () => {
                const inputs = document.querySelectorAll(
                    '.el-date-editor input, input.el-range-input, input[placeholder*="时间"], input[placeholder*="日期"]'
                );
                return Array.from(inputs).filter(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                }).slice(0, 4).map(el => ({
                    placeholder: el.placeholder || '',
                    className: el.className || '',
                    value: el.value || ''
                }));
            }
        """)
        logger.info(f"可见的日期 input: {visible_time_inputs}")

        # 找到可见的 range-input 或日期 input
        visible_inputs = await page.evaluate("""
            () => {
                const inputs = document.querySelectorAll(
                    '.el-date-editor input, input.el-range-input, input[placeholder*="时间"], input[placeholder*="日期"]'
                );
                const result = [];
                for (const el of inputs) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none') {
                        result.push(el);
                    }
                }
                return result;
            }
        """)

        if len(visible_inputs) >= 2:
            # 用 locator 确保操作可见元素
            locators = page.locator('.el-date-editor input:visible, input.el-range-input:visible').all()
            locators = await locators
            if len(locators) >= 2:
                logger.info(f"使用可见 input 选择器,找到 {len(locators)} 个")
                await self._fill_el_input(page, locators[0], start_str)
                await self._fill_el_input(page, locators[1], end_str)
                return

        # 策略3: 兜底 - 用 placeholder 匹配
        logger.info("使用 placeholder 兜底匹配")
        try:
            await page.fill('input[placeholder*="开始"]', start_str, timeout=5000)
            await page.fill('input[placeholder*="结束"]', end_str, timeout=5000)
        except Exception:
            # 最后兜底: 用 evaluate 直接设值并触发事件
            await page.evaluate(f"""
                () => {{
                    const inputs = document.querySelectorAll('input.el-range-input:visible, .el-date-editor input:visible');
                    if (inputs.length >= 2) {{
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        setter.call(inputs[0], '{start_str}');
                        inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
                        setter.call(inputs[1], '{end_str}');
                        inputs[1].dispatchEvent(new Event('input', {{ bubbles: true }}));
                    }}
                }}
            """)

    async def _fill_el_input(self, page, element, value: str):
        """填充 Element UI 日期输入框

        Element UI 的 input 可能需要: 点击 → 清空 → 输入 → 回车确认
        """
        try:
            # 方式1: 直接 fill(适用于可见的 range-input)
            await element.fill(value, timeout=10000)
            # 触发 input 和 change 事件确保 Vue 响应
            await element.dispatchEvent("input")
            await element.dispatchEvent("change")
        except Exception as e:
            logger.warning(f"fill 失败,尝试 evaluate 设值: {e}")
            # 方式2: 用 JS 直接设值并触发事件
            await element.evaluate(f"""
                (el) => {{
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    setter.call(el, '{value}');
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            """)

    async def _do_fetch_one(
        self,
        data_type: str,
        start_date: datetime,
        end_date: datetime,
        config: dict,
        result: FetchResult,
    ):
        """执行单个数据源的抓取流程(配置来自 sync_source_configs)"""
        await self._init_browser()

        base_url = config["base_url"]
        username = config["username"]
        password = config["password"]
        login_url = f"{base_url}/index.html#/login"
        page_url = f"{base_url}{config['page_path']}"

        # ── storage_state 复用(优先加载已保存登录态,跳过验证码) ──
        state_path = _storage_state_path(data_type)
        storage_state = None
        if config.get("requires_captcha") and state_path.exists():
            try:
                storage_state = str(state_path)
                logger.info(f"[{data_type}] 加载已保存登录态: {state_path.name}")
            except Exception as e:
                logger.warning(f"[{data_type}] 读取 storage_state 失败,回退到常规登录: {e}")
                storage_state = None

        # 每个数据源使用独立的 context(不同子域名需要分别登录)
        context_kwargs = {
            "accept_downloads": True,
            "viewport": {"width": 1920, "height": 1080},
        }
        if storage_state:
            context_kwargs["storage_state"] = storage_state
        context = await self._browser.new_context(**context_kwargs)
        page = await context.new_page()

        try:
            logged_in_via_state = False
            if storage_state:
                # 有 storage_state: 直接进目标页,验证是否仍登录
                logger.info(f"[{data_type}] 验证登录态是否有效...")
                await page.goto(page_url, wait_until="networkidle")
                await asyncio.sleep(2)
                if "login" not in page.url.lower():
                    logged_in_via_state = True
                    logger.info(f"[{data_type}] 登录态有效,跳过登录步骤")

            if not logged_in_via_state:
                # storage_state 不存在或已过期,走登录流程
                # 1. 登录
                logger.info(f"[{data_type}] 正在登录 {base_url}...")
                await page.goto(login_url, wait_until="networkidle")
                await asyncio.sleep(2)

                await page.fill('input[type="text"], input[placeholder*="账号"], input[placeholder*="用户名"]', username)
                await page.fill('input[type="password"], input[placeholder*="密码"]', password)

                # ── 闪猫:验证码处理(后续接入前端交互) ──
                # 目前: 如检测到验证码输入框且无交互验证码值,报错提示需先在同步设置页完成登录
                if config.get("requires_captcha"):
                    captcha_input = await page.query_selector(
                        'input[placeholder*="验证码"], input[aria-label*="验证码"]'
                    )
                    if captcha_input and await captcha_input.is_visible():
                        # 检查是否通过 storage_state / cookie 等已绕过(实际上前面已 goto login 所以必然需要)
                        raise RuntimeError(
                            "需要验证码登录:请前往「数据中心-数据采集-同步设置」点击「登录闪猫」按钮完成验证码登录"
                        )

                await asyncio.sleep(1)
                await page.click('button:has-text("登录"), button[type="submit"]')
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(3)

                if "login" in page.url.lower():
                    error_elem = await page.query_selector(".el-message__content, .el-form-item__error")
                    if error_elem:
                        error_text = await error_elem.inner_text()
                        raise RuntimeError(f"登录失败: {error_text}")
                    raise RuntimeError("登录失败: 账号或密码错误")
                logger.info(f"[{data_type}] 登录成功")

                # ── 登录成功:保存 storage_state(下次自动复用) ──
                if config.get("requires_captcha"):
                    try:
                        await context.storage_state(path=str(state_path))
                        logger.info(f"[{data_type}] 登录态已保存至 {state_path.name}")
                    except Exception as e:
                        logger.warning(f"[{data_type}] 保存 storage_state 失败(不影响本次抓取): {e}")

                # 2. 进入目标页面
                logger.info(f"[{data_type}] 进入 {config.get('name', data_type)}...")
                await page.goto(page_url, wait_until="networkidle")
                await asyncio.sleep(3)

            # 3. 设置时间范围
            start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[{data_type}] 设置时间范围: {start_str} ~ {end_str}")

            # 3.1 如果配置了 time_labels(页面有多个时间筛选框),按标签精确查找
            time_labels = config.get("time_labels")
            if time_labels and len(time_labels) >= 2:
                await self._set_time_by_labels(page, time_labels[0], time_labels[1], start_str, end_str)
            else:
                # 3.2 通用策略:找页面上的日期范围选择器
                await self._set_time_range(page, start_str, end_str)
            await asyncio.sleep(1)

            # 4. 搜索
            logger.info(f"[{data_type}] 点击搜索...")
            await page.click('button:has-text("搜索")')
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            # 5. 导出
            logger.info(f"[{data_type}] 点击导出...")
            download_promise = page.wait_for_event("download", timeout=60000)
            await page.click('button:has-text("导出")')
            download = await download_promise
            logger.info(f"[{data_type}] 文件下载成功: {download.suggested_filename}")

            # 6. 保存
            original_filename = download.suggested_filename or f"{data_type}_export.csv"
            month_dir = datetime.now().strftime("%Y-%m")
            upload_dir = Path(settings.UPLOAD_DIR) / month_dir
            upload_dir.mkdir(parents=True, exist_ok=True)

            stem = Path(original_filename).stem
            suffix = Path(original_filename).suffix
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            dest_filename = f"{stem}_{ts}{suffix}"
            dest_path = upload_dir / dest_filename

            await download.save_as(str(dest_path))
            logger.info(f"[{data_type}] 文件已保存: {dest_path}")

            result.success = True
            result.file_path = f"{month_dir}/{dest_filename}"
            result.filename = dest_filename
        finally:
            await context.close()

    async def fetch_all(
        self,
        tasks: list[dict],
        on_progress: callable = None,
    ) -> list[FetchResult]:
        """批量抓取多个数据源 - 优化:按 base_url 分组复用登录 context

        原有行为兼容: 保持 BMS 1 分钟限制(两个源之间间隔 OPERATION_INTERVAL 秒)。
        优化内容:
          - 把同 base_url 的任务放在一组(如 ticket/wallet/device_combo 都是
            support.cloud-cat.cn, 组内共享同一个 browser context),组内第 1 个源登录后
            其余源直接跳转,省去重复登录耗时。
          - 不同 base_url 的组仍是独立 context(跨域不共享登录态)。
        """
        results_map: dict[str, FetchResult] = {}
        # 保留原始顺序(避免按 base_url 重排导致前端 Steps 错乱)
        original_order = [t["data_type"] for t in tasks]

        # 1. 按 base_url 分组(并保持每组分内的出现顺序)
        groups: list[tuple[str, list[dict]]] = []  # [(base_url, [task, ...]), ...]
        for t in tasks:
            base = t["config"].get("base_url", "")
            if not groups or groups[-1][0] != base:
                # 看是否之前已有同 base 的组
                found = False
                for existing_base, existing_list in groups:
                    if existing_base == base:
                        existing_list.append(t)
                        found = True
                        break
                if not found:
                    groups.append((base, [t]))
            else:
                groups[-1][1].append(t)

        logger.info(
            f"fetch_all 分组: 共 {len(tasks)} 个源, 拆为 {len(groups)} 个域名组: "
            + ", ".join(f"{b.split('//')[-1] if '//' in b else b}({len(ts)}个)" for b, ts in groups)
        )

        # 2. 依次处理每个组(复用 context)
        for group_idx, (base_url, group_tasks) in enumerate(groups):
            if not group_tasks:
                continue
            await self._init_browser()

            # ── storage_state 复用:组内首个源 requires_captcha 时优先加载已保存登录态 ──
            first_cfg = group_tasks[0].get("config", {})
            first_type = group_tasks[0].get("data_type", "")
            group_state_path = _storage_state_path(first_type)
            group_storage_state = None
            if first_cfg.get("requires_captcha") and group_state_path.exists():
                try:
                    group_storage_state = str(group_state_path)
                    logger.info(f"[组{group_idx+1}] 加载已保存登录态({first_type}): {group_state_path.name}")
                except Exception as e:
                    logger.warning(f"[组{group_idx+1}] 读取 storage_state 失败: {e}")
                    group_storage_state = None

            # 同组共享一个 context + 共享页面登录态
            context_kwargs = {
                "accept_downloads": True,
                "viewport": {"width": 1920, "height": 1080},
            }
            if group_storage_state:
                context_kwargs["storage_state"] = group_storage_state
            context = await self._browser.new_context(**context_kwargs)
            page = await context.new_page()

            # storage_state 登录态预校验(有 state 时先验证是否仍有效,避免后续每个源都试一次)
            state_logged_in = False
            if group_storage_state:
                try:
                    first_page_url = f"{first_cfg['base_url']}{first_cfg['page_path']}"
                    await page.goto(first_page_url, wait_until="networkidle")
                    await asyncio.sleep(2)
                    if "login" not in page.url.lower():
                        state_logged_in = True
                        logger.info(f"[组{group_idx+1}] 登录态有效(通过 storage_state)")
                    else:
                        logger.info(f"[组{group_idx+1}] 登录态已过期,需要重新登录")
                except Exception as e:
                    logger.warning(f"[组{group_idx+1}] 校验 storage_state 失败: {e}")

            logged_in_ok = state_logged_in  # 组内首个成功登录后为 True,后续跳过
            try:
                for i, task in enumerate(group_tasks):
                    data_type = task["data_type"]
                    start, end = task["start"], task["end"]
                    config = task["config"]
                    if on_progress:
                        await on_progress(data_type, "fetching", None)

                    fr = FetchResult()
                    fr.data_type = data_type
                    try:
                        # 单源抓取 + 复用登录态(组内首个源登录)
                        await asyncio.wait_for(
                            self._do_fetch_one_shared(
                                data_type=data_type,
                                start_date=start,
                                end_date=end,
                                config=config,
                                result=fr,
                                context=context,
                                page=page,
                                already_logged_in=logged_in_ok,
                            ),
                            timeout=180,
                        )
                        if fr.success:
                            logged_in_ok = True
                    except asyncio.TimeoutError:
                        fr.error = f"抓取超时(180秒): {config.get('name', data_type)}"
                        logger.error(fr.error)
                    except Exception as e:
                        fr.error = f"抓取失败: {e}"
                        logger.error(fr.error, exc_info=True)

                    results_map[data_type] = fr
                    if on_progress:
                        # 回调 phase=done 表示抓取阶段完成(之后是等待或导入)
                        await on_progress(data_type, "done", fr)

                    # 是否需要等待:
                    #  - 组内(同 base_url)两个源之间: 必须等 65 秒(同一个 BMS Web 系统 1 分钟查询限制)
                    #  - 跨组(不同域名): 两个不同 Web 系统,各自独立限流,无需等待
                    is_last_in_group = (i == len(group_tasks) - 1)
                    is_last_group = (group_idx == len(groups) - 1)
                    if not is_last_in_group:
                        # 组内后续源: 等待 65s (同域名 BMS 限制)
                        next_type = group_tasks[i + 1]["data_type"]
                        next_name = group_tasks[i + 1]["config"].get("name", next_type)
                        logger.info(
                            f"[同域名内] 还需等待 {OPERATION_INTERVAL} 秒后抓取 {next_name} "
                            f"(同一 BMS 系统查询/导出间隔 >= 1 分钟)"
                        )
                        if on_progress:
                            await on_progress(next_type, "waiting", None)
                        await asyncio.sleep(OPERATION_INTERVAL)
                    elif not is_last_group:
                        # 组与组之间是不同域名(如 wifi → support),属于两个不同 Web,无需等 65s
                        next_group = groups[group_idx + 1]
                        next_first = next_group[1][0]
                        next_type = next_first["data_type"]
                        next_name = next_first["config"].get("name", next_type)
                        logger.info(
                            f"[跨域名跳过等待] 下一个源 {next_name} 是不同 BMS 域名,直接切换不等待"
                        )
                        # 直接进入下一组,不 sleep
            finally:
                await context.close()

        # 3. 按 original_order 组装 results
        return [results_map[dt] for dt in original_order if dt in results_map]

    async def _do_fetch_one_shared(
        self,
        *,
        data_type: str,
        start_date: datetime,
        end_date: datetime,
        config: dict,
        result: FetchResult,
        context,  # 同组共享
        page,     # 同组共享
        already_logged_in: bool,
    ):
        """复用 context/page 版的单源抓取流程(同域名共享登录态)

        - already_logged_in=True: 跳过登录,直接 goto 目标页
        - already_logged_in=False: 执行登录并验证成功后才进入页面
        """
        if not config or not config.get("base_url"):
            result.error = f"数据源未配置或缺少 base_url: {data_type}"
            return
        if not config.get("username") or not config.get("password"):
            result.error = f"数据源未配置账号密码: {data_type}(请在系统管理-同步设置中配置)"
            return

        base_url = config["base_url"]
        username = config["username"]
        password = config["password"]
        login_url = f"{base_url}/index.html#/login"
        page_url = f"{base_url}{config['page_path']}"

        if not already_logged_in:
            # ── storage_state 预登录验证(在 fetch_all 已做,但作为兜底再检查) ──
            via_state = False
            state_path = _storage_state_path(data_type)
            if config.get("requires_captcha") and state_path.exists():
                # context 创建时已注入 storage_state,检查是否已处于登录页之外
                try:
                    await page.goto(page_url, wait_until="networkidle")
                    await asyncio.sleep(2)
                    if "login" not in page.url.lower():
                        via_state = True
                        logger.info(f"[{data_type}] 登录态验证通过,跳过登录")
                except Exception:
                    pass

            if not via_state:
                # 组内首个源: 登录
                logger.info(f"[{data_type}] (首次,登录) 正在登录 {base_url}...")
                await page.goto(login_url, wait_until="networkidle")
                await asyncio.sleep(2)
                await page.fill('input[type="text"], input[placeholder*="账号"], input[placeholder*="用户名"]', username)
                await page.fill('input[type="password"], input[placeholder*="密码"]', password)

                # ── 闪猫:验证码检测(需要前端交互登录) ──
                if config.get("requires_captcha"):
                    captcha_input = await page.query_selector(
                        'input[placeholder*="验证码"], input[aria-label*="验证码"]'
                    )
                    if captcha_input and await captcha_input.is_visible():
                        raise RuntimeError(
                            "需要验证码登录:请前往「数据中心-数据采集-同步设置」点击「登录闪猫」按钮完成验证码登录"
                        )

                await asyncio.sleep(1)
                await page.click('button:has-text("登录"), button[type="submit"]')
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(3)
                if "login" in page.url.lower():
                    error_elem = await page.query_selector(".el-message__content, .el-form-item__error")
                    if error_elem:
                        error_text = await error_elem.inner_text()
                        raise RuntimeError(f"登录失败: {error_text}")
                    raise RuntimeError("登录失败: 账号或密码错误")

                # ── 登录成功:保存 storage_state(下次自动复用) ──
                if config.get("requires_captcha"):
                    try:
                        await context.storage_state(path=str(state_path))
                        logger.info(f"[{data_type}] 登录态已保存至 {state_path.name}")
                    except Exception as e:
                        logger.warning(f"[{data_type}] 保存 storage_state 失败(不影响本次抓取): {e}")

                logger.info(f"[{data_type}] 登录成功(后续同组源复用登录态)")
        else:
            # 复用登录态: 不重新登录,直接跳页
            logger.info(f"[{data_type}] 复用登录态,直接跳转目标页")

        # 2. 进入目标页面
        logger.info(f"[{data_type}] 进入 {config.get('name', data_type)}...")
        await page.goto(page_url, wait_until="networkidle")
        await asyncio.sleep(3)

        # 3. 设置时间范围
        start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[{data_type}] 设置时间范围: {start_str} ~ {end_str}")

        time_labels = config.get("time_labels")
        if time_labels and len(time_labels) >= 2:
            await self._set_time_by_labels(page, time_labels[0], time_labels[1], start_str, end_str)
        else:
            await self._set_time_range(page, start_str, end_str)
        await asyncio.sleep(1)

        # 4. 搜索
        logger.info(f"[{data_type}] 点击搜索...")
        await page.click('button:has-text("搜索")')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)

        # 5. 导出
        logger.info(f"[{data_type}] 点击导出...")
        download_promise = page.wait_for_event("download", timeout=60000)
        await page.click('button:has-text("导出")')
        download = await download_promise
        logger.info(f"[{data_type}] 文件下载成功: {download.suggested_filename}")

        # 6. 保存
        original_filename = download.suggested_filename or f"{data_type}_export.csv"
        month_dir = datetime.now().strftime("%Y-%m")
        upload_dir = Path(settings.UPLOAD_DIR) / month_dir
        upload_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(original_filename).stem
        suffix = Path(original_filename).suffix
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        dest_filename = f"{stem}_{ts}{suffix}"
        dest_path = upload_dir / dest_filename

        await download.save_as(str(dest_path))
        logger.info(f"[{data_type}] 文件已保存: {dest_path}")

        result.success = True
        result.file_path = f"{month_dir}/{dest_filename}"
        result.filename = dest_filename

    async def test_connection(self) -> bool:
        """测试浏览器是否可用"""
        try:
            await self._init_browser()
            page = await self._browser.new_page()
            await page.goto("about:blank")
            await page.close()
            return True
        except Exception:
            return False
