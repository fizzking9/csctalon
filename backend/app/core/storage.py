"""本地文件存储抽象 - 审计贴图等附件存储

第一版: 内网本地目录存储(按报告/日期分目录)。
存储层抽象接口,后续文件量增大可平滑迁对象存储(替换 save/get_url 实现即可,业务代码不变)。
"""

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import settings

# 上传根目录(容器内 /app/uploads,宿主机 ./backend/uploads)
UPLOAD_ROOT = Path(settings.UPLOAD_DIR)
# 审计贴图子目录
AUDIT_DIR_NAME = "audit_attachments"
AUDIT_DIR = UPLOAD_ROOT / AUDIT_DIR_NAME

# 允许的图片扩展名(白名单,防上传可执行文件)
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

# 图片 magic bytes(防改扩展名伪装, 如 .html 改名 .png 上传)
_IMAGE_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),  # RIFF 头, WEBP 标识在偏移 8
    ".bmp": (b"BM",),
}


def validate_image_content(content: bytes, ext: str) -> None:
    """校验图片内容 magic bytes 与扩展名一致(P0-3 纵深防御)。

    webp 额外校验偏移 8-12 为 WEBP。不一致抛 ValueError, 由调用方转 400。
    """
    mags = _IMAGE_MAGIC.get(ext.lower())
    if not mags:
        return  # 未登记扩展名不做 magic 校验(白名单已在调用方做)
    if not content.startswith(mags):
        raise ValueError(f"{ext} 文件内容与扩展名不匹配(magic bytes 校验失败)")
    if ext.lower() == ".webp" and content[8:12] != b"WEBP":
        raise ValueError("webp 文件内容与扩展名不匹配(magic bytes 校验失败)")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_audit_attachment(file: UploadFile, report_id: int | None = None) -> tuple[str, str]:
    """保存审计贴图,返回 (相对路径, 访问URL)。

    存储结构: audit_attachments/{report_id 或 日期}/{时间戳}_{安全文件名}
    路径穿越防护: 仅取文件名 basename。
    """
    _ensure_dir(AUDIT_DIR)
    # 分子目录: 优先按报告分组,无报告则按日期
    sub = str(report_id) if report_id else datetime.now().strftime("%Y%m%d")
    sub_dir = AUDIT_DIR / sub
    _ensure_dir(sub_dir)

    # 安全文件名 + 时间戳防重名
    safe_name = Path(file.filename or "attachment").name
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    filename = f"{ts}_{safe_name}"
    dst = sub_dir / filename

    with dst.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    rel = f"{AUDIT_DIR_NAME}/{sub}/{filename}"
    url = f"/uploads/{rel}"
    return rel, url


def attachment_url(rel_path: str) -> str:
    """相对路径转访问URL"""
    return f"/uploads/{rel_path}"


def resolve_upload_path(stored_path: str | None, filename: str | None = None) -> Path:
    """安全解析上传文件绝对路径, 防路径穿越(P0-4)。

    优先用 stored_path, 缺失则回退到 filename 的 basename。
    解析后路径必须仍在 UPLOAD_DIR 内, 否则拒绝(400)。
    调用方仍需自行判断 .exists() 返回 404。
    """
    base = Path(settings.UPLOAD_DIR).resolve()
    if stored_path:
        candidate = (base / stored_path).resolve()
    elif filename:
        candidate = (base / Path(filename).name).resolve()
    else:
        raise HTTPException(status_code=400, detail="必须提供 stored_path 或 filename")
    if not candidate.is_relative_to(base):
        raise HTTPException(status_code=400, detail="非法文件路径")
    return candidate
