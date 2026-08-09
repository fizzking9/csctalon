"""操作日志服务 - 动作派生 + 显式记录

- derive_action(method, path): 从请求方法与路径派生 (action, target_type, target_id)
- log_operation(...): 显式写入一条操作日志(供 auth 登录等无 JWT 事件使用)
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.operation_log import OperationLog

# 资源 → 中文标签
RESOURCE_LABELS: dict[str, str] = {
    "auth": "认证",
    "strategy": "策略",
    "tasks": "专项",
    "todos": "待办",
    "users": "用户",
    "uploads": "数据",
    "data-sources": "数据源",
    "persons": "人员",
    "devices": "设备",
    "analysis-views": "视图",
    "classification-rules": "分类规则",
    "match": "关联",
    "incidents": "异常事件",
    "measures": "管理措施",
    "policies": "管理制度",
    "dept-info-syncs": "部门同步",
}

# 资源 → 操作对象类型(用于 target_type 字段)
RESOURCE_TARGET_TYPE: dict[str, str] = {
    "auth": "auth",
    "strategy": "strategy_doc",
    "tasks": "task",
    "todos": "todo",
    "users": "user",
    "uploads": "upload",
    "data-sources": "data_source",
    "persons": "person",
    "devices": "device",
    "analysis-views": "analysis_view",
    "classification-rules": "classification_rule",
    "match": "match",
    "incidents": "incident",
    "measures": "measure",
    "policies": "policy",
    "dept-info-syncs": "dept_info_sync",
}

# 子动作后缀 → 中文
SUB_ACTIONS: dict[str, str] = {
    "publish": "发布",
    "new-version": "新建版本",
    "read": "标记已读",
    "receipts": "已读情况",
    "followups": "跟进",
    "execute": "执行",
    "preview-diff": "预览差异",
    "login": "登录",
    "login-json": "登录",
    "logout": "登出",
    "me": "查看自己",
    "reset-password": "重置密码",
    "fields": "字段",
    "convert": "转预期事项",
    "review": "审核",
    "approve": "批准",
    "reject": "驳回",
    "archive": "归档",
    "adjust": "调整",
    "register": "登记",
    "classify": "分类",
    "timeline": "审计时序",
    "review-note": "复盘批注",
}

# 方法 → 默认动作(资源级 CRUD)
METHOD_DEFAULTS: dict[str, str] = {
    "POST": "创建",
    "PUT": "更新",
    "PATCH": "更新",
    "DELETE": "删除",
}

# 不记录的路径前缀(只读/系统/登录由处理器显式记录)
SKIP_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth/login",  # 登录由 auth 处理器显式记录(含用户名)
    "/health",
    "/docs",
    "/openapi",
    "/redoc",
)


def derive_action(method: str, path: str) -> tuple[str, str | None, str | None]:
    """从 (method, path) 派生 (action_label, target_type, target_id)

    例:
      POST   /api/v1/strategy              -> ("策略.创建",   "strategy_doc", None)
      POST   /api/v1/strategy/12/publish   -> ("策略.发布",   "strategy_doc", "12")
      DELETE /api/v1/tasks/5               -> ("专项.删除",   "task",         "5")
      POST   /api/v1/tasks/5/followups     -> ("专项.跟进",   "task",         "5")
    """
    p = path.split("?", 1)[0].replace("/api/v1", "").strip("/")
    parts = [x for x in p.split("/") if x]
    if not parts:
        return (f"{method}", None, None)

    resource = parts[0]
    resource_label = RESOURCE_LABELS.get(resource, resource)
    target_type = RESOURCE_TARGET_TYPE.get(resource)

    # 第一个数字段作为 target_id
    target_id = next((x for x in parts if x.isdigit()), None)

    # 子动作:资源段之后第一个非数字段(从后往前找,跳过纯数字)
    sub = None
    for x in reversed(parts[1:]):
        if not x.isdigit():
            sub = x
            break

    if sub and sub in SUB_ACTIONS:
        action = f"{resource_label}.{SUB_ACTIONS[sub]}"
    elif sub:
        # 未知子动作,用资源+方法
        action = f"{resource_label}.{METHOD_DEFAULTS.get(method, method)}"
    else:
        # 资源级 CRUD
        action = f"{resource_label}.{METHOD_DEFAULTS.get(method, method)}"

    return (action, target_type, target_id)


def log_operation(
    db: Session,
    *,
    user_id: int | None = None,
    username: str | None = None,
    method: str,
    path: str,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    status_code: int | None = None,
    detail: Any = None,
    ip: str | None = None,
) -> None:
    """显式写入一条操作日志(异常被吞掉,绝不影响主业务)"""
    try:
        if action is None or target_type is None or target_id is None:
            a, tt, tid = derive_action(method, path)
            action = action or a
            target_type = target_type or tt
            target_id = target_id or tid
        detail_text: str | None = None
        if detail is not None:
            detail_text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
        log = OperationLog(
            user_id=user_id,
            username=username,
            method=method,
            path=path,
            action=action,
            target_type=target_type,
            target_id=target_id,
            status_code=status_code,
            detail=detail_text,
            ip=ip,
        )
        db.add(log)
        db.commit()
    except Exception:
        # 审计日志失败不应阻断业务
        db.rollback()


def should_log(method: str, path: str) -> bool:
    """是否需要记录该请求(只记录变更请求,跳过只读与系统路径)"""
    if method.upper() not in ("POST", "PUT", "PATCH", "DELETE"):
        return False
    p = path.split("?", 1)[0]
    if any(p.startswith(pre) for pre in SKIP_PATH_PREFIXES):
        return False
    return True
