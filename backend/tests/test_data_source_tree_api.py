# -*- coding: utf-8 -*-
"""数据查看树接口契约测试（工单 #100）

锁定 `GET /data-sources` 的**返回形态**，因为前端完全依赖它渲染树:
  · 投影项必须出现在列表里（否则投诉组点不到工单）
  · 投影项必须带 is_projection / true_body_* 标记（否则前端无法声明"真身在哪"）
  · 排序必须由服务端决定（否则分组顺序与组内顺序出现两处定义 → 漂移）
  · 投影的 records/fields 必须能正常工作（解析回真身）

为什么这些必须测接口层而不只测种子:
  #100 的教训是"配置对不代表渲染对" —— 种子里的 category 正确，但如果接口
  把投影过滤掉了、或排序被前端覆盖，使用者看到的树仍然是错的。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.main import app
from app.services.data_source_registry import CATEGORY_ORDER


@pytest.fixture()
def authed_client(db):
    """带假用户的 TestClient（复用 test_data_sources_api.py 的形态）"""
    from app.models.user import RoleEnum, User

    fake_user = User(id=1, username="tester", role=RoleEnum.AGENT.value, is_active=True)

    def _override_user():
        return fake_user

    def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _tree(client) -> list[dict]:
    resp = client.get("/api/v1/data-sources", params={"include_placeholder": "true"})
    assert resp.status_code == 200
    return resp.json()["data"]


class TestTreeShapeApi:
    """树形态: 分组齐全 + 服务端排序"""

    def test_tree_contains_all_categories(self, authed_client):
        """★ 五个分组必须都出现（且顺序与 CATEGORY_ORDER 一致）"""
        cats = []
        for item in _tree(authed_client):
            if item["category"] not in cats:
                cats.append(item["category"])
        assert cats == CATEGORY_ORDER, (
            f"接口返回的分组顺序与 CATEGORY_ORDER 不一致: {cats} vs {CATEGORY_ORDER}"
        )

    def test_sorted_within_category(self, authed_client):
        """★ 组内顺序必须由服务端给出（前端不得重排）

        验证方式: 同一分组内, 相邻项的 sort_order 必须单调不降。
        sort_order 为 None 的项（旧种子未声明）视为 999, 排在组末尾。
        """
        by_cat: dict[str, list[int]] = {}
        for item in _tree(authed_client):
            order = item.get("sort_order")
            by_cat.setdefault(item["category"], []).append(999 if order is None else order)

        for cat, orders in by_cat.items():
            assert orders == sorted(orders), (
                f"分组「{cat}」内 sort_order 非单调: {orders} —— "
                "接口排序失效, 前端会看到乱序"
            )


class TestProjectionApi:
    """投影项: 可见性 + 标记 + 可读性"""

    def test_projection_present_in_tree(self, authed_client):
        """★ 投诉组必须出现 service_tickets 投影（否则投诉工作流点不到工单）"""
        items = _tree(authed_client)
        projs = [i for i in items if i.get("is_projection")]
        assert projs, "接口未返回任何投影项 —— 投诉组下的工单入口会缺失"

        complaint_ticket = next(
            (i for i in projs if i["code"] == "complaint_ticket"), None
        )
        assert complaint_ticket is not None, "投诉工单投影（complaint_ticket）缺失"
        assert complaint_ticket["category"] == "投诉"
        assert complaint_ticket["table_name"] == "service_tickets"

    def test_projection_carries_true_body_marks(self, authed_client):
        """★ 投影必须带真身标记 —— 前端据此显示「投影」标签与真身位置

        AGENTS.md 界面纪律第 5 条: 投影必须声明真身位置，否则视为违规重复展示。
        """
        for item in _tree(authed_client):
            if not item.get("is_projection"):
                continue
            assert item["projection_of"], f"{item['code']} 缺 projection_of"
            assert item["true_body_category"], f"{item['code']} 缺 true_body_category"
            assert item["true_body_name"], f"{item['code']} 缺 true_body_name"
            assert item["readonly"] is True, f"{item['code']} 投影必须只读"
            # 投影 id 恒为 0（无数据库行），便于前端区分实体/别名
            assert item["id"] == 0, f"{item['code']} 投影不应有数据库 id"

    def test_non_projection_items_not_marked(self, authed_client):
        """反向: 真实数据源不得被误标为投影（否则会丢掉写权限判断）"""
        for item in _tree(authed_client):
            if item["code"] == "complaint_ticket":
                continue
            assert not item.get("is_projection"), (
                f"{item['code']} 被误标为投影"
            )


class TestProjectionDataAccess:
    """投影的数据访问: 必须解析回真身（否则「点了没反应」）"""

    def test_projection_fields_resolve_to_true_body(self, authed_client, db):
        """★ 投影的字段接口必须返回真身字段（投影未落库，不解析必 404）"""
        from app.services.data_source_registry import ensure_data_sources_registered

        ensure_data_sources_registered(db)

        resp = authed_client.get("/api/v1/data-sources/complaint_ticket/fields")
        assert resp.status_code == 200, (
            f"投影字段接口失败（未解析回真身?）: {resp.status_code} {resp.text[:200]}"
        )
        fields = resp.json()["data"]
        assert fields, "投影字段列表为空"
        names = {f["field_name"] for f in fields}
        # 真身 service_tickets 的关键字段必须在
        assert {"ticket_no", "service_category", "register_time"} <= names

    def test_projection_fields_equal_true_body_fields(self, authed_client, db):
        """投影与真身字段**完全一致** —— 两者共用同一份字段元数据"""
        from app.services.data_source_registry import ensure_data_sources_registered

        ensure_data_sources_registered(db)

        proj = authed_client.get("/api/v1/data-sources/complaint_ticket/fields").json()["data"]
        real = authed_client.get("/api/v1/data-sources/ticket/fields").json()["data"]
        assert [f["field_name"] for f in proj] == [f["field_name"] for f in real], (
            "投影与真身字段不一致 —— 投影机制已漂移"
        )
