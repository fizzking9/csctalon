"""共享文档页面内登录态会话管理 (#45, A-20260910-001 LOCKED v1.0)

背景:
  共享文档采集(腾讯/金山)的登录态此前只能在服务器跑 CLI 脚本生成
  (`scripts/tencent_doc_login.py`)。页面上写着「失效需重新扫码」却**没有扫码入口**,
  管理员看到提示后唯一出路是 SSH 上服务器 —— 这是当前最大断点。

本模块把该能力提升到 Web 层:
  1. POST start    → 起有头浏览器打开目标文档 → 截图当前页面(二维码/登录页) → 返回 session + base64
  2. GET  status   → 前端轮询: pending / scanned / authorized / expired / closed
  3. POST confirm  → 用户点「我已扫码完成」→ 轻量校验后保存 storage_state(覆盖前先备份)
  4. DELETE        → 取消会话

复用既有骨架:
  - 会话管理/TTL 思路与 `services/captcha_session.py` 一致(TTL 600s, 内存态)
  - 登录态落盘位置/格式与 CLI 脚本一致(BROWSER_STATE_DIR / tencent_doc.json | kdocs.json),
    保证脚本方式仍可作降级路径

安全与稳定性要点(见架构 §五 风险清单):
  - **同平台单例锁**: 并发登录会互相覆盖 state 文件 → 已有活跃会话时 start 返回冲突
  - **confirm 后轻量校验**: 用户可能未真正登录就点确认 → 校验不通过**不覆盖**旧 state
  - **覆盖前备份**: 旧 state 存为 .json.bak(与 CLI 脚本行为一致)
  - **显示服务按需拉起**: 容器装了 xvfb 包但**没人启动进程**时($DISPLAY 为空),
    首次启动会话前按需拉起 `Xvfb :99` 并注入 DISPLAY → 不再把 502 原始日志吐给用户
  - **无显示服务降级**: 确实无法提供虚拟显示 → 返回 501 + 可执行的两段式命令(不抛 500)
  - **会话结束必须关浏览器**: chromium 单实例 200~400MB, 泄漏会拖垮单 worker 后端
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# 会话最长存活(用户扫码 + 页面操作的允许时间)
SESSION_TTL = 600  # 10 分钟

# 登录态建议有效期(超过则提示重新登录; 仅提示, 不阻断)
STATE_STALE_DAYS = 7

# 各平台登录态文件名(与 shared_doc_sync_service._DEFAULT_STATE_FILE 一致)
DEFAULT_STATE_FILE = {"tencent": "tencent_doc.json", "kdocs": "kdocs.json"}

# 平台登录域名关键词(用于判定"是否仍在登录页")
_LOGIN_URL_HINTS = {
    "tencent": ("docs.qq.com/login", "login", "passport", "oauth"),
    "kdocs": ("passport", "login", "account.wps", "auth"),
}

# 平台会话 cookie 关键词(判定已登录的辅助信号)
_SESSION_COOKIE_HINTS = {
    "tencent": ("uid", "uin", "skey", "doc_session", "TOK"),
    "kdocs": ("wps_sid", "wpsua", "csrf", "sid"),
}

# ── 虚拟显示(Xvfb) ──
# ⚠️ 2026-09-13 生产故障(工单 #49 验收暴露, 详见 docs/ops/数据采集-运维手册.md §七):
#   旧判据是 `$DISPLAY 存在 or which(xvfb-run)` —— 探的是**安装状态**。
#   容器里 Dockerfile 装了 xvfb 包 → xvfb-run 必然在 PATH → 判据返回 True,
#   但**没有任何进程启动 Xvfb**($DISPLAY 为空) → `launch(headless=False)` 仍炸
#   "Missing X server or $DISPLAY", 被兜成 502 并把 Playwright 原始日志吐给用户;
#   设计 A-20260910-001 §四 的「501 降级(强制)」**从未被触发过**。
#   根因一句话: **「装了没」≠「跑着没」** → 必须探运行态(X socket), 并负责把服务拉起来。
XVFB_DISPLAY = ":99"          # 按需拉起的虚拟显示号(可用环境变量 XVFB_DISPLAY 覆盖)
_XVFB_SCREEN = "1280x800x24"  # 与浏览器 viewport 一致, 供二维码截图清晰可扫
_XVFB_WAIT_MAX = 5.0          # 等待 socket 出现的硬上限(秒); 只在失败路径才会等满
# Xavfb 需的 xauth 认证文件: 放在 socket 同目录, 权限 600 由 umask 保证
_XAUTHORITY = "/tmp/.X11-unix/xvfb-xauth"


def _display_number(display: str) -> str:
    """从 ":99" / "host:99.0" 取显示号, 供 socket 路径与 Xvfb 参数使用。"""
    return display.rsplit(":", 1)[-1].split(".")[0]


def _display_is_remote(display: str) -> bool:
    """DISPLAY 带主机名(如 "10.0.0.1:0") → 走 TCP, 本地无 socket 可探, 默认信任。"""
    return not display.startswith(":")


def _x_socket_path(display: str) -> str:
    """X server 的 unix socket 路径(抽象命名空间/文件系统两种形式都兼容)。"""
    num = _display_number(display)
    return f"/tmp/.X11-unix/X{num}"


def _socket_alive(display: str) -> bool:
    """探测 X server 是否**真在跑**(而不是只装了包)。

    判据 = socket 可连接。不直接用 os.path.exists 的原因: 陈旧的 socket **文件**会在
    X server 崩掉/被 kill 后残留(内核不会自动删), 只看存在会把死服务当活的。
    """
    try:
        import socket as _socket

        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        try:
            sock.settimeout(0.5)
            sock.connect(_x_socket_path(display))
            return True
        finally:
            sock.close()
    except Exception:  # noqa: BLE001  连不上(含文件不存在/陈旧 socket) → 不可用
        return False


def _start_xvfb_and_wait(display: str) -> bool:
    """按需拉起 Xvfb(幂等)并等待可连接。

    两个关键细节(首版写错, 生产实测才暴露 —— 见 2026-09-13 验证记录):
      ① **必须 `-ac`(关闭访问控制)**: `xvfb-run` 默认启用 xauth 保护, 而 chromium 是
         另一个进程, 拿不到该 cookie → 报 `Authorization required, but no
         authorization protocol specified` → 浏览器依旧起不来。
         本容器内的 X 仅本机+同容器使用(未监听 TCP), `-ac` 不扩大对外暴露面。
      ② **预热 auth 文件**: 手动拉起时 xauth 不会自动创建文件, X 客户端可能报
         "~/.Xauthority does not exist" → 预先 touch 一个空文件并以 DISPLAY 一起导出。

    注: 不能像首版那样用 `xvfb-run -n <n> sleep infinity` 包裹 —— xvfb-run 是
    一次性包装器, 它起的 Xvfb 生命周期绑定在 `sleep` 上; 一旦我们 kill 掉 sleep
    收尾, Xvfb 就会一起死。改用直接拉起 Xvfb 作常驻进程:
      - 由后端进程 spawn, 容器重建/重启时随之回收(不污染宿主机)
      - 幂等: socket 已可连接则直接复用, 不重复起进程
    """
    if _socket_alive(display):
        return True
    import shutil as _shutil

    if _shutil.which("Xvfb") is None:
        return False  # 镜像没装 xvfb → 调用方走 501 降级

    # ① 预热 auth 文件(空文件即可; -ac 下不校验内容, 仅除客户端警告)
    try:
        Path(_XAUTHORITY).touch(exist_ok=True)
        os.chmod(_XAUTHORITY, 0o600)
    except Exception:  # noqa: BLE001  仅预警, 不阻断
        logger.debug("[shared-doc-login] 预热 xauth 文件失败", exc_info=True)

    try:
        # ② 直接拉起 Xvfb 常驻: -ac 关访问控制; -nolisten tcp 不开 TCP 端口
        subprocess.Popen(
            [
                "Xvfb",
                display,
                "-screen",
                "0",
                _XVFB_SCREEN,
                "-ac",
                "-nolisten",
                "tcp",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001  起不来不抛错, 交给 501 降级
        logger.warning("[shared-doc-login] 拉起 Xvfb 失败", exc_info=True)
        return False
    deadline = time.time() + _XVFB_WAIT_MAX
    while time.time() < deadline:
        time.sleep(0.1)
        if _socket_alive(display):
            logger.info("[shared-doc-login] 已按需拉起 Xvfb, DISPLAY=%s", display)
            return True
    logger.warning("[shared-doc-login] Xvfb 拉起后 %.1fs 内未能连接: %s",
                   _XVFB_WAIT_MAX, display)
    return False


class DisplayUnavailableError(RuntimeError):
    """无可用显示服务且无法按需拉起 → 路由层转 501 + 给出两段式降级命令。"""


@dataclass
class LoginSession:
    """一次共享文档登录会话(内存态, TTL 600s)。"""

    session_id: str
    platform: str
    source_id: int | None
    doc_url: str
    state_path: Path
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending / scanned / authorized / closed / expired
    current_url: str = ""
    # Playwright 对象(不参与序列化)
    _pw: object = None
    _browser: object = None
    _ctx: object = None
    _page: object = None

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > SESSION_TTL

    @property
    def elapsed(self) -> int:
        return int(time.time() - self.created_at)

    def close(self) -> None:
        """关闭浏览器上下文(必须调用, 否则 chromium 进程泄漏)。"""
        for attr in ("_ctx", "_browser", "_pw"):
            obj = getattr(self, attr, None)
            if obj is None:
                continue
            try:
                stop = getattr(obj, "close" if attr != "_pw" else "stop", None)
                if callable(stop):
                    stop()
            except Exception:  # noqa: BLE001  关闭失败不影响主流程
                logger.debug("[shared-doc-login] 关闭 %s 失败", attr, exc_info=True)
        self._ctx = self._browser = self._pw = self._page = None


class SharedDocLoginManager:
    """登录会话管理器(进程内单例)。

    ⚠️ 单 worker 假设: 与项目 Gunicorn 单 worker 部署一致(互斥锁才有意义)。
    多 worker 部署下会话会落到不同进程 → 需改 Redis 存储, 当前架构不适用。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LoginSession] = {}
        self._lock = threading.Lock()

    # ── 内部工具 ──
    def _reap(self) -> None:
        """回收过期会话(必须关浏览器)。调用方需持锁。"""
        now = time.time()
        dead = [sid for sid, s in self._sessions.items() if (now - s.created_at) > SESSION_TTL]
        for sid in dead:
            sess = self._sessions.pop(sid)
            sess.status = "expired"
            sess.close()
            logger.info("[shared-doc-login] 会话超时回收: %s platform=%s", sid, sess.platform)

    def _active_for_platform(self, platform: str) -> LoginSession | None:
        """查同平台的活跃会话(用于单例锁)。调用方需持锁。"""
        for s in self._sessions.values():
            if s.platform == platform and s.status not in ("closed", "expired"):
                return s
        return None

    # ── 显示服务(运行态判定 + 按需拉起) ──
    @staticmethod
    def check_display_available() -> bool:
        """**只读**探测「当前是否已有可用显示服务」, 不产生副作用(供 /states 提示用)。

        判据(2026-09-13 修正, 见模块顶部注释):
          - $DISPLAY 指向的 X server **可连接** → True(X server 确实在跑)
          - 镜像装了 `Xvfb` → True(点「扫码登录」时会**按需拉起**, 届时可用)
          - 两者皆无(如本地 macOS: 无 X11 也无 Xvfb) → False → 页面显示降级提示

        注意: 真正启动会话前用 `ensure_display_available()`(会自愈), 不是这个。
        """
        current = (os.environ.get("DISPLAY") or "").strip()
        if current and (_display_is_remote(current) or _socket_alive(current)):
            return True
        return shutil.which("Xvfb") is not None

    @staticmethod
    def ensure_display_available() -> bool:
        """**可自愈**确保有可用显示服务(必要时按需拉起 Xvfb 并注入 DISPLAY)。

        与 check_display_available() 的分工:
          - check_*: 只读, 用于接口回显状态(不能因为一个 GET 就把 daemon 拉起来)
          - ensure_*: 写操作(起进程 + 改 DISPLAY), 仅在真正要 launch 浏览器前调用

        Returns:
            True 表示随后可以 `launch(headless=False)`; False → 路由层转 501。
        """
        # ① 已有可用的本地/远程 X server → 直接用
        current = (os.environ.get("DISPLAY") or "").strip()
        if current and (_display_is_remote(current) or _socket_alive(current)):
            return True
        # ② 无 DISPLAY(生产容器现状: 装了包但没人起进程) → 按需拉起并注入 DISPLAY
        display = (os.environ.get("XVFB_DISPLAY") or XVFB_DISPLAY).strip()
        if _start_xvfb_and_wait(display):
            os.environ["DISPLAY"] = display
            # 同时导出 XAUTHORITY: 不导出时 X 客户端会尝试 ~/.Xauthority, 缺文件会告警刷日志。
            # Xvfb 以 -ac 启动(关闭访问控制), 此文件为空仅用于除告警, 不参与鉴权。
            os.environ.setdefault("XAUTHORITY", _XAUTHORITY)
            return True
        return False

    @staticmethod
    def cli_fallback_command(platform: str, doc_url: str) -> str:
        """生成降级命令(确实无法提供虚拟显示时, 给运维的可执行路径)。

        ⚠️ 2026-09-13 修正: 旧命令 `cd /path/to/backend && ./.venv/bin/python ../scripts/...`
        是**假路径** —— 生产容器里既没有 `scripts/` 也没有 `.venv`, 拷了也跑不通。
        而本机(macOS)有 GUI, 脚本 `headless=False` 可直接扫码。故改为**两段式**:
          ① 本机生成 storage_state → ② scp 上传到宿主机挂载目录(容器内 /app/data/browser_states)
        """
        script = "tencent_doc_login.py" if platform == "tencent" else "kdocs_login.py"
        state_file = DEFAULT_STATE_FILE.get(platform, f"{platform}.json")
        return (
            f"# ① 本地(macOS 有 GUI)生成登录态; 脚本会弹浏览器, 扫码后回车保存\n"
            f'cd backend && ./.venv/bin/python ../scripts/{script} --url "{doc_url}"\n'
            f"# ② 上传到生产宿主机(挂载点 → 容器内 /app/data/browser_states)\n"
            f"scp backend/data/browser_states/{state_file} tanzk@<服务器IP>:/tmp/ && \\\n"
            f"  ssh tanzk@<服务器IP> \"sudo mv /tmp/{state_file} /opt/csctalon/browser_states/\""
        )

    # ── 会话生命周期 ──
    def start(self, platform: str, doc_url: str, state_path: Path,
              source_id: int | None = None) -> LoginSession:
        """启动登录会话: 起浏览器 → 打开文档 → 截图。

        Raises:
            DisplayUnavailableError: 无显示服务且无法按需拉起
            RuntimeError: 同平台已有活跃会话(路由层转 409)
        """
        with self._lock:
            self._reap()
            existing = self._active_for_platform(platform)
            if existing is not None and not existing.expired:
                raise RuntimeError(
                    f"该平台已有正在进行的登录会话({existing.session_id}), 请先完成或取消"
                )

        # 显示服务: 运行态判定 + 按需拉起 Xvfb(生产容器装了包但没人起进程 → 此处自愈)
        if not self.ensure_display_available():
            raise DisplayUnavailableError(
                "服务器无法提供虚拟显示服务(Xvfb 未安装或拉起失败), 无法在容器内启动浏览器"
            )

        sid = uuid.uuid4().hex[:12]
        sess = LoginSession(
            session_id=sid,
            platform=platform,
            source_id=source_id,
            doc_url=doc_url,
            state_path=state_path,
        )

        # 延迟导入: playwright 在 app 导入链上不顶层加载(见 CI 说明)
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            # headless=False: 需要用户扫码, 必须有真实页面渲染
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()
            page.goto(doc_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            try:
                pw.stop()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"打开登录页失败: {e}") from e

        sess._pw, sess._browser, sess._ctx, sess._page = pw, browser, ctx, page
        sess.current_url = page.url

        with self._lock:
            self._sessions[sid] = sess
        logger.info("[shared-doc-login] 会话已启动: %s platform=%s url=%s",
                    sid, platform, doc_url)
        return sess

    def get(self, session_id: str) -> LoginSession | None:
        with self._lock:
            self._reap()
            return self._sessions.get(session_id)

    def refresh_status(self, sess: LoginSession) -> str:
        """探测当前登录状态(不落盘), 更新 sess.status 并返回。"""
        if sess.expired:
            sess.status = "expired"
            return sess.status
        page = sess._page
        if page is None:
            sess.status = "closed"
            return sess.status
        try:
            url = page.url or ""
            sess.current_url = url
        except Exception:  # noqa: BLE001  页面已关闭
            sess.status = "closed"
            return sess.status

        # 判定: 是否仍在登录相关域名/路径
        hints = _LOGIN_URL_HINTS.get(sess.platform, ())
        in_login = any(h.lower() in url.lower() for h in hints)

        # 辅助信号: cookie 中出现平台会话键
        has_session_cookie = False
        try:
            names = [c.get("name", "") for c in (sess._ctx.cookies() if sess._ctx else [])]
            cookie_hints = _SESSION_COOKIE_HINTS.get(sess.platform, ())
            has_session_cookie = any(
                any(h.lower() in n.lower() for h in cookie_hints) for n in names
            )
        except Exception:  # noqa: BLE001
            pass

        if not in_login and has_session_cookie:
            sess.status = "authorized"
        elif in_login:
            sess.status = "scanned" if has_session_cookie else "pending"
        else:
            # 不在登录页但未见会话 cookie —— 不武断判 ready, 交给 confirm 的轻量校验
            sess.status = "scanned"
        return sess.status

    def screenshot_b64(self, sess: LoginSession) -> str:
        """截取当前页面(登录二维码/登录页)。"""
        page = sess._page
        if page is None:
            return ""
        try:
            data = page.screenshot(type="png")
            return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        except Exception:  # noqa: BLE001  截图失败不阻塞流程(前端仍可点确认)
            logger.warning("[shared-doc-login] 截图失败: %s", sess.session_id, exc_info=True)
            return ""

    def verify_logged_in(self, sess: LoginSession) -> tuple[bool, str]:
        """轻量校验: 用当前上下文重新打开目标文档, 判断是否已脱离登录页。

        用途(A-20260910-001 §五.4): 用户可能在未真正登录时点「我已扫码完成」,
        若不校验会写入无效 state 并**覆盖**原有可用登录态。

        Returns:
            (ok, message)
        """
        page = sess._page
        if page is None:
            return False, "浏览器会话已关闭, 请重新发起登录"
        try:
            # 重新导航到文档, 观察最终落点
            page.goto(sess.doc_url, wait_until="domcontentloaded", timeout=45000)
            final_url = (page.url or "").lower()
        except Exception as e:  # noqa: BLE001
            return False, f"校验时打开文档失败: {e}"

        hints = _LOGIN_URL_HINTS.get(sess.platform, ())
        if any(h.lower() in final_url for h in hints):
            return False, "未检测到登录态(页面仍停留在登录/授权页), 请完成扫码后重试"

        # cookie 数量作为辅助信号(过少通常意味着未登录)
        try:
            cookies = sess._ctx.cookies() if sess._ctx else []
        except Exception:  # noqa: BLE001
            cookies = []
        if not cookies:
            return False, "未检测到登录 Cookie, 请确认已完成扫码登录"
        return True, "ok"

    def save_state(self, sess: LoginSession) -> dict:
        """保存 storage_state(覆盖前备份旧文件)。"""
        sess.state_path.parent.mkdir(parents=True, exist_ok=True)
        backup_created = False
        if sess.state_path.exists():
            bak = sess.state_path.with_suffix(".json.bak")
            try:
                shutil.copyfile(sess.state_path, bak)
                backup_created = True
            except Exception:  # noqa: BLE001  备份失败不阻断(但记录)
                logger.warning("[shared-doc-login] 备份旧登录态失败: %s", bak, exc_info=True)
        sess._ctx.storage_state(path=str(sess.state_path))
        cookies = []
        try:
            cookies = sess._ctx.cookies() or []
        except Exception:  # noqa: BLE001
            pass
        return {
            "state_path": sess.state_path.name,  # 只回文件名, 不回绝对路径
            "cookies": len(cookies),
            "backup_created": backup_created,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

    def finish(self, session_id: str) -> None:
        """结束会话(关浏览器 + 移除)。幂等。"""
        with self._lock:
            sess = self._sessions.pop(session_id, None)
        if sess is not None:
            sess.status = "closed"
            sess.close()
            logger.info("[shared-doc-login] 会话已结束: %s", session_id)

    def close_all(self) -> None:
        """应用关闭时清理全部会话(防进程退出残留 chromium)。"""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.close()
        if sessions:
            logger.info("[shared-doc-login] 应用关闭, 清理 %d 个登录会话", len(sessions))


# 进程内单例
login_manager = SharedDocLoginManager()


# ─────────────────────────────────────────────────────────────────────
# 登录态健康度
# ─────────────────────────────────────────────────────────────────────
def state_health(platform: str, state_file: str | None = None) -> dict:
    """读取某平台登录态文件的健康度(mtime / 大小 / 是否超期)。

    超期仅**提示**(各平台 token 实际有效期不同, 无法准确判定), 不阻断采集。
    """
    filename = state_file or DEFAULT_STATE_FILE.get(platform, f"{platform}.json")
    path = Path(settings.BROWSER_STATE_DIR) / filename
    if not path.exists():
        return {
            "platform": platform,
            "state_file": filename,
            "exists": False,
            "stale": True,
            "advice": "未登录, 请先扫码登录",
        }
    st = path.stat()
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    age_days = round((datetime.now(timezone.utc) - mtime).total_seconds() / 86400, 1)
    stale = age_days > STATE_STALE_DAYS
    return {
        "platform": platform,
        "state_file": filename,
        "exists": True,
        "size_bytes": st.st_size,
        "mtime": mtime.isoformat(),
        "age_days": age_days,
        "stale": stale,
        "advice": f"登录态已 {age_days} 天, 建议重新登录" if stale else "",
    }
