"""设备月流量同步服务 - 单 SN × 按月 拉取并入库(设备审计)

来源: BMS 设备流量页 HTTP JSON API(与 ai_acc_fetcher 同思路, 但 BMS 需浏览器登录态):
  GET https://wifi.cloud-cat.cn/bms-portal/deviceFlow/sn/pageList
      ?page=1&rows=<n>&sn=<SN>&timeType=2&flowDate=<YYYY-MM 可选>
  → 200 {"code":"0","data":{"total":N,"totalFlow":X,"items":[{sn,businessId,businessName,
        flowDate,flowValue(GB),deviceTypeName,deviceGroupName,deviceComboName}]}}

语义(实测 2026-09-05):
  - timeType=2 按月; flowDate 留空 = 返回该 SN 所有有流量月份(每行一月, 倒序可多月)
  - flowDate=YYYY-MM 只查指定月
  - 当月未完整月份可反复重取覆盖(不完整月统计会随日期推进更新)

实现:
  1. Playwright 登录 wifi BMS(复用 bms-wifi 组凭证, storage_state 缓存登录态)
  2. goto 设备流量页后在页面内 fetch API(自动带登录 cookie), 拿 JSON items
  3. 构造 DataFrame → run_upsert("device_flow_usage") 幂等入库 (device_sn+stat_month)
  4. 返回 {sn, month, total_rows, inserted, updated, total_flow_gb, months}

说明: 该源为"按需/审计触发", 不纳入定时同步(BMS_DATA_SOURCES.default_sync=False)。
"""

import asyncio
import logging
from pathlib import Path

import pandas as pd

from app.core.config import settings

logger = logging.getLogger(__name__)

# 设备流量查询 API(BMS 后台 REST 在根路径 /bms-portal, 与前端页面 /bms/ 不同前缀)
# ⚠️ 实测: 前端 https://wifi.cloud-cat.cn/bms/index.html#/device/device-flow
#           API  https://wifi.cloud-cat.cn/bms-portal/deviceFlow/sn/pageList
FLOW_API_BASE = "https://wifi.cloud-cat.cn/bms-portal"
FLOW_API_PATH = "/deviceFlow/sn/pageList"
# 设备流量页路由(登录后进页, 用于建立会话上下文; API 本身只需同源 cookie)
FLOW_PAGE_URL = "https://wifi.cloud-cat.cn/bms/index.html#/device/device-flow"
# 单次拉取行数上限(有流量月份通常很少, 设大防截断)
PAGE_ROWS = 500


def _storage_state_path() -> Path:
    base = Path(settings.BROWSER_STATE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base / "device_flow_usage.json"


async def _dismiss_modal_dialogs(page, max_rounds: int = 3) -> None:
    """关闭 BMS 登录后可能弹出的 Element UI 模态提示框(el-message-box)。

    背景(2026-09-06): wifi 域登录成功后会弹「提示」确认框(el-message-box__wrapper),
    遮挡后续按钮点击 → device_flow 重新登录时 fetch_device_flow 在
    `page.click('button:has-text("登录")')` 处被拦截 30s 超时。
    处理(与 bms_fetcher._dismiss_modal_dialogs 同思路, 最小实现):
      1. 点 .el-message-box__btns 内"确定/知道了/关闭"类确认按钮;
      2. 仍残留 → JS 移除 .el-message-box__wrapper(仅提示框, 不碰 el-dialog 业务面板)。
    """
    import re

    try:
        for _round in range(max_rounds):
            try:
                n = await page.locator(".el-message-box__wrapper:visible").count()
            except Exception:
                n = 0
            if n == 0:
                return
            # (1) 点确认按钮(Element UI MessageBox 按钮)
            clicked = False
            for sel in (".el-message-box__btns button:visible", ".el-message-box button:visible"):
                try:
                    btns = page.locator(sel)
                    cnt = await btns.count()
                    for i in range(cnt):
                        txt = (await btns.nth(i).text_content() or "").strip()
                        # 只点"接受/确认"语义按钮关闭提示; 破坏性(删除/危险)不点
                        if txt and not re.search(r"删除|危险|确定删除", txt):
                            await btns.nth(i).click(timeout=3000)
                            clicked = True
                            break
                    if clicked:
                        break
                except Exception:
                    continue
            if clicked:
                await page.wait_for_timeout(600)
                continue
            # (2) JS 移除 message-box 容器(仅此类)
            try:
                await page.evaluate(
                    """() => { document.querySelectorAll('.el-message-box__wrapper').forEach(el => el.remove()); }"""
                )
            except Exception:
                pass
            await page.wait_for_timeout(400)
    except Exception as e:  # noqa: BLE001 弹框关闭失败不影响后续
        logger.warning(f"[device_flow] 模态提示框关闭异常(忽略): {e}")


async def fetch_device_flow(
    sn: str,
    month: str | None = None,
    username: str | None = None,
    password: str | None = None,
    base_url: str = "https://wifi.cloud-cat.cn/bms",
) -> list[dict]:
    """拉取单 SN 月流量明细(登录一次, 复用 storage_state; 失效自动剔除重试)

    Args:
        sn: 设备 SN(必填, 上游一次只能一个 SN)
        month: "YYYY-MM"; None = 该 SN 全量有流量月份
    Returns:
        items: [{sn, businessId, businessName, flowDate, flowValue, ...}]

    健壮性(2026-09-06 修复): 已保存登录态(storage_state)可能过期/失效, 带失效
    cookie 访问 login 页会干扰登录(点登录后 url 仍在 login → 误报"凭证错误")。
    处理: 首次带 state 尝试; 若登录失败(url 仍 login) → 剔除失效 state, 用全新
    context 重试一次(此时才真正确认凭证是否正确)。
    """
    from playwright.async_api import async_playwright

    state_path = _storage_state_path()

    class _LoginFailed(RuntimeError):
        pass

    async def _ensure_logged_in(page, context) -> None:
        """进入登录页; 已登录(state 有效)直接返回; 否则表单登录; 失败抛 _LoginFailed。"""
        await page.goto(f"{base_url}/index.html#/login", wait_until="domcontentloaded")
        # 显式等待登录表单(SPA hash 路由, 用 domcontentloaded + 轮询)
        try:
            await page.wait_for_selector(
                'input[name="username"], input[placeholder*="账号" i]',
                state="visible", timeout=10000,
            )
        except Exception:
            await asyncio.sleep(2)
        # 若已登录(storage_state 有效, 登录页会自动跳走)则直接返回
        if "login" not in page.url.lower():
            logger.info("device_flow 复用已保存登录态")
            return
        # 表单登录
        user_sel = (
            'input[name="username"]:not([readonly]):not([disabled]),'
            'input[placeholder*="账号" i]:not([readonly]):not([disabled]),'
            'input[type="text"]:not([readonly]):not([disabled])'
        )
        try:
            await page.locator(user_sel).first.fill(username or "", timeout=15000)
        except Exception:
            await page.locator("input:visible:not([readonly]):not([disabled])").first.fill(username or "", timeout=15000)
        pwd_sel = 'input[type="password"], input[placeholder*="密码" i]'
        await page.locator(pwd_sel).first.fill(password or "", timeout=15000)
        # ⚠️ 2026-09-06: 登录按钮可能被已弹出的「提示」框(el-message-box)遮挡
        await _dismiss_modal_dialogs(page)
        await page.click('button:has-text("登录"), button[type="submit"]')
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(3)
        # 登录成功常弹「提示」框, 关闭避免遮挡后续导航/操作
        await _dismiss_modal_dialogs(page)
        if "login" in page.url.lower():
            raise _LoginFailed("设备流量采集登录失败(请检查 bms-wifi 组凭证)")
        # 登录成功存 storage_state(下次免登)
        try:
            await context.storage_state(path=str(state_path))
            logger.info("device_flow 登录成功, 登录态已保存")
        except Exception as e:
            logger.warning(f"保存 device_flow storage_state 失败: {e}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        last_err: Exception | None = None
        for attempt in (1, 2):
            # 第 2 次重试前剔除失效 state(全新 context 不带旧 cookie 登录)
            if attempt == 2:
                try:
                    state_path.unlink(missing_ok=True)
                    logger.warning("device_flow 登录态失效, 剔除后全新登录重试")
                except Exception:
                    pass
            state = str(state_path) if state_path.exists() else None
            context = await browser.new_context(
                storage_state=state if state else None,
                timezone_id="Asia/Shanghai",  # ⚠️ BMS 前端用浏览器本地时间, 必须北京时区(经验)
            )
            page = await context.new_page()
            try:
                await _ensure_logged_in(page, context)
                # ── 进设备流量页(建立会话上下文) ──
                await page.goto(FLOW_PAGE_URL, wait_until="domcontentloaded")
                await asyncio.sleep(2)
                await _dismiss_modal_dialogs(page)

                # ── 页面内 fetch API(自动带登录 cookie) ──
                params = f"page=1&rows={PAGE_ROWS}&sn={sn}&timeType=2"
                if month:
                    params += f"&flowDate={month}"
                api_url = f"{FLOW_API_BASE}{FLOW_API_PATH}?{params}"
                payload = await page.evaluate(
                    """async (url) => {
                        const r = await fetch(url, {headers: {'Accept': 'application/json'}});
                        if (!r.ok) return {httpError: r.status, body: (await r.text()).slice(0, 300)};
                        return await r.json();
                    }""",
                    api_url,
                )
                if payload.get("httpError"):
                    raise RuntimeError(f"设备流量 API 请求失败 HTTP {payload['httpError']}: {payload.get('body')}")
                code = payload.get("code")
                if code not in ("0", 0, 0.0):
                    raise RuntimeError(f"设备流量 API 返回业务错误 code={code}: {str(payload)[:300]}")
                data = payload.get("data") or {}
                items = data.get("items") or []
                return items
            except _LoginFailed as e:
                last_err = e
                logger.warning(f"device_flow 登录失败(第 {attempt} 次): {e}")
                continue  # 剔除失效 state 后重试
            finally:
                await context.close()
        # 两次都失败(第 2 次为全新登录仍失败 = 凭证确实有问题)
        raise RuntimeError(str(last_err or "设备流量采集登录失败"))


def _items_to_dataframe(items: list[dict]) -> pd.DataFrame:
    """API items → DataFrame(列名与 upsert 模板对齐)"""
    rows = []
    for it in items:
        rows.append({
            "sn": it.get("sn"),
            "business_id": it.get("businessId"),
            "business_name": it.get("businessName"),
            "device_type_name": it.get("deviceTypeName"),
            "device_group_name": it.get("deviceGroupName"),
            "device_combo_name": it.get("deviceComboName"),
            "flow_date": it.get("flowDate"),
            "flow_value": it.get("flowValue"),
        })
    return pd.DataFrame(rows)


def sync_device_flow_sn(
    sn: str,
    month: str | None = None,
    username: str | None = None,
    password: str | None = None,
    base_url: str = "https://wifi.cloud-cat.cn/bms",
) -> dict:
    """同步单个 SN 的月流量到本地(同步入口, 内部跑事件循环 + upsert)

    Returns:
        {sn, month, fetched_rows, inserted, updated, total_flow_gb, months, message}
    """
    from app.core.database import SessionLocal

    items = asyncio.run(fetch_device_flow(
        sn=sn, month=month, username=username, password=password, base_url=base_url
    ))
    if not items:
        return {
            "sn": sn, "month": month, "fetched_rows": 0,
            "inserted": 0, "updated": 0, "total_flow_gb": 0.0,
            "months": [], "message": "上游未返回该 SN 的流量记录(可能无流量或无权限)",
        }
    df = _items_to_dataframe(items)
    db = SessionLocal()
    try:
        from app.services.data_parser import suggest_mapping
        from app.services.upsert import run_upsert

        mapping = suggest_mapping("device_flow_usage", list(df.columns))
        result = run_upsert(df, "device_flow_usage", mapping, db)
        months = sorted({str(it.get("flowDate")) for it in items if it.get("flowDate")})
        total_flow = sum(float(it.get("flowValue") or 0) for it in items)
        return {
            "sn": sn,
            "month": month,
            "fetched_rows": len(items),
            "inserted": result.inserted,
            "updated": result.updated,
            "total_flow_gb": round(total_flow, 4),
            "months": months,
            "message": f"入库成功: 新增 {result.inserted}, 更新 {result.updated}",
        }
    finally:
        db.close()
