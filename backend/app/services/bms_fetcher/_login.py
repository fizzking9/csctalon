"""OCR 验证码自动登录: 验证码定位/识别/填写/错误分类/熔断判定。"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# 验证码自动识别(懒加载,首次调用才 import ddddocr)
# 失败时 fallback 为 None(走人工/报错)
# 除了 try_ocr_captcha(字符串版),还加载带置信度版 + 常量阈值
# P1-2: 新增数学计算式验证码识别(闪猫/SCRM 等平台使用 7*0=? / 9-5=? 形式)
try:
    from app.services.captcha_session import (  # type: ignore
        _OCR_AVG_PROB,
        _OCR_MIN_CHAR_PROB,
        _OCR_PRECHECK_MAX_REFRESH,
        try_ocr_captcha,
        try_ocr_captcha_with_confidence,
        try_ocr_math_captcha,
        try_ocr_math_captcha_with_confidence,
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



# ──────────────────────────────────────────────────────────────────────
# 登录类错误熔断判定(2026-09-07 抽取纯函数, 供 fetch_all 熔断记忆 + 单测)
# ──────────────────────────────────────────────────────────────────────
# 宽松"登录类"关键词(fetch_all 通用 except 分支用, 命中即视为登录环节出问题)
_LOGIN_CLASS_KEYS = (
    "登录失败", "账号或密码", "账号或密码错误", "用户名或密码",
    "验证码自动识别登录失败", "登录", "密码", "验证码",
    "captcha", "vcode",
)

def is_login_class_error(err_txt: str, page_crashed: bool = False) -> bool:
    """错误文本是否属于"登录类错误"(登录/账号/密码/验证码环节失败)。

    排除 page_crashed(浏览器崩溃属另一类, 由调用方单独处理)。
    """
    if page_crashed:
        return False
    if not err_txt:
        return False
    lower = err_txt.lower()
    return any(k in lower for k in _LOGIN_CLASS_KEYS)

def should_circuit_break_group_on_login_error(
    err_txt: str, *, credential_is_own: bool, page_crashed: bool = False,
) -> bool:
    """登录类错误后, 是否应熔断(跳过)同组剩余源。

    - 本源用**组/共享凭证**(credential_is_own=False): 共享账号坏 → 同组后续源用同一
      账号也必失败 → 熔断整组(True)。
    - 本源用**单源独立凭证**(credential_is_own=True): 只是该源自己的账号坏, 同组其它
      源用组凭证仍可能成功 → 不熔断(False), 让后续源以 already_logged_in=False 重新登录。

    修复 2026-09-07 device_combo 误填 tanzk 连坐同组 5 源事故。
    """
    if not is_login_class_error(err_txt, page_crashed=page_crashed):
        return False
    # 单源独立凭证失败 → 不连坐同组
    return not credential_is_own

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
        else:
            # 后续尝试:优先点验证码图片刷新;失败才 reload
            refreshed = await _refresh_captcha_image(page, cap_elem)
            if not refreshed:
                try:
                    await page.reload(wait_until="networkidle", timeout=60000)
                    await asyncio.sleep(2)
                    await _fill_accounts(page, username, password)
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

        asyncio.create_task(asyncio.sleep(0))  # placeholder,实际下面覆盖

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
