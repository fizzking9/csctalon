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
import concurrent.futures
import logging
import multiprocessing
import os
import queue
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

# 各平台默认登录态文件名(与 shared_doc_sync_service._DEFAULT_STATE_FILE 一致)
DEFAULT_STATE_FILE = {"tencent": "tencent_doc.json", "kdocs": "kdocs.json"}

# #148 腾讯直接打开共享表(doc_url)会进访客只读视图, 看到表格内容却没有登录二维码。
# 必须先打开腾讯首页 → 点右上角"登录" → 勾协议 → 点 "Log in now" 才显示扫码二维码。
TENCENT_HOME_URL = "https://docs.qq.com/"

# 平台登录域名关键词(用于判定"是否仍在登录页")
_LOGIN_URL_HINTS = {
    "tencent": ("docs.qq.com/login", "login", "passport", "oauth"),
    "kdocs": ("passport", "singlesso", "login", "account.wps", "auth"),
}

# 平台会话 cookie 关键词(判定已登录的辅助信号)
# ⚠️ 2026-09-13 修正: kdocs 原先含 "csrf" —— 实测登录前 cookie 里**就已经有 csrf**
#   (共 13 个 cookie 即命中) → 页面还停在登录弹窗却判成 authorized。
#   只保留真正的登录态 cookie 名。
_SESSION_COOKIE_HINTS = {
    "tencent": ("uid", "uin", "skey", "doc_session", "TOK"),
    "kdocs": ("wps_sid", "wpsua"),
}

# 二维码是否已出现(判定"页面渲染完成 + 可扫码")
# 注意: 不能只找 <img src=*qrcode*>, 因为腾讯/金山都用 canvas 或异步图渲染二维码。
#
# ── 登录按钮自动点击(2026-09-13 新增) ──
# 实测 kdocs: 打开共享链接后弹窗**默认没有二维码**, 只有
#   `Open the document with WPS` / `Sign in` 两个按钮;
# 二维码要**点了 Sign in 才出现**(点击后 `qrcode_img_block` 180x180 真实图才加载)。
# 而前端弹窗只是静态截图, 用户**点不到**那个按钮 → 必须由后端代点, 否则永远扫不到码。
_KDOCS_SIGNIN_BUTTONS = (
    ".wlcp-btn:not(.is-highlight)",       # 实测有效: 非高亮那个按钮
    "button:has-text('Sign in')",
    "button:has-text('登录')",
)

# 判定二维码是否已出现(供 wait 与状态探测共用)
#
# ⚠️ 2026-09-13 踩坑: 首版判据只看「qrcode 容器尺寸 ≥80px」→ **假阳性**。
#   实测 kdocs: 点击 Sign in 后容器 `.qrcode_img_block`(180x180) 立刻存在,
#   但里面的 `<img id="miniprogramImportImg">` **还没有 src / 还没加载完**,
#   该区域像素 100% 纯白(灰度种类=1)。容器尺寸判据让等待提前通过 → 截图仍是空白。
#   图片约 1~3s 后加载完成(naturalWidth=430)。
#   → 判据必须要求**图片真的加载成功**(naturalWidth > 0), 不能只看容器盒子。
_QRCODE_PRESENT_JS = """() => {
  // ① img 型二维码: 必须真正加载成功(naturalWidth>0), 否则容器在但图是空的
  for (const s of ['[class*="qrcode"] img','[class*="Qrcode"] img',
                   '[id*="qrcode"] img','[id*="Qrcode"] img']) {
    for (const el of document.querySelectorAll(s)) {
      const r = el.getBoundingClientRect();
      if (r.width >= 80 && r.height >= 80 && el.naturalWidth > 0) return true;
    }
  }
  // ② canvas 渲染型(腾讯文档等): 有尺寸即视为已渲染
  //    腾讯微信码是多层 APNG 动画 canvas(class="common-apng-item-in/out"), 不含 qr 字样
  for (const s of ['canvas[class*="qr"]','[class*="qrcode"] canvas','[id*="qrcode"] canvas',
                   'canvas[class*="apng"]']) {
    for (const el of document.querySelectorAll(s)) {
      const r = el.getBoundingClientRect();
      // apng 首页营销动画在视口外(y 很大), 必须限制在视口内才认作登录码
      if (r.width >= 80 && r.height >= 80 &&
          r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth) return true;
    }
  }
  // ③ 跨域 iframe 型(腾讯微信扫码 open.weixin.qq.com/connect/qrconnect):
  //    主文档无法穿透 iframe, 只要该微信二维码 iframe 可见即视为登录码展示中
  for (const el of document.querySelectorAll('iframe')) {
    const r = el.getBoundingClientRect();
    const s = el.src || '';
    if (r.width >= 120 && r.height >= 120 && s.includes('weixin') &&
        s.toLowerCase().includes('qr') && r.top >= 0 && r.bottom <= innerHeight) return true;
  }
  return false;
}"""

# #148 返回二维码元素相对视口的包围盒(CSS 像素), 供截图聚焦放大; 找不到返回 null。
# 选择器与 _QRCODE_PRESENT_JS 对齐, 多个候选时取面积最大者(避开装饰性小图)。
_QRCODE_BOX_JS = """() => {
  // ① 跨域微信 iframe(腾讯): 整卡即二维码区域, 直接返回 iframe 盒, kind=iframe
  for (const el of document.querySelectorAll('iframe')) {
    const r = el.getBoundingClientRect();
    const s = el.src || '';
    if (r.width >= 120 && r.height >= 120 && s.includes('weixin') &&
        s.toLowerCase().includes('qr') && r.top >= 0 && r.bottom <= innerHeight) {
      return {x:r.x, y:r.y, width:r.width, height:r.height, kind:'iframe'};
    }
  }
  // ② 主文档内的 img / canvas 二维码, 多个候选取面积最大者(避开装饰小图)
  const cands = [];
  const push = (el) => {
    const r = el.getBoundingClientRect();
    // 二维码近正方形、边长足够、完整落在视口内(排除视口外的 apng 营销动画)
    if (r.width >= 90 && r.height >= 90 &&
        r.width / r.height > 0.7 && r.width / r.height < 1.4 &&
        r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth) {
      cands.push({x:r.x, y:r.y, width:r.width, height:r.height, area:r.width*r.height});
    }
  };
  for (const s of ['[class*="qrcode"] img','[class*="Qrcode"] img',
                   '[id*="qrcode"] img','[id*="Qrcode"] img']) {
    for (const el of document.querySelectorAll(s)) if (el.naturalWidth > 0) push(el);
  }
  for (const s of ['canvas[class*="qr"]','[class*="qrcode"] canvas','[id*="qrcode"] canvas',
                   'canvas[class*="apng"]']) {
    for (const el of document.querySelectorAll(s)) push(el);
  }
  if (!cands.length) return null;
  cands.sort((a, b) => b.area - a.area);
  const best = cands[0];
  return {x: best.x, y: best.y, width: best.width, height: best.height, kind: 'qrcode'};
}"""

# ── 虚拟显示(Xvfb) ──
# ⚠️ 2026-09-13 生产故障(工单 #49 验收暴露, 详见 docs/ops/数据采集-运维手册.md §七):
#   旧判据是 `$DISPLAY 存在 or which(xvfb-run)` —— 探的是**安装状态**。
#   容器里 Dockerfile 装了 xvfb 包 → xvfb-run 必然在 PATH → 判据返回 True,
#   但**没有任何进程启动 Xvfb**($DISPLAY 为空) → `launch(headless=False)` 仍炸
#   "Missing X server or $DISPLAY", 被兜成 502 并把 Playwright 原始日志吐给用户;
#   设计 A-20260910-001 §四 的「501 降级(强制)」**从未被触发过**。
#   根因一句话: **「装了没」≠「跑着没」** → 必须探运行态(X socket), 并负责把服务拉起来。
XVFB_DISPLAY = ":99"          # 按需拉起的虚拟显示号(可用环境变量 XVFB_DISPLAY 覆盖)
_XVFB_SCREEN = "1600x1000x24"  # 与浏览器 viewport 一致, 供二维码截图清晰可扫(#148 放大)
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


# ── #48: 多 worker 部署检测 ──
# 背景: SharedDocLoginManager 是进程内单例(L269 类注释「⚠️ 单 worker 假设」),
#   会话内存态 + chromium 进程在同一 worker 内存活。若 Gunicorn 多 worker (-w N),
#   ① start 命中 worker A, 后续 status/confirm 命中 worker B → 会话找不到 → 404;
#   ② 多 worker 各自起 chromium → 单机内存爆炸。
# 检测方式: multiprocessing.parent_process() 在 Gunicorn fork 出的 worker 进程里
#   返回非 None(父进程即 master), 在单进程 run(main.py 直接 run) 或 gunicorn -w 1
#   (无 fork, 直接 exec)时返回 None。这是最可靠判据, 不依赖环境变量(可被误改)。
class MultiWorkerDeploymentError(RuntimeError):
    """部署形态不支持页面内扫码登录(多 worker) → 路由层转 501 + 给出 CLI 降级命令。

    A2 方案: 启动只打 WARNING 不 fail-fast(不阻断其他业务); 仅 4 个 login 端点
    显式返回 501 让前端立刻知道这条链路不可用。
    """


def is_multi_worker() -> bool:
    """检测当前进程是否运行在 Gunicorn 多 worker 模式下。

    返回 True → 多 worker(SharedDocLoginManager 不安全);
    返回 False → 单 worker / 直接 python main.py(SharedDocLoginManager 可用)。

    判据: multiprocessing.parent_process() 在被 fork/spawn 出的 worker 进程里
    返回非 None; 在主进程(单 worker -w 1 不 fork, 或直接 python main.py)返回 None。
    函数位置: multiprocessing.process 模块, 顶层 multiprocessing 包 re-export。
    """
    try:
        # multiprocessing.parent_process 仅 3.8+ 有; 走顶层避免 context 模块查不到。
        parent = multiprocessing.parent_process()
        return parent is not None
    except Exception:  # noqa: BLE001  拿不到父进程信息 → 按单 worker 处理(不阻断业务)
        return False


def check_single_worker() -> bool:
    """启动期检测: 多 worker 时返回 False 并打 WARNING(不 fail-fast)。

    A2 设计(用户拍板 2026-09-18): 启动只打 WARNING 不阻断整个 backend;
    仅 4 个 login 端点(/login/start|status|confirm|cancel)在多 worker 时
    显式返回 501 + CLI 降级命令, 其他业务照常服务。
    """
    if is_multi_worker():
        logger.warning(
            "[shared-doc-login] 检测到多 worker 部署(WEB_CONCURRENCY>1 或 gunicorn -w N): "
            "页面内扫码登录已禁用(SharedDocLoginManager 是进程内单例, 跨 worker 会话不可达)。"
            "请改用 CLI 脚本(scripts/tencent_doc_login.py / kdocs_login.py), 或改回单 worker 部署。"
            "其他业务不受影响。"
        )
        return False
    return True


class _SessionWorker:
    """每个登录会话独占的 Playwright 工作线程。

    #148 根因: Playwright Sync API 在 sync_playwright().start() 后, 其 dispatcher
    greenlet 永久挂在 `loop.run_until_complete(run_as_sync())` 里, 把所在 OS 线程的
    thread-local running event loop 一直置位。FastAPI 的 sync 端点跑在 anyio 共享
    线程池: ① 线程复用 → 第二个 sync_playwright() 命中 "inside the asyncio loop";
    ② Playwright 对象绑定创建线程, 跨线程做 screenshot/status/confirm 也会报错。
    给每个会话一条专属线程 + 任务队列, 所有浏览器操作都在该线程顺序执行, 彻底隔离。
    """

    def __init__(self) -> None:
        self._tasks: "queue.Queue" = queue.Queue()
        # Playwright 对象只在工作线程内创建/访问(外部线程禁止直接触碰)
        self.pw = None
        self.browser = None
        self.ctx = None
        self.page = None
        self._thread = threading.Thread(
            target=self._main,
            name=f"doc-login-{uuid.uuid4().hex[:6]}",
            daemon=True,
        )
        self._thread.start()

    def _main(self) -> None:
        # 专属线程从不 set/run 主 asyncio 循环 → get_running_loop() 恒抛 RuntimeError,
        # Playwright 自建 own loop, 不会触发 "inside the asyncio loop"。
        while True:
            item = self._tasks.get()
            if item is None:  # 停止信号
                return
            fn, args, fut = item
            try:
                fut.set_result(fn(self, *args))
            except BaseException as exc:  # noqa: BLE001  回传给 submit 方
                fut.set_exception(exc)

    def run(self, fn, *args, timeout: float | None = None):
        """在专属线程同步执行 fn(worker, *args), 阻塞调用线程并返回结果。"""
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._tasks.put((fn, args, fut))
        return fut.result(timeout=timeout)

    def shutdown(self) -> None:
        self._tasks.put(None)


def _browser_teardown(w: _SessionWorker) -> None:
    """在工作线程内关闭浏览器上下文(必须调用, 否则 chromium 进程泄漏)。"""
    for obj, method in ((w.ctx, "close"), (w.browser, "close"), (w.pw, "stop")):
        if obj is None:
            continue
        try:
            fn = getattr(obj, method, None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001  关闭失败不影响主流程
            logger.debug("[shared-doc-login] 关闭浏览器对象失败", exc_info=True)
    w.pw = w.browser = w.ctx = w.page = None


# 浏览器 UA(与 CLI 登录脚本/导出服务保持一致)
_LOGIN_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _browser_open(w: _SessionWorker, platform: str, doc_url: str) -> str:
    """在专属工作线程内启动有头浏览器并打开登录二维码页, 返回当前 URL。

    所有 Playwright Sync 调用都必须在此线程执行(page/ctx 绑定创建线程)。
    """
    # 工作线程是干净线程(无 running loop), sync API 不会触发 asyncio 报错
    from playwright.sync_api import sync_playwright

    w.pw = sync_playwright().start()
    try:
        w.browser = w.pw.chromium.launch(headless=False)
        w.ctx = w.browser.new_context(
            user_agent=_LOGIN_UA,
            # #148 放大 viewport: 金山登录弹窗二维码太小扫不清
            viewport={"width": 1600, "height": 1000},
            # #148 2x 渲染: Xvfb 仍是 1600x1000 逻辑屏, 但截图/二维码物理像素翻倍,
            # 前端把整页缩到 ~480px 容器时二维码仍有足够像素可被微信识别
            device_scale_factor=2,
        )
        w.page = w.ctx.new_page()
        page = w.page
        if platform == "tencent":
            # #148 腾讯: 不能直接打开 doc_url(进访客只读视图, 无二维码), 走首页登录弹窗
            SharedDocLoginManager._open_tencent_qrcode(page)
        else:
            page.goto(doc_url, wait_until="domcontentloaded", timeout=60000)
            # domcontentloaded 后弹窗/SPA 还要 ~3s 才渲染, 立刻截图会是纯白空框
            page.wait_for_timeout(3500)
            SharedDocLoginManager._open_qrcode_if_needed(page, platform)
            # #148 金山 account.wps.cn 登录卡协议框默认不勾, 不勾则扫码确认后无法回跳完成登录
            SharedDocLoginManager._ensure_agreement_checked(page)
        # 二维码图/canvas 异步加载, 等它真就位(超时不阻断, 至少截到当前状态)
        if SharedDocLoginManager.qrcode_visible(page):
            try:
                page.wait_for_function(_QRCODE_PRESENT_JS, timeout=10000)
            except Exception:  # noqa: BLE001
                logger.info("[shared-doc-login] 等二维码图片加载超时, 按当前状态截图")
        return page.url or ""
    except Exception:
        # 启动失败在同一工作线程内停掉 pw, 避免 chromium 泄漏
        try:
            if w.pw is not None:
                w.pw.stop()
        except Exception:  # noqa: BLE001
            pass
        w.pw = w.browser = w.ctx = w.page = None
        raise


def _browser_probe(w: _SessionWorker, platform: str) -> tuple[str, str]:
    """探测登录状态 → (status, current_url)。在工作线程执行。"""
    page = w.page
    if page is None:
        return "closed", ""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001  页面已关闭
        return "closed", ""

    hints = _LOGIN_URL_HINTS.get(platform, ())
    in_login = any(h.lower() in url.lower() for h in hints)

    has_session_cookie = False
    try:
        names = [c.get("name", "") for c in (w.ctx.cookies() if w.ctx else [])]
        cookie_hints = _SESSION_COOKIE_HINTS.get(platform, ())
        has_session_cookie = any(
            any(h.lower() in n.lower() for h in cookie_hints) for n in names
        )
    except Exception:  # noqa: BLE001
        pass

    # 看得到二维码 = 一定还没登录(2026-09-13 修正, 防 cookie 误判 authorized)
    qr_visible = SharedDocLoginManager.qrcode_visible(page)
    if qr_visible:
        status = "scanned" if has_session_cookie else "pending"
    elif not in_login and has_session_cookie:
        status = "authorized"
    elif in_login:
        status = "scanned" if has_session_cookie else "pending"
    else:
        # 不在登录页/无二维码/无会话 cookie —— 不武断判 ready, 交给 confirm 校验
        status = "scanned"
    return status, url


def _browser_screenshot(w: _SessionWorker) -> str:
    """截取登录二维码 → data URI; 失败返回空串。在工作线程执行。

    #148 不截整页: 整页 1600 宽里二维码仅占 ~11%, 前端缩到 ~480px 弹窗后只剩
    ~50px, 微信无法识别。改为按二维码包围盒裁剪(含标题/协议留白), 让二维码占画面
    1/3 左右且在 device_scale_factor=2 下有 ~380 物理像素; 定位失败再回退整页。
    """
    page = w.page
    if page is None:
        return ""
    data = b""
    try:
        box = page.evaluate(_QRCODE_BOX_JS)
        if box and box.get("width") and box.get("height"):
            vp = page.viewport_size or {"width": 1600, "height": 1000}
            win_w, win_h = float(vp["width"]), float(vp["height"])
            if box.get("kind") == "iframe":
                # 微信 iframe 盒本身就是完整登录卡, 四周仅留小边距, 不再外扩
                pad = 12.0
                cx0, cy0 = float(box["x"]) - pad, float(box["y"]) - pad
                clip_w, clip_h = float(box["width"]) + pad * 2, float(box["height"]) + pad * 2
            else:
                # img/canvas 小方码: 横向留登录卡宽度; 向上留标题、向下多留协议/按钮
                side = max(float(box["width"]), float(box["height"]))
                half_w, up, down = side * 1.6, side * 1.5, side * 2.2
                clip_w, clip_h = min(half_w * 2, win_w), min(up + down, win_h)
                cx0 = float(box["x"]) + float(box["width"]) / 2 - half_w
                cy0 = float(box["y"]) + float(box["height"]) / 2 - up
            clip_w = min(clip_w, win_w)
            clip_h = min(clip_h, win_h)
            x0 = min(max(cx0, 0), max(win_w - clip_w, 0))
            y0 = min(max(cy0, 0), max(win_h - clip_h, 0))
            data = page.screenshot(type="png", clip={
                "x": x0, "y": y0, "width": clip_w, "height": clip_h,
            })
    except Exception as e:  # noqa: BLE001  裁剪是增强项, 任何异常都回退整页
        logger.warning("[shared-doc-login] 二维码区域裁剪失败, 回退整页截图: %s", e)
    if not data:
        try:
            data = page.screenshot(type="png")
        except Exception as e:  # noqa: BLE001  页面已关闭等
            logger.warning("[shared-doc-login] 截图失败: %s", e)
            return ""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _browser_verify(w: _SessionWorker, platform: str, doc_url: str) -> tuple[bool, str]:
    """confirm 前轻量校验: 用与同步层一致的访客检测判据确认登录态。

    #148 实测: 金山的真正登录态校验是 JS `__WPSENV__.user.id` 不以 fake_ 开头, 不是
    cookie 名。腾讯是 body 不含 skeleton-guest-mode + cookies 含 skey/uin。

    关键: 不盲目 goto —— 用户扫码后页面已自己跳回文档并设好 __WPSENV__/skey,
    goto 重新加载会冲掉这些 JS 变量 → 误判访客。先在当前页检查, 通过则直接返回;
    不通过才 goto + 等 + 复查。
    """
    page = w.page
    if page is None:
        return False, "浏览器会话已关闭, 请重新发起登录"

    try:
        cookie_names = [c.get("name", "") for c in (w.ctx.cookies() if w.ctx else [])]
    except Exception:  # noqa: BLE001
        cookie_names = []

    def _check_guest():
        """在当前 page 执行访客检测, 返回 (is_guest, detail)。"""
        if platform == "tencent":
            try:
                body_cls = page.evaluate("() => (document.body && document.body.className) || ''")
                if not isinstance(body_cls, str):
                    body_cls = ""
            except Exception:  # noqa: BLE001
                body_cls = ""
            guest = "skeleton-guest-mode" in body_cls
            return guest, f"body_cls={body_cls[:60]}"
        else:
            try:
                uid = page.evaluate(
                    """() => {
                        const env = window.__WPSENV__ || {};
                        const u = (env.file_info && env.file_info.user) || env.user || {};
                        return String(u.id || '');
                    }"""
                )
            except Exception:  # noqa: BLE001
                uid = ""
            if not isinstance(uid, str):
                uid = ""
            guest = not uid or uid.startswith("fake_")
            return guest, f"uid={uid[:30]}"

    # ① 先在当前页检查(用户扫码后页面已跳回文档, __WPSENV__/skey 已就位)
    is_guest, detail = _check_guest()
    final_url = ""
    try:
        final_url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        "[shared-doc-login] verify(当前页) platform=%s url=%s is_guest=%s %s cookies=%d %s",
        platform, final_url[:100], is_guest, detail, len(cookie_names), cookie_names[:15],
    )

    # 腾讯: 访客检测 + 会话 cookie 双重校验; 金山: 仅 __WPSENV__.user.id
    # 通过后仍需检查 URL/二维码(防误判)
    def _pass_url_qr():
        hints = _LOGIN_URL_HINTS.get(platform, ())
        if any(h.lower() in final_url for h in hints):
            return False, "未检测到登录态(页面仍停留在登录/授权页), 请完成扫码后重试"
        if SharedDocLoginManager.qrcode_visible(page):
            return False, "页面仍显示登录二维码, 未检测到登录态, 请完成扫码后重试"
        return True, "ok"

    if not is_guest:
        if platform == "tencent":
            session_hints = _SESSION_COOKIE_HINTS["tencent"]
            has_session = any(any(h in n for h in session_hints) for n in cookie_names)
            if has_session:
                return _pass_url_qr()
        else:
            return _pass_url_qr()

    # ② 当前页不通过, 可能扫码回调还在进行中 → 等 8s 再查一次
    try:
        page.wait_for_timeout(8000)
    except Exception:  # noqa: BLE001
        pass
    is_guest, detail = _check_guest()
    try:
        final_url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        pass
    logger.info(
        "[shared-doc-login] verify(等8s后) platform=%s url=%s is_guest=%s %s",
        platform, final_url[:100], is_guest, detail,
    )

    if not is_guest:
        if platform == "tencent":
            session_hints = _SESSION_COOKIE_HINTS["tencent"]
            has_session = any(any(h in n for h in session_hints) for n in cookie_names)
            if has_session:
                return _pass_url_qr()
        else:
            return _pass_url_qr()

    # ③ 仍不通过, 最后试 goto doc_url 重新加载(有时页面 SPA 状态卡住需刷新)
    try:
        page.goto(doc_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
    except Exception as e:  # noqa: BLE001
        logger.warning("[shared-doc-login] verify goto 失败 platform=%s err=%s", platform, e)
        return False, f"校验时打开文档失败: {e}"

    try:
        final_url = (page.url or "").lower()
    except Exception:  # noqa: BLE001
        final_url = ""
    is_guest, detail = _check_guest()
    logger.info(
        "[shared-doc-login] verify(goto后) platform=%s url=%s is_guest=%s %s",
        platform, final_url[:100], is_guest, detail,
    )

    if is_guest:
        return False, (
            f"{'腾讯文档仍为访客模式' if platform == 'tencent' else '金山文档仍为访客身份'}"
            f"({detail}), 请确认已在手机上完成微信授权"
        )
    if platform == "tencent":
        session_hints = _SESSION_COOKIE_HINTS["tencent"]
        has_session = any(any(h in n for h in session_hints) for n in cookie_names)
        if not has_session:
            return False, "未检测到腾讯会话 Cookie(skey/uin), 请确认已在手机上完成微信授权"

    # 通用: 不在登录页 + 无二维码
    hints = _LOGIN_URL_HINTS.get(platform, ())
    if any(h.lower() in final_url for h in hints):
        return False, "未检测到登录态(页面仍停留在登录/授权页), 请完成扫码后重试"
    if SharedDocLoginManager.qrcode_visible(page):
        return False, "页面仍显示登录二维码, 未检测到登录态, 请完成扫码后重试"
    return True, "ok"


def _browser_save(w: _SessionWorker, state_path: Path) -> dict:
    """备份旧登录态 + 保存当前 storage_state。在工作线程执行。"""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    backup_created = False
    if state_path.exists():
        bak = state_path.with_suffix(".json.bak")
        try:
            shutil.copyfile(state_path, bak)
            backup_created = True
        except Exception:  # noqa: BLE001
            logger.warning("[shared-doc-login] 备份旧登录态失败: %s", bak, exc_info=True)
    w.ctx.storage_state(path=str(state_path))
    cookies = []
    try:
        cookies = w.ctx.cookies() or []
    except Exception:  # noqa: BLE001
        pass
    return {
        "state_path": state_path.name,  # 只回文件名, 不回绝对路径
        "cookies": len(cookies),
        "backup_created": backup_created,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


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
    # 专属 Playwright 工作线程(不参与序列化)
    _worker: _SessionWorker | None = None

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > SESSION_TTL

    @property
    def elapsed(self) -> int:
        return int(time.time() - self.created_at)

    def close(self) -> None:
        """关闭浏览器 + 回收专属线程(幂等)。"""
        w = self._worker
        if w is None:
            return
        try:
            w.run(_browser_teardown, timeout=30)
        except Exception:  # noqa: BLE001  teardown 超时/异常也继续 shutdown
            logger.debug("[shared-doc-login] teardown 失败", exc_info=True)
        w.shutdown()
        self._worker = None


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

        # 浏览器在会话专属工作线程内启动/操作(见 _SessionWorker 文档: 避开 anyio 线程池
        # 复用 + Playwright dispatcher greenlet 永久占用线程 running-loop 的问题, #148)。
        worker = _SessionWorker()
        try:
            current_url = worker.run(_browser_open, platform, doc_url, timeout=90)
        except Exception as e:
            worker.shutdown()
            raise RuntimeError(f"打开登录页失败: {e}") from e

        sess._worker = worker
        sess.current_url = current_url

        with self._lock:
            self._sessions[sid] = sess
        logger.info("[shared-doc-login] 会话已启动: %s platform=%s url=%s",
                    sid, platform, doc_url)
        return sess

    @staticmethod
    def qrcode_visible(page) -> bool:
        """当前页面是否已出现可扫的二维码(≥80px 的 qrcode 容器)。"""
        try:
            return bool(page.evaluate(_QRCODE_PRESENT_JS))
        except Exception:  # noqa: BLE001  页面已关闭等
            return False

    @staticmethod
    def _open_tencent_qrcode(page) -> None:
        """#148 腾讯: 打开首页 → 点登录 → 勾协议 → 点 Log in now → 显示扫码二维码。

        直接打开共享表 doc_url 会进访客只读视图(看到表格内容, 右上角仍是 Login 按钮),
        不会引导扫码。必须走首页登录弹窗这条路(实测 2026-09-18 验证)。
        """
        page.goto(TENCENT_HOME_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        # ① 点右上角"登录"按钮弹出登录方式弹窗
        for sel in ("text=登录", "button:has-text('登录')", "a:has-text('登录')"):
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(2000)
                    break
            except Exception:  # noqa: BLE001  该选择器不匹配, 试下一个
                continue
        # ② 勾选服务协议(弹窗底部 checkbox, 不勾则 Log in now 可能禁用)
        try:
            cb = page.query_selector("input[type='checkbox']")
            if cb and not cb.is_checked():
                cb.click()
                page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001
            pass
        # ③ 点 "Log in now" 切换到扫码二维码视图
        for sel in ("text=Log in now", "button:has-text('Log in now')"):
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_timeout(3000)
                    break
            except Exception:  # noqa: BLE001
                continue
        # ④ 等二维码渲染(微信 tab 默认选中; 二维码图/canvas 异步加载)
        page.wait_for_timeout(2000)

    @staticmethod
    def _open_qrcode_if_needed(page, platform: str) -> None:
        """若无二维码但存在登录入口按钮 → 代为点击, 让二维码出现。

        实测(kdocs, 2026-09-13): 打开共享链接后弹窗只有
        `Open the document with WPS` / `Sign in`, **没有二维码**;
        二维码必须点 `Sign in` 才出现。前端弹窗是静态截图, 用户点不到 →
        不代点的话用户永远只能看到「空框」。
        只在确实没有二维码时才点(腾讯文档默认就展示二维码, 不会被误触)。
        """
        if platform != "kdocs":
            return  # 当前只有 kdocs 有这一层
        if SharedDocLoginManager.qrcode_visible(page):
            return
        for sel in _KDOCS_SIGNIN_BUTTONS:
            try:
                page.click(sel, timeout=2500)
                # 等二维码就位(点击后异步加载真实二维码图)
                try:
                    page.wait_for_function(_QRCODE_PRESENT_JS, timeout=8000)
                    logger.info("[shared-doc-login] 已自动展开二维码(选择器=%s)", sel)
                except Exception:  # noqa: BLE001  按钮点了但二维码判定没命中
                    page.wait_for_timeout(2000)
                    logger.info("[shared-doc-login] 已点击登录按钮(%s), 二维码判定未命中但继续", sel)
                return
            except Exception:  # noqa: BLE001  该选择器不匹配, 试下一个
                continue
        logger.info("[shared-doc-login] 未找到登录入口按钮(kdocs 可能改了文案), 保持当前页面")

    @staticmethod
    def _ensure_agreement_checked(page) -> None:
        """#148 勾选登录卡上未选中的协议 checkbox(金山默认不勾, 不勾则扫码确认后无法回跳)。

        登录页 checkbox 通常只有「保持登录」「同意协议」; 逐个把**未选中**的勾上,
        force 跳过自定义控件的可操作性检查(真实 input 常被透明 label 覆盖)。幂等安全。
        """
        try:
            boxes = page.query_selector_all("input[type='checkbox']")
        except Exception:  # noqa: BLE001
            return
        for cb in boxes:
            try:
                if not cb.is_checked():
                    cb.check(force=True)
                    page.wait_for_timeout(200)
            except Exception:  # noqa: BLE001  该框不可勾(只读/装饰), 跳过
                continue

    def get(self, session_id: str) -> LoginSession | None:
        with self._lock:
            self._reap()
            return self._sessions.get(session_id)

    def refresh_status(self, sess: LoginSession) -> str:
        """探测当前登录状态(不落盘), 更新 sess.status 并返回。

        判定逻辑在会话专属工作线程执行(见 _browser_probe), 这里只做短路与写回。
        """
        if sess.expired:
            sess.status = "expired"
            return sess.status
        w = sess._worker
        if w is None:
            sess.status = "closed"
            return sess.status
        try:
            status, url = w.run(_browser_probe, sess.platform, timeout=30)
        except Exception:  # noqa: BLE001  页面已关闭/工作线程异常
            sess.status = "closed"
            return sess.status
        sess.status = status
        if url:
            sess.current_url = url
        return status

    def screenshot_b64(self, sess: LoginSession) -> str:
        """截取当前页面(登录二维码/登录页), 在专属工作线程执行。"""
        w = sess._worker
        if w is None:
            return ""
        try:
            return w.run(_browser_screenshot, timeout=30)
        except Exception:  # noqa: BLE001  截图失败不阻塞流程(前端仍可点确认)
            logger.warning("[shared-doc-login] 截图失败: %s", sess.session_id, exc_info=True)
            return ""

    def verify_logged_in(self, sess: LoginSession) -> tuple[bool, str]:
        """轻量校验(在专属工作线程): 重新打开目标文档, 判断是否已脱离登录页。

        用途(A-20260910-001 §五.4): 用户可能在未真正登录时点「我已扫码完成」,
        若不校验会写入无效 state 并**覆盖**原有可用登录态。
        """
        w = sess._worker
        if w is None:
            return False, "浏览器会话已关闭, 请重新发起登录"
        return w.run(_browser_verify, sess.platform, sess.doc_url, timeout=60)

    def save_state(self, sess: LoginSession) -> dict:
        """保存 storage_state(覆盖前备份旧文件), 在专属工作线程执行。"""
        w = sess._worker
        if w is None:
            raise RuntimeError("浏览器会话已关闭, 无法保存登录态")
        return w.run(_browser_save, sess.state_path, timeout=60)

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
