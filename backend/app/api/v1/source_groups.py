"""数据源分组路由(2026-08-25 新增)

分组视图 / 组级凭证管理 / 源归组 / 登录验证。
同一 Web 平台(域)下多个数据源共用登录凭证, 组级可配置账号密码, 单源可覆盖。
"""

import logging

from fastapi import APIRouter

from app.api.deps import AdminUser, DBSession, ManagerUser
from app.schemas.common import R
from app.services.source_group_service import (
    assign_source_group,
    list_source_groups,
    update_source_group,
)
from app.services.sync_source_config_service import get_sync_source_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/source-groups", tags=["同步源分组"])


def _fmt_account(u: str | None) -> str:
    """账号显示: Fernet 密文特征(gAAAAAB 前缀)说明解密失败(密钥不匹配), 提示而非显示密文"""
    if u and str(u).startswith("gAAAAAB"):
        return "<账号密文无法解密(加密密钥不匹配)>"
    return u or "<空>"


@router.get("", response_model=R, summary="数据源分组视图(组 + 组内源 + 凭证状态)")
def list_groups(db: DBSession, _: ManagerUser):
    return R(data=list_source_groups(db))


@router.put("/{group_key}", response_model=R, summary="更新组配置(组名/域名/组级账号密码, 留空不改)")
def update_group(group_key: str, payload: dict, db: DBSession, _: AdminUser):
    g = update_source_group(db, group_key, payload)
    if not g:
        return R(code=1, message="分组不存在")
    return R(data=g, message="组配置已保存")


@router.post("/{group_key}/assign", response_model=R, summary="数据源归组(手动调整)")
def assign_group(group_key: str, payload: dict, db: DBSession, _: AdminUser):
    """payload: {data_type: str, group_key?: str} 或 {data_type: str, unassign: true}"""
    data_type = payload.get("data_type")
    target = payload.get("group_key", group_key)
    if payload.get("unassign"):
        target = None
    if not data_type:
        return R(code=1, message="缺少 data_type")
    if not assign_source_group(db, data_type, target):
        return R(code=1, message="数据源不存在")
    return R(message="归组成功")


@router.post("/{data_type}/test-login", response_model=R, summary="验证数据源登录(用配置/传入的账号密码)")
async def test_login(data_type: str, payload: dict | None = None, db: DBSession = None, _: AdminUser = None):
    """验证某数据源的账号密码能否登录。

    - HTTP 源(cs_record): 直接调 ai-acc 登录 API 验证
    - Playwright 无验证码源(BMS): 启动浏览器填账号密码尝试登录
    - 有验证码源(闪猫/SCRM网页): 返回提示改用「验证码登录」
    payload 可选传入 {username, password} 覆盖配置值(验证新填的凭证)。
    """
    cfg = get_sync_source_config(db, data_type)
    if not cfg:
        return R(code=1, message=f"数据源不存在: {data_type}")
    username = (payload or {}).get("username") or cfg.get("username")
    password = (payload or {}).get("password") or cfg.get("password")
    if not username or not password:
        return R(code=1, message="未配置账号密码, 无法验证")
    # 提示信息带 账号名 + 平台地址, 便于定位是哪个配置的问题
    src_name = cfg.get("name", data_type)
    src_url = (cfg.get("base_url") or "") + (cfg.get("page_path") or "")
    who = f"{_fmt_account(username)} @ {src_url or '未填URL'}"

    fetch_mode = cfg.get("fetch_mode", "playwright")

    # HTTP 源: 直接调登录 API
    if fetch_mode == "http":
        try:
            import httpx

            from app.services.ai_acc_fetcher import _login
            async with httpx.AsyncClient(timeout=15) as client:
                token = await _login(client, cfg["base_url"], username, password)
            from app.services.system_event_service import record_system_event
            record_system_event(
                db, "auth", "test_login", f"登录验证: {src_name}",
                summary=f"HTTP 登录成功 ({who}, token len={len(token)})",
                status="success",
                detail=f"data_type={data_type} 账号={_fmt_account(username)} url={src_url}",
            )
            return R(data={"success": True, "account": username, "url": src_url}, message=f"登录成功: {who}")
        except Exception as e:
            from app.services.system_event_service import record_system_event
            err = str(e)[:300]
            record_system_event(
                db, "auth", "test_login", f"登录验证: {src_name}",
                summary=f"登录失败 ({who}): {err}", status="failed",
                detail=f"data_type={data_type} 账号={_fmt_account(username)} url={src_url}",
            )
            return R(data={"success": False, "account": username, "url": src_url}, message=f"登录失败: {who} - {err}")

    # Playwright 源
    try:
        return await _test_playwright_login(db, cfg, username, password)
    except Exception as e:
        logger.exception(f"[test-login] {data_type} 验证异常: {e}")
        return R(data={"success": False, "account": username, "url": src_url}, message=f"验证异常: {who} - {str(e)[:200]}")


async def _test_playwright_login(db, cfg: dict, username: str, password: str) -> R:
    """Playwright 登录验证: 打开登录页 → 填账号密码 → 提交 → 检测结果"""
    from playwright.async_api import async_playwright

    data_type = cfg["data_type"]
    base_url = (cfg.get("base_url") or "").rstrip("/")
    src_url = base_url + (cfg.get("page_path") or "")
    who = f"{_fmt_account(username)} @ {src_url or '未填URL'}"
    options = cfg.get("options") or {}
    if cfg.get("requires_captcha"):
        return R(data={"success": False, "account": username, "url": src_url},
                 message=f"该源({who})登录需验证码, 无法自动验证; 请使用「验证码登录」完成一次性登录")

    login_path = options.get("login_url_path", "/login")
    url = base_url + (login_path if login_path.startswith("/") else f"/{login_path}")
    user_sel = options.get("username_selector", "input[type='text'], input[name='username'], input[placeholder*='账号'], input[placeholder*='手机']")
    pwd_sel = options.get("password_selector", "input[type='password']")
    submit_text = options.get("submit_button_text", "登录")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)

                # 检测验证码输入框(即使 requires_captcha 未标, 页面真有也提示)
                cap_sel = options.get("captcha_input_selector", "input[placeholder*='验证码'], input[placeholder*='验证码' i], input[name='captcha']")
                cap = page.locator(cap_sel).first
                if await cap.count() > 0 and await cap.is_visible():
                    return R(data={"success": False, "account": username, "url": src_url},
                             message=f"页面({who})需要输入验证码, 无法自动验证; 请使用「验证码登录」")

                # 填账号密码
                filled = False
                user_inp = page.locator(user_sel).first
                if await user_inp.count() > 0 and await user_inp.is_visible():
                    await user_inp.click()
                    await user_inp.fill(username)
                    filled = True
                pwd_inp = page.locator(pwd_sel).first
                if await pwd_inp.count() > 0 and await pwd_inp.is_visible():
                    await pwd_inp.click()
                    await pwd_inp.fill(password)
                    filled = True
                if not filled:
                    return R(data={"success": False, "account": username, "url": src_url},
                             message=f"未找到账号/密码输入框({who}), 可能页面结构已变化")

                # 提交
                submit = page.locator(
                    f"button[type='submit'], button:has-text('{submit_text}'), button:has-text('登 录')"
                ).first
                if await submit.count() > 0 and await submit.is_visible():
                    await submit.click()
                else:
                    await page.keyboard.press("Enter")
                await page.wait_for_timeout(4000)

                # 检测: 当前 URL 不含 login 且密码框消失 → 登录成功
                url_now = (page.url or "").lower()
                pwd_after = await page.locator(pwd_sel).count()
                logged_in = ("login" not in url_now) or (pwd_after == 0)
                if logged_in:
                    from app.services.system_event_service import record_system_event
                    record_system_event(
                        db, "auth", "test_login", f"登录验证: {cfg.get('name', data_type)}",
                        summary=f"网页登录成功 ({who})", status="success",
                        detail=f"data_type={data_type} 账号={_fmt_account(username)} url={src_url}",
                    )
                    return R(data={"success": True, "account": username, "url": src_url}, message=f"登录成功: {who}")
                return R(data={"success": False, "account": username, "url": src_url},
                         message=f"未能确认登录成功({who}), 账号密码可能错误或页面结构变化")
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass
    except Exception as e:
        logger.exception(f"[test-login] {data_type} Playwright 异常: {e}")
        return R(data={"success": False, "account": username, "url": src_url},
                 message=f"验证异常: {who} - {str(e)[:200]}")
