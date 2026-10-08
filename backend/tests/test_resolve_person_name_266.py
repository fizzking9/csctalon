# -*- coding: utf-8 -*-
"""#266: _resolve_person_name 支持 bigint person_id 直接 join persons.id"""

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql.schema import Column

from app.services.analysis_engine import _resolve_person_name

Base = declarative_base()


class FakeModel(Base):
    __tablename__ = "fake"
    id = Column(Integer, primary_key=True)
    person_id = Column(BigInteger)
    operator_name = Column(String(64))


def test_resolve_person_name_bigint_path():
    """bigint person_id 列应直接生成 persons.id = raw_col 子查询"""
    col = FakeModel.person_id
    subq = _resolve_person_name(col)
    sql = str(subq)
    assert "persons.id" in sql
    assert "person_aliases" not in sql


def test_resolve_person_name_varchar_path():
    """字符串别名列仍走 person_aliases.alias 解析"""
    col = FakeModel.operator_name
    subq = _resolve_person_name(col)
    sql = str(subq)
    assert "person_aliases" in sql
    assert "alias" in sql
