"""BMS 平台数据抓取服务

支持 6 个数据源的抓取:
#   - refund (退费单): https://support.cloud-cat.cn/bms/index.html#/pay/refund-manage
  - ticket (服务请求): https://support.cloud-cat.cn/bms/index.html#/service-request/service-request-manage
  - wallet (提现操作): https://support.cloud-cat.cn/bms/index.html#/settle/customerWalletOperationRecord
  - device_combo (设备套餐): https://support.cloud-cat.cn/bms/index.html#/device/device-combo
  - cs_record (智能助手处理): http://ai-acc.cloud-cat.cn (HTTP API 导出)
  - shanmao_refund (闪猫退款): https://fast.cloud-cat.cn/sc_brm/#/order-alrrefundapply-alrrefundapply

注意:
  - 账号密码从 sync_source_configs 表读取(加密存储),按源独立配置
  - BMS 主平台(support.cloud-cat.cn) 的 refund/ticket/wallet/device_combo/device_tag_binding 共用同一登录态
  - 闪猫(fast.cloud-cat.cn) 是独立平台,登录需验证码,采用 storage_state 复用机制
  - BMS 系统限制:1分钟内不能连续做查询导出下载动作
  - BMS_DATA_SOURCES 现仅作为默认种子(首次启动写入 DB),运行时配置以 DB 为准
  - shanmao_refund (闪猫) 登录需验证码,缺失时走前端交互验证码登录
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.core.config import settings

# 验证码自动识别(懒加载,首次调用才 import ddddocr)
# 失败时 fallback 为 None(走人工/报错)
# 除了 try_ocr_captcha(字符串版),还加载带置信度版 + 常量阈值
# P1-2: 新增数学计算式验证码识别(闪猫/SCRM 等平台使用 7*0=? / 9-5=? 形式)
try:
    from app.services.captcha_session import (  # type: ignore
        try_ocr_captcha,
        try_ocr_captcha_with_confidence,
        try_ocr_math_captcha,
        try_ocr_math_captcha_with_confidence,
        _OCR_MIN_CHAR_PROB,
        _OCR_AVG_PROB,
        _OCR_PRECHECK_MAX_REFRESH,
    )
except Exception as _e_import_ocr:  # pragma: no cover - 导入失败不影响运行,只是 OCR 自动识别不可用
    try_ocr_captcha = None  # type: ignore
    try_ocr_captcha_with_confidence = None  # type: ignore
    try_ocr_math_captcha = None  # type: ignore
    try_ocr_math_captcha_with_confidence = None  # type: ignore
    _OCR_MIN_CHAR_PROB = 0.72
    _OCR_AVG_PROB = 0.86
    _OCR_PRECHECK_MAX_REFRESH = 20
    logging.getLogger(__name__).warning(
        f"加载 captcha_session.try_ocr_captcha 失败({_e_import_ocr!r}),"
        f" 闪猫等需验证码登录的数据源将无法自动识别验证码,需要手动登录。"
    )

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
        "base_url": "https://wifi.cloud-cat.cn/bms",
        "page_path": "/index.html#/service-request/service-request-manage",
        "default_sync": True,
        # ⚠️  2026-08-06 修复: 原默认 time_labels=["起始完成时间","结束完成时间"] 会导致两类严重错误:
        #     (1) 今天登记但尚未完成 / status != 已完成 / finish_time IS NULL 的工单 100% 漏同步(含当日新增投诉工单)
        #     (2) 老工单(上周/上月登记) 因 BMS 凌晨 03:15 批量覆盖 finish_time 被算进今日, 导致同步数据"看起来完成很多但其实是旧记录被重算"
        #  改按 "起始登记时间 / 结束登记时间" 拉取:
        #     - 好处 1: 登记时间 只要工单进入系统就一定会写, 不会漏掉"今天新登记的任何工单"
        #     - 好处 2: 对已经导入的历史老工单, 后续的状态/备注更新 依靠 upsert(ticket_no 唯一) 自动覆盖, 不会因时间窗口而丢字段
        #  兼容说明: BMS 服务请求管理页面"登记时间"筛选框的中文标签在不同版本存在两种写法("起始登记时间"或直接"登记时间"),
        #  _set_time_by_labels 内部用 has_text() 包含匹配(不是完全相等), 所以 ["起始登记时间","结束登记时间"] 两种写法都能命中.
        #  若以后 BMS 升级改标签名, 只需更新 sync_source_configs 对应行 time_labels 字段 (无需再改代码默认种子).
        "time_labels": ["起始登记时间", "结束登记时间"],
    },
    "wallet": {
        "name": "提现操作记录",
        "base_url": "https://wifi.cloud-cat.cn/bms",
        "page_path": "/index.html#/settle/customerWalletOperationRecord",
        "default_sync": True,
    },
    "device_combo": {
        "name": "设备套餐",
        "base_url": "https://wifi.cloud-cat.cn/bms",
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
        # ⚠️  闪猫平台日期筛选组件 class=el-date-editor--datetimerange
        #     (Element UI 日期+时间范围选择器,两个 .el-range-input 输入框左右排列),
        #     必须填完整「YYYY-MM-DD HH:mm:ss」(否则 Vue 组件不接受,清空为 '',导出会拿到历史全量 1976 行)。
        #     同时要防止 Vue 渲染稍慢导致 query_selector('.el-date-editor--datetimerange') 首轮失败,
        #     策略1已加最多 6s 轮询;策略4也加了「优先命中 el-range-input 所在行」。
        "time_format": "%Y-%m-%d %H:%M:%S",
    },
    # BMS 设备标签管理页面(https://support.cloud-cat.cn/bms/index.html#/device/device-tags)
    # ⚠️  注意: 该页面点击「导出」导出的并不是「标签定义字典」(device_tag),
    # 而是每台设备被打标签的明细行(设备SN × 标签名 × 打标人 × 更新时间),
    # 对应数据源 code=device_tag_binding。标签定义(device_tag)从明细里按 tag_name 自动去重补插
    # (见 upsert_device_tag_bindings 里 resolve_tag_id 临时插 DeviceTag 行的逻辑),
    # 且可在本系统侧手动维护,不再做 BMS 自动同步。
    # ⚠️  2026-08-11 修复: base_url 从 wifi.cloud-cat.cn 统一到 support.cloud-cat.cn
    #   (两域名指向同一套 BMS 系统,统一后可与 refund/ticket/wallet 共享登录态,避免单独登录)
    "device_tag_binding": {
        "name": "设备标签(打标明细)",
        "base_url": "https://wifi.cloud-cat.cn/bms",
        "page_path": "/index.html#/device/device-tags",
        "default_sync": False,  # 标签绑定明细变动少,按需手动同步
    },
}

# 每次操作之间的最小间隔(秒),BMS 系统限制1分钟
OPERATION_INTERVAL = 65

# 浏览器登录态保存目录(复用 storage_state 跳过验证码登录)
# ⚠️ 独立于 UPLOAD_DIR: 不在 /uploads 静态挂载下, 避免登录 cookie 被任意下载(会话劫持)
# 目录结构: settings.BROWSER_STATE_DIR/{data_type}.json
def _storage_state_path(data_type: str) -> Path:
    """获取某数据源的 storage_state 文件路径(登录态单独存于 BROWSER_STATE_DIR)"""
    base = Path(settings.BROWSER_STATE_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{data_type}.json"


def _parse_filename_utc_ts(filename: str) -> datetime | None:
    """从 BMS 导出文件名中解析内嵌的 UTC 时间戳。

    文件名形如: 退费单导出列表-2026-09-01_01_40_25_108_20260901014025.csv
    前半段 `2026-09-01_01_40_25` 是 BMS 服务端生成导出时的 UTC 时刻
    (容器内 Playwright 浏览器时区 = UTC, BMS 前端把该时刻写进文件名)。
    解析失败返回 None(不阻塞主流程)。
    """
    try:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})_(\d{2})_(\d{2})", filename)
        if not m:
            return None
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
    except Exception:
        return None


# BMS 网页版时间填入的时区开关。
# 背景(2026-09-01 三次排查后定案): 稳定 8h 滞后的**真正根因是容器 Playwright 浏览器时区 = UTC**。
#   BMS 前端 Vue DatePicker 用浏览器本地时间作为"当前时刻"并据此截断查询窗口:
#   - 容器浏览器 UTC → BMS 认为当前是 UTC(北京-8h) → 即使填入正确北京时间 end,
#     也会被截断到 8h 前 → 数据只到「执行时刻 UTC」=「北京当前-8h」
#   - 用户手动在 BMS 页面导出(浏览器=北京) → 当前时刻正确 → 数据完整
# 修复: 在 new_context 设置 timezone_id='Asia/Shanghai'(两处), 让 BMS 前端当前时刻=北京。
# 在此前提下, 填入 BMS 的时间字符串**应保持北京时间**(BMS 按字面值解析), 即 fill_utc=False。
# ⚠️ 曾误判: 之前以为 BMS 按浏览器时区解析输入字符串 → 把北京时间转 UTC 填入(fill_utc=True),
#   反而让生产同步窗口变成 8h 前(只拿到凌晨 51 行)。方向反了, 已改回 False。
# 每源仍可经 config time_fill_utc 覆盖。
_BMS_FILL_TZ_UTC_DEFAULT = False


def _bms_time_str(dt: datetime, time_format: str = "%Y-%m-%d %H:%M:%S", fill_utc: bool = True) -> str:
    """生成填入 BMS 日期输入框的时间字符串。

    - fill_utc=True: 把北京 aware 时间转成 UTC 再格式化(已废弃, 曾误判为修复)。
    - fill_utc=False(默认): 按 dt 自身时区格式化(北京时间)。配合浏览器时区=Asia/Shanghai,
      BMS 按字面北京时间解析 + 当前时刻=北京 → 窗口正确。
    """
    if fill_utc and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).strftime(time_format)
    return dt.strftime(time_format)


def _crop_bounds_naive(start_date: datetime, end_date: datetime):
    """把裁剪边界统一转成 naive 北京时间 pandas Timestamp, 用于与文件内时间列比较。

    背景(2026-09-01): BMS/闪猫导出文件里的时间列(创建时间/审核时间/申请时间等)
    是 naive 北京时间字符串(如 "2026-08-30 21:44:00"), 而传入的 start_date/end_date
    是 aware UTC+8 datetime。原实现 `pd.to_datetime(start_date)` 得到 aware Timestamp,
    与 naive 的列值比较会抛 `TypeError: Cannot compare tz-naive and tz-aware` →
    被 except 吞 → mask=None → **裁剪从未真正生效(全量保留)**。

    修复: 把边界按北京时区转成 naive 字面值(与列值同语义), 比较才有效。
    返回值: (start_naive, end_naive) 均为 naive pd.Timestamp。
    """
    import pandas as pd
    TZ_CN = timezone(timedelta(hours=8))

    def _to_naive(dt: datetime):
        if dt.tzinfo is not None:
            dt = dt.astimezone(TZ_CN).replace(tzinfo=None)
        return pd.Timestamp(dt)

    return _to_naive(start_date), _to_naive(end_date)


# ──────────────────────────────────────────────────────────────────────
# P1-1: 导出下载超时控制 + 自动重试
#       - wallet(提现操作记录):数据量大,导出通常需要 3-10 分钟,给 600s 超时 + 最多 2 次重试
#       - shanmao_refund(闪猫退款):文件不大(~172KB),默认 90s 即可,但给 1 次重试对付偶发 500
#       - 其他(refund/ticket/device_combo):通常 90s 够用,给 1 次重试
# ──────────────────────────────────────────────────────────────────────
_DOWNLOAD_TIMEOUT_MS: dict[str, int] = {
    "wallet": 600_000,       # 10 分钟
    "shanmao_refund": 90_000,
    "refund": 180_000,       # 退费单也经常较大,放宽到 3 分钟
    "ticket": 180_000,
    "device_combo": 90_000,
    "device_tag": 90_000,
    "device_tag_binding": 90_000,
}
_DOWNLOAD_DEFAULT_TIMEOUT_MS = 120_000  # 未知类型默认 2 分钟
_DOWNLOAD_MAX_RETRIES: dict[str, int] = {
    "wallet": 2,
    "shanmao_refund": 1,
    "refund": 1,
    "ticket": 1,
}
_DOWNLOAD_DEFAULT_RETRIES = 1


async def _click_export_button(page) -> None:
    """点击「导出」按钮,找不到标准按钮时 fallback 遍历所有可见按钮"""
    import logging as _lg
    _log = _lg.getLogger(__name__)
    _log.info("  [导出按钮] 开始定位导出按钮...")
    try:
        await page.click('button:has-text("导出")', timeout=15000)
        _log.info("  [导出按钮] 标准 selector 命中并点击成功")
        return
    except Exception as e:
        _log.info(f"  [导出按钮] 标准 selector 未命中: {type(e).__name__}, 改为遍历所有按钮")
    btns = await page.query_selector_all('button:visible')
    _log.info(f"  [导出按钮] 页面可见按钮数: {len(btns)}")
    clicked = False
    for idx, b in enumerate(btns):
        try:
            t = (await b.inner_text()).strip()
        except Exception:
            t = ""
        if "导出" in t and "重置" not in t:
            _log.info(f"  [导出按钮] 遍历命中第 {idx} 个: text={t!r}, 准备点击")
            try:
                await b.click(timeout=15000)
                clicked = True
                _log.info(f"  [导出按钮] 遍历点击成功")
                break
            except Exception as ce:
                _log.warning(f"  [导出按钮] 遍历点击失败: {ce}")
                continue
    if not clicked:
        # 列出所有按钮文本,方便诊断
        all_texts = []
        for b in btns:
            try:
                all_texts.append((await b.inner_text()).strip())
            except Exception:
                pass
        _log.warning(f"  [导出按钮] 未找到导出按钮! 所有按钮文本: {all_texts[:20]}")
        raise RuntimeError("未找到导出按钮")


async def _download_with_retry(
    page,
    data_type: str,
    logger_obj,
    expect_end_dt: datetime | None = None,
):
    """封装 Playwright 导出下载:按 data_type 配置超时 + 最多 N 次重试。

    每次重试都会:
      1. 重新等待 download 事件(新的超时窗口)
      2. 重新点「导出」按钮(避免上次失败事件未触发后,按钮点了没反应)
    重试间隔 2 秒,避免给服务器太大压力。

    ⚠️ 2026-08-11 修复: BMS 导出有"1分钟内请勿重复操作"限流
      - 监听 export 响应,如果返回 JSON({"success":false,...})说明被限流
      - 被限流时不会触发 download 事件,需等待 60s+ 后重试
      - wallet 的 max_retries=2,每次 600s 超时,足够等待限流解除

    Args:
        expect_end_dt: 期望窗口结束时间(北京时间 aware, 即本次要拉的 end)。
            用于校验 BMS 返回文件名里内嵌的 UTC 时间戳是否与期望窗口吻合,
            记录告警(不改变流程)——用于监控"8小时轮次滞后"是否仍存在。
    """
    timeout_ms = _DOWNLOAD_TIMEOUT_MS.get(data_type, _DOWNLOAD_DEFAULT_TIMEOUT_MS)
    max_retries = _DOWNLOAD_MAX_RETRIES.get(data_type, _DOWNLOAD_DEFAULT_RETRIES)
    last_exc: Exception | None = None

    for attempt in range(0, max_retries + 1):
        download_task: asyncio.Task | None = None
        on_response_removed = False

        def _remove_response_listener(p):
            nonlocal on_response_removed
            if on_response_removed:
                return
            try:
                p.remove_listener("response", _on_response)
            except Exception:
                pass
            on_response_removed = True

        def _drain_task(t: asyncio.Task | None):
            """取消 task 并消费其结果/异常,避免「Task exception was never retrieved」噪音"""
            if t is None or t.done():
                return
            try:
                t.cancel()
            except Exception:
                pass
            # 不 await(避免阻塞重试),只挂回调消费结果
            def _cb(fut):
                try:
                    fut.result()
                except (asyncio.CancelledError, Exception):
                    pass
            t.add_done_callback(_cb)

        try:
            if attempt > 0:
                logger_obj.info(
                    "[%s] 下载重试第 %d/%d 次(上次失败:%s),等待 %ds 后重新点导出...",
                    data_type, attempt, max_retries,
                    last_exc, timeout_ms // 1000,
                )
                await asyncio.sleep(2)
            logger_obj.info("[%s] [%d/%d] 注册 download 事件监听(timeout=%ds)...",
                            data_type, attempt + 1, max_retries + 1, timeout_ms // 1000)
            # ⚠️ 每次重试都为 wait_for_event 创建独立 Task(= ensure_future),
            #   避免历史上裸协程复用 → "cannot reuse already awaited coroutine" 报错
            download_task = asyncio.create_task(
                page.wait_for_event("download", timeout=timeout_ms)
            )

            # ★ 监听 export 响应:如果返回 JSON(限流),提前抛错触发重试
            export_rate_limited = {"hit": False, "msg": ""}

            def _on_response(resp):
                try:
                    if "/export" in resp.url.lower() and resp.request.method == "GET":
                        ctype = (resp.headers.get("content-type") or "").lower()
                        if "json" in ctype:
                            # JSON 响应 = 被限流(或异常),不会触发 download 事件
                            export_rate_limited["hit"] = True
                            async def _read_body():
                                try:
                                    body = await resp.text()
                                    export_rate_limited["msg"] = body[:200]
                                except Exception:
                                    export_rate_limited["msg"] = "(body 读取失败)"
                            # 用 create_task 挂后台,避免同步回调里直接 await 协程
                            asyncio.create_task(_read_body())
                except Exception:
                    pass
            page.on("response", _on_response)

            # 每次尝试都重新点导出(上次可能只是注册了事件但按钮没真的点到/服务器没反应)
            logger_obj.info("[%s] [%d/%d] 开始点击导出按钮...",
                            data_type, attempt + 1, max_retries + 1)
            await _click_export_button(page)
            logger_obj.info("[%s] [%d/%d] 导出按钮已点击,等待 download 事件触发...",
                            data_type, attempt + 1, max_retries + 1)

            # 轮询等待 download 事件,同时检测限流响应
            download = None
            poll_start = asyncio.get_event_loop().time()
            while True:
                # 检查限流
                if export_rate_limited["hit"]:
                    _remove_response_listener(page)
                    _drain_task(download_task)
                    raise RuntimeError(
                        f"BMS 导出返回 JSON(疑似限流或异常): {export_rate_limited['msg']}"
                    )
                # 检查 download 事件是否已触发(用 shield+短 timeout 轮询)
                try:
                    download = await asyncio.wait_for(asyncio.shield(download_task), timeout=2.0)
                    break
                except asyncio.TimeoutError:
                    # 2 秒内没触发,继续轮询(只要总时间没超 timeout_ms)
                    elapsed = asyncio.get_event_loop().time() - poll_start
                    if elapsed * 1000 > timeout_ms:
                        _drain_task(download_task)
                        raise RuntimeError(f"等待 download 事件超时({int(elapsed)}s)")
                    continue

            _remove_response_listener(page)
            logger_obj.info(
                "[%s] 文件下载成功(尝试 %d/%d):%s",
                data_type, attempt + 1, max_retries + 1, download.suggested_filename,
            )
            # ── 2026-09-01: 文件名内嵌 UTC 时间戳 vs 当前时刻交叉校验(监控用) ──
            #   BMS 文件名形如 退费单导出列表-2026-09-01_01_40_25_xxx.csv, 前半段为导出任务
            #   生成的 UTC 时刻(容器浏览器时区=UTC)。正常情况它应 ≈ 当前 UTC 时刻。
            #   若文件名时间戳比当前 UTC 落后 ≥2h, 说明 download 事件命中的是"历史轮次的旧导出缓存"
            #   (BMS 导入导出中心复用旧任务), 而不是本次点击生成的新导出。
            #   这里仅记录告警(不改变流程), 便于核对 fill_utc 修复是否生效 / 是否偶发命中旧缓存。
            try:
                fname_ts = _parse_filename_utc_ts(download.suggested_filename or "")
                if fname_ts is not None:
                    now_utc = datetime.now(timezone.utc)
                    lag_hours = (now_utc - fname_ts).total_seconds() / 3600.0
                    if lag_hours > 2.0:
                        logger_obj.warning(
                            "[%s] 导出文件名时间戳(%s UTC) 比当前时刻(%s UTC) 落后 %.1fh,"
                            " 疑似命中历史轮次导出缓存(BMS 导入导出中心复用旧任务)。"
                            " 若持续出现请检查 BMS 导出任务队列是否被旧任务占用",
                            data_type, fname_ts.strftime("%m-%d %H:%M"), now_utc.strftime("%m-%d %H:%M"), lag_hours,
                        )
            except Exception as _fe:
                logger_obj.warning(f"[{data_type}] 文件名时间戳校验异常(忽略): {_fe}")
            return download
        except Exception as e:
            # ⚠️ 清理:移除 response listener + 取消残留 download_task,避免泄漏
            _remove_response_listener(page)
            _drain_task(download_task)
            last_exc = e
            err_msg = str(e)
            # 如果是被限流,等待 60 秒再重试(BMS 限流窗口是 1 分钟)
            if "限流" in err_msg or "1分钟" in err_msg or "请勿重复" in err_msg:
                logger_obj.warning(
                    "[%s] 下载被限流(尝试 %d/%d):%s,等待 60s 后重试...",
                    data_type, attempt + 1, max_retries + 1, err_msg,
                )
                await asyncio.sleep(60)
            else:
                logger_obj.warning(
                    "[%s] 下载失败(尝试 %d/%d):%s: %s",
                    data_type, attempt + 1, max_retries + 1, type(e).__name__, e,
                )
                await asyncio.sleep(2)
            continue
        finally:
            # ⚠️ 无论 成功/异常/被取消, 都清理被 asyncio.shield 保护的 download_task。
            #    shield 不会随外层协程取消而停止: 若外层被取消/页面关闭, 残留 task 会
            #    一直运行到页面关闭抛异常且无人 retrieve → "Task exception was never retrieved"。
            _drain_task(download_task)

    # 所有重试都用完了 → 把最后一次异常抛出去
    raise RuntimeError(
        f"[{data_type}] 导出下载 {max_retries + 1} 次均失败: "
        f"{last_exc!r}. 请检查 BMS 后台导出是否正常、数据量是否过大、网络是否拥塞。"
    )


# ──────────────────────────────────────────────────────────────────────
# 验证码自动登录:定位验证码图片 -> ddddocr 识别 -> 填单重试最多 N 次
# ──────────────────────────────────────────────────────────────────────
_CAPTCHA_OCR_MAX_RETRY = 8  # 自动识别最多重试次数(每次刷新验证码)
# 验证码类错误关键词(只有命中这些的错误,才值得刷新验证码再试)
_CAPTCHA_ERROR_KEYS = ("验证码", "校验码", "驗證碼", "captcha", "vcode", "图形码", "图形验证码")
# 账号密码类错误关键词(命中则立即终止重试,不要再浪费机会)
_ACCOUNT_ERROR_KEYS = (
    "账号", "用户名", "账户", "用户", "密码", "口令",
    "错误", "不正确", "不对", "无效", "错误的", "不匹配",
    "锁定", "禁用", "被锁", "封禁",
    "登录名", "登录密码",
)


async def _locate_captcha_element(page) -> tuple[object | None, bytes | None]:
    """定位验证码元素并截图(返回:元素 locator, png_bytes)。

    策略与 /captcha-login/start 完全一致,确保两条路径识别效果一致:
    1) 直接找 src/class/id 带 captcha/vcode/verify/code 的 img/canvas
    2) 找含「验证码/校验码」文字的表单项下的 img/canvas
    3) 再兜底:整个登录表单或页面截图
    """
    captcha_elem = None
    for sel in [
        'img[src*="captcha" i]',
        'img[src*="vcode" i]',
        'img[src*="verify" i]',
        'img[src*="code" i]',
        'canvas[class*="captcha" i]',
        'canvas[id*="captcha" i]',
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                captcha_elem = loc
                break
        except Exception:
            continue
    if captcha_elem is None:
        try:
            form_item = page.locator(
                ".el-form-item:has-text('验证码'), .el-form-item:has-text('校验码')"
            ).first
            if await form_item.count() > 0:
                for sel in ["img", "canvas"]:
                    loc = form_item.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        captcha_elem = loc
                        break
        except Exception:
            pass
    # 截图
    png: bytes | None = None
    if captcha_elem is not None:
        try:
            png = await captcha_elem.screenshot(type="png")
        except Exception:
            png = None
    if not png:
        try:
            form = page.locator(".login-container, .login-form, form, .el-form").first
            if await form.count() > 0 and await form.is_visible():
                png = await form.screenshot(type="png")
            else:
                png = await page.screenshot(type="png", full_page=False)
        except Exception:
            try:
                png = await page.screenshot(type="png", full_page=False)
            except Exception:
                png = None
    return captcha_elem, png


async def _fill_accounts(page, username: str, password: str) -> None:
    """统一的账号密码填写逻辑(与 _captcha_do_fill_and_login 保持一致)"""
    # ⚠️ 2026-08-12: 排除 readonly el-select 内部 input,避免命中"请选择"下拉框
    user_sel = (
        'input[name="username"]:not([readonly]):not([disabled]),'
        'input[placeholder*="账号" i]:not([readonly]):not([disabled]),'
        ' input[placeholder*="用户名" i]:not([readonly]):not([disabled]),'
        ' input[placeholder*="手机号" i]:not([readonly]):not([disabled]),'
        ' input[type="text"]:not([readonly]):not([disabled])'
    )
    try:
        await page.locator(user_sel).first.fill(username, timeout=10000)
    except Exception:
        await page.locator("input:visible:not([readonly]):not([disabled])").first.fill(username, timeout=10000)
    pwd_sel = 'input[type="password"], input[placeholder*="密码" i]'
    await page.locator(pwd_sel).first.fill(password, timeout=10000)


async def _fill_captcha_and_click(page, captcha: str) -> None:
    """填入验证码并点击登录按钮(或按回车提交,后者更稳妥不会点错装饰按钮)"""
    captcha_sel = (
        '.el-form-item:has-text("验证码") input:visible,'
        '.el-form-item:has-text("校验码") input:visible,'
        'input[placeholder*="验证码" i], input[placeholder*="校验码" i],'
        ' input[placeholder*="captcha" i]'
    )
    cap_inp = None
    try:
        loc = page.locator(captcha_sel).first
        if await loc.count() > 0 and await loc.is_visible(timeout=3000):
            await loc.fill(captcha, timeout=8000)
            cap_inp = loc
    except Exception:
        # 兜底: 找第三个可见 input(账号/密码/验证码顺序)
        # ⚠️ 2026-08-12: 排除 readonly,避免把 el-select 下拉框算进去导致索引错位
        try:
            vis = page.locator("input:visible:not([readonly]):not([disabled])")
            if await vis.count() >= 3:
                nth = vis.nth(2)
                await nth.fill(captcha, timeout=5000)
                cap_inp = nth
        except Exception:
            pass
    await asyncio.sleep(0.8)

    # ── 登录提交:严格的优先级,避免误点到页面顶部装饰性 el-button--primary(如"欢迎来到闪猫加速") ──
    # 优先级 1: 页面显式 type=submit 按钮(最可靠)
    # 优先级 2: 文本明确写着「登录/登 录/Sign in/LOGIN」的可见按钮(必须文本匹配,乱点装饰按钮的坑就在这!)
    # 优先级 3: form 里最后一个可见按钮(往往是提交)
    # 优先级 4: 聚焦验证码输入框 → 按 Enter(万能兜底,调试里就是靠这个成功的)
    submitted = False

    # 1) type=submit
    if not submitted:
        try:
            sels = [
                'button[type="submit"]:visible',
                'input[type="submit"]:visible',
            ]
            for sel in sels:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                    await loc.click(timeout=8000)
                    submitted = True
                    break
        except Exception:
            submitted = False

    # 2) 文本精确匹配「登录/登 录/Sign in/LOGIN」的可见按钮(必须满足 has-text + 长度小,排除"欢迎来到闪猫加速"!)
    if not submitted:
        texts = ["登录", "登 录", "Sign in", "Sign In", "SIGN IN", "LOGIN", "Login", "立即登录", "登錄"]
        try:
            # 先找所有可见 button
            btns = page.locator("button:visible")
            cnt = await btns.count()
            for i in range(cnt):
                b = btns.nth(i)
                try:
                    txt = (await b.inner_text(timeout=400)).strip()
                    if not txt or len(txt) > 20:
                        continue
                    hit = any(t.lower() == txt.lower() for t in texts) or any(
                        t in txt for t in texts if t in ("登录", "登 录", "登錄")
                    )
                    if not hit:
                        continue
                    # 再额外校验: 按钮大概率在 input 附近(表单区域,不是页面最顶部装饰区)
                    box = await b.bounding_box()
                    if box and box["y"] < 100:
                        # 页面最顶部的导航栏/欢迎语按钮,直接跳过(就是"欢迎来到闪猫加速"那个坑!)
                        continue
                    await b.click(timeout=8000)
                    submitted = True
                    break
                except Exception:
                    continue
        except Exception:
            submitted = False

    # 3) form 里的最后一个按钮
    if not submitted:
        try:
            forms = page.locator("form:visible")
            if await forms.count() > 0:
                f = forms.first
                btns = f.locator("button:visible")
                cnt = await btns.count()
                if cnt > 0:
                    # 取最后一个,通常是"登录"
                    await btns.nth(cnt - 1).click(timeout=8000)
                    submitted = True
        except Exception:
            submitted = False

    # 4) 兜底:聚焦验证码输入框(或密码框)→ 回车(表单原生 submit,绝对不会点错装饰按钮!)
    if not submitted:
        try:
            # 优先聚焦验证码输入框(验证码是最后一个填的,通常按 Enter 就提交了)
            if cap_inp is not None and await cap_inp.count() > 0:
                try:
                    await cap_inp.focus(timeout=1000)
                except Exception:
                    await cap_inp.click(timeout=1000)
                await asyncio.sleep(0.2)
                await page.keyboard.press("Enter")
                submitted = True
        except Exception:
            submitted = False
    if not submitted:
        try:
            # 最后兜底:焦点直接给 body,按 Enter
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.1)
            await page.keyboard.press("Enter")
        except Exception:
            raise RuntimeError("找不到任何可用的登录提交方式(按钮/Enter 均失败)")


async def _poll_login_errors_for(
    page,
    seconds: float = 5.0,
    interval: float = 0.25,
) -> list[str]:
    """登录提交后,连续轮询一段时间,收集所有出现过的错误提示(toast/表单/alert/页面关键词)。

    因为 Element UI / Antd 的错误 toast 默认 2-3 秒就会消失,单次抓取很容易漏掉;
    这个函数在按钮点击后的前 N 秒内持续轮询,返回去重后的所有错误消息列表(按首次出现顺序)。
    """
    seen: set[str] = set()
    order: list[str] = []
    import math
    rounds = max(1, int(math.ceil(seconds / interval)))
    # 全覆盖选择器:Element UI / Antd / Vant / 原生常见错误容器
    sels = [
        # Element UI
        ".el-message",
        ".el-message__content",
        ".el-message-box__message",
        ".el-notification__content",
        ".el-notification__message",
        ".el-form-item__error",
        ".el-form-item__error-text",
        # Antd
        ".ant-message-error",
        ".ant-notification-notice-message",
        ".ant-notification-notice-description",
        # Vant
        ".van-toast",
        ".van-toast__text",
        # 通用
        ".toast",
        ".toast-message",
        ".toast-content",
        ".notice-content",
        ".alert-content",
        ".form-error",
        ".error-text",
        ".error-message",
    ]
    for _ in range(rounds):
        # 1) 轮询所有 toast/error 容器
        for sel in sels:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                for i in range(cnt):
                    n = loc.nth(i)
                    try:
                        # is_visible 带超时可能抛,加 short timeout
                        vis = await n.is_visible(timeout=50)
                        if not vis:
                            continue
                        t = (await n.inner_text(timeout=200)).strip()
                        if t and len(t) <= 300 and t not in seen:
                            seen.add(t)
                            order.append(t)
                    except Exception:
                        continue
            except Exception:
                continue
        # 2) 页面 body 关键词兜底(每 5 轮一次即可,避免太频繁)
        if _ % 5 == 0:
            try:
                body = await page.inner_text("body", timeout=400)
                for kw in (
                    "验证码错误", "验证码失效", "验证码过期", "验证码不对",
                    "密码错误", "密码不正确", "密码不对", "用户名或密码",
                    "账号密码", "账号不存在", "账号已锁定", "账号已禁用",
                    "用户不存在", "用户被锁定",
                    "登录失败", "登录名或密码",
                ):
                    if kw in body:
                        i = body.find(kw)
                        frag = body[max(0, i - 20): i + 80].strip()
                        if frag and frag not in seen:
                            seen.add(frag)
                            order.append(frag)
                        break
            except Exception:
                pass
        await asyncio.sleep(interval)
    return order


async def _read_login_error(page) -> str:
    """登录失败后读取页面错误提示(单次采样版,兼容旧 API 调用方)。

    新代码建议优先用 _poll_login_errors_for,在按钮点击后的「黄金 3-5 秒」内轮询抓取,
    避免 toast 消失后漏抓。
    """
    errors = await _poll_login_errors_for(page, seconds=1.0, interval=0.3)
    if errors:
        return " | ".join(errors)
    try:
        whole = await page.inner_text("body", timeout=1500)
        for kw in ["验证码", "错误", "失败", "密码", "用户名"]:
            if kw in whole:
                idx = whole.find(kw)
                return whole[max(0, idx - 20): idx + 40].strip()
    except Exception:
        pass
    return ""


async def _classify_login_errors(errors: list[str]) -> str:
    """根据本轮轮询到的错误消息列表,返回本轮错误的类别。

    Returns:
      "captcha"  -> 只有验证码错,刷新验证码继续试
      "account"  -> 账号/密码/账号状态错,不要再试
      "unknown"  -> 不知道啥错,默认继续试
    """
    if not errors:
        return "unknown"
    text = "\n".join(errors)
    has_account_kw = any(k in text for k in _ACCOUNT_ERROR_KEYS)
    has_captcha_kw = any(k in text for k in _CAPTCHA_ERROR_KEYS)
    # 账号密码类关键词优先级更高(除非明确只讲验证码)
    if has_account_kw and not has_captcha_kw:
        return "account"
    if has_captcha_kw:
        # 注意:「用户名或密码错误,请重新输入(验证码也顺便清了)」这种文案
        # 可能同时出现,但核心根因其实是账号密码。把「错误/不正确/不对」
        # 前缀+没有出现「验证码」关键词的句子,单独判断一下。
        if "验证码错误" in text or "验证码失效" in text or "验证码过期" in text:
            return "captcha"
        # 出现了验证码关键词,但不是"验证码错误"(例如「请输入验证码」),还是按验证码类
        # 除非明确有账号密码类的强特征
        strong_account_kws = (
            "用户名或密码", "账号或密码", "账号密码错误", "密码错误",
            "密码不正确", "用户不存在", "账号不存在",
            "账号已锁定", "账号已禁用", "用户被锁定",
        )
        if any(kw in text for kw in strong_account_kws):
            return "account"
        return "captcha"
    return "unknown"


async def _refresh_captcha_image(page, captcha_elem) -> bool:
    """尝试「点击验证码图片刷新验证码」;失败返回 False。

    大部分 BMS 系列的图形验证码图片,点击本身就会刷新;
    比整页 goto/reload 快很多,并且不会丢失已填的账号密码。
    """
    if captcha_elem is None:
        return False
    try:
        # 点击前截一张图用于对比(部分 canvas 内容不变会导致字节相同)
        try:
            before = await captcha_elem.screenshot(type="png")
        except Exception:
            before = None
        try:
            # 先等待可点击
            await captcha_elem.wait_for(state="visible", timeout=2000)
            await captcha_elem.click(timeout=2000)
        except Exception:
            # 元素被 overlay 覆盖,试试 force
            await captcha_elem.click(force=True, timeout=2000)
        await asyncio.sleep(0.6)
        try:
            after = await captcha_elem.screenshot(type="png")
        except Exception:
            after = None
        if before and after and before != after:
            return True
        # 图片字节没变但也可能是刷新了(比如随机刷新),按成功算
        return True
    except Exception:
        return False


async def _try_auto_login_via_ocr(
    page,
    context,
    data_type: str,
    login_url: str,
    username: str,
    password: str,
    state_path: Path,
) -> tuple[bool, str]:
    """「检测到需要验证码」时,先走 ddddocr 自动识别登录(最多 8 次)。

    内部重试策略(比旧版快 + 更准):
      - 首次: goto 登录页并填账号密码
      - 后续(验证码类错误 → 继续): 优先点验证码图片刷新(不丢失已填账号密码,比整页 reload 快 3-5 秒)
      - 账号/密码类错误 → 立即终止重试(不要再浪费机会)
      - 点登录后 0.25s 间隔轮询 5 秒 toast(避免 3s 自动消失漏抓)
      - URL 离开 login 路由 或 出现"工作台/订单/管理"等成功关键词 → 判定登录成功,保存 storage_state
    """
    log = logger
    ocr_avail = callable(try_ocr_captcha)
    if not ocr_avail:
        return False, "验证码自动识别未启用(ddddocr 未加载)"

    last_err: str = ""
    account_password_filled = False  # 第 1 次填过后,后续点击刷新验证码就不用重填账号密码了
    cap_elem = None

    # P1-2: 根据 data_type 自动切换验证码识别模式
    #       - shanmao_refund(闪猫加速 https://fast.cloud-cat.cn/sc_brm) 是 5 字符纯文本验证码,
    #         实测 ddddocr 普通模式可识别(如 '4vrqg'),走 try_ocr_captcha_with_confidence
    #       - BMS 其他数据源(refund/ticket/wallet/cs_record 等)大多无验证码或纯文本验证码
    #       - SCRM 的数学计算式验证码(7*0=? / 9-5=?)在 scrm_web_fetcher.py 独立处理,不经过这里
    #       若后续需要按配置切换,可把这个判断改成读 config.get("captcha_type")
    use_math_captcha = False  # BMS 系列数据源默认走纯文本验证码
    if use_math_captcha:
        log.info(f"[{data_type}] 检测到数学计算式验证码,启用 try_ocr_math_captcha 流程")
    else:
        log.info(f"[{data_type}] 使用纯文本验证码识别(try_ocr_captcha_with_confidence)")

    for attempt in range(1, _CAPTCHA_OCR_MAX_RETRY + 1):
        # ───────── 1) 刷新验证码 / 页面 ─────────
        if attempt == 1:
            # 第一次走完整 goto + 填账号密码
            try:
                await page.goto(login_url, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(2)
            except Exception as e:
                last_err = f"第{attempt}次: 刷新登录页失败: {e}"
                log.warning(f"[{data_type}] {last_err}")
                continue
            await _fill_accounts(page, username, password)
            account_password_filled = True
        else:
            # 后续尝试:优先点验证码图片刷新;失败才 reload
            refreshed = await _refresh_captcha_image(page, cap_elem)
            if not refreshed:
                try:
                    await page.reload(wait_until="networkidle", timeout=60000)
                    await asyncio.sleep(2)
                    await _fill_accounts(page, username, password)
                    account_password_filled = True
                except Exception as e:
                    last_err = f"第{attempt}次: reload 登录页失败: {e}"
                    log.warning(f"[{data_type}] {last_err}")
                    continue
            else:
                # 点刷新验证码通常会清空验证码输入框,账号密码一般还在;
                # 保险起见,如果账号密码框是空的就再填一次(比如某些系统清整个 form)
                try:
                    u_sel = (
                        'input[name="username"]:not([readonly]):not([disabled]),'
                        'input[placeholder*="账号" i]:not([readonly]):not([disabled]),'
                        'input[placeholder*="用户名" i]:not([readonly]):not([disabled]),'
                        'input[type="text"]:not([readonly]):not([disabled])'
                    )
                    p_sel = 'input[type="password"], input[placeholder*="密码" i]'
                    uval = await page.locator(u_sel).first.input_value(timeout=1500)
                    pval = await page.locator(p_sel).first.input_value(timeout=1500)
                    if (not uval) or (not pval):
                        await _fill_accounts(page, username, password)
                        account_password_filled = True
                    # 验证码输入框:清空之前可能填过但提交失败的旧值
                    try:
                        captcha_inp = page.locator(
                            '.el-form-item:has-text("验证码") input:visible,'
                            '.el-form-item:has-text("校验码") input:visible,'
                            'input[placeholder*="验证码" i], input[placeholder*="校验码" i]'
                        ).first
                        if await captcha_inp.count() > 0:
                            await captcha_inp.fill("", timeout=1500)
                    except Exception:
                        pass
                except Exception:
                    pass

        # ───────── 2) 定位验证码 → OCR 置信度预检(不登录,低置信度就刷新验证码重认) ─────────
        # 核心策略(用户提出的优化):避免「低把握的识别结果强行去登录 → 触发验证码错误toast + 验证码失效」
        # 先在本地反复刷新验证码,直到 OCR 给出一个"有把握"的结果再去提交,最大限度减少服务端报错。
        cap_elem, cap_png = await _locate_captcha_element(page)
        if not cap_png:
            last_err = "定位不到验证码图片元素,无法自动识别"
            break

        # P1-2: 根据模式选择不同的 OCR 函数 + 结果字段:
        #   数学验证码 → try_ocr_math_captcha_with_confidence, 结果填值用 answer (计算出来的数字答案)
        #   文本验证码 → try_ocr_captcha_with_confidence, 结果填值用 code  (直接识别出的字符串)
        if use_math_captcha:
            fn_confidence = try_ocr_math_captcha_with_confidence
            fn_plain = try_ocr_math_captcha
            ans_field = "answer"
            ocr_disp_field = "normalized_expr"  # 展示用:显示识别出的算式
        else:
            fn_confidence = try_ocr_captcha_with_confidence
            fn_plain = try_ocr_captcha
            ans_field = "code"
            ocr_disp_field = "code"

        use_confidence_mode = callable(fn_confidence)
        best_result: dict | None = None
        best_score = -1.0
        pre_tried = 0
        ocr_text: str | None = None

        while pre_tried < _OCR_PRECHECK_MAX_REFRESH:  # type: ignore[operator]
            pre_tried += 1
            cur_result: dict | None = None
            if use_confidence_mode:
                try:
                    cur_result = fn_confidence(cap_png)  # type: ignore[misc]
                except Exception as _e_ocr:
                    log.warning(f"[{data_type}] 带置信度 OCR 抛错,回退到纯字符串模式: {_e_ocr}")
                    cur_result = None
            if cur_result is None:
                # 回退: 纯字符串模式(验证码→计算答案),按退化中等置信度打分
                tmp = fn_plain(cap_png) if callable(fn_plain) else None
                if tmp is None:
                    break
                cur_result = {
                    ans_field: tmp,
                    "used_beta": None,
                    "min_prob": 0.70,
                    "avg_prob": 0.80,
                    "probs": [0.70] * len(tmp),
                    "chars": list(tmp),
                    "confident": False,
                }
                if use_math_captcha:
                    cur_result["normalized_expr"] = tmp
            # 综合评分:单字符最低概率权重加倍(因为只要有一个字符认错,整条验证码就错了)
            try:
                min_p = float(cur_result.get("min_prob", 0.0))
                avg_p = float(cur_result.get("avg_prob", 0.0))
            except Exception:
                min_p = 0.0
                avg_p = 0.0
            cur_score = (min_p * 2.0 + avg_p) / 3.0
            if cur_score > best_score:
                best_score = cur_score
                best_result = cur_result
            # 达标了 → 立即停止预检,拿去登录
            if bool(cur_result.get("confident")):
                disp_val = cur_result.get(ocr_disp_field) or cur_result.get(ans_field)
                log.info(
                    "[%s] OCR 预检第%d/%d次:识别=%s, min=%.3f avg=%.3f,达到阈值(min>=%s avg>=%s) → 直接拿去登录",
                    data_type, pre_tried, _OCR_PRECHECK_MAX_REFRESH, disp_val,
                    min_p, avg_p, _OCR_MIN_CHAR_PROB, _OCR_AVG_PROB,
                )
                ocr_text = cur_result[ans_field]
                break
            # 未达标 → 刷一下验证码图片,下次循环重新识别
            disp_val = cur_result.get(ocr_disp_field) or cur_result.get(ans_field)
            log.info(
                "[%s] OCR 预检第%d/%d次:识别=%s, min=%.3f avg=%.3f,不达标 → 刷新验证码再认",
                data_type, pre_tried, _OCR_PRECHECK_MAX_REFRESH, disp_val,
                min_p, avg_p,
            )
            if pre_tried >= _OCR_PRECHECK_MAX_REFRESH:  # type: ignore[operator]
                break
            refreshed = await _refresh_captcha_image(page, cap_elem)
            if not refreshed:
                # 验证码点不动,没必要再预检了
                break
            # 刷新后重新截图(元素对象可能没变但内容变了)
            new_png = None
            try:
                if cap_elem is not None:
                    new_png = await cap_elem.screenshot(type="png")
            except Exception:
                new_png = None
            if not new_png:
                _, new_png = await _locate_captcha_element(page)
            if not new_png:
                break
            cap_png = new_png

        # 预检循环结束: 要么找到了 confident 结果(ocr_text 已赋值),要么超次数,用最高分兜底
        if ocr_text is None and best_result is not None:
            ocr_text = best_result.get(ans_field)  # type: ignore[assignment]
            if isinstance(ocr_text, str) and ocr_text:
                disp_val = best_result.get(ocr_disp_field) or ocr_text
                log.warning(
                    "[%s] OCR 预检 %d 次均未达到置信度阈值,取最高分=%.3f 的识别结果=%s 登录兜底",
                    data_type, pre_tried, best_score, disp_val,
                )
        if not ocr_text:
            # 最后兜底:一次普通字符串 OCR
            ocr_text = fn_plain(cap_png) if callable(fn_plain) else None
        if not ocr_text:
            last_err = f"第{attempt}次: ddddocr 未识别出合法验证码(返回 None)"
            log.warning(f"[{data_type}] 验证码 OCR: {last_err}")
            break
        log.info(
            f"[{data_type}] 验证码自动识别第{attempt}/{_CAPTCHA_OCR_MAX_RETRY}次:"
            f" 截图{len(cap_png)}字节,识别结果={ocr_text!r},填入登录..."
        )
        # ───────── 3) 填验证码 + 点登录(并发启动:URL跳转监听 + 错误轮询) ─────────
        try:
            await _fill_captcha_and_click(page, ocr_text)
        except Exception as e:
            last_err = f"填入验证码或点击登录失败: {e}"
            log.warning(f"[{data_type}] {last_err}")
            continue

        url_skipped = asyncio.create_task(asyncio.sleep(0))  # placeholder,实际下面覆盖

        async def _wait_url_jump() -> bool:
            try:
                await page.wait_for_url(
                    lambda u: (
                        "login" not in u.lower().split("#")[-1]
                        if "#" in u
                        else "login" not in u.lower()
                    ),
                    timeout=15000,
                )
                return True
            except Exception:
                return False

        async def _poll_errors_task() -> tuple[str, list[str]]:
            errs = await _poll_login_errors_for(page, seconds=5.0, interval=0.25)
            if errs:
                combined = " | ".join(errs)
                log.info(f"[{data_type}] 第{attempt}次登录后捕获到错误: {combined}")
            return (await _classify_login_errors(errs)), errs

        # 并发:等待 URL 跳转 + 前 5 秒持续轮询错误
        url_task = asyncio.create_task(_wait_url_jump())
        err_task = asyncio.create_task(_poll_errors_task())
        done, _ = await asyncio.wait(
            [url_task, err_task],
            return_when=asyncio.FIRST_COMPLETED,
            timeout=16.0,
        )
        # URL 先跳了 → 登录成功
        if url_task in done and url_task.result():
            log.info(
                f"[{data_type}] 验证码自动识别登录成功(第{attempt}次尝试,OCR={ocr_text},URL已跳转)"
            )
            err_task.cancel()
            try:
                await asyncio.sleep(3)
                await context.storage_state(path=str(state_path))
                log.info(f"[{data_type}] 登录态已保存: {state_path.name}")
            except Exception as e:
                log.warning(f"[{data_type}] 保存登录态失败(不影响本次抓取): {e}")
            return True, "验证码自动识别登录成功"
        # URL 没跳,但错误收集任务可能已经返回 → 等它
        err_kind = "unknown"
        err_list: list[str] = []
        try:
            if not err_task.done():
                err_kind, err_list = await asyncio.wait_for(err_task, timeout=1.0)
            else:
                err_kind, err_list = err_task.result()
        except Exception:
            pass
        # URL 等 16s 都没跳,最后再判定一次(避免极端情况刚好 15.9s 才跳)
        still_on_login = False
        try:
            u = page.url.lower()
            still_on_login = ("login" in u.split("#")[-1]) if "#" in u else ("login" in u)
        except Exception:
            still_on_login = True
        if not still_on_login:
            log.info(
                f"[{data_type}] 第{attempt}次URL判定已离开登录,视为登录成功(OCR={ocr_text})"
            )
            try:
                await asyncio.sleep(3)
                await context.storage_state(path=str(state_path))
            except Exception as e:
                log.warning(f"[{data_type}] 保存登录态失败(不影响): {e}")
            return True, "验证码自动识别登录成功"
        # 判定失败原因
        err_text = (
            (" | ".join(err_list))
            if err_list
            else (await _read_login_error(page) or "未跳转,可能验证码错误或账号密码错误")
        )
        last_err = f"第{attempt}次识别({ocr_text})登录失败: {err_text}"
        log.warning(f"[{data_type}] {last_err}")
        # ⚠️ 关键修复: 验证码登录前几次不要轻易判定为"账号密码错误"而终止重试!
        #    很多 BMS/闪猫这类站点,为了防撞库,故意把「验证码错误」「账号被锁定」「验证码过期」
        #    等多种失败原因的提示语都写成"用户名或密码错误!",错误分类器一旦命中就会直接 break.
        #    结果是: 实际上只是 OCR 把 WXEAH 认成了别的,却再也没有机会刷新重试下一张了。
        #
        #    策略: 前 CAPTCHA_ACCOUNT_GUARD_ATTEMPTS 次(默认3次)内,即使分类器返回 account,
        #    也当作验证码错误继续刷新重试,避免被模糊的提示语误伤。超过这个阈值仍失败,才认定为
        #    真·账号密码问题并终止。
        CAPTCHA_ACCOUNT_GUARD_ATTEMPTS = 3
        if err_kind == "account" and attempt > CAPTCHA_ACCOUNT_GUARD_ATTEMPTS:
            log.warning(
                f"[{data_type}] 已重试{attempt}次({CAPTCHA_ACCOUNT_GUARD_ATTEMPTS}次验证码豁免窗口已用完),"
                f" 判定为账号/密码/账号状态类错误,终止重试;错误=%r",
                err_list,
            )
            break
        if err_kind == "account" and attempt <= CAPTCHA_ACCOUNT_GUARD_ATTEMPTS:
            log.warning(
                "[%s] 错误文本看似为账号密码类(%r),但仍在验证码豁免窗口(%d/%d)内,"
                " 当作验证码错误继续刷新下一张重试(防止被站点『统一提示为用户名或密码错误』误伤)",
                data_type, err_list, attempt, CAPTCHA_ACCOUNT_GUARD_ATTEMPTS,
            )
        # captcha / unknown / 被豁免的 account → 进入下次重试
        url_task.cancel()
    return False, last_err or f"自动识别{_CAPTCHA_OCR_MAX_RETRY}次均失败"


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
        # ⚠️ 2026-08-12: 标记本源是否已完成登录(即使后续下载超时/失败,登录态仍可复用)
        #   解决: refund 登录成功但下载超时 → logged_in_ok 未传递 → ticket 在残留的
        #   refund 页面上"重新登录" → 命中 readonly el-select → 全组连锁失败
        self.logged_in: bool = False


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

        策略(优先级从高到低,高优先级命中即 return):
          0) [快速轮询] 最多等 6s 找 Element UI datetimerange/daterange 容器
               class=el-date-editor--datetimerange 或 el-date-editor--daterange
               → 内部两个 .el-range-input 填 start/end(填完再读 value 确认,避免 Vue 清空)
          1) 可见 input 里找 className 含 el-range-input / 父节点含 el-date-editor 的前 2 个
          2) 可见 placeholder 含「日期/时间/开始/结束」的 input
          3) [策略4兜底] y 行分组:优先命中含 el-range-input 的 y 行;否则取最上一行 ≥2 个非 readonly text input

        Element UI datetimerange 结构:
          .el-date-editor.el-date-editor--datetimerange
            input.el-range-input (开始)
            input.el-range-input (结束)
        """
        # ── 策略0(推荐):快速轮询 datetimerange/daterange 容器(最多 6s,每 0.75s 一次) ──
        #   解决:闪猫/Vue 渲染稍慢时,首轮 query_selector 找不到即退到策略4选错 input。
        logger.info(
            "[策略0] 轮询找 datetimerange 容器(最多 6s):期望找到 .el-date-editor--datetimerange > 2x .el-range-input"
        )
        range_pat_selectors = [
            ".el-date-editor--datetimerange",
            ".el-date-editor--daterange",
            ".el-range-editor",
        ]
        range_container = None
        range_inputs = None
        for try_i in range(8):
            for sel in range_pat_selectors:
                try:
                    rc = await page.query_selector(f"{sel}:visible")
                    if rc:
                        inps = await rc.query_selector_all("input.el-range-input:visible, input:visible")
                        # 只取非 readonly/非 disabled 的前 2 个
                        good = []
                        for ip in inps:
                            try:
                                if not await ip.is_visible(): continue
                                if await ip.get_attribute("readonly") or await ip.get_attribute("disabled"):
                                    continue
                                good.append(ip)
                            except Exception:
                                continue
                        if len(good) >= 2:
                            range_container = rc
                            range_inputs = [good[0], good[1]]
                            break
                except Exception:
                    pass
            if range_inputs is not None:
                break
            await asyncio.sleep(0.75)
        if range_container and range_inputs:
            logger.info(
                f"  [策略0] 命中(轮询第 {try_i+1}/8):尝试填 start={start_str!r} end={end_str!r}"
            )
            # ── 填值:模拟真实用户 click→ctrl+a→backspace→type 键盘输入,确保 Vue v-model 内部 state 更新 ──
            #   原因:Element UI DatePicker/DateTimeRange 组件内部维护 v-model,
            #   直接 fill / eval 修改 DOM input.value 不会同步到 Vue 实例的 this.value,
            #   导致查询/导出时 Vue 仍按空值处理,筛选无效。
            for i_el, val in [(range_inputs[0], start_str), (range_inputs[1], end_str)]:
                try:
                    await i_el.scroll_into_view_if_needed()
                    await asyncio.sleep(0.18)
                    await i_el.click()
                    await asyncio.sleep(0.28)
                    await page.keyboard.press("Control+A")
                    await asyncio.sleep(0.10)
                    await page.keyboard.press("Backspace")
                    await asyncio.sleep(0.15)
                    await i_el.type(val, delay=35)
                    await asyncio.sleep(0.30)
                except Exception as e:
                    logger.warning(f"  [策略0] 模拟键盘 type 失败,回退到 _fill_el_input: {e}")
                    await self._fill_el_input(page, i_el, val)
            # 触发两个 input blur → 点击页面右上角空白区域,让 DateTimeRange picker 失焦并同步内部值
            try:
                viewport = page.viewport_size or {"width":1920,"height":1080}
                await page.mouse.click(max(int(viewport.get("width",1920)) - 40, 1850), 20)
            except Exception:
                pass
            await asyncio.sleep(0.9)

            # 回填 value 确认(DOM value + Vue2 实例 value 都要检查)
            verify = await page.evaluate(
                """([sEl, eEl]) => {
                    const sDom = sEl.value || '';
                    const eDom = eEl.value || '';
                    // 向上找最近父节点带 __vue__ 的(DateRange 组件本身)
                    function upVue(el) {
                        let n = el;
                        for (let i=0; i<8 && n; i++) {
                            if (n.__vue__) return n.__vue__;
                            n = n.parentElement;
                        }
                        return null;
                    }
                    const v = upVue(sEl) || upVue(eEl);
                    let vueVal = null;
                    try { if (v && v.value !== undefined) vueVal = JSON.parse(JSON.stringify(v.value)); }
                    catch(_) { vueVal = String(v && v.value).slice(0,200); }
                    return {
                        s: sDom, e: eDom,
                        sCls: (sEl.className || '').slice(0,80),
                        eCls: (eEl.className || '').slice(0,80),
                        vueValue: vueVal,
                        vueFound: !!v,
                        vueName: v ? (v.$options && v.$options.name) : null,
                    };
                }""",
                [range_inputs[0], range_inputs[1]],
            )
            s_ok = start_str in (verify.get('s') or '')
            e_ok = end_str in (verify.get('e') or '')
            vv = verify.get("vueValue")
            # vue value 正确格式:长度=2 的数组, 2 个元素分别等于 start_str/end_str (或者 start_str 在其字符串里)
            vue_ok = False
            if isinstance(vv, (list, tuple)) and len(vv) >= 2:
                vs0 = str(vv[0] or '')
                vs1 = str(vv[1] or '')
                vue_ok = (start_str in vs0) and (end_str in vs1)
            if (not s_ok or not e_ok) and not vue_ok:
                logger.warning(
                    "  [策略0] 回填验证失败 s=%r(%s) e=%r(%s)  vue=%r(%s) → fallback _fill_el_input+setter",
                    verify.get('s'), "OK" if s_ok else "MISS",
                    verify.get('e'), "OK" if e_ok else "MISS",
                    vv, "OK" if vue_ok else "MISS",
                )
                # fallback: 再次调用 _fill_el_input + setter dispatchEvent (双保险)
                await self._fill_el_input(page, range_inputs[0], start_str)
                await self._fill_el_input(page, range_inputs[1], end_str)
                await page.evaluate(
                    """([sEl, sVal, eEl, eVal]) => {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        for (const [el, val] of [[sEl,sVal],[eEl,eVal]]) {
                            if (setter) setter.call(el, val); else el.value = val;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.blur && el.blur();
                        }
                    }""",
                    [range_inputs[0], start_str, range_inputs[1], end_str],
                )
                await asyncio.sleep(0.5)
                # ── 二次校验:fallback 后必须再读一次 DOM/Vue 值,确认填入成功 ──
                #   原因:之前 fallback 后直接 return,调用方会继续点搜索,
                #   导致带着错误时间范围(默认近30天)查询,下回错月份数据。
                verify2 = await page.evaluate(
                    """([sEl, eEl]) => {
                        function upVue(el) {
                            let n = el;
                            for (let i=0; i<8 && n; i++) {
                                if (n.__vue__) return n.__vue__;
                                n = n.parentElement;
                            }
                            return null;
                        }
                        const v = upVue(sEl) || upVue(eEl);
                        let vueVal = null;
                        try { if (v && v.value !== undefined) vueVal = JSON.parse(JSON.stringify(v.value)); }
                        catch(_) { vueVal = String(v && v.value).slice(0,200); }
                        return { s: sEl.value || '', e: eEl.value || '', vueValue: vueVal };
                    }""",
                    [range_inputs[0], range_inputs[1]],
                )
                s_ok2 = start_str in (verify2.get('s') or '')
                e_ok2 = end_str in (verify2.get('e') or '')
                vv2 = verify2.get("vueValue")
                vue_ok2 = False
                if isinstance(vv2, (list, tuple)) and len(vv2) >= 2:
                    vue_ok2 = (start_str in str(vv2[0] or '')) and (end_str in str(vv2[1] or ''))
                if (not s_ok2 or not e_ok2) and not vue_ok2:
                    # fallback 仍失败 → 必须中止,避免用错误时间范围查询导出脏数据
                    logger.error(
                        "  [策略0] fallback 后二次校验仍失败 s=%r(%s) e=%r(%s) vue=%r(%s) → 抛错中止,避免按错误时间范围查询",
                        verify2.get('s'), "OK" if s_ok2 else "MISS",
                        verify2.get('e'), "OK" if e_ok2 else "MISS",
                        vv2, "OK" if vue_ok2 else "MISS",
                    )
                    raise RuntimeError(
                        f"时间范围填入失败: 目标 start={start_str!r} end={end_str!r}, "
                        f"实际 DOM s={verify2.get('s')!r} e={verify2.get('e')!r}, "
                        f"vue={vv2!r}; 已中止查询以避免按错误时间范围导出脏数据"
                    )
                logger.info(
                    "  [策略0] fallback 后二次校验通过 s=%r/%r  e=%r/%r  vueValue=%r ✅",
                    verify2.get('s'), start_str, verify2.get('e'), end_str, vv2,
                )
            else:
                logger.info(
                    "  [策略0] 回填验证通过 s=%r/%r  e=%r/%r  vueValue=%r ✅",
                    verify.get('s'), start_str, verify.get('e'), end_str, vv,
                )
            # 关闭可能遮挡搜索按钮的日期选择器弹窗 (el-picker-panel / el-date-picker__time-header)
            try:
                await page.evaluate(
                    """() => {
                        // 点击页面空白处关闭 Element UI 日期弹窗
                        const popups = document.querySelectorAll('.el-picker-panel, .el-picker-panel__body, .el-date-picker__time-header');
                        if (popups.length) {
                            // 触发 ESC 关闭
                            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                        }
                        return true;
                    }"""
                )
                await asyncio.sleep(0.3)
            except Exception:
                pass
            return

        # 策略1: Element UI datetimerange 范围选择器(只取第一个,避免多时间字段干扰)
        range_editor = await page.query_selector(".el-date-editor--datetimerange")
        if range_editor:
            inputs = await range_editor.query_selector_all(".el-range-input")
            if len(inputs) >= 2:
                logger.info(f"使用 datetimerange 选择器,找到 {len(inputs)} 个 input")
                await self._fill_el_input(page, inputs[0], start_str)
                await self._fill_el_input(page, inputs[1], end_str)
                return
            # 兜底:任意后代 input(前两个非 readonly)
            any_ins = await range_editor.query_selector_all("input")
            good = []
            for i in any_ins:
                try:
                    ro = (await i.get_attribute("readonly")) is not None
                    dis = (await i.get_attribute("disabled")) is not None
                    if not ro and not dis: good.append(i)
                except Exception:
                    pass
            if len(good) >= 2:
                logger.info(f"使用 datetimerange 容器(兜底后代 input),找到 {len(good)} 个可用 input")
                await self._fill_el_input(page, good[0], start_str)
                await self._fill_el_input(page, good[1], end_str)
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
            return
        except Exception:
            pass

        # 策略4(终极兜底,专门适配「无 label/placeholder 全空」的页面):
        #   把所有可见 input 按 y 坐标分组(差 ≤30px 视为同一行),
        #   优先选择「含 el-range-input / 父节点含 el-date-editor」的组;
        #   否则取最上面一组(筛选条所在行)里「非 readonly、类型是 text」的前两个,
        #   依次填 开始/结束时间。
        logger.info(
            "[兜底策略4] 未匹配到日期类选择器,尝试按可见 input 的 y 行分组(优先命中 el-range-input 组)"
        )
        info = await page.evaluate("""
            () => {
                const all = document.querySelectorAll('input');
                const rows = new Map();  // key=roundedY(10)  value=[{x,y,readonly,type,el}]
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    if (style.visibility === 'hidden' || style.display === 'none') continue;
                    const y = rect.top + rect.height / 2;
                    const key = Math.round(y / 10) * 10;
                    // 命中 el-range-input:说明它属于 Element UI 日期范围选择器
                    const cls = (el.className || '').toString();
                    const pcls = (el.parentElement && el.parentElement.className || '').toString();
                    const is_datey = /el-range-input|el-date-editor|date-picker|daterange|datetimerange/i.test(cls + ' ' + pcls);
                    const info = {
                        x: rect.left + rect.width / 2,
                        y,
                        readonly: !!el.readOnly || el.hasAttribute('readonly'),
                        disabled: !!el.disabled || el.hasAttribute('disabled'),
                        type: (el.getAttribute('type') || 'text').toLowerCase(),
                        placeholder: el.placeholder || '',
                        is_datey,
                    };
                    if (!rows.has(key)) rows.set(key, []);
                    rows.get(key).push({ info, el });
                }
                const sortedKeys = Array.from(rows.keys()).sort((a,b)=>a-b);
                // 第一优先级:y 行里存在 is_datey=true,且 ≥2 个非 readonly 非 disabled input
                for (const k of sortedKeys) {
                    const row = rows.get(k);
                    const has_datey = row.some(r => r.info.is_datey);
                    if (!has_datey) continue;
                    const candidates = row
                        .filter(r => !r.info.readonly && !r.info.disabled && (r.info.type === 'text' || r.info.type === ''))
                        .sort((a,b) => a.info.x - b.info.x);
                    if (candidates.length >= 2) {
                        window.__START_INP__ = candidates[0].el;
                        window.__END_INP__   = candidates[1].el;
                        return {
                            ok: true,
                            why: 'datey-row',
                            row_y: k,
                            start_x: candidates[0].info.x,
                            end_x:   candidates[1].info.x,
                            start_ph: candidates[0].info.placeholder,
                            end_ph:   candidates[1].info.placeholder,
                        };
                    }
                }
                // 第二优先级:取最上一行(最小 y)里有 ≥2 个非 readonly 非 disabled text input
                for (const k of sortedKeys) {
                    const row = rows.get(k);
                    const candidates = row
                        .filter(r => !r.info.readonly && !r.info.disabled && (r.info.type === 'text' || r.info.type === ''))
                        .sort((a,b) => a.info.x - b.info.x);
                    if (candidates.length >= 2) {
                        window.__START_INP__ = candidates[0].el;
                        window.__END_INP__   = candidates[1].el;
                        return {
                            ok: true,
                            why: 'top-row-fallback',
                            row_y: k,
                            start_x: candidates[0].info.x,
                            end_x:   candidates[1].info.x,
                            start_ph: candidates[0].info.placeholder,
                            end_ph:   candidates[1].info.placeholder,
                        };
                    }
                }
                return { ok: false };
            }
        """)
        if info and info.get("ok"):
            logger.info(
                "  命中: why=%s 第 y=%s 行,起止 x=(%s,%s) placeholder=(%r,%r) → 开始填值并触发事件",
                info.get("why"), info.get("row_y"), info.get("start_x"), info.get("end_x"),
                info.get("start_ph"), info.get("end_ph"),
            )
            filled = await page.evaluate(
                """([sVal, eVal]) => {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    const s = window.__START_INP__; const e = window.__END_INP__;
                    if (!s || !e) return false;
                    for (const [el, val] of [[s, sVal], [e, eVal]]) {
                        if (setter) setter.call(el, val); else el.value = val;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    try { delete window.__START_INP__; delete window.__END_INP__; } catch (_) {}
                    return true;
                }""",
                [start_str, end_str],
            )
            if filled:
                return
        # 最后兜底: evaluate 直接找 range
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

    async def _wait_until_page_has_any_clickable(
        self, page, max_wait_sec: int = 20, interval_sec: float = 1.0
    ) -> int:
        """进入目标页(SPA hash 路由)后的「渲染就绪」守卫: 轮询 ANY_CLICKABLE 直到出现 ≥1 个可见元素。

        2026-08-14 新增(修复 bms_sync_logs#806 偶发「找不到任何查询/搜索按钮」):
          - 旧代码只有 goto + 固定 `asyncio.sleep(3)`,但 SPA 拉完资源后,
            Vue 还需要请求菜单权限/页面初始接口才能把「查询」按钮渲染到 DOM;
          - 当 BMS 接口慢(>3 秒)时,Playwright 已开始执行 `_click_query_button`,
            但 DOM 里根本还没有按钮 → 造成"同样的页面 一会儿成功 一会儿失败"。
          - 用户截图也证明:退费单管理「查询」一直是标准绿色 <button>, 没有改版过,
            之前「伪按钮改版」假设推翻, 真实根因 = 固定 sleep 3s 不稳定。
        """
        ANY_CLICKABLE = (
            "button:visible,"
            "div[role='button']:visible,"
            "span[role='button']:visible,"
            "a[class*='btn']:visible,"
            "a[class*='button']:visible,"
            "div[class*='btn']:visible,"
            "div[class*='button']:visible,"
            "span[class*='el-button']:visible,"
            "div[class*='el-button']:visible"
        )
        start = asyncio.get_event_loop().time()
        deadline = start + max_wait_sec
        tries = 0
        while True:
            tries += 1
            try:
                n = len(await page.query_selector_all(ANY_CLICKABLE))
            except Exception as q_e:
                logger.warning(f"  [渲染就绪守卫] query_selector_all 异常: {q_e}")
                n = 0
            if n > 0:
                cost = asyncio.get_event_loop().time() - start
                extra = (
                    f" (比固定 sleep 多等了 {max(0.0, cost - 3.0):.1f}s)"
                    if cost > 3 else ""
                )
                logger.info(
                    f"  [渲染就绪守卫] OK 已就绪 cost={cost:.1f}s tries={tries}"
                    f" ANY_CLICKABLE可见={n}{extra}"
                )
                return n
            now = asyncio.get_event_loop().time()
            if now >= deadline:
                logger.warning(
                    f"  [渲染就绪守卫] 已等满 {max_wait_sec}s 仍未出现 ANY_CLICKABLE 可见元素"
                    f" (tries={tries}) → 交给 _click_query_button 继续兜底判错"
                )
                return 0
            await asyncio.sleep(interval_sec)

    async def _fill_el_input(self, page, element, value: str):
        """填充 Element UI 日期输入框(通用 input 也能用)。

        注意: Playwright 的 ElementHandle/Locator 是 Python 代理对象,
        不要调用 Python 对象上的 element.dispatchEvent(不存在!),所有 DOM 事件必须
        在浏览器上下文里(evaluate/JS 回调)触发。
        """
        ok = False
        try:
            await element.fill(value, timeout=8000)
            ok = True
        except Exception as e:
            logger.warning(f"fill 失败,退化为 evaluate 直接设值: {e}")
        try:
            await element.evaluate(
                """(el, val) => {
                    if (el.value !== val) {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        if (setter) setter.call(el, val); else el.value = val;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                value,
            )
            ok = True
        except Exception as e2:
            logger.warning(f"evaluate 触发 input/change 失败({e2}),但 fill 可能已经成功;不抛")
        if not ok:
            raise RuntimeError(f"_fill_el_input: fill + evaluate 都失败 value={value!r}")

    async def _click_query_button(self, page) -> None:
        """点击「查询/搜索/筛选」按钮(兼容各站点的按钮文案 & DOM,避免写死 has-text("搜索"))。

        2026-08-14 诊断结论: refund/wallet 等 BMS 页面的查询按钮就是标准绿色 <button>,
        不是伪按钮。之前失败的真因是遮挡层预处理(ESC+JS移除弹层+空白点击)无条件前置 →
        Playwright 真实 click 被干扰超时 → 降级为 JS el.click() → Vue 事件未正确触发 →
        查询后页面状态异常、导出按钮文本变空。

        修复: 新增「策略0-快速直击」——先用简单 button:has-text 直接 click(不做遮挡层清理),
        诊断脚本已证明此路径 100% 可用。只有策略0失败时才执行遮挡层清理+后续策略级联。

        优先级:
          0) [新增] 快速直击: button:has-text("搜索"/"查询") 直接 Playwright click
          1) 文本精确匹配「查询/搜索/筛选...」→ 候选集合扩大到 button/div(role=button)/span(role=button)/a(btn类)
          2) 找不到 → y 坐标最靠上的筛选条区域里第一个非「重置/导出」按钮 (DOM 集合同扩大)
          3) 再找不到 → 用 JS 在整个文档扩大范围内搜 (querySelectorAll 同扩大)
          4) 仍失败 → 按 Enter 兜底 (聚焦日期框/最后一个 input 然后 Enter)
          5) [新增] 仍失败 → 表单 submit / [aria-label="搜索"] / [class*=submit]-btn 点击
        """
        keywords = ["查询", "查 询", "搜索", "搜 索", "筛选", "开始查询", "查询统计", "确认查询", "查 询 统 计"]
        exclude_keywords = ["重置", "导出", "下载", "清空", "取消", "关闭", "重置查询"]
        # 扩大候选 DOM 选择器: 真按钮 + 常见伪按钮实现 (div 挂 role / a 挂 btn-class / span 挂 el-button)
        ANY_CLICKABLE = (
            "button:visible,"
            "div[role='button']:visible,"
            "span[role='button']:visible,"
            "a[class*='btn']:visible,"
            "a[class*='button']:visible,"
            "div[class*='btn']:visible,"
            "div[class*='button']:visible,"
            "span[class*='el-button']:visible,"
            "div[class*='el-button']:visible"
        )

        # ═══════════════════════════════════════════════════════════
        # 2026-08-14 新增: 收集当前页 ALL 候选按钮的结构化"快照"函数
        #   - 正常路径: 仅打数量 + 前 N 个摘要 (info 级别, 不吵)
        #   - 即将抛错前: 全量打到 error 级别, 包含 tag/text/aria-label/class/x/y
        # ═══════════════════════════════════════════════════════════
        async def _build_buttons_snapshot(max_items: int | None = None):
            try:
                all_el = await page.query_selector_all(ANY_CLICKABLE)
            except Exception:
                return []
            snap = []
            for idx, b in enumerate(all_el):
                try:
                    tag = (await b.evaluate("e=>e.tagName") or "?").upper()
                except Exception:
                    tag = "?"
                try:
                    txt = (await b.inner_text() or "").strip().replace("\n", " ")
                except Exception:
                    txt = ""
                try:
                    al = (await b.get_attribute("aria-label") or "").strip()
                except Exception:
                    al = ""
                try:
                    cls = (await b.get_attribute("class") or "").strip()
                except Exception:
                    cls = ""
                try:
                    box = await b.bounding_box()
                    if box:
                        pos = f"({int(box['x'])},{int(box['y'])},{int(box['width'])}x{int(box['height'])})"
                    else:
                        pos = "(?)"
                except Exception:
                    pos = "(?)"
                if len(txt) > 40:
                    txt = txt[:40] + "…"
                if len(cls) > 60:
                    cls = cls[:60] + "…"
                snap.append(f"  [#{idx}] {tag}{pos} text={txt!r} aria={al!r} class={cls!r}")
                if max_items and idx + 1 >= max_items:
                    snap.append(f"  ... 仅展示前 {max_items} 个, 剩余 {len(all_el) - max_items} 个省略")
                    break
            return snap

        page_url_for_log = page.url or "<unknown>"
        logger.info(f"  [策略总览] 开始查找查询按钮 当前页面URL={page_url_for_log} 选择器={ANY_CLICKABLE!r}")

        # ═══════════════════════════════════════════════════════════
        # 2026-08-14 策略0-快速直击: 先用简单 button:has-text 直接 click,
        #   不做任何遮挡层清理。诊断脚本(diag_refund_export.py)证明:
        #   refund/wallet 页面的查询按钮就是标准绿色 <button>,
        #   Playwright 原生 click 可以 100% 成功,不需要 ESC/JS清理/空白点击。
        #   之前遮挡层预处理无条件前置 → Playwright click 被干扰超时 →
        #   降级 JS el.click() → Vue 事件未正确触发 → 查询后导出按钮消失。
        # ═══════════════════════════════════════════════════════════
        for _kw in ["搜索", "查询", "搜 索", "查 询"]:
            try:
                _btn = await page.query_selector(f'button:has-text("{_kw}"):visible')
                if _btn:
                    # force=True: 即使按钮被 user-dropdown 遮挡也直接点击
                    await _btn.click(timeout=5000, force=True)
                    logger.info(f"  [策略0-快速直击] button:has-text({_kw!r}) force-click 成功 ✅")
                    return
            except Exception:
                continue

        # ═══════════════════════════════════════════════════════════
        # 2026-08-14 遮挡层预处理（仅当策略0-快速直击失败后才执行）
        #   生产日志(12h refund) 出现:
        #     `<ul id="dropdown-menu-3551" class="el-dropdown-menu el-popper user-dropdown">`
        #     intercepts pointer events 导致 Playwright 真实 click 被拦截 5s,
        #     只能 fallback 到 `evaluate(el.click())` 才能成功。
        #   做 3 招清场:
        #     1) 发送 ESC(Element UI 的弹层/下拉几乎都会响应 ESC 关闭);
        #     2) 对所有打开的 el-popper / el-dropdown-menu 调 JS remove() /
        #        设置 display:none (避免 user-dropdown 这类 hover 残留的下拉);
        #     3) 最后点击页面 body 左上角绝对空白 (x=8,y=8,一般是无元素的背景),
        #        触发 Element UI 的"点击空白关闭"机制。
        # ═══════════════════════════════════════════════════════════
        try:
            # (1) ESC 按 2 次,间隔 50ms
            for _esc_i in range(2):
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.05)
            # (2) JS 移除/隐藏所有弹层: el-popper / el-dialog__wrapper / el-drawer__wrapper / el-picker-panel
            _popper_cleaned = await page.evaluate("""() => {
                // 2026-08-14 修复: 只移除 user-dropdown / el-dropdown-menu 这两类明确遮挡
                // 点击的弹层。之前对所有 .el-popper 设 display:none 太暴力,
                // BMS 某些页面把筛选栏也包在 .el-popper 里 → 整个查询/导出按钮
                // 全部被隐藏 → ANY_CLICKABLE 从 26 暴跌到 3 → 后续策略只能乱点。
                const sel = '.el-dropdown-menu, .user-dropdown';
                const list = document.querySelectorAll(sel);
                let n = 0, rm = 0;
                for (let i = 0; i < list.length; i++) {
                    const el = list[i];
                    try {
                        el.parentNode && el.parentNode.removeChild(el);
                        rm++;
                    } catch(_) {
                        el.style.display = 'none';
                    }
                    n++;
                }
                return {matched: n, removed: rm};
            }""")
            logger.info(f"  [遮挡层预处理-清理弹层] {_popper_cleaned!r}")
            # (3) 点击左上角空白(x=8,y=8),触发 Element UI "click-outside" 关闭规则
            try:
                await page.mouse.click(8, 8)
                await asyncio.sleep(0.05)
                # 再点一次(有的弹层需要两下空白才能清掉)
                await page.mouse.click(8, 8)
                logger.info("  [遮挡层预处理] 左上角空白点击 2 次 OK")
            except Exception as mc_e:
                logger.warning(f"  [遮挡层预处理] 鼠标点空白失败(跳过): {mc_e}")
        except Exception as pre_err:
            logger.warning(f"  [遮挡层预处理] 整体异常(不影响后续策略): {pre_err}")

        # ═══════════════════════════════════════════════════════════
        # 策略0-retry: 遮挡层清理后,重新查询按钮再试一次 Playwright 原生 click
        #   关键: 遮挡层清理可能通过 removeChild 把按钮的父元素也删了,
        #   导致旧 element handle 变成 detached DOM → Playwright click 报
        #   "Element is not attached to the DOM" → 降级 JS click → Vue 不触发。
        #   所以这里用全新的 query_selector 重新获取按钮引用。
        # ═══════════════════════════════════════════════════════════
        for _kw in ["搜索", "查询", "搜 索", "查 询"]:
            try:
                _btn = await page.query_selector(f'button:has-text("{_kw}"):visible')
                if _btn:
                    await _btn.click(timeout=5000, force=True)
                    logger.info(f"  [策略0-重试] 遮挡层清理后 button:has-text({_kw!r}) force-click 成功 ✅")
                    return
            except Exception:
                continue

        try:
            # ── 1) 文本关键词匹配 (扩大候选 DOM) ──
            btns = await page.query_selector_all(ANY_CLICKABLE)
            logger.info(f"  [策略1-关键词] ANY_CLICKABLE 命中 {len(btns)} 个可见元素")
            scanned_preview = await _build_buttons_snapshot(max_items=15)
            if scanned_preview:
                # 成功/失败路径都打印前 15 个的摘要, 成功路径日志也能让用户知道当前页有哪些按钮
                logger.info("  [策略1-关键词] 候选摘要(前15个):\n" + "\n".join(scanned_preview))
            s1_miss_reason_counts: dict[str, int] = {"no_text": 0, "excluded": 0, "kw_not_match": 0}
            for idx, b in enumerate(btns):
                try:
                    txt = (await b.inner_text()).strip()
                except Exception:
                    txt = ""
                al_source = ""
                if not txt:
                    # 再尝试 aria-label (伪按钮常把文案放 aria-label 而不是 innerText)
                    try:
                        al = await b.get_attribute("aria-label") or ""
                        txt = al.strip()
                        if txt:
                            al_source = " [aria]"
                    except Exception:
                        txt = ""
                if not txt:
                    s1_miss_reason_counts["no_text"] += 1
                    continue
                matched_kw = next((kw for kw in keywords if kw in txt), None)
                excluded = next((kw for kw in exclude_keywords if kw in txt), None)
                if matched_kw and not excluded:
                    tag = (await b.evaluate("e=>e.tagName") or "?").upper()
                    logger.info(
                        f"  [策略1-关键词#{idx}] 命中! keyword={matched_kw!r} text={txt!r}{al_source} tag={tag} → 执行点击"
                    )
                    try:
                        await b.click(timeout=5000)
                        logger.info(f"  [策略1-关键词#{idx}] click() 正常返回 OK")
                        return
                    except Exception as click_err:
                        logger.warning(f"  [策略1-关键词#{idx}] Playwright click 异常: {click_err}, 改用 JS 点击")
                        await b.evaluate("(el) => el.click()")
                        logger.info(f"  [策略1-关键词#{idx}] JS evaluate click 已返回 OK")
                        return
                elif excluded:
                    s1_miss_reason_counts["excluded"] += 1
                else:
                    s1_miss_reason_counts["kw_not_match"] += 1
            logger.info(
                f"  [策略1-关键词] 全部未命中 原因统计: {s1_miss_reason_counts!r}"
                f" (无文案={s1_miss_reason_counts['no_text']} 个/被排除词命中={s1_miss_reason_counts['excluded']}"
                f" 个/关键词不匹配={s1_miss_reason_counts['kw_not_match']} 个)"
            )

            # ── 2) 找筛选条(y 坐标合理范围) 里第一个非「重置/导出」可点击元素 ──
            y_sorted = []
            for idx, b in enumerate(btns):
                try:
                    box = await b.bounding_box()
                    if not box:
                        continue
                    # 放宽 y 上限到 800 (1080p 屏幕下筛选条不会在 800+ 位置; 之前 600 可能会漏掉高分辨率页面)
                    if box["y"] < 60 or box["y"] > 800:
                        continue
                    txt = ""
                    try:
                        txt = (await b.inner_text()).strip()
                    except Exception:
                        pass
                    if not txt:
                        try:
                            al = await b.get_attribute("aria-label") or ""
                            txt = al.strip()
                        except Exception:
                            pass
                    if any(kw in txt for kw in exclude_keywords):
                        continue
                    y_sorted.append((box["y"], box["x"], idx, b, txt))
                except Exception:
                    continue
            y_sorted.sort(key=lambda r: (r[0], r[1]))
            logger.info(
                f"  [策略2-y坐标] y∈[60,800] 且非重置/导出 候选共 {len(y_sorted)} 个"
                + (
                    f" → Top5: "
                    + " / ".join(
                        f"#{idx}[{y},{x}]{txt!r}" for (y, x, idx, _b, txt) in y_sorted[:5]
                    )
                    if y_sorted
                    else " (空, 进入策略3)"
                )
            )
            if y_sorted:
                y, x, idx, b, txt = y_sorted[0]
                tag = (await b.evaluate("e=>e.tagName") or "?").upper()
                logger.info(
                    f"  [策略2-y坐标] 选中第1个 候选序号=#{idx} pos=({x},{y}) tag={tag} text={txt!r} → 执行点击"
                )
                try:
                    await b.click(timeout=5000)
                    logger.info(f"  [策略2-y坐标] click() 正常返回 OK")
                    return
                except Exception as ce:
                    logger.warning(f"  [策略2-y坐标] Playwright click 异常: {ce}, 改用 JS 点击")
                    await b.evaluate("(el) => el.click()")
                    logger.info(f"  [策略2-y坐标] JS evaluate click 已返回 OK")
                    return
        except Exception as e:
            logger.warning(f"  查询按钮点击策略(1-2)异常: {e}", exc_info=True)
        # ── 3) JS 全局查找扩大范围内可点击并点击 (兜底) ──
        logger.info("  [策略3-JS全局] 进入 evaluate 全文档搜索(ANY_CLICKABLE 去掉 :visible)...")
        try:
            js_sel = ANY_CLICKABLE.replace(":visible", "")
            clicked_and_stats = await page.evaluate(
                """(kw_list, excl_list, any_sel) => {
                    const btns = document.querySelectorAll(any_sel);
                    const stats = {total: btns.length, noText: 0, excluded: 0, kwNotMatch: 0, considered: []};
                    for (let i = 0; i < btns.length; i++) {
                        const b = btns[i];
                        const it = (b.innerText || '').replace(/\\s+/g,' ').trim();
                        const al = (b.getAttribute && b.getAttribute('aria-label')) || '';
                        const txt = (it + ' ' + al).trim();
                        const cls = (b.className && typeof b.className === 'string') ? b.className : '';
                        if (!txt) { stats.noText++; continue; }
                        const matched = kw_list.find(k => txt.includes(k));
                        const excluded = excl_list.find(k => txt.includes(k));
                        if (matched && !excluded) {
                            b.click();
                            stats.hit = {idx: i, tag: b.tagName, text: txt, cls: cls, matchedKw: matched};
                            return stats;
                        }
                        if (excluded) stats.excluded++; else stats.kwNotMatch++;
                        if (stats.considered.length < 20) {
                            stats.considered.push({idx:i, tag:b.tagName, text:txt.slice(0,40), cls:cls.slice(0,60)});
                        }
                    }
                    return stats;
                }""",
                keywords, exclude_keywords, js_sel,
            )
            total = clicked_and_stats.get("total", -1)
            if isinstance(clicked_and_stats, dict) and clicked_and_stats.get("hit"):
                hit = clicked_and_stats["hit"]
                logger.info(
                    f"  [策略3-JS全局] 成功! 候选总数={total},"
                    f" 命中 idx={hit.get('idx')} tag={hit.get('tag')} keyword={hit.get('matchedKw')!r} text={hit.get('text')!r}"
                )
                return
            # 没命中, 打印候选概况 (这里打到 warning 因为已经到策略3了, 意味着前面都失败, 需要多一点上下文)
            logger.warning(
                f"  [策略3-JS全局] evaluate 没有命中 候选={total} 个"
                f" noText={clicked_and_stats.get('noText')} excluded={clicked_and_stats.get('excluded')}"
                f" kwNotMatch={clicked_and_stats.get('kwNotMatch')}"
            )
            cons = clicked_and_stats.get("considered") or []
            if cons:
                logger.warning(
                    "  [策略3-JS全局] 评估过的前" + str(len(cons)) + "个元素:\n"
                    + "\n".join(
                        f"    [#{c.get('idx')}] {c.get('tag')} txt={c.get('text')!r} cls={c.get('cls')!r}"
                        for c in cons
                    )
                )
        except Exception as e:
            logger.warning(f"  [策略3-JS全局] evaluate 抛异常: {e}", exc_info=True)
        # ── 4) Enter 兜底 - 聚焦第一个筛选框后按 Enter ──
        logger.info("  [策略4-Enter] 进入键盘 Enter 兜底...")
        try:
            date_inp = await page.query_selector('input.el-range-input:visible')
            if date_inp:
                logger.info("  [策略4-Enter] 找到 el-range-input, 聚焦后 Enter")
                await date_inp.focus()
                await asyncio.sleep(0.1)
                await page.keyboard.press("Enter")
                logger.info("  Enter 兜底触发查询(日期框) OK")
                return
            last_inp = page.locator('input:visible').last
            if await last_inp.count() > 0:
                try:
                    li_place = await last_inp.get_attribute("placeholder")
                except Exception:
                    li_place = None
                logger.info(f"  [策略4-Enter] 回退到最后一个可见 input (placeholder={li_place!r}) 聚焦后 Enter")
                await last_inp.focus(timeout=1000)
                await asyncio.sleep(0.1)
                await page.keyboard.press("Enter")
                logger.info("  Enter 兜底触发查询(最后一个输入框) OK")
                return
            logger.warning("  [策略4-Enter] 页面上没有任何可见 input, 跳过")
        except Exception as e:
            logger.warning(f"  [策略4-Enter] 异常: {e}", exc_info=True)
        # ── 5) 2026-08-14 新增: 最后两层终极兜底 ──
        #    5a. 找 form 包着的输入并 submit()
        #    5b. 找 [aria-label ~= 搜索/查询] 或 class*="submit"/"search-submit"/"query-submit" 的元素
        logger.info("  [策略5-终极兜底] 进入 form.submit + aria-label/submit-class 兜底...")
        try:
            submit_ok = await page.evaluate("""() => {
                const out = {};
                // 5a. 找包含至少 1 个日期输入的 form, 提交它
                const forms = document.querySelectorAll('form');
                out.formCount = forms.length;
                for (let i = 0; i < forms.length; i++) {
                    const f = forms[i];
                    const hasDate = f.querySelector('input[placeholder*="日期"],.el-date-editor,input[type="date"]');
                    if (hasDate) {
                        out.hitForm = {idx: i, hasDate: true, formClass: (f.className||'').slice(0,80)};
                        if (typeof f.requestSubmit === 'function') f.requestSubmit();
                        else f.submit();
                        out.hitForm.by = 'requestSubmit';
                        return out;
                    }
                }
                // 5b. aria-label 搜 搜索/查询
                const byAria = document.querySelector(
                    '[aria-label*="搜索" i],[aria-label*="查询" i],' +
                    '[class*="submit-btn" i],[class*="search-submit" i],[class*="query-submit" i]'
                );
                if (byAria && byAria.click) {
                    out.hitAria = {
                        tag: byAria.tagName,
                        ariaLabel: (byAria.getAttribute && byAria.getAttribute('aria-label')) || '',
                        className: (byAria.className && typeof byAria.className === 'string') ? byAria.className.slice(0,80) : ''
                    };
                    byAria.click();
                    return out;
                }
                return out;
            }""")
            logger.info(f"  [策略5-终极兜底] evaluate 返回结果: {submit_ok!r}")
            if submit_ok and (submit_ok.get("hitForm") or submit_ok.get("hitAria")):
                what = submit_ok.get("hitForm") and "form.requestSubmit" or "aria-label/submit-class"
                detail = submit_ok.get("hitForm") or submit_ok.get("hitAria")
                logger.info(f"  [策略5-终极兜底] 命中 {what} 分支: detail={detail!r}")
                return
            logger.warning(
                f"  [策略5-终极兜底] 没有命中任何分支 formCount={submit_ok.get('formCount')} 没有含日期form / aria/submit-class 也没找到"
            )
        except Exception as e:
            logger.warning(f"  [策略5-终极兜底] 异常: {e}", exc_info=True)

        # ═══════════════════════════════════════════════════════════
        # 全部 5 层兜底都没命中 → 在抛错前以 ERROR 级别输出全量候选 DOM 快照
        # ═══════════════════════════════════════════════════════════
        try:
            full_snap = await _build_buttons_snapshot(max_items=None)
            if full_snap:
                logger.error(
                    "  [兜底全部失败] ANY_CLICKABLE 全量按钮快照("
                    + f"URL={page_url_for_log} 共 {len(full_snap)} 个元素):\n"
                    + "\n".join(full_snap)
                )
            else:
                logger.error(
                    f"  [兜底全部失败] 页面上 ANY_CLICKABLE 一个都没命中! URL={page_url_for_log}"
                    " 请人工检查: 是不是按钮选择器需要再扩展?"
                )
        except Exception as snap_err:
            logger.error(f"  [兜底全部失败] 打印快照失败: {snap_err}", exc_info=True)

        raise RuntimeError(
            "找不到任何查询/搜索按钮,所有兜底均失败 (候选已扩大到 div/span/a 伪按钮;"
            " 详细排查请搜日志「[策略1-关键词] 候选摘要」与「[兜底全部失败] 全量按钮快照」)"
        )

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
            # ⚠️ 2026-09-01 真根因修复: BMS 前端 Vue DatePicker 用浏览器本地时间作为
            #   "当前时刻"并据此截断查询窗口。容器默认时区=UTC, 导致 BMS 认为当前是 UTC
            #   (北京-8h), 即使填入正确的北京时间 end, 也会被截断到 8h 前 → 稳定 8h 滞后。
            #   这里强制浏览器时区=Asia/Shanghai, 让 BMS 前端的"当前时刻"= 北京时间。
            "timezone_id": "Asia/Shanghai",
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
                # ⚠️ 2026-08-12: SPA hash 路由跳转时 networkidle 立即 resolve,改用
                #   domcontentloaded + 显式等待登录表单渲染(同 _do_fetch_one_shared 修复)
                await page.goto(login_url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_selector(
                        'input[name="username"], input[placeholder*="账号" i], input[placeholder*="用户名" i]',
                        state="visible", timeout=10000,
                    )
                except Exception:
                    await asyncio.sleep(2)

                # ⚠️ 必须用 locator(...).first.fill(), 不能直接 page.fill(选择器字符串列表).
                #    当页面有多个匹配元素时(比如顶部导航搜索框 + 登录表单账号框),page.fill() 会在
                #    "ambiguous match" 分支下一直等到 30 秒超时,表现为 Page.fill Timeout 而没有
                #    报错根因,用户侧只看到"登录死/抓取死".用 .first 可以稳扎稳打取第一个表单里的元素.
                # ⚠️ 2026-08-12: 排除 readonly el-select 内部 input,避免命中"请选择"下拉框
                user_sel = (
                    'input[name="username"]:not([readonly]):not([disabled]),'
                    'input[placeholder*="账号" i]:not([readonly]):not([disabled]),'
                    'input[placeholder*="用户名" i]:not([readonly]):not([disabled]),'
                    'input[type="text"]:not([readonly]):not([disabled])'
                )
                try:
                    await page.locator(user_sel).first.fill(username, timeout=15000)
                except Exception:
                    await page.locator("input:visible:not([readonly]):not([disabled])").first.fill(username, timeout=15000)
                pwd_sel = 'input[type="password"], input[placeholder*="密码" i]'
                await page.locator(pwd_sel).first.fill(password, timeout=15000)

                # ── 闪猫:验证码处理(自动识别优先,最多 3 次重试,失败再提示手动登录) ──
                ocr_logged_in = False
                if config.get("requires_captcha"):
                    captcha_input = await page.query_selector(
                        'input[placeholder*="验证码"], input[aria-label*="验证码"]'
                    )
                    if captcha_input and await captcha_input.is_visible():
                        ok, msg = await _try_auto_login_via_ocr(
                            page, context, data_type, login_url, username, password, state_path
                        )
                        if not ok:
                            raise RuntimeError(
                                "验证码自动识别登录失败(" + msg + ");"
                                "请前往「数据中心-数据采集-同步设置」点击「登录闪猫」按钮完成手动登录"
                            )
                        # OCR 登录成功:内部已经做完填单/点登录/保存 storage_state,跳过下面的 click 登录
                        ocr_logged_in = True
                        logger.info(f"[{data_type}] OCR 自动登录完成,跳过后续点击登录步骤")

                if not ocr_logged_in:
                    await asyncio.sleep(1)
                    await page.click('button:has-text("登录"), button[type="submit"]')
                    await page.wait_for_load_state("networkidle", timeout=15_000)
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

                await self._wait_until_page_has_any_clickable(page)

            # ⚠️ 2026-08-12: 标记登录已完成(无论 storage_state 有效还是刚登录成功)
            result.logged_in = True

            # 3. 设置时间范围
            time_format = config.get("time_format", "%Y-%m-%d %H:%M:%S")
            # ⚠️ 2026-09-01 修复: 真根因是浏览器时区=UTC导致 BMS 前端"当前时刻"错位,
            #   已在 new_context 设 timezone_id='Asia/Shanghai'。因此这里填**北京时间**(fill_utc=False), 与用户手动导出一致。
            fill_utc = bool(config.get("time_fill_utc", _BMS_FILL_TZ_UTC_DEFAULT))
            start_str = _bms_time_str(start_date, time_format, fill_utc=fill_utc)
            end_str = _bms_time_str(end_date, time_format, fill_utc=fill_utc)
            logger.info(
                f"[{data_type}] 设置时间范围: {start_str} ~ {end_str} (fill_utc={fill_utc}, format={time_format})"
            )

            # 3.1 如果配置了 time_labels(页面有多个时间筛选框),按标签精确查找
            time_labels = config.get("time_labels")
            if time_labels and len(time_labels) >= 2:
                await self._set_time_by_labels(page, time_labels[0], time_labels[1], start_str, end_str)
            else:
                # 3.2 通用策略:找页面上的日期范围选择器
                await self._set_time_range(page, start_str, end_str)
            await asyncio.sleep(1)

            # 4. 搜索
            logger.info(f"[{data_type}] 点击查询/搜索...")
            await self._click_query_button(page)

            # 4.1 【shanmao_refund/需要日期筛选的数据源】轮询分页器「共 x 条」,确认筛选真的生效
            #     原问题:闪猫退款前端组件 v-model 可能更新不及时,或用户筛选条件未同步到查询接口,
            #           导致查询结果仍为全量(1976 条),后续导出也必为全量。
            initial_total = None
            after_total = None
            try:
                for poll_i in range(20):
                    await asyncio.sleep(1.0 if poll_i < 8 else 0.75)
                    try:
                        total_text = await page.evaluate("""() => {
                            const p = document.querySelector('.el-pagination__total');
                            if (!p) {
                                // fallback:整个 body 里搜 "共 xxx 条"
                                const m = (document.body.innerText || '').match(/共\\s*(\\d+(?:,\\d+)?)\\s*条/);
                                return m ? m[1].replace(/,/g,'') : null;
                            }
                            const m = (p.innerText || '').match(/共\\s*(\\d+(?:,\\d+)?)\\s*条/);
                            return m ? m[1].replace(/,/g,'') : null;
                        }""")
                        if total_text:
                            try:
                                tn = int(total_text)
                                if initial_total is None:
                                    initial_total = tn
                                else:
                                    after_total = tn
                                    # 若明显减少 (>100 条差距) 或 <100,认为筛选生效
                                    if (initial_total - tn > 100) or (tn < 100):
                                        logger.info(
                                            f"[{data_type}] 筛选生效(轮询 {poll_i+1}s): 初始 {initial_total} 条 → 现在 {tn} 条"
                                        )
                                        break
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[{data_type}] 轮询筛选结果总数异常(不影响流程): {e}")

            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            await asyncio.sleep(2)

            # 5. 导出
            #    ⚠️  闪猫退款平台 exportRefundApply 接口不接受任何请求参数!
            #       传任何非空 body(包括 JSON 字符串数组/毫秒时间戳数组)都会返回 HTTP 500 错误,
            #       只有空 body 才能 200 返回全量历史 xlsx(约 1976 行 172KB),
            #       因此这里不做任何 monkey patch / route 拦截,避免 500。
            #       筛选逻辑改到第 6.1 步:用 pandas 按「退费单号前缀 TFYYYYMMDD 或时间列」
            #       精确裁剪,只保留目标日期范围内的行再覆盖写回同一个 dest_path。
            # P1-1: 调用统一的 _download_with_retry (自动按 data_type 配置超时 + 重试)
            download = await _download_with_retry(page, data_type, logger, expect_end_dt=end_date)

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

            # 5.2 【极速校验】若文件 <16KB 且开头是 { → 极可能是接口 500/401 返回 JSON 伪 Excel!直接抛异常(否则 success=True 但文件根本打不开)!
            try:
                if dest_path.exists():
                    _sz = dest_path.stat().st_size
                    if _sz < 16384 and _sz > 0:
                        _head = dest_path.read_bytes()[:256]
                        _body = _head[3:] if _head[:3] == b'\xef\xbb\xbf' else _head
                        if _body[:1] == b'{':
                            _txt = _body.decode("utf-8", errors="replace")
                            _emsg = ""
                            try:
                                _info = json.loads(_txt)
                                _emsg = str(_info.get("msg") or _info.get("message") or _info)[:400]
                            except Exception:
                                _emsg = _txt[:400]
                            raise RuntimeError(
                                f"[{data_type}] 导出接口返回 JSON 错误(不是 Excel! size={_sz}B): {_emsg}"
                            )
            except RuntimeError:
                raise
            except Exception as _ck:
                logger.warning(f"[{data_type}] 文件头检查跳过: {_ck}")

            # 6.1 【shanmao_refund 核心:全量导出后按日期范围裁剪写回】
            #    闪猫导出接口不支持筛选参数(传参=500),只能拿到全量,
            #    因此用 pandas 精确保留 start_date~end_date 范围内的行再覆盖写回。
            #    裁剪逻辑提取为独立方法,_do_fetch_one 和 _do_fetch_one_shared 共用。
            if data_type == "shanmao_refund":
                dest_path, dest_filename = await self._crop_shanmao_refund_by_date_range(
                    dest_path, dest_filename, start_date, end_date, data_type
                )

            # 6.2 【refund:BMS 退费单全量导出后按日期范围裁剪写回】
            if data_type == "refund":
                dest_path, dest_filename = await self._crop_refund_by_date_range(
                    dest_path, dest_filename, start_date, end_date, data_type
                )

            result.success = True
            result.file_path = f"{month_dir}/{dest_filename}"
            result.filename = dest_filename
        finally:
            await context.close()

    async def _crop_shanmao_refund_by_date_range(
        self, dest_path, dest_filename, start_date, end_date, data_type
    ):
        """闪猫退款单导出后按日期范围裁剪写回(全量导出 → 仅保留目标日期范围行)

        闪猫导出接口不支持时间筛选参数(传参=500),只能拿到全量,
        因此用 pandas 精确保留 start_date~end_date 范围内的行再覆盖写回:
        - 策略1: 退费单号前缀 TFYYYYMMDD(100% 精确,因为单号里包含真实申请日期)
        - 策略2: 审核时间 / 申请时间 / 退款时间 / 创建时间 / 打款时间 等 between 判断

        裁剪后仍为空也不抛错(可能该日期确实无退款申请),只是写 0 行出去。
        .xls 老格式会转换为 .xlsx 写回,此时 dest_path/dest_filename 会变,需要返回更新后的值。
        """
        try:
            import pandas as pd
            suffix_l = str(dest_path).lower()
            if suffix_l.endswith(".xlsx"):
                df = pd.read_excel(dest_path, engine="openpyxl", dtype=str)
            elif suffix_l.endswith(".xls"):
                try:
                    df = pd.read_excel(dest_path, dtype=str)
                except Exception as exc:
                    raise RuntimeError(
                        f"[{data_type}] .xls 老格式缺少解析引擎(需 xlrd==1.2.0/pyxlsb): {exc}"
                    )
            else:
                df = pd.read_csv(dest_path, dtype=str)

            orig_rows = len(df)
            mask = None
            chosen_col = None
            # 策略1: 退费单号前缀 TFYYYYMMDD → 100% 精准(闪猫单号里嵌入了申请日期)
            if "退费单号" in df.columns:
                _pref = df["退费单号"].astype(str).str[:10]
                expected_prefix = start_date.strftime("TF%Y%m%d")
                mask = _pref == expected_prefix
                chosen_col = "退费单号 prefix=" + expected_prefix
                # 防御: 若 start_date != end_date(跨天范围),改用逐行 datetime 解析 退单号里的日期
                if (end_date - start_date).days != 0:
                    try:
                        _ymd = _pref.str[2:10]  # "TF" 后面的 YYYYMMDD
                        _dates = pd.to_datetime(_ymd, format="%Y%m%d", errors="coerce")
                        sd = pd.to_datetime(start_date.date())
                        ed = pd.to_datetime(end_date.date())
                        mask = (_dates >= sd) & (_dates <= ed)
                        chosen_col = "退费单号 YYYYMMDD in range"
                    except Exception:
                        pass
            # 策略2: 审核时间 / 申请时间 / 打款时间 等 between
            if mask is None:
                # ⚠️ 2026-09-01: 边界统一转 naive 北京时间, 与文件内 naive 时间列比较,
                #   否则 aware vs naive 抛 TypeError → 裁剪静默失效(全量保留)。
                try:
                    sd_naive, ed_naive = _crop_bounds_naive(start_date, end_date)
                except Exception:
                    sd_naive = ed_naive = None
                for col in ("审核时间", "申请时间", "退款时间", "创建时间", "打款时间"):
                    if col in df.columns:
                        try:
                            vs = pd.to_datetime(df[col].dropna(), errors="coerce")
                            if sd_naive is not None:
                                mask = (vs >= sd_naive) & (vs <= ed_naive)
                            else:
                                mask = pd.Series(False, index=vs.index)
                            chosen_col = f"{col} between [{sd_naive},{ed_naive}]"
                            # 把空行(NaT)视为 False
                            mask = mask.fillna(False)
                            if len(mask) != len(df):
                                # 索引对齐, 缺失行 False
                                _tmp = pd.Series(False, index=df.index)
                                _tmp.loc[mask.index[mask.values]] = True
                                mask = _tmp
                            break
                        except Exception:
                            continue
            if mask is None:
                logger.warning(
                    f"[{data_type}] ⚠️  找不到任何可用于日期范围裁剪的列(退费单号/审核时间/申请时间...)"
                    f", 保留原 xlsx 的 {orig_rows} 行(未裁剪)。请检查导出列命名是否变更?"
                )
            else:
                df_filtered = df.loc[mask].copy()
                kept = len(df_filtered)
                logger.info(
                    f"[{data_type}] 日期范围裁剪完成: 依据列={chosen_col}, 原 {orig_rows} 行 → 保留 {kept} 行"
                    f" (范围 {start_date.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_date.strftime('%Y-%m-%d %H:%M:%S')})"
                )
                # ★ 覆盖写回同一个 dest_path → 下游任务看到的就是裁剪后的 xlsx
                if suffix_l.endswith(".xlsx"):
                    with pd.ExcelWriter(dest_path, engine="openpyxl") as w:
                        df_filtered.to_excel(w, index=False)
                elif suffix_l.endswith(".xls"):
                    # 老格式尽量写 xlsx(覆盖扩展),避免缺少 xlwt 写失败
                    new_p = dest_path.with_suffix(".xlsx")
                    if new_p != dest_path:
                        try: dest_path.unlink()
                        except: pass
                        dest_path = new_p
                        dest_filename = dest_path.name
                    with pd.ExcelWriter(str(dest_path), engine="openpyxl") as w:
                        df_filtered.to_excel(w, index=False)
                else:
                    df_filtered.to_csv(dest_path, index=False)
                logger.info(
                    f"[{data_type}] ✅ 裁剪后文件已覆盖写回 {dest_path} (最终 {kept} 行)"
                )
                # 读回来再做一次校验(避免写回失败)
                try:
                    if str(dest_path).lower().endswith(".xlsx"):
                        df_check = pd.read_excel(dest_path, engine="openpyxl", dtype=str)
                    else:
                        df_check = pd.read_csv(dest_path, dtype=str)
                    logger.info(
                        f"[{data_type}] 二次校验 OK:写回后实际 rows={len(df_check)}"
                    )
                except Exception as wb:
                    logger.warning(f"[{data_type}] ⚠️  裁剪后二次读取失败(不影响主流程): {wb}")
        except Exception as e:
            logger.warning(f"[{data_type}] 日期范围裁剪/写回失败(保留原文件,不影响主流程): {type(e).__name__}: {e}")
        return dest_path, dest_filename

    async def _crop_refund_by_date_range(
        self, dest_path, dest_filename, start_date, end_date, data_type
    ):
        """BMS 退费单导出后按日期范围裁剪写回(全量导出 → 仅保留目标日期范围行)

        BMS exportRefundApply 导出接口不接受筛选参数,只能拿到全量,
        因此用 pandas 精确保留 start_date~end_date 范围内的行再覆盖写回:
        - 主策略: 创建时间 between start_date ~ end_date
        - 辅策略: 退费单号中嵌入的 YYYYMMDD 日期(BMS 单号如 BR2026022502165999775)

        裁剪后仍为空也不抛错(可能该日期确实无退款申请),只是写 0 行出去。
        """
        try:
            import pandas as pd
            suffix_l = str(dest_path).lower()
            if suffix_l.endswith(".xlsx"):
                df = pd.read_excel(dest_path, engine="openpyxl", dtype=str)
            elif suffix_l.endswith(".xls"):
                try:
                    df = pd.read_excel(dest_path, dtype=str)
                except Exception as exc:
                    raise RuntimeError(
                        f"[{data_type}] .xls 老格式缺少解析引擎(需 xlrd==1.2.0/pyxlsb): {exc}"
                    )
            else:
                # BMS 导出 CSV 问题: (1) 每行末尾多一个 \t 导致字段数+1(末尾空字段)
                #   (2) 部分行内部含换行/\t 导致字段严重错位(如 18→10 字段)
                #   (3) pandas on_bad_lines="skip" 在"字段数+1"场景下会硬塞,
                #       导致"实退金额"列被塞入【退微信】等文本→数值溢出
                # 修复: 用 data_parser 的修复+校验方案 (2026-08-26)
                #   贪婪拼接截断行(含跨行换行), 合并多余字段, 后校验退费单号
                import csv as _csv
                import io as _io
                raw_text = Path(dest_path).read_text(encoding="utf-8-sig", errors="replace")
                raw_lines = raw_text.splitlines()
                if not raw_lines:
                    raise RuntimeError(f"[{data_type}] CSV 文件为空")

                # 解析表头
                header_fields = next(_csv.reader(_io.StringIO(raw_lines[0])))
                header_fields = [h.rstrip("\t") for h in header_fields]
                expected_n = len(header_fields)

                # 修复+校验: 贪婪拼接截断行, 多于字段合并到末列, 后校验退费单号
                from app.services.data_parser import repair_csv_blocks
                clean_rows, skipped = repair_csv_blocks(
                    raw_lines=raw_lines[1:],
                    header_len=expected_n,
                    bms_clean=True,
                    log_prefix=f"[{data_type}]",
                    logger=logger,
                )

                # 重新拼成 CSV 文本交给 pandas
                buf = _io.StringIO()
                w = _csv.writer(buf)
                w.writerow(header_fields)
                for r in clean_rows:
                    w.writerow(r)
                df = pd.read_csv(_io.StringIO(buf.getvalue()), dtype=str)
                if skipped > 0:
                    logger.info(
                        f"[{data_type}] 修复+校验: 跳过 {skipped} 行校验失败的截断行"
                        f"(期望 {expected_n} 字段), 保留 {len(df)} 行"
                    )

            if df.empty:
                logger.warning(f"[{data_type}] CSV 解析为空,跳过裁剪")
                return dest_path, dest_filename

            orig_rows = len(df)
            mask = None
            chosen_col = None
            # ⚠️ 2026-09-01: 边界统一转 naive 北京时间, 与文件内 naive 时间列比较,
            #   否则 aware vs naive 抛 TypeError → 裁剪静默失效(全量保留)。
            try:
                sd_naive, ed_naive = _crop_bounds_naive(start_date, end_date)
            except Exception:
                sd_naive = ed_naive = None

            # ⚠️ 2026-09-01 口径修复: BMS 页面"实际退款时间"筛选 = 更新时间(退款完成时刻),
            #   而"几天前创建、今天完成退款(已退款)"的单创建时间不在当天, 原实现按"创建时间"
            #   裁剪会把这批单全踢掉 → 看板"今日退款(已退款)"看不到当天实际完成的单。
            #   修复: 保留「创建时间 ∈ 窗口」∪「更新时间 ∈ 窗口 且 status=已退款」并集。
            #   注意: 更新时间含少量 03:15 SYS 批量覆盖(文档已注明), 因此只对 status='已退款'
            #   的单用更新时间补充, 避免把"系统批量触碰"的未退款单误拉入。
            mask_created = None
            mask_updated_refunded = None

            # 子集1(主): 创建时间 between —— 当天创建的退费单(含未完成)
            if "创建时间" in df.columns:
                try:
                    vs = pd.to_datetime(df["创建时间"], errors="coerce")
                    if sd_naive is not None:
                        mask_created = (vs >= sd_naive) & (vs <= ed_naive)
                    else:
                        mask_created = pd.Series(False, index=vs.index)
                    mask_created = mask_created.fillna(False)
                    logger.info(
                        f"[{data_type}] 裁剪-创建时间: 命中 {mask_created.sum()}/{orig_rows} 行"
                    )
                except Exception:
                    mask_created = None

            # 子集2(补): 更新时间 ∈ 窗口 且 status=已退款 —— 当天实际完成退款的单
            #   (可能创建于几天前, 按 BMS"实际退款时间"口径应计入当天)
            if "更新时间" in df.columns and "状态" in df.columns:
                try:
                    vu = pd.to_datetime(df["更新时间"], errors="coerce")
                    if sd_naive is not None:
                        mask_updated_refunded = (vu >= sd_naive) & (vu <= ed_naive) & (df["状态"].astype(str).str.strip() == "已退款")
                    else:
                        mask_updated_refunded = pd.Series(False, index=df.index)
                    mask_updated_refunded = mask_updated_refunded.fillna(False)
                    logger.info(
                        f"[{data_type}] 裁剪-更新时间(已退款): 命中 {mask_updated_refunded.sum()}/{orig_rows} 行"
                    )
                except Exception:
                    mask_updated_refunded = None

            # 合并: 并集(创建时间 或 更新时间已退款); 兜底退费单号/更新时间
            if mask_created is not None or mask_updated_refunded is not None:
                if mask_created is not None and mask_updated_refunded is not None:
                    mask = mask_created | mask_updated_refunded
                    chosen_col = "创建时间∈窗口 ∪ 更新时间∈窗口且已退款"
                    logger.info(
                        f"[{data_type}] 裁剪-并集: 保留 {mask.sum()}/{orig_rows} 行"
                    )
                elif mask_created is not None:
                    mask = mask_created
                    chosen_col = "创建时间 between"
                else:
                    mask = mask_updated_refunded
                    chosen_col = "更新时间∈窗口且已退款"

            # 兜底: 若上面都失效(如列缺失), 退费单号中嵌入日期 / 更新时间
            if mask is None or (isinstance(mask, pd.Series) and mask.sum() == 0):
                # 退费单号中嵌入日期 —— BMS 单号如 BR2026022502165999775(创建日期)
                if "退费单号" in df.columns:
                    try:
                        _nos = df["退费单号"].astype(str).str.strip()
                        _ymd = _nos.str.extract(r'^[A-Z]{2,3}(\d{4})(\d{2})(\d{2})', expand=False)
                        if _ymd is not None and len(_ymd) > 0:
                            _dates = pd.to_datetime(
                                _ymd[0].fillna('') + _ymd[1].fillna('') + _ymd[2].fillna(''),
                                format="%Y%m%d", errors="coerce"
                            )
                            sd_d = pd.Timestamp(start_date.date()) if start_date.tzinfo is None else pd.Timestamp(start_date.astimezone(timezone(timedelta(hours=8))).date())
                            ed_d = pd.Timestamp(end_date.date()) if end_date.tzinfo is None else pd.Timestamp(end_date.astimezone(timezone(timedelta(hours=8))).date())
                            mask = (_dates >= sd_d) & (_dates <= ed_d)
                            mask = mask.fillna(False)
                            chosen_col = f"退费单号嵌入日期 in range"
                            logger.info(
                                f"[{data_type}] 裁剪-退费单号: 命中 {mask.sum()}/{orig_rows} 行"
                            )
                    except Exception:
                        mask = None

            # 最后兜底: 更新时间 between(仅当前面全失效时)
            if mask is None or (isinstance(mask, pd.Series) and mask.sum() == 0):
                if "更新时间" in df.columns:
                    try:
                        vs = pd.to_datetime(df["更新时间"], errors="coerce")
                        if sd_naive is not None:
                            mask = (vs >= sd_naive) & (vs <= ed_naive)
                        else:
                            mask = pd.Series(False, index=vs.index)
                        mask = mask.fillna(False)
                        chosen_col = f"更新时间 between [{sd_naive},{ed_naive}]"
                        logger.info(
                            f"[{data_type}] 裁剪-更新时间兜底: 命中 {mask.sum()}/{orig_rows} 行"
                        )
                    except Exception:
                        mask = None

            if mask is None:
                logger.warning(
                    f"[{data_type}] ⚠️  找不到任何可用于日期范围裁剪的列(创建时间/退费单号/更新时间)"
                    f", 保留原文件的 {orig_rows} 行(未裁剪)。请检查导出列命名是否变更?"
                )
            else:
                df_filtered = df.loc[mask].copy()
                kept = len(df_filtered)
                logger.info(
                    f"[{data_type}] 日期范围裁剪完成: 依据列={chosen_col}, 原 {orig_rows} 行 → 保留 {kept} 行"
                    f" (范围 {start_date.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_date.strftime('%Y-%m-%d %H:%M:%S')})"
                )
                # ★ 覆盖写回同一个 dest_path → 下游任务看到的就是裁剪后的文件
                if suffix_l.endswith(".xlsx"):
                    with pd.ExcelWriter(dest_path, engine="openpyxl") as w:
                        df_filtered.to_excel(w, index=False)
                elif suffix_l.endswith(".xls"):
                    new_p = dest_path.with_suffix(".xlsx")
                    if new_p != dest_path:
                        try: dest_path.unlink()
                        except: pass
                        dest_path = new_p
                        dest_filename = dest_path.name
                    with pd.ExcelWriter(str(dest_path), engine="openpyxl") as w:
                        df_filtered.to_excel(w, index=False)
                else:
                    df_filtered.to_csv(dest_path, index=False)
                logger.info(
                    f"[{data_type}] ✅ 裁剪后文件已覆盖写回 {dest_path} (最终 {kept} 行)"
                )
                # 读回来再做一次校验(避免写回失败)
                try:
                    if str(dest_path).lower().endswith(".xlsx"):
                        df_check = pd.read_excel(dest_path, engine="openpyxl", dtype=str)
                    else:
                        df_check = pd.read_csv(dest_path, dtype=str)
                    logger.info(
                        f"[{data_type}] 二次校验 OK:写回后实际 rows={len(df_check)}"
                    )
                except Exception as wb:
                    logger.warning(f"[{data_type}] ⚠️  裁剪后二次读取失败(不影响主流程): {wb}")
        except Exception as e:
            logger.warning(f"[{data_type}] 日期范围裁剪/写回失败(保留原文件,不影响主流程): {type(e).__name__}: {e}")
        return dest_path, dest_filename

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
                # ⚠️ 2026-09-01 真根因修复: 同 _do_fetch_one, 强制浏览器时区=Asia/Shanghai,
                #   让 BMS 前端 DatePicker 的"当前时刻"= 北京时间, 避免查询窗口被截断到 8h 前。
                "timezone_id": "Asia/Shanghai",
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
            # ── 组级登录失败熔断记忆 ──
            # 同组 (base_url相同) 下的多个源共用一套账号密码;
            # 组内第一个源如果因"登录类错误"失败(账号/密码/验证码/页面跳转仍在/login),
            # 后续同组源直接跳过登录和抓取,复用同一个登录失败错误,避免 2/3 次相同登录错误反复弹日志。
            group_login_failed_err: str | None = None

            try:
                for i, task in enumerate(group_tasks):
                    # ═══════════════════════════════════════════════════════════
                    # 2026-08-13 关键修复: 同组(同 base_url) 的每个源用全新 page,
                    #   而不是共用同一个 page! 登录态保存在 context(cookies/
                    #   localStorage),page 只是渲染容器,重建 page 不会丢失登录。
                    #   此前:ticket 导出 8500+ 行后,页面 JS heap / Blob 内存膨胀,
                    #       ticket_finish/wallet 复用同一个 page,page.goto 直接
                    #       "Page crashed",整组 3 个源连锁失败。
                    #   现在:每处理一个源前 close 旧 page + new_page,
                    #       彻底隔离每轮 DOM/内存开销,同时登录态 context 共享省 3 次登录。
                    # ═══════════════════════════════════════════════════════════
                    try:
                        # 先尝试关闭当前 page(非首个,第 1 个源之前已 new_page 过)
                        if i > 0 and page is not None:
                            try:
                                await page.close()
                            except Exception:
                                pass
                        # 无论关闭是否成功,都从 context 新建干净的 page
                        page = await context.new_page()
                    except Exception as _e:
                        # 如果连 new_page 都失败,通常是 context 本身崩了,
                        # 这时只能标记组内后续源失败并 break 出组
                        logger.error(
                            f"[组{group_idx+1}] 创建新页面失败(context 可能已崩溃), "
                            f"源 {task.get('data_type')} 及后续同组源将失败: {_e}"
                        )
                        # 把当前 task 记失败 + 剩余 group_tasks 也标记失败
                        _cur_type = task.get("data_type")
                        _cur_cfg = task.get("config") or {}
                        _fr_cur = FetchResult(data_type=_cur_type or "", error=f"创建新页面失败: {_e}")
                        results_map[_cur_type] = _fr_cur
                        if on_progress:
                            await on_progress(_cur_type, "failed", _fr_cur)
                        for _j in range(i + 1, len(group_tasks)):
                            _t = group_tasks[_j]
                            _dt = _t["data_type"]
                            _fr2 = FetchResult(data_type=_dt, error=f"[同组页面创建失败连锁] {_e}")
                            results_map[_dt] = _fr2
                            if on_progress:
                                await on_progress(_dt, "failed", _fr2)
                        break  # 直接跳出组循环
                    data_type = task["data_type"]
                    start, end = task["start"], task["end"]
                    config = task["config"]
                    if on_progress:
                        await on_progress(data_type, "fetching", None)

                    fr = FetchResult()
                    fr.data_type = data_type
                    try:
                        # ⚠️ 同组已记过登录类失败 → 直接跳过,不再反复启登录表单 / OCR / 验证码重试
                        if group_login_failed_err is not None:
                            logger.warning(
                                "[组%s] %s 同组已记录登录类失败:%r, 直接跳过本次抓取(避免反复弹相同登录错误)",
                                group_idx + 1, data_type, group_login_failed_err,
                            )
                            fr.error = f"[同域登录熔断复用] {group_login_failed_err}"
                            results_map[data_type] = fr
                            if on_progress:
                                await on_progress(data_type, "done", fr)
                            # 即使跳过也要假装走了等待逻辑:组内非末位仍要等 65s,否则后续源在错误的时刻启动
                            is_last_in_group = (i == len(group_tasks) - 1)
                            is_last_group = (group_idx == len(groups) - 1)
                            if not is_last_in_group:
                                next_type = group_tasks[i + 1]["data_type"]
                                next_name = group_tasks[i + 1]["config"].get("name", next_type)
                                logger.info(
                                    f"[同域名内·熔断跳过后] 还需等待 {OPERATION_INTERVAL} 秒, 准备下一个源 {next_name}"
                                )
                                if on_progress:
                                    await on_progress(next_type, "waiting", None)
                                await asyncio.sleep(OPERATION_INTERVAL)
                            continue

                        # 单源抓取 + 复用登录态(组内首个源登录)
                        # ⚠️ 超时按 data_type 取(与 _download_with_retry 的超时配置一致):
                        #   wallet 600s / refund 180s / ticket 180s / 其他 120s
                        #   历史问题: 之前硬编码 timeout=180 导致 wallet 抓取 1-2 月大数据量时超时失败
                        _fetch_timeout = _DOWNLOAD_TIMEOUT_MS.get(data_type, _DOWNLOAD_DEFAULT_TIMEOUT_MS) // 1000
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
                            timeout=_fetch_timeout,
                        )
                        if fr.success:
                            logged_in_ok = True
                    except asyncio.TimeoutError:
                        fr.error = f"抓取超时({_fetch_timeout}秒): {config.get('name', data_type)}"
                        logger.error(fr.error)
                        # ⚠️ 2026-08-12: 即使下载超时,登录可能已成功 → 标记登录态供后续源复用
                        #   此前: refund 登录成功但下载超时 → logged_in_ok 未传递 → ticket
                        #   在 refund 残留页面上"重新登录" → 命中 readonly 下拉框 → 全组连锁失败
                        if fr.logged_in:
                            logged_in_ok = True
                            logger.info(f"[组{group_idx+1}] {data_type} 虽超时但登录已完成,后续源复用登录态")
                    except Exception as e:
                        # ── 2026-08-13 新增: Page crashed 自动重建干净 page ──
                        err_txt_full = str(e)
                        err_txt_lower = err_txt_full.lower()
                        page_crashed = any(k in err_txt_lower for k in (
                            "page crashed", "target page", "has been closed", "target closed",
                        ))
                        if page_crashed and fr.logged_in:
                            logger.warning(
                                f"[组{group_idx+1}] {data_type} 因 Page crashed 失败,但登录已成功;"
                                " 已在下次循环新建 page,登录态不丢失"
                            )
                            logged_in_ok = True
                        fr.error = f"抓取失败: {err_txt_full}"
                        logger.error(fr.error, exc_info=True)
                        # ⚠️ 记忆化: 如果是"登录类错误",写入 group_login_failed_err
                        if any(k in err_txt_lower for k in (
                            "登录失败", "账号或密码", "账号或密码错误", "用户名或密码", "验证码自动识别登录失败",
                            "登录", "密码", "验证码", "captcha", "vcode",
                        )) and not page_crashed:
                            group_login_failed_err = err_txt_full
                            logger.warning(
                                "[组%s] 源 %s 登录类失败:%r; 组内剩余 %d 个同源将直接跳过登录抓取",
                                group_idx + 1, data_type, group_login_failed_err,
                                max(0, len(group_tasks) - i - 1),
                            )
                        # ⚠️ 2026-08-12: 非登录类错误(如下载/设置时间失败)发生在登录之后,
                        #   登录态仍可复用,避免后续源在残留页面上"重新登录"
                        if fr.logged_in and not group_login_failed_err:
                            logged_in_ok = True

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
                # 关闭当前 page,再关闭 context(Playwright 官方推荐顺序,避免上下文泄露)
                try:
                    if page is not None:
                        await page.close()
                except Exception:
                    pass
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
                # ⚠️ 2026-08-12: SPA hash 路由(如 #/pay/refund-manage → #/login)变化时,
                #   wait_until="networkidle" 会立即 resolve(无新网络请求),但 SPA 视图
                #   可能尚未切换 → 登录表单未渲染 → input:visible.first 命中上一页的
                #   readonly el-select 筛选下拉框(placeholder="请选择") → fill 超时。
                #   修复: 用 domcontentloaded + 显式等待登录表单的 username 输入框出现。
                await page.goto(login_url, wait_until="domcontentloaded")
                # 显式等待登录表单的账号输入框出现(确认 SPA 已切换到 login 视图)
                try:
                    await page.wait_for_selector(
                        'input[name="username"], input[placeholder*="账号" i], input[placeholder*="用户名" i]',
                        state="visible", timeout=10000,
                    )
                except Exception:
                    # 兜底: 如果选择器没命中,等 2s 让 SPA 完成渲染
                    await asyncio.sleep(2)

                # ⚠️ 选择器排除 readonly: el-select 下拉框内部 input 是 type="text" + readonly,
                #   此前 input[type="text"] 会优先命中它 → fill "element is not editable" 超时
                user_sel = (
                    'input[name="username"]:not([readonly]):not([disabled]),'
                    'input[placeholder*="账号" i]:not([readonly]):not([disabled]),'
                    'input[placeholder*="用户名" i]:not([readonly]):not([disabled]),'
                    'input[type="text"]:not([readonly]):not([disabled])'
                )
                try:
                    await page.locator(user_sel).first.fill(username, timeout=15000)
                except Exception:
                    await page.locator("input:visible:not([readonly]):not([disabled])").first.fill(username, timeout=15000)
                pwd_sel = 'input[type="password"], input[placeholder*="密码" i]'
                await page.locator(pwd_sel).first.fill(password, timeout=15000)

                # ── 闪猫:验证码自动识别登录(失败才提示需手动) ──
                ocr_logged_in = False
                if config.get("requires_captcha"):
                    captcha_input = await page.query_selector(
                        'input[placeholder*="验证码"], input[aria-label*="验证码"]'
                    )
                    if captcha_input and await captcha_input.is_visible():
                        ok, msg = await _try_auto_login_via_ocr(
                            page, context, data_type, login_url, username, password, state_path
                        )
                        if not ok:
                            raise RuntimeError(
                                "验证码自动识别登录失败(" + msg + ");"
                                "请前往「数据中心-数据采集-同步设置」点击「登录闪猫」按钮完成手动登录"
                            )
                        ocr_logged_in = True
                        logger.info(f"[{data_type}] OCR 自动登录完成(组内首个源),跳过后续点击登录步骤")

                if not ocr_logged_in:
                    await asyncio.sleep(1)
                    await page.click('button:has-text("登录"), button[type="submit"]')
                    await page.wait_for_load_state("networkidle", timeout=15_000)
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

        # ⚠️ 2026-08-12: 标记登录已完成 — 即使后续下载超时被 asyncio.wait_for 取消,
        #   此标记已写入 result,调用方可据此让同组后续源复用登录态,避免在残留页面上"重新登录"
        result.logged_in = True

        # 2. 进入目标页面
        logger.info(f"[{data_type}] 进入 {config.get('name', data_type)}...")
        await page.goto(page_url, wait_until="networkidle")
        await asyncio.sleep(3)

        await self._wait_until_page_has_any_clickable(page)

        # 3. 设置时间范围
        # ⚠️ 2026-09-01 修复: 同 _do_fetch_one, 真根因是浏览器时区=UTC(已设 timezone_id=Asia/Shanghai),
        #   这里填**北京时间**(fill_utc=False), 与用户手动导出一致。
        _time_format = config.get("time_format", "%Y-%m-%d %H:%M:%S")
        _fill_utc = bool(config.get("time_fill_utc", _BMS_FILL_TZ_UTC_DEFAULT))
        start_str = _bms_time_str(start_date, _time_format, fill_utc=_fill_utc)
        end_str = _bms_time_str(end_date, _time_format, fill_utc=_fill_utc)
        logger.info(f"[{data_type}] 设置时间范围: {start_str} ~ {end_str} (fill_utc={_fill_utc})")

        time_labels = config.get("time_labels")
        if time_labels and len(time_labels) >= 2:
            await self._set_time_by_labels(page, time_labels[0], time_labels[1], start_str, end_str)
        else:
            await self._set_time_range(page, start_str, end_str)
        await asyncio.sleep(1)

        # 4. 搜索 — 带页面卡死检测 + 刷新重试
        # ⚠️ 2026-08-11 修复: BMS 导出有"1分钟内请勿重复操作"限流
        #   历史问题: 之前 _download_with_retry 重试时连续点导出按钮,触发限流,
        #   返回 JSON {"success":false,"message":"1分钟内请勿重复操作"},
        #   不会触发 download 事件,导致 wait_for_event("download") 等到 600s 超时
        #   修复: 在导出响应返回 JSON(限流)时,等待 60s 后重试,而不是立即重试点按钮
        #   (注意: networkidle < 1s 是正常的,因为页面早就加载完了,点搜索只是局部 ajax)
        logger.info(f"[{data_type}] 点击查询/搜索...")
        await self._click_query_button(page)
        logger.info(f"[{data_type}] 查询按钮已点击,开始等待页面加载(最多15s)...")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
            logger.info(f"[{data_type}] 页面已达到 networkidle 状态")
        except Exception as e:
            logger.info(f"[{data_type}] networkidle 等待结束(超时或异常,忽略): {type(e).__name__}: {e}")
        await asyncio.sleep(2)
        logger.info(f"[{data_type}] 准备点击导出按钮并等待下载...")

        # 5. 导出
        # P1-1: 调用统一的 _download_with_retry (自动按 data_type 配置超时 + 重试)
        #       - wallet: 600s + 2次重试 (数据量大,经常超时)
        #       - refund/ticket: 180s + 1次重试
        download = await _download_with_retry(page, data_type, logger, expect_end_dt=end_date)

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

        # 【shanmao_refund 核心:全量导出后按日期范围裁剪写回】
        # 闪猫导出接口不支持时间筛选参数(传参=500),只能拿到全量,
        # 因此用 pandas 精确保留 start_date~end_date 范围内的行再覆盖写回。
        # (与 _do_fetch_one 共用 _crop_shanmao_refund_by_date_range 方法)
        if data_type == "shanmao_refund":
            dest_path, dest_filename = await self._crop_shanmao_refund_by_date_range(
                dest_path, dest_filename, start_date, end_date, data_type
            )

        # 【refund:BMS 退费单全量导出后按日期范围裁剪写回】
        if data_type == "refund":
            dest_path, dest_filename = await self._crop_refund_by_date_range(
                dest_path, dest_filename, start_date, end_date, data_type
            )

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
