"""投诉数据采集方式开关服务

三种接入方式(主动监测/被动推送/文件导入)统一开关, 避免冗余采集与日志混淆。
- monitor 关闭 → 调度器停止轮询腾讯文档
- push 关闭   → push 接口返回 503(即使 token 已配置)
- import 恒可用(手动操作, 无开关语义)
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ingest_mode_config import IngestModeConfig

logger = logging.getLogger(__name__)

MODE_MONITOR = "monitor"
MODE_PUSH = "push"
MODE_IMPORT = "import"

# 默认模式定义(mode → 显示名)
_DEFAULT_MODES = {
    MODE_MONITOR: "主动监测",
    MODE_PUSH: "被动推送",
    MODE_IMPORT: "文件导入",
}


def ensure_ingest_modes(db: Session) -> int:
    """幂等 seed 三行采集方式开关(缺失则创建, 已存在保留用户设置)"""
    created = 0
    for mode, name in _DEFAULT_MODES.items():
        row = db.scalar(select(IngestModeConfig).where(IngestModeConfig.mode == mode))
        if row is None:
            db.add(IngestModeConfig(mode=mode, name=name, enabled=True))
            created += 1
    if created:
        db.commit()
    return created


def list_ingest_modes(db: Session) -> list[dict]:
    """列出全部采集方式开关状态"""
    rows = db.scalars(select(IngestModeConfig).order_by(IngestModeConfig.id)).all()
    existing = {r.mode: r for r in rows}
    result = []
    for mode, name in _DEFAULT_MODES.items():
        row = existing.get(mode)
        result.append(
            {
                "mode": mode,
                "name": name,
                "enabled": row.enabled if row else True,
            }
        )
    return result


def update_ingest_mode(db: Session, mode: str, enabled: bool) -> bool:
    """设置单个采集方式开关(模式不存在则忽略返回 False)"""
    if mode not in _DEFAULT_MODES:
        return False
    row = db.scalar(select(IngestModeConfig).where(IngestModeConfig.mode == mode))
    if row is None:
        row = IngestModeConfig(mode=mode, name=_DEFAULT_MODES[mode], enabled=enabled)
        db.add(row)
    else:
        row.enabled = bool(enabled)
    db.commit()
    logger.info(f"[ingest_mode] 采集方式开关: {mode} → {'开启' if enabled else '关闭'}")
    return True


def is_ingest_mode_enabled(db: Session, mode: str) -> bool:
    """快捷检查某采集方式是否开启(缺行视为开启, 兼容旧数据)"""
    row = db.scalar(select(IngestModeConfig).where(IngestModeConfig.mode == mode))
    return row.enabled if row else True
