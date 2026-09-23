# -*- coding: utf-8 -*-
"""提示词版本管理路由契约测试（工单 #178）

背景（2026-09-23 生产 404 事故）
--------------------------------
`app/api/v1/admin_ai_prompt_config.py` 里 router、端点、service 依赖都写好了，
但**从未在 `app/api/v1/router.py` 中 import + `include_router`** → 该模块成了
「孤儿模块」，前端「提示词评估中心」的版本管理区请求

    GET /api/v1/admin/ai/prompt-eval/prompt-configs?code=gen_system

直接 404（同一前缀下 #173 评估侧的路由正常，故现象是「页面部分功能 404」）。

为什么既有测试没抓到
--------------------
`test_prompt_config_service.py` 测的是 **service 层**，只要模块没挂载，
service 再正确也不可达 —— 这类缺陷**只有路由契约测试能覆盖**。

本测试的防复发点
----------------
1. 3 条路径必须出现在 OpenAPI paths 中（= 已挂载到应用）；
2. 未认证必须是 401/403 而非 404 —— 404 意味着「路由不存在」，
   与「鉴权拦截」是完全不同的故障，必须能区分开。

刻意**不依赖 DB / 不 mock**：路由注册是静态事实，引入 DB 只会增加 flaky 与耗时。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

BASE = "/api/v1/admin/ai/prompt-eval/prompt-configs"

EXPECTED_PATHS = [
    BASE,
    f"{BASE}/{{config_id}}/activate",
    f"{BASE}/{{config_id}}/rollback",
]


def test_prompt_config_paths_are_registered():
    """#178 的 3 条路径都必须在 OpenAPI 里（防「模块写好但忘了 include_router」）"""
    paths = set(app.openapi()["paths"].keys())
    missing = [p for p in EXPECTED_PATHS if p not in paths]
    assert not missing, (
        f"提示词版本管理路由未注册（孤儿模块？）: {missing} —— "
        f"检查 app/api/v1/router.py 是否 import 并 include_router(admin_ai_prompt_config.router)"
    )


def test_prompt_config_requires_auth_not_404():
    """未认证应为 401/403；若 404 则说明路由根本没挂载（即 2026-09-23 事故形态）"""
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(f"{BASE}?code=gen_system")
    assert r.status_code != 404, (
        "路由未挂载（404）—— 检查 app/api/v1/router.py 是否 include_router(admin_ai_prompt_config.router)"
    )
    assert r.status_code in (401, 403), f"预期鉴权拦截，实际 {r.status_code}"
