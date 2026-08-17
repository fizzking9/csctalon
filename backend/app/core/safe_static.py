"""受控静态文件服务: 对 /uploads 挂载做安全加固

背景: 原 main.py 用裸 StaticFiles 挂载整个 UPLOAD_DIR, 导致
  - .browser_states/ 下的 Playwright 登录 cookie 可被任意下载(会话劫持)
  - uploads/yyyy-MM/ 下含手机号/退费单/工单的真实 CSV/XLSX 可被任意下载
  - 上传的 .html 可在同源直接执行(存储型 XSS)

本子类在 StaticFiles 基础上增加:
  1. 拒绝路径穿越(`..`)与点文件/点目录(.browser_states 等)
  2. 仅放行白名单图片扩展名(csv/xlsx/html/svg 等不可静态访问)
  3. 所有响应附加 X-Content-Type-Options: nosniff, 阻止类型嗅探

注: .browser_states 已另行移至 settings.BROWSER_STATE_DIR(不在 UPLOAD_DIR 下),
此处点文件拦截为纵深防御。
"""

from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

# 允许通过 /uploads 静态访问的扩展名(仅图片)
# csv/xlsx/xls 走 /api/v1/uploads/* 受鉴权接口; html/svg 一律拒绝(防 XSS)
ALLOWED_STATIC_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class SafeStaticFiles(StaticFiles):
    """StaticFiles 安全加固子类"""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        # 1. 空路径 → 404(禁止目录列举)
        if not path:
            raise StarletteHTTPException(status_code=404)

        # 2. 路径穿越与点文件防御: 任一段为 .. 或以 . 开头即拒绝
        #    覆盖 .browser_states/xxx.json、.env、../etc/passwd 等
        segments = path.split("/")
        if any(seg == ".." or (seg and seg[0] == ".") for seg in segments):
            raise StarletteHTTPException(status_code=404)

        # 3. 扩展名白名单(仅图片) — 在文件落盘前拒绝, 不泄露文件是否存在
        ext = Path(path).suffix.lower()
        if ext not in ALLOWED_STATIC_EXT:
            raise StarletteHTTPException(status_code=404)

        # 4. 交父类读取真实文件
        response = await super().get_response(path, scope)

        # 5. 附加安全头(即便 404 也加, 防类型嗅探); 图片内联展示不强制下载
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers.setdefault("Content-Disposition", "inline")
        return response
