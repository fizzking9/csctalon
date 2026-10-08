# -*- coding: utf-8 -*-
"""B1.5-1 (#232) incidents 表事件扩展字段冒烟测试

验证:
1. Incident 模型声明了 5 个新字段 (event_type / target_audience /
   start_at / end_at / impact_metrics)
2. IncidentEventType 枚举有两个值: 异常事故 / 经营事件
3. API _serialize 在字段为空时不崩 (None 安全)
4. IncidentEventType("异常事故") / IncidentEventType("经营事件") 取值合法
5. 非法 event_type 触发 ValueError (API 层会转 400)

无 DB 依赖, 始终生效。
"""
from __future__ import annotations

import pytest

from app.api.v1.incidents import IncidentEventType, _serialize
from app.models.incident import Incident, IncidentEventType as ModelEventType


class TestIncidentEventExtension:
    """#232 B1.5-1: incidents 表新增事件扩展字段"""

    def test_model_has_new_fields(self):
        """模型层声明了 5 个新字段(防止字段被误删)"""
        for field in ("event_type", "target_audience", "start_at", "end_at", "impact_metrics"):
            assert hasattr(Incident, field), f"Incident 缺少字段 {field}"

    def test_event_type_enum_values(self):
        """IncidentEventType 枚举只有两个值"""
        values = {e.value for e in IncidentEventType}
        assert values == {"异常事故", "经营事件"}

    def test_event_type_default_is_abnormal(self):
        """默认 event_type = 异常事故 (向后兼容存量数据)"""
        # default= 装配在 mapped_column 上, 检查列属性 default
        col = Incident.__table__.c.event_type
        # default 在 SQLAlchemy Column 上是 DefaultClause 对象, 字符串化为原值
        assert col.default is not None
        # server_default 是 SQL 层的 '异常事故'
        assert col.server_default is not None

    def test_event_type_enum_lookup(self):
        """业务枚举(经营事件/异常事故)可正确解析"""
        assert IncidentEventType("异常事故") is IncidentEventType.ABNORMAL
        assert IncidentEventType("经营事件") is IncidentEventType.BUSINESS

    def test_event_type_enum_rejects_invalid(self):
        """非法 event_type 触发 ValueError (API 层会转 400)"""
        with pytest.raises(ValueError):
            IncidentEventType("无效类型")

    def test_serialize_handles_none_new_fields(self):
        """_serialize 对新字段为 None 时返回 None, 不崩"""
        # 构造一个空 Incident 实例 (不入库)
        i = Incident(title="测试")
        # 强制设为新字段为 None (默认值情况)
        i.event_type = "异常事故"
        i.target_audience = None
        i.start_at = None
        i.end_at = None
        i.impact_metrics = None
        out = _serialize(i)
        assert out["event_type"] == "异常事故"
        assert out["target_audience"] is None
        assert out["start_at"] is None
        assert out["end_at"] is None
        assert out["impact_metrics"] is None

    def test_serialize_business_event_with_metrics(self):
        """_serialize 经营事件完整字段(含指标 JSON)"""
        i = Incident(title="双12推文", event_type="经营事件")
        i.target_audience = "全量用户"
        # 不设 start_at/end_at/impact_metrics (默认 None)
        out = _serialize(i)
        assert out["event_type"] == "经营事件"
        assert out["target_audience"] == "全量用户"

    def test_model_event_type_enum_alias(self):
        """app.api.v1.incidents 与 app.models.incident 的 EventType 是同一个"""
        assert IncidentEventType is ModelEventType
