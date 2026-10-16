# -*- coding: utf-8 -*-
"""TR-5.1~5.4 E 路企业商户维表构建 (#332 T5)

夹具五类 SN(时间均为 Asia/Shanghai):
- SN-SINGLE : 单商户 M1@t1,t2 → 1 段, valid_to NULL, conflict=none
- SN-SWITCH : M1@t1,t2 → M2@t3,t4 (跨度互斥) → 2 段, 首段 valid_to=t3, conflict=none
- SN-OVARB  : M1@t1,t3 与 M2@t2 跨度相交(重叠) + 档案 merchant_name=商户甲
              → 3 段全 conflict=overlap, M1 两段 arbiter_pick=TRUE, M2 段 FALSE
- SN-OVNO   : 同上重叠但档案商户名两边都不命中 → 全段 arbiter_pick=FALSE
- 11111111  : 假 SN(全重复位) → 不产出分段, 计入 fake_sns
作业内部 commit 落测试库, SN 名固定加 OV/SW 前缀避免跨用例干扰。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.governance.dim_device_merchant_service import rebuild_dim_device_merchant

_T1 = "2026-01-01 10:00:00+08"
_T2 = "2026-02-01 10:00:00+08"
_T3 = "2026-03-01 10:00:00+08"
_T4 = "2026-04-01 10:00:00+08"


@pytest.fixture()
def seeded(db):
    """灌入 E 路夹具 + 设备档案仲裁行 + 云客猫集团映射, 并跑首次重建。"""
    # 防御性清理: 早期版本作业曾 commit 污染持久测试库, 先删自身键保证可重跑
    db.execute(text("DELETE FROM ods.refund_record_reports WHERE id BETWEEN 9601 AND 9614"))
    db.execute(text(
        "DELETE FROM devices WHERE sn IN "
        "('SN-SINGLE','SN-SWITCH','SN-OVARB','SN-OVNO','SN-YKM','11111111')"
    ))
    db.execute(text(
        "DELETE FROM dim_merchant_group WHERE merchant_id IN "
        "('1611876437','1675918225','1683343734')"
    ))
    # 设备档案须最先插: ① E 路 device_sn 有 FK → devices.sn, 所有夹具 SN 先建档;
    # ② 仲裁: OVARB 命中商户甲; OVNO 两边都不命中; 其余 NULL 不参与仲裁
    db.execute(text("""
        INSERT INTO devices (sn, merchant_name) VALUES
            ('SN-SINGLE', NULL),
            ('SN-SWITCH', NULL),
            ('SN-OVARB', '商户甲'),
            ('SN-OVNO',  '无关商户丙'),
            ('SN-YKM',   NULL),
            ('11111111', NULL)
    """))
    db.execute(text("""
        INSERT INTO ods.refund_record_reports
            (id, device_sn, merchant_id, merchant_name, refund_amount, refund_time)
        VALUES
            (9601, 'SN-SINGLE', '1001', '商户甲', 1.0, :t1),
            (9602, 'SN-SINGLE', '1001', '商户甲', 1.0, :t2),
            (9603, 'SN-SWITCH', '1001', '商户甲', 1.0, :t1),
            (9604, 'SN-SWITCH', '1001', '商户甲', 1.0, :t2),
            (9605, 'SN-SWITCH', '1002', '商户乙', 1.0, :t3),
            (9606, 'SN-SWITCH', '1002', '商户乙', 1.0, :t4),
            (9607, 'SN-OVARB',  '1001', '商户甲', 1.0, :t1),
            (9608, 'SN-OVARB',  '1002', '商户乙', 1.0, :t2),
            (9609, 'SN-OVARB',  '1001', '商户甲', 1.0, :t3),
            (9610, 'SN-OVNO',   '1001', '商户甲', 1.0, :t1),
            (9611, 'SN-OVNO',   '1002', '商户乙', 1.0, :t2),
            (9612, 'SN-OVNO',   '1001', '商户甲', 1.0, :t3),
            (9613, '11111111',  '1001', '商户甲', 1.0, :t1),
            (9614, 'SN-YKM',    '1611876437', '云客猫(深)', 1.0, :t1)
    """), {"t1": _T1, "t2": _T2, "t3": _T3, "t4": _T4})
    # 云客猫集团映射(迁移种子不进 create_all, 测试库自灌)
    db.execute(text("""
        INSERT INTO dim_merchant_group (merchant_id, merchant_name, group_name, mapping_basis)
        VALUES
            ('1611876437', '云客猫(深)', '云客猫', 'business_confirmed_20261016'),
            ('1675918225', '大秦WIFI管家', '云客猫', 'business_confirmed_20261016'),
            ('1683343734', '云客猫', '云客猫', 'business_confirmed_20261016')
    """))
    db.flush()
    stats = rebuild_dim_device_merchant(db, commit=False)
    return stats


def _rows(db, sn: str):
    return db.execute(text("""
        SELECT merchant_id, merchant_name, merchant_group,
               to_char(valid_from AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') AS vf,
               to_char(valid_to   AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI') AS vt,
               conflict_flag, arbiter_pick
        FROM dim_device_merchant WHERE device_sn = :sn
        ORDER BY valid_from
    """), {"sn": sn}).all()


# ── TR-5.1 五类夹具分段断言 ─────────────────────────────────────────────────
def test_single_merchant_one_segment(db, seeded):
    rows = _rows(db, "SN-SINGLE")
    assert len(rows) == 1
    r = rows[0]
    assert r.merchant_id == "1001" and r.merchant_group == "商户甲"  # 无映射=商户名
    assert r.vf == "01-01 10:00" and r.vt is None  # 末段 valid_to 为空
    assert r.conflict_flag == "none" and r.arbiter_pick is False


def test_mutually_exclusive_switch(db, seeded):
    rows = _rows(db, "SN-SWITCH")
    assert len(rows) == 2
    assert rows[0].merchant_id == "1001" and rows[0].vt == "03-01 10:00"  # = 下段 valid_from
    assert rows[1].merchant_id == "1002" and rows[1].vt is None
    assert all(r.conflict_flag == "none" for r in rows)


def test_overlap_arbitrable(db, seeded):
    rows = _rows(db, "SN-OVARB")
    assert len(rows) == 3
    assert [r.merchant_id for r in rows] == ["1001", "1002", "1001"]
    assert all(r.conflict_flag == "overlap" for r in rows)
    # 档案命中商户甲 → 仅 M1 两段被仲裁选中
    assert [r.arbiter_pick for r in rows] == [True, False, True]
    assert rows[0].vt == "02-01 10:00" and rows[1].vt == "03-01 10:00" and rows[2].vt is None


def test_overlap_not_arbitrable(db, seeded):
    rows = _rows(db, "SN-OVNO")
    assert len(rows) == 3
    assert all(r.conflict_flag == "overlap" for r in rows)
    assert all(r.arbiter_pick is False for r in rows)  # 无法仲裁 → 消费侧冲突兜底


def test_fake_sn_excluded(db, seeded):
    assert _rows(db, "11111111") == []
    assert seeded["fake_sns"] >= 1
    assert seeded["overlap_devices"] >= 2  # OVARB + OVNO
    assert seeded["arbiter_picks"] == 2    # 仅 OVARB 的 M1 两段


# ── TR-5.2 云客猫集团映射 ───────────────────────────────────────────────────
def test_yunkemao_group(db, seeded):
    rows = _rows(db, "SN-YKM")
    assert len(rows) == 1 and rows[0].merchant_group == "云客猫"


# ── TR-5.3 重跑幂等(内容校验和一致) ─────────────────────────────────────────
_CHECKSUM_SQL = text("""
    SELECT md5(string_agg(md5(concat_ws('|', device_sn, merchant_id, merchant_name,
        merchant_group, valid_from, valid_to, conflict_flag, arbiter_pick)),
        '' ORDER BY device_sn, valid_from))
    FROM dim_device_merchant
""")


def test_rebuild_idempotent(db, seeded):
    before = db.execute(_CHECKSUM_SQL).scalar()
    stats2 = rebuild_dim_device_merchant(db, commit=False)
    after = db.execute(_CHECKSUM_SQL).scalar()
    assert before == after
    assert stats2["segments"] == seeded["segments"]


# ── TR-5.4 as-of 查询: 切换前后两个事件时点取到不同商户 ─────────────────────
def test_asof_join_switch(db, seeded):
    def asof(t: str):
        return db.execute(text("""
            SELECT dm.merchant_id, dm.merchant_group FROM dim_device_merchant dm
            WHERE dm.device_sn = 'SN-SWITCH'
              AND dm.valid_from <= CAST(:t AS timestamptz)
              AND (dm.valid_to IS NULL OR CAST(:t AS timestamptz) < dm.valid_to)
            ORDER BY dm.arbiter_pick DESC, dm.valid_from DESC
            LIMIT 1
        """), {"t": t}).one()
    assert asof("2026-02-15 12:00:00+08").merchant_id == "1001"  # 切换前
    assert asof("2026-03-15 12:00:00+08").merchant_id == "1002"  # 切换后
    # 首段之前 → 无命中(消费侧 NULL 兜底)
    n = db.execute(text("""
        SELECT count(*) FROM dim_device_merchant dm
        WHERE dm.device_sn = 'SN-SWITCH' AND dm.valid_from <= '2025-12-31 00:00:00+08'::timestamptz
          AND (dm.valid_to IS NULL OR '2025-12-31 00:00:00+08'::timestamptz < dm.valid_to)
    """)).scalar()
    assert n == 0
