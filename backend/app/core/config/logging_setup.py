# -*- coding: utf-8 -*-
"""日志初始化 —— 由运行模式 Profile 决定级别与格式。

## 改造前的问题

`main.py` 里硬编码：

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

即**所有环境同一套日志**：
- dev 看不到 DEBUG（排障时最需要的信息被过滤掉）
- prod 用人类可读格式（不利于日志采集/告警系统解析）

## 改造后

| 模式 | 级别 | 格式 | 理由 |
|---|---|---|---|
| dev  | DEBUG   | detailed（含文件:行号） | 本地排障需要最全信息与**代码位置** |
| test | WARNING | plain                  | 压噪音，让失败信息突出 |
| prod | INFO    | json                   | 结构化，便于 Filebeat/Loki 等采集与告警 |

## 为什么 JSON 只用于 prod

JSON 日志在本地**极难阅读**（一行长串转义）。它只在有采集系统时才体现价值，
故仅在 prod 启用。dev/test 保持人类可读。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .profiles import Profile

# 人类可读格式（dev 带代码位置，test 不带以便压行宽）
_FORMAT_DETAILED = (
    "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
)
_FORMAT_PLAIN = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class _JsonFormatter(logging.Formatter):
    """结构化日志（prod）。

    ★ 为什么不用第三方库（如 python-json-logger）：
      本需求用 ~20 行即可满足，不引入新依赖（AGENTS.md「工具使用」第 4 条：
      不擅自引入新工具链）。

    ⚠️ 必须保留 `exc_info`：500 堆栈是排障的核心，结构化不能丢异常信息。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 代码位置：便于从日志直接定位到源文件
        payload["src"] = f"{record.filename}:{record.lineno}"
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # 透传 extra 中的自定义字段（约定：以 ctx_ 开头的键）
        for k, v in record.__dict__.items():
            if k.startswith("ctx_"):
                payload[k[4:]] = v
        return json.dumps(payload, ensure_ascii=False)


def _make_formatter(profile: Profile) -> logging.Formatter:
    if profile.log_format == "json":
        return _JsonFormatter()
    if profile.log_format == "detailed":
        return logging.Formatter(_FORMAT_DETAILED)
    return logging.Formatter(_FORMAT_PLAIN)


def setup_logging(profile: Profile, *, log_dir: str | None = None) -> None:
    """按 profile 配置根 logger。

    参数:
      profile: 运行模式 profile（决定级别与格式）
      log_dir: 可选，持久化日志目录（None = 不写文件）

    ⚠️ 与 `main.py` 原逻辑的差异：原实现无条件 `level=logging.INFO`，
       且把文件 handler 硬编码为 INFO + detailed 格式。现统一由 profile 决定。
    """
    formatter = _make_formatter(profile)
    level = getattr(logging, profile.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    # ★ 先清空既有 handler：避免重复初始化导致日志翻倍
    #   （uvicorn --reload / 多次导入 main 时会触发；原实现有此隐患）
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    root.setLevel(level)

    if log_dir:
        try:
            from logging.handlers import RotatingFileHandler
            from pathlib import Path

            Path(log_dir).mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                f"{log_dir}/app.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setFormatter(formatter)
            root.addHandler(fh)
        except Exception:
            # 目录不可写不影响启动（与原实现同策略）
            pass

    # 第三方库降噪：这些库在 DEBUG 级别会淹没业务日志
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "playwright"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
