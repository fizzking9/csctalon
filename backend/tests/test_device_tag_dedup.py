# -*- coding: utf-8 -*-
"""device_tag 同批 tag_name 去重回归测试(#38)

背景(#38): upsert_device_tags 用 tag_name 做 ON CONFLICT DO UPDATE 冲突键,
若同批 df 含重复 tag_name(如从打标明细反推/手填字典), 单条 INSERT 遇同批重复
冲突键会抛 CardinalityViolation: cannot affect row a second time。
修复: 构造 rows 后按 tag_name 去重(保留末条, 与 on_conflict do_update 语义一致)。

覆盖:
1. 同批重复 tag_name → 不再抛错, 只保留 1 行(末条值生效)
2. 正常无重复 → 行为不变
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session


def _tag_prefix() -> str:
    return f"__t38_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def _test_session(_engine):
    """独立 session(非 db fixture 显式事务), 让 run_upsert 的 commit 生效。"""
    engine = _engine
    session = Session(engine, autocommit=False, autoflush=False)
    from app.models.device import DeviceTag

    # teardown 清理本文件建的测试标签(前缀 __t38_)
    try:
        yield session
    finally:
        try:
            tags = session.execute(
                select(DeviceTag).where(DeviceTag.tag_name.like("__t38_%"))
            ).scalars().all()
            for t in tags:
                session.delete(t)
            session.commit()
        except Exception:
            session.rollback()
        session.close()


def _make_df(tag_name: str, tag_type: str, rows: int = 1):
    """构造 device_tag 模板 df: 同一 tag_name 出现 rows 行(模拟明细反推重复)。"""
    import pandas as pd

    return pd.DataFrame([
        {"标签名称": tag_name, "标签类型": tag_type, "颜色": f"#{i}"}
        for i in range(rows)
    ])


def test_upsert_device_tag_same_name_dedup_no_crash(_test_session):
    """同批同一 tag_name 多行 → 不抛 CardinalityViolation, 只保留 1 行(末条)。"""
    from app.models.device import DeviceTag
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    tag_name = _tag_prefix()
    # 同一 tag_name 出现 3 行(标签字典手动/明细反推常见重复)
    df = _make_df(tag_name, "自定义", rows=3)
    mapping = suggest_mapping("device_tag", list(df.columns))
    result = run_upsert(df, "device_tag", mapping, _test_session)

    assert result.failed == 0, f"不应有失败(去重后无同批冲突): errors={result.errors}"
    # 库里该 tag_name 只应有 1 行(去重保留末条)
    rows = _test_session.execute(
        select(DeviceTag).where(DeviceTag.tag_name == tag_name)
    ).scalars().all()
    assert len(rows) == 1, f"同批重复 tag_name 应去重为 1 行, 实际 {len(rows)}"
    assert rows[0].tag_type == "custom"  # 中文"自定义"归一为英文 custom


def test_upsert_device_tag_no_dup_normal(_test_session):
    """无重复 tag_name → 正常插入, 行为不变。"""
    from app.models.device import DeviceTag
    from app.services.data_parser import suggest_mapping
    from app.services.upsert import run_upsert

    t1, t2 = _tag_prefix(), _tag_prefix()
    import pandas as pd
    df = pd.DataFrame([
        {"标签名称": t1, "标签类型": "预设"},
        {"标签名称": t2, "标签类型": "自定义"},
    ])
    mapping = suggest_mapping("device_tag", list(df.columns))
    result = run_upsert(df, "device_tag", mapping, _test_session)

    assert result.failed == 0
    cnt = _test_session.execute(
        select(DeviceTag).where(DeviceTag.tag_name.in_([t1, t2]))
    ).scalars().all()
    assert len(cnt) == 2
