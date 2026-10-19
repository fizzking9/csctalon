# -*- coding: utf-8 -*-
"""采纳治理四列在数据查看树的展示配置(#333)

锁定:
1. 经营五路 A~E 注册条目含 adopt_state/adopt_reason/adopt_rule_ver/adopted_at
   四列, 且中文名/枚举/可筛选属性正确(经 ensure_data_sources_registered 反射落库);
2. adopt_reason enum 下发 "规则码=中文释义" 格式, 与采纳作业模块规则码全集一致
   (改规则码不改释义 → 本测试失败, 防两处漂移);
3. 采纳作业模块 ADOPT_REASON_CN 覆盖 ADOPT_STATE 相关全部规则码常量。

为什么测注册结果而不只测 FIELD_OVERRIDES 常量: #100 教训是「配置对不代表渲染对」
—— 前端完全依赖 /data-sources/{code}/fields 下发的字段元数据, 注册漏了列, 界面
就没有该列; enum 断在注册层, tooltip 就没有释义。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.data_source import DataSource, DataSourceField
from app.services.data_source_registry import ensure_data_sources_registered
from app.services.governance.refund_adoption import (
    ADOPT_REASON_CN,
    ADOPT_STATE_ENUM,
)

_ADOPT_CODES = ("refund", "wallet", "shanmao_refund", "manual_refund", "refund_record_report")
_ADOPT_FIELDS = ("adopt_state", "adopt_reason", "adopt_rule_ver", "adopted_at")


@pytest.fixture()
def registered(db: Session):
    """跑一次幂等注册, 返回 code → {field_name: DataSourceField} 映射。"""
    ensure_data_sources_registered(db)
    db.commit()
    out: dict[str, dict[str, DataSourceField]] = {}
    for code in _ADOPT_CODES:
        ds = db.scalar(select(DataSource).where(DataSource.code == code))
        fields = list(db.scalars(select(DataSourceField).where(DataSourceField.source_id == ds.id)))
        out[code] = {f.field_name: f for f in fields}
    return out


class TestAdoptFieldRegistration:
    def test_all_five_sources_have_adopt_fields(self, registered):
        """★ 五路原始表都必须注册采纳四列(漏注册 = 界面没有该列)"""
        for code, fmap in registered.items():
            missing = [f for f in _ADOPT_FIELDS if f not in fmap]
            assert not missing, f"{code} 缺采纳字段: {missing}"

    def test_adopt_state_meta(self, registered):
        """adopt_state: 三态枚举 + 可筛选(待定行可过滤的依据) + 中文名"""
        for code, fmap in registered.items():
            f = fmap["adopt_state"]
            assert f.enum_values == ADOPT_STATE_ENUM
            assert f.is_filterable is True
            assert "采纳状态" in (f.field_name_cn or "")

    def test_adopt_reason_meta(self, registered):
        """adopt_reason: enum 为 '规则码=中文释义' 格式且覆盖规则码全集; 不可筛选"""
        for code, fmap in registered.items():
            f = fmap["adopt_reason"]
            assert f.is_filterable is False
            enums = f.enum_values or []
            parsed = dict(e.split("=", 1) for e in enums)
            assert set(parsed) == set(ADOPT_REASON_CN), f"{code} 规则码释义与全集不一致"

    def test_adopt_reason_covers_all_rule_constants(self):
        """★ 规则码常量全集必须都有中文释义(新增规则码忘加释义 → 在此拦截)"""
        import re

        import app.services.governance.refund_adoption as m

        consts = {
            v for k, v in vars(m).items()
            if re.fullmatch(r"[ABCDE]_(ADOPT|EX)_[A-Z_]+", k or "")
        }
        consts.add(m.PENDING_UNKNOWN)
        assert consts <= set(ADOPT_REASON_CN), f"缺释义的规则码: {consts - set(ADOPT_REASON_CN)}"

    def test_adopt_ver_and_time_not_filterable(self, registered):
        """adopt_rule_ver/adopted_at 仅展示, 不进筛选(避免误把版本当业务条件)"""
        for code, fmap in registered.items():
            assert fmap["adopt_rule_ver"].is_filterable is False
            assert fmap["adopted_at"].is_filterable is False
