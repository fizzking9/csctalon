"""本地文件存储抽象 - 审计贴图等附件存储

第一版: 内网本地目录存储(按报告/日期分目录)。
存储层抽象接口,后续文件量增大可平滑迁对象存储(替换 save/get_url 实现即可,业务代码不变)。
"""

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings

# 上传根目录(容器内 /app/uploads,宿主机 ./backend/uploads)
UPLOAD_ROOT = Path(settings.UPLOAD_DIR)
# 审计贴图子目录
AUDIT_DIR_NAME = "audit_attachments"
AUDIT_DIR = UPLOAD_ROOT / AUDIT_DIR_NAME

# 允许的图片扩展名(白名单,防上传可执行文件)
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB


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
