"""浏览器交互工具: 弹窗关闭 / 按钮定位点击 / 无数据判定 / 下载重试(不含登录)。"""

import asyncio
import re
from datetime import datetime, timezone


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



# ──────────────────────────────────────────────────────────────────────
# P1-1: 导出下载超时控制 + 自动重试
#       - wallet(提现操作记录):数据量大,导出通常需要 3-10 分钟,给 600s 超时 + 最多 2 次重试
#       - shanmao_refund(闪猫退款):文件不大(~172KB),默认 90s 即可,但给 1 次重试对付偶发 500
#       - 其他(refund/ticket/device_combo):通常 90s 够用,给 1 次重试
# ⚠️ 2026-09-05: 补 BMS 五源扩容新增源(refund_record_report 等)超时/重试配置。
#   此前它们不在白名单 → 走默认 120s, 大导出易超时且无重试。
# ──────────────────────────────────────────────────────────────────────
_DOWNLOAD_TIMEOUT_MS: dict[str, int] = {
    "wallet": 600_000,       # 10 分钟(提现操作记录,数据量大)
    "shanmao_refund": 90_000,
    "refund": 180_000,       # 退费单也经常较大,放宽到 3 分钟
    "ticket": 180_000,
    "ticket_finish": 180_000,   # ticket 双维度补抓(按完成时间),同 ticket 量级
    "device_combo": 90_000,
    "device_tag": 90_000,
    "device_tag_binding": 90_000,
    # BMS 五源扩容新增(2026-09-05, A-20260905-001)
    "refund_record_report": 180_000,  # 退款记录报表:当天全量可达数千行,给 3 分钟
    "bms_order": 300_000,             # 订单管理:月 43 万单量级,导出慢,给 5 分钟
    "return_tracking": 180_000,       # 退货跟踪:中量,3 分钟
    "device_profile": 180_000,        # 设备管理:实测单日导出 8720 行,3 分钟
}

_DOWNLOAD_DEFAULT_TIMEOUT_MS = 120_000  # 未知类型默认 2 分钟

_DOWNLOAD_MAX_RETRIES: dict[str, int] = {
    "wallet": 2,
    "shanmao_refund": 1,
    "refund": 1,
    "ticket": 1,
    "ticket_finish": 1,
    # 新源: 数据量大/导出易慢,给 1 次重试兜底偶发超时/限流
    "refund_record_report": 1,
    "bms_order": 1,
    "return_tracking": 1,
    "device_profile": 1,
}

_DOWNLOAD_DEFAULT_RETRIES = 1



# 导出/查询按钮候选 DOM 选择器(与查询按钮 ANY_CLICKABLE 一致)。
# ⚠️ 2026-09-06 事故复盘(3d48c1f 回归): 这里只作为"遍历候选"的集合(每个都带按钮
#   标识: tag=button / role=button / class 含 btn|button|el-button), **不可**用裸
#   `div:has-text("导出")` 直接 click —— has-text 子串匹配会把工具栏/页脚/父容器
#   ("导出管理""支持导出")及模态框内部都纳入匹配, 配合 `>> nth=0`(对整个列表取第1个)
#   会点到非按钮容器, page.click 却返回"成功" → 误记命中却永不触发 download。
#   点击前必须经 _is_clickable_button 做身份校验(见 _click_export_button)。
_EXPORT_ANY_CLICKABLE = (
    "button:visible,"
    "div[role='button']:visible,"
    "span[role='button']:visible,"
    "a[class*='btn']:visible,"
    "a[class*='button']:visible,"
    "span[class*='el-button']:visible,"
    "div[class*='el-button']:visible"
)

async def _dismiss_modal_dialogs(page, max_wait_sec: float = 3.0) -> int:
    """识别并关闭遮挡点击的 Element UI 模态提示框(el-message-box)。

    背景(2026-09-06 定案): BMS 页面(尤其大窗口查询后)会弹
      `<div role="dialog" aria-label="提示" class="el-message-box__wrapper">`
    模态「提示」框(带确定/关闭按钮), 用户手动会点掉, 但脚本从不处理 →
    它 intercepts pointer events 挡住后续导出/查询按钮的真实点击。

    ⚠️ 2026-09-06 事故复盘修复:
      1) **可见性检测盲区**: 旧实现用 `el.offsetParent !== null` 判可见, 但
         Element UI 模态框 wrapper 是 position:fixed → offsetParent 恒为 null →
         真实弹着的模态框永远检测不到(n 恒 0), 修复形同虚设。
         现改用 getBoundingClientRect() 宽高 + display/visibility/opacity 判可见。
      2) **误删业务对话框**: 旧实现 JS 移除含 `.el-dialog__wrapper`(业务详情/筛选
         面板同为 el-dialog), 会把正常业务弹层连根移除、破坏 Vue 状态。
         现 JS 移除**仅限 `.el-message-box__wrapper`**(纯提示框容器), 不再碰
         `.el-dialog__wrapper`。
      3) **无差别点"确定"**: 旧实现把"确定/OK"(接受)与"取消/×"(取消)同样点击,
         可能点掉破坏性 confirm。现只接受**确认类纯提示框**(message-box 内按钮),
         且默认点"确定/知道了"以关闭提示, 对含"删除/危险"语义的弹层不强点确定。

    关闭顺序(由礼貌到暴力):
      1) 优先点 MessageBox 的「确定/OK/知道了」按钮(触发 Vue 正常回调);
      2) 找不到按钮 → ESC(Element UI 弹层几乎都响应 ESC);
      3) 仍残留 → JS 移除 .el-message-box__wrapper(仅此类, 不碰 el-dialog)。

    Returns: 关闭的模态框数量(仅诊断用)。
    """
    import logging as _lg
    _log = _lg.getLogger(__name__)
    dismissed = 0
    try:
        for _round in range(2):  # 最多两轮(新弹窗可能在关闭后异步再弹)
            n = await page.evaluate("""() => {
                // 仅检测 el-message-box(纯提示框); el-dialog 可能是业务面板, 不自动关
                const sels = ['.el-message-box__wrapper'];
                let n = 0;
                for (const sel of sels) {
                    document.querySelectorAll(sel).forEach((el) => {
                        const cs = window.getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        const visible =
                            cs.display !== 'none' &&
                            cs.visibility !== 'hidden' &&
                            parseFloat(cs.opacity || '1') > 0 &&
                            r.width > 0 && r.height > 0;
                        if (visible) n++;
                    });
                }
                return n;
            }""")
            if not n:
                return dismissed
            _log.info(f"  [模态弹窗] 检测到 {n} 个可见提示框, 尝试关闭(第 {_round + 1} 轮)...")
            # (1) 点确认按钮(Element UI MessageBox 按钮在 .el-message-box__btns 内)
            #    ⚠️ 只处理 message-box 的"接受/确认"类按钮, 不点 el-dialog 的业务按钮,
            #    也绝不点含"删除/清空"等破坏性语义的弹层确定。
            btn_clicked = False
            for sel in (
                ".el-message-box__btns button:visible",
                ".el-message-box button:visible",
            ):
                try:
                    btns = await page.query_selector_all(sel)
                    for b in btns:
                        try:
                            t = (await b.inner_text()).strip()
                        except Exception:
                            t = ""
                        # 只点"接受/确认"语义按钮以关闭提示; 含破坏性文案的弹层不自动点
                        if t in ("确定", "OK", "知道了", "确认", "好的", "关闭", "关 闭"):
                            try:
                                # 不 force: 若被真正业务层遮挡, 宁可走 ESC/JS 移除也不强点
                                await b.click(timeout=3000)
                                btn_clicked = True
                                dismissed += 1
                                _log.info(f"  [模态弹窗] 已点击按钮 text={t!r}")
                                break
                            except Exception as ce:
                                _log.warning(f"  [模态弹窗] 点按钮 {t!r} 失败: {ce}")
                    if btn_clicked:
                        break
                except Exception:
                    continue
            if btn_clicked:
                await asyncio.sleep(0.3)
                continue
            # (2) ESC
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.2)
            except Exception:
                pass
            # (3) JS 移除 message-box 容器(仅提示框, 不影响 el-dialog 业务面板)
            try:
                removed = await page.evaluate("""() => {
                    let rm = 0;
                    document.querySelectorAll('.el-message-box__wrapper').forEach((el) => {
                        try {
                            el.parentNode && el.parentNode.removeChild(el);
                            rm++;
                        } catch (_) {
                            el.style.display = 'none';
                        }
                    });
                    return rm;
                }""")
                if removed:
                    dismissed += removed
                    _log.info(f"  [模态弹窗] JS 移除 {removed} 个提示框容器(最后手段)")
                    await asyncio.sleep(0.3)
            except Exception as me:
                _log.warning(f"  [模态弹窗] JS 移除失败: {me}")
    except Exception as e:
        _log.warning(f"  [模态弹窗] 处理异常(忽略): {e}")
    return dismissed



# 元素是否具备"可点击按钮"外观(用于点击前身份校验, 防把容器/纯文本当按钮点)
_BTN_LIKE_CLASS_HINTS = ("btn", "button")

_BTN_LIKE_ROLE_HINTS = ("button",)

async def _is_clickable_button(handle) -> tuple[bool, str]:
    """判断 ElementHandle 是否为"可点击按钮"(而非纯容器/文本)。

    判定(任一满足即认为按钮):
      - tag == BUTTON
      - tag in (A, SPAN, DIV) 且 class 含 btn/button(el-button / role=button / btn-primary 等)
      - 有 role=button
    返回 (是否按钮, 诊断摘要)。防止把含"导出"文案的祖先 div/toolbar 当导出按钮点。
    """
    try:
        info = await handle.evaluate("""(el) => {
            const cls = (el.className || '').toString();
            const role = (el.getAttribute && el.getAttribute('role')) || '';
            const tag = el.tagName || '';
            const txt = (el.innerText || el.textContent || '').trim();
            return { tag, cls, role, txt: txt.slice(0, 30) };
        }""")
    except Exception:
        return False, "(evaluate失败)"
    tag = (info or {}).get("tag") or ""
    cls = (info or {}).get("cls") or ""
    role = (info or {}).get("role") or ""
    txt = (info or {}).get("txt") or ""
    summary = f"<{tag}> text={txt!r} role={role!r} class={cls!r}"
    if tag.upper() == "BUTTON":
        return True, summary
    low_cls = cls.lower()
    if role.lower() in _BTN_LIKE_ROLE_HINTS:
        return True, summary
    if any(h in low_cls for h in _BTN_LIKE_CLASS_HINTS):
        return True, summary
    return False, summary

async def _locate_export_button(page, dry_run: bool = False):
    """定位「导出」按钮并返回可点击的 ElementHandle(不执行点击)。

    ⚠️ 用途: 开发/联调/回归验证阶段, 只需确认"能稳定找到真正的导出按钮"即可初步
      判定采集链路可行 —— **不要执行点击/下载**。BMS 的 1 分钟操作限制(OPERATION_INTERVAL
      =65s)只约束真实查询/导出/下载, 定位按钮不触发该限制; 而真点击会触发 60s 冷却,
      若每次都真下载做验证会撞限流、白等 180~600s、污染账号。

    本函数 = _click_export_button 的"定位"部分(dry_run=True 时纯定位不点击, 供测试/
      验证脚本复用); dry_run=False 时也仅定位返回, 点击由 _click_export_button 负责。

    返回: 定位到的按钮 ElementHandle(已通过 _is_clickable_button 身份校验)。
    抛错: BmsSessionExpired(会话被踢落登录页) / RuntimeError("未找到导出按钮")。

    ⚠️ 2026-09-06 事故修复(3d48c1f 引入回归, 生产 8:55 起全源 download 超时):
      旧实现标准 selector 混入 `div:has-text("导出") >> nth=0`, 会点到含"导出"文案的
      祖先容器/工具栏/模态框内部(has-text 子串匹配 + nth=0 对整个列表取第1个),
      page.click 对容器也返回"成功" → 误记「命中成功」却永不触发 download。
      本次修复原则:
      1) 只接受"按钮语义"元素(tag=button / class 含 btn|button / role=button), 点击前先身份校验,
         命中非按钮(容器/文本) → 跳过并记 warning, 绝不无把握记「命中成功」;
      2) 候选选择器同样收敛到 _EXPORT_ANY_CLICKABLE(带按钮标识), 不再用裸 div:has-text。
    """
    import logging as _lg
    _log = _lg.getLogger(__name__)
    # 判断当前是否已落回登录页(会话失效), 避免把"登录"当导出按钮 / 误导性报错
    try:
        url_lower = (page.url or "").lower()
        login_btn = await page.query_selector('button:has-text("登录")')
        if "login" in url_lower or login_btn is not None:
            btns_text = []
            try:
                for b in await page.query_selector_all("button:visible"):
                    try:
                        btns_text.append((await b.inner_text()).strip())
                    except Exception:
                        pass
            except Exception:
                pass
            if btns_text and set(btns_text) <= {"登录", ""}:
                _log.warning(
                    f"  [导出按钮] 页面疑似已落回登录页(仅按钮: {btns_text[:10]}), "
                    "判定会话失效 → 抛 BmsSessionExpired"
                )
                raise BmsSessionExpired(
                    "定位导出前检测到页面仅剩登录按钮(会话被踢/失效), 同组下一源将重新登录"
                )
    except BmsSessionExpired:
        raise
    except Exception:
        pass

    # 定位前先关闭可能遮挡的模态「提示」框(不影响按钮定位, 仅清障)
    try:
        await _dismiss_modal_dialogs(page)
    except Exception as _de:
        _log.warning(f"  [导出按钮] 关闭模态框异常(忽略,继续): {_de}")

    _log.info("  [导出按钮] 开始定位导出按钮..." + ("(dry_run 仅验证不点击)" if dry_run else ""))

    # ── ① 标准按钮精确匹配(旧代码路径, 生产 8:00/8:24 证明可靠) ──
    #    仅限真实 <button> / span.el-button(Element UI), 绝不混入裸 div:has-text。
    #    文本需"包含 导出 且不含 导入/重置"(防误点"导入"/"重置导出范围")。
    standard_sel = (
        'button:visible:has-text("导出"), '
        'span.el-button:visible:has-text("导出")'
    )
    try:
        cand = page.locator(standard_sel)
        # 排除文本含"导入/重置"的误命中(如"批量导入导出"/"重置后导出"不存在, 但防御性处理)
        n = await cand.count()
        for i in range(min(n, 5)):
            el = cand.nth(i)
            try:
                t = (await el.inner_text()).strip()
            except Exception:
                continue
            if "导出" in t and "导入" not in t and "重置" not in t:
                handle = await el.element_handle()
                ok, summary = await _is_clickable_button(handle)
                if not ok:
                    _log.warning(f"  [导出按钮] 候选 #{i} 非按钮(跳过): {summary}")
                    continue
                _log.info(f"  [导出按钮] 标准命中 #{i}: {summary}")
                return handle
        _log.info(f"  [导出按钮] 标准 selector 无有效按钮(共{n}个候选), 改遍历")
    except Exception as e:
        _log.info(f"  [导出按钮] 标准 selector 未命中: {type(e).__name__}, 改为遍历候选")

    # ── ② 遍历候选按钮(伪按钮/role=button 覆盖, 均做身份校验) ──
    try:
        btns = await page.query_selector_all(_EXPORT_ANY_CLICKABLE)
    except Exception:
        btns = await page.query_selector_all("button:visible")
    _log.info(f"  [导出按钮] 页面候选按钮数: {len(btns)}")
    for idx, b in enumerate(btns):
        try:
            t = (await b.inner_text()).strip()
        except Exception:
            t = ""
        if "导出" in t and "重置" not in t and "导入" not in t:
            # 点击前身份校验: 防祖先容器/文本节点被当按钮(has-text 子串匹配的坑)
            ok, summary = await _is_clickable_button(b)
            if not ok:
                _log.warning(f"  [导出按钮] 遍历候选 #{idx} 非按钮, 跳过: {summary}")
                continue
            _log.info(f"  [导出按钮] 遍历命中第 {idx} 个: {summary}")
            # 命中前再次关闭模态框(前一次点击可能刚触发新弹窗)
            try:
                await _dismiss_modal_dialogs(page)
            except Exception:
                pass
            return b
    # 列出所有候选按钮文本 + 身份, 方便诊断
    all_texts = []
    for b in btns:
        try:
            all_texts.append((await b.inner_text()).strip())
        except Exception:
            pass
    _log.warning(f"  [导出按钮] 未找到导出按钮! 所有候选文本: {all_texts[:20]}")
    raise RuntimeError("未找到导出按钮")

async def _click_export_button(page) -> None:
    """定位并点击「导出」按钮(触发真实导出 → 由 _download_with_retry 等待 download)。

    定位逻辑见 _locate_export_button(含 3d48c1f 回归修复说明)。此处仅负责:
      1) 调 _locate_export_button 拿到已通过身份校验的真按钮;
      2) 点击它(若被模态框拦截则 dismiss 后 force-click 兜底)。
    """
    import logging as _lg
    _log = _lg.getLogger(__name__)
    btn = await _locate_export_button(page, dry_run=False)
    try:
        await btn.click(timeout=8000)
        _log.info("  [导出按钮] 标准 selector 命中并点击成功")
        return
    except Exception as ce:
        err = str(ce)
        if "intercepts pointer events" in err or "el-message-box" in err:
            _log.warning(f"  [导出按钮] 点击被模态框拦截, 先关闭再重试: {ce}")
            try:
                await _dismiss_modal_dialogs(page)
                await btn.click(timeout=8000, force=True)
                _log.info("  [导出按钮] 关闭模态框后 force-click 成功")
                return
            except Exception as ce2:
                _log.warning(f"  [导出按钮] 关闭模态框后仍点击失败: {ce2}")
                raise
        _log.warning(f"  [导出按钮] 点击失败: {ce}")
        raise



# Element UI 表格"无数据"空态选择器(兼容 el-table / el-table-v2 / 各版本文案)
_EMPTY_STATE_SELECTORS = (
    ".el-table__empty-text:visible",          # Element Plus 表格空态
    ".el-table__empty-block:visible",         # Element UI 旧版空态容器
    "text=暂无数据",                            # 中文空态文案
    "text=没有数据",                            # 备用文案
    "text=No Data",                            # 英文
)

async def _page_shows_no_data(page) -> bool:
    """检测页面表格是否处于"无数据"空态(查询结果为空)。

    2026-09-06 修复: BMS 某些源(refund_record_report 等)在时间窗口内无数据时,
    点导出不会触发任何 download/网络请求 → 会被「4s 无网络快速失败」误判为点空失败。
    正确语义: 空结果是正常业务态, 应识别出来按"0 行成功"处理而非抓取失败。
    """
    try:
        for sel in _EMPTY_STATE_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

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
            # ⚠️ 2026-09-01 审计 P2-1: 追踪后台 _read_body 任务,
            #   避免页面/上下文关闭后 "Task exception was never retrieved" 泄漏
            #   (生产 08-28 出现 2 次)。create_task 的后台协程若在页面关闭后
            #   被取消/抛 TargetClosedError, 无人消费其异常 → asyncio 报错。
            _body_tasks: list[asyncio.Task] = []

            def _on_response(resp):
                try:
                    # ⚠️ 2026-09-06 放宽: 限流/导出接口可能是 POST 或 URL 含 download/record
                    #   (旧实现仅匹配 GET /export, 覆盖不到其它导出入口)。
                    url_l = resp.url.lower()
                    method = resp.request.method if resp.request else ""
                    if (
                        ("export" in url_l or "download" in url_l or "record" in url_l)
                        and method in ("GET", "POST")
                    ):
                        ctype = (resp.headers.get("content-type") or "").lower()
                        if "json" in ctype:
                            # JSON 响应 = 被限流(或异常),不会触发 download 事件
                            export_rate_limited["hit"] = True
                            async def _read_body():
                                try:
                                    body = await resp.text()
                                    export_rate_limited["msg"] = body[:200]
                                    # #335: 导出异常时记录请求端点与 payload ——
                                    # 定位 BMS 前端实际发送的参数形态(如 return_tracking
                                    # 日期被页面序列化为 ISO-UTC 触发 400), 为 route
                                    # 改写提供事实依据; 正常下载不经过此路径。
                                    try:
                                        req = resp.request
                                        logger_obj.warning(
                                            "[%s] 导出异常请求诊断: url=%s method=%s post_data=%r",
                                            data_type, resp.url,
                                            req.method if req else "?",
                                            (req.post_data or "")[:500] if req else "",
                                        )
                                    except Exception:
                                        pass
                                except Exception:
                                    export_rate_limited["msg"] = "(body 读取失败)"
                            # 用 create_task 挂后台,避免同步回调里直接 await 协程;
                            # 记录到 _body_tasks,结束时统一消费/取消,防止任务异常泄漏
                            try:
                                _t = asyncio.create_task(_read_body())
                                _body_tasks.append(_t)
                                _t.add_done_callback(lambda fut: _body_tasks.remove(fut) if fut in _body_tasks else None)
                            except Exception:
                                pass
                except Exception:
                    pass
            page.on("response", _on_response)

            # ── 2026-09-06 点空快速失败: 记录点击后是否产生任何新网络请求;
            #    若点击后长时间无任何新网络活动 → 判定"点空/点错元素", 立即抛错触发重试,
            #    不再傻等满 timeout_ms(旧实现点空会白等 180s×2 / 600s×3)。
            #    注意: 有网络活动但无 download = 正常(导出慢/限流), 继续等。
            _request_seen_after_click = False

            def _on_request(req):
                nonlocal _request_seen_after_click
                try:
                    if req.url and not req.url.startswith("data:"):
                        _request_seen_after_click = True
                except Exception:
                    pass
            page.on("request", _on_request)
            request_listener_on = True

            def _remove_request_listener(p):
                nonlocal request_listener_on
                if not request_listener_on:
                    return
                try:
                    p.remove_listener("request", _on_request)
                except Exception:
                    pass
                request_listener_on = False

            # 每次尝试都重新点导出(上次可能只是注册了事件但按钮没真的点到/服务器没反应)
            logger_obj.info("[%s] [%d/%d] 开始点击导出按钮...",
                            data_type, attempt + 1, max_retries + 1)
            await _click_export_button(page)
            logger_obj.info("[%s] [%d/%d] 导出按钮已点击,等待 download 事件触发...",
                            data_type, attempt + 1, max_retries + 1)

            # 轮询等待 download 事件,同时检测限流响应 / 点空快速失败
            download = None
            poll_start = asyncio.get_event_loop().time()
            no_req_deadline = poll_start + 4.0  # 点击后 4s 内必须有新网络活动(防点空)
            _click_effect_checked = False
            while True:
                # 检查限流
                if export_rate_limited["hit"]:
                    _remove_request_listener(page)
                    _remove_response_listener(page)
                    _drain_task(download_task)
                    raise RuntimeError(
                        f"BMS 导出返回 JSON(疑似限流或异常): {export_rate_limited['msg']}"
                    )
                # ⚠️ 点空快速失败: 点击后 4s 内无任何新请求, 且 download 也未触发
                #   → 两种可能: ①点到非按钮容器(选择器误匹配) ②**查询结果为空**(BMS 不触发下载)
                #   先排除②: 若页面显示"暂无数据", 属正常空结果, 抛 EmptyQueryResult
                #   由上层按"0 行成功"处理(而非误报抓取失败); 否则才是真点空, 立即失败。
                #   仅在"点击后首次"检查一次; 若已有网络活动则说明点击生效, 后续安心等 download。
                now_t = asyncio.get_event_loop().time()
                if (not _click_effect_checked) and now_t >= no_req_deadline:
                    _click_effect_checked = True
                    if not _request_seen_after_click and download is None:
                        # ① 空数据(正常业务态: 窗口内无记录) → 不重试/不失败, 上层按成功空结果
                        if await _page_shows_no_data(page):
                            _remove_request_listener(page)
                            _remove_response_listener(page)
                            _drain_task(download_task)
                            raise EmptyQueryResult(
                                f"[{data_type}] 查询结果为空(BMS 页面暂无数据), 无导出下载属正常"
                            )
                        # ② 真点空/点错元素 → 快速失败让上层重试/告警
                        _remove_request_listener(page)
                        _remove_response_listener(page)
                        _drain_task(download_task)
                        raise RuntimeError(
                            "点击导出按钮后 4s 内无任何网络请求(download 也未触发), "
                            "疑似点到非按钮元素或导出请求未发出。请检查导出按钮选择器命中是否正确"
                        )
                # 检查 download 事件是否已触发(用 shield+短 timeout 轮询)
                try:
                    download = await asyncio.wait_for(asyncio.shield(download_task), timeout=2.0)
                    break
                except asyncio.TimeoutError:
                    # 2 秒内没触发,继续轮询(只要总时间没超 timeout_ms)
                    elapsed = asyncio.get_event_loop().time() - poll_start
                    if elapsed * 1000 > timeout_ms:
                        _remove_request_listener(page)
                        _drain_task(download_task)
                        raise RuntimeError(f"等待 download 事件超时({int(elapsed)}s)")
                    continue

            _remove_request_listener(page)
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
            # ⚠️ 清理:移除 response/request listener + 取消残留 download_task,避免泄漏
            try:
                _remove_request_listener(page)
            except Exception:
                pass
            _remove_response_listener(page)
            _drain_task(download_task)
            # ⚠️ 2026-09-06: 会话失效(BmsSessionExpired)不重试, 直接冒泡
            #   让上层重新登录(否则本源内盲目重试会白等 180~600s×N 才报误导性错误)。
            #   BmsSessionExpired 是 RuntimeError 子类, 需在通用 except 前特判。
            if isinstance(e, BmsSessionExpired):
                raise
            # ⚠️ 2026-09-06: 空查询结果(EmptyQueryResult)不重试, 直接冒泡
            #   上层按"0 行成功"处理; 重试无意义(再查也是空)。
            if isinstance(e, EmptyQueryResult):
                raise
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
            # 兜底移除 request/response listener(正常路径已手动移除; 取消/异常路径防泄漏)
            try:
                _remove_request_listener(page)
            except Exception:
                pass
            try:
                _remove_response_listener(page)
            except Exception:
                pass
            # ⚠️ 2026-09-01 审计 P2-1: 一并清理 _read_body 后台任务(消费结果/取消),
            #   避免页面关闭后它们抛 TargetClosedError 无人 retrieve。
            for _bt in list(_body_tasks):
                try:
                    if not _bt.done():
                        _bt.cancel()
                except Exception:
                    pass
            _body_tasks.clear()

    # 所有重试都用完了 → 把最后一次异常抛出去
    raise RuntimeError(
        f"[{data_type}] 导出下载 {max_retries + 1} 次均失败: "
        f"{last_exc!r}. 请检查 BMS 后台导出是否正常、数据量是否过大、网络是否拥塞。"
    )

class BmsSessionExpired(RuntimeError):
    """BMS 登录态中途失效(复用登录态跳页后被踢回 #/login)。

    与「账号密码错误」不同: 不是凭证问题, 是会话被 BMS 侧失效(并发登录互踢/超时/僵尸会话),
    重新登录即可恢复。上层(fetch_all 组循环)捕获后应把 logged_in_ok 置 False,
    让同组下一个源以 already_logged_in=False 重新登录, 而不是熔断跳过整组。
    """

class EmptyQueryResult(RuntimeError):
    """查询结果为空(BMS 页面显示"暂无数据"), 导出不会产生 download/网络请求。

    2026-09-06 修复: refund_record_report 等源在时间窗口内无数据时, 点导出后 BMS
    不触发任何下载 → 被「4s 无网络快速失败」误判为"点错元素"而失败。
    正确语义: 空结果是正常业务态(窗口内无新增), 应视为 0 行成功, 而非抓取失败。
    上层(_download_with_retry 调用方)捕获后按"空结果成功"处理。
    """
