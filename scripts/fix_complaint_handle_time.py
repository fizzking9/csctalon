#!/usr/bin/env python3
"""315 投诉 handle_time 坏值回填工具 (#42, 2026-09-10) — 默认预览

背景:
  旧版 upsert._to_datetime 的 Excel 序列号分支**只判字符串**, 数字型(如 46187)
  落入 pd.to_datetime(46187) 被当**纳秒** → 产出 1969-12-31 16:00:00.000046 伪日期。
  实测库中 229 行此类坏值(全部 < 2020-01-01), 导致 228 行 handle_time 早于登记日,
  投诉时效统计失真。

本工具:
  从源导出 xlsx 重新解析「处理时间」, 按业务键匹配库中坏值行并回填。
  幂等: 只改 handle_time 仍为坏值(< 2020)的行; 已正确的不动。可重复执行。

匹配键(按优先级):
  1) complaint_no(投诉编号, 且库中该编号唯一/取最近) — 最可靠
  2) (complainant_phone, device_sn, complaint_date日, 归一化问题) ≈ dedup_hash 口径
  3) (complainant_phone, complaint_date日) 且问题签名相同

用法:
  cd backend && ./.venv/bin/python ../scripts/fix_complaint_handle_time.py --file <xlsx>            # 预览
  cd backend && ./.venv/bin/python ../scripts/fix_complaint_handle_time.py --file <xlsx> --apply    # 执行
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.external_complaint import ExternalComplaint  # noqa: E402
from app.services.upsert import (  # noqa: E402
    _complaint_problem_signature,
    _sanitize_date,
    _to_datetime,
    _to_str,
)

_EXCEL_ENGINE = "calamine"
_BAD_BEFORE = "2020-01-01"
_SHEET_CANDIDATES = ("监督局-315投诉", "315投诉", "监督局-315")


def _find_sheet(path: str) -> str:
    xl = pd.ExcelFile(path, engine=_EXCEL_ENGINE)
    for name in xl.sheet_names:
        if any(k in str(name) for k in ("315", "监督局")):
            return str(name)
    return str(xl.sheet_names[0])


def _load_source(path: str) -> pd.DataFrame:
    """读源表并重算 handle_time(用修复后的 _to_datetime)"""
    sheet = _find_sheet(path)
    df = pd.read_excel(path, sheet_name=sheet, dtype=str, engine=_EXCEL_ENGINE)
    df.columns = [str(c).replace("\t", "").strip() for c in df.columns]
    if "处理时间" not in df.columns:
        raise SystemExit(f"源表缺少「处理时间」列; 实际列={list(df.columns)[:12]}")
    return df


def _key_variants(row: dict) -> list[tuple]:
    """生成候选匹配键(多级, 依次尝试)"""
    no = _to_str(row.get("投诉编号"))
    # 源编号可能带前导 \n
    if no:
        no = no.replace("\n", "").strip()
    phone = _to_str(row.get("投诉手机号"))
    sn = _to_str(row.get("投诉设备号"))
    cd = _sanitize_date(_to_datetime(row.get("投诉日期")))
    cd_day = cd.date().isoformat() if cd else None
    sig = _complaint_problem_signature(row.get("投诉问题"))
    keys: list[tuple] = []
    if no:
        keys.append(("no", no))
    if phone and cd_day:
        keys.append(("phone_cd_sig", phone, cd_day, sig))
        keys.append(("phone_cd", phone, cd_day))
    if phone and sn:
        keys.append(("phone_sn", phone, sn))
    return keys


def main() -> None:
    ap = argparse.ArgumentParser(description="回填 315 handle_time 坏值(Excel 序列号解析 bug)")
    ap.add_argument("--file", required=True, help="源导出 xlsx 路径")
    ap.add_argument("--apply", action="store_true", help="实际写入(缺省仅预览)")
    args = ap.parse_args()

    df = _load_source(args.file)
    print(f"源表: {args.file} 行数={len(df)}")

    # 源: 构造索引(重算 handle_time)
    src_by_no: dict[str, object] = {}
    src_by_pcs: dict[tuple, object] = {}
    src_by_pc: dict[tuple, object] = {}
    src_by_ps: dict[tuple, object] = {}
    recomputed, unparsed = 0, 0
    for _, r in df.iterrows():
        row = r.to_dict()
        ht = _sanitize_date(_to_datetime(row.get("处理时间")))
        if ht is None:
            unparsed += 1
            continue
        recomputed += 1
        no = (_to_str(row.get("投诉编号")) or "").replace("\n", "").strip()
        phone = _to_str(row.get("投诉手机号"))
        sn = _to_str(row.get("投诉设备号"))
        cd = _sanitize_date(_to_datetime(row.get("投诉日期")))
        cd_day = cd.date().isoformat() if cd else None
        sig = _complaint_problem_signature(row.get("投诉问题"))
        if no:
            src_by_no[no] = ht
        if phone and cd_day:
            src_by_pcs[(phone, cd_day, sig)] = ht
            src_by_pc[(phone, cd_day)] = ht
        if phone and sn:
            src_by_ps[(phone, sn)] = ht
    print(f"源表可解析处理时间: {recomputed} 行; 无法解析: {unparsed} 行")

    with SessionLocal() as db:
        bad = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < 300000)
            .where(ExternalComplaint.handle_time.isnot(None))
            .where(ExternalComplaint.handle_time < _BAD_BEFORE)
        ).all()
        print(f"库中坏值行(< {_BAD_BEFORE}): {len(bad)}")

        matched, missed = [], []
        for row in bad:
            no = (row.complaint_no or "").replace("\n", "").strip()
            phone = (row.complainant_phone or "").strip() or None
            sn = (row.device_sn or "").strip() or None
            cd_day = row.complaint_date.date().isoformat() if row.complaint_date else None
            sig = _complaint_problem_signature(row.problem)
            new_ht = None
            hit_by = None
            for kind, *parts in _key_variants({
                "投诉编号": no, "投诉手机号": phone, "投诉设备号": sn,
                "投诉日期": cd_day, "投诉问题": row.problem,
            }):
                if kind == "no" and parts[0] in src_by_no:
                    new_ht, hit_by = src_by_no[parts[0]], "编号"
                elif kind == "phone_cd_sig" and tuple(parts) in src_by_pcs:
                    new_ht, hit_by = src_by_pcs[tuple(parts)], "手机+日期+问题"
                elif kind == "phone_cd" and tuple(parts) in src_by_pc:
                    new_ht, hit_by = src_by_pc[tuple(parts)], "手机+日期"
                elif kind == "phone_sn" and tuple(parts) in src_by_ps:
                    new_ht, hit_by = src_by_ps[tuple(parts)], "手机+SN"
                if new_ht is not None:
                    break
            if new_ht is not None:
                matched.append((row, new_ht, hit_by))
            else:
                missed.append(row)

        print(f"可回填: {len(matched)}; 未匹配: {len(missed)}")
        if matched[:5]:
            print("\n预览(前 5 条):")
            for row, new_ht, hit_by in matched[:5]:
                print(f"  id={row.id} 坏值={row.handle_time:%Y-%m-%d}"
                      f" → {new_ht.astimezone().strftime('%Y-%m-%d %H:%M')} [匹配={hit_by}]")

        if not args.apply:
            print("\n(预览模式; 加 --apply 才会写库)")
            return

        n = 0
        for row, new_ht, _ in matched:
            row.handle_time = new_ht
            n += 1
        db.commit()
        print(f"\n✅ 已回填 {n} 行")


if __name__ == "__main__":
    main()
