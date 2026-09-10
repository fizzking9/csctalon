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
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

logger = logging.getLogger("fix_complaint_handle_time")

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
# 有效投诉编号(与 complaint_sync._VALID_COMPLAINT_NO_RE 同口径): 数字/字母/连字符, 长度 6~64
_NO_RE = re.compile(r"^[0-9A-Za-z\-]{6,64}$")
# 日期匹配容差(天): 源表 complaint_date 与库中存储可能相差 1 天
# (源为北京日期 0 点, 入库转 UTC 后按北京归日可能落在前一日)
_DAY_TOL = 1


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


def _valid_no(v: str | None) -> str | None:
    """投诉编号有效性: 过滤 '/'、空、纯符号等无效值(与 complaint_sync._is_valid_complaint_no 同口径)"""
    if not v:
        return None
    s = str(v).replace("\n", "").strip()
    if not s or not _NO_RE.match(s):
        return None
    return s


def _key_variants(row: dict) -> list[tuple]:
    """生成候选匹配键(多级, 依次尝试)"""
    no = _valid_no(_to_str(row.get("投诉编号")))
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
    ap.add_argument("--yes", action="store_true", help="与 --apply 搭配时跳过二次确认")
    ap.add_argument("--max-id", type=int, default=300000,
                   help="真实主批次 id 上限(排除疑似脏副本; 不同环境 id 段不同, 需按实际调整)")
    args = ap.parse_args()

    df = _load_source(args.file)
    logger.info(f"源表: {args.file} 行数={len(df)}")

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
    logger.info(f"源表可解析处理时间: {recomputed} 行; 无法解析: {unparsed} 行")
    with SessionLocal() as db:
        bad = db.scalars(
            select(ExternalComplaint)
            .where(ExternalComplaint.id < args.max_id)
            .where(ExternalComplaint.handle_time.isnot(None))
            .where(ExternalComplaint.handle_time < _BAD_BEFORE)
        ).all()
        logger.info(f"库中坏值行(< {_BAD_BEFORE}, id<{args.max_id}): {len(bad)}")

        matched, missed, ambiguous = [], [], []
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
            # 容差匹配(编号缺失/日期跨时区的行): 手机号相同 且 日期相差 ≤1 天。
            # ⚠️ 安全护栏(2026-09-10 审查): 同手机号当日可能有多条不同投诉,
            #    必须"唯一候选才回填", 多条候选宁可跳过(记入 ambiguous), 防张冠李戴。
            if new_ht is None and phone:
                cands: list[tuple[str, Any]] = []
                for (p_, cd_s, _s_), cand in src_by_pcs.items():
                    if p_ != phone or not (cd_day and cd_s):
                        continue
                    try:
                        d1 = datetime.fromisoformat(cd_day)
                        d2 = datetime.fromisoformat(cd_s)
                    except ValueError:
                        continue
                    if abs((d1 - d2).days) <= _DAY_TOL:
                        cands.append((cd_s, cand))
                if len(cands) == 1:
                    new_ht, hit_by = cands[0][1], "手机+日期±1天(唯一)"
                elif len(cands) > 1:
                    ambiguous.append((row.id, phone, [c[0] for c in cands]))
            if new_ht is not None:
                matched.append((row, new_ht, hit_by))
            else:
                missed.append(row)

        logger.info(f"可回填: {len(matched)}; 未匹配: {len(missed)}; 多候选跳过: {len(ambiguous)}")
        if matched[:5]:
            logger.info("预览(前 5 条):")
            for row, new_ht, hit_by in matched[:5]:
                logger.info(
                    f"  id={row.id} 坏值={row.handle_time:%Y-%m-%d}"
                    f" → {new_ht.astimezone().strftime('%Y-%m-%d %H:%M')} [匹配={hit_by}]"
                )
        if ambiguous:
            logger.warning(f"多候选无法确定 {len(ambiguous)} 行(保持原值, 需人工):")
            for rid, ph, days in ambiguous[:20]:
                logger.warning(f"  id={rid} 手机={ph} 候选日期={days}")
        if missed:
            # 未匹配行必须留痕可查(源表无对应/业务键缺失), 便于人工复核
            logger.warning(f"未匹配 {len(missed)} 行(源表找不到对应, 保持原值):")
            for row in missed[:20]:
                logger.warning(
                    f"  id={row.id} 编号={(row.complaint_no or '(空)')[:24]}"
                    f" 手机={row.complainant_phone or '(空)'}"
                    f" SN={row.device_sn or '(空)'}"
                    f" 登记={(row.complaint_date.date().isoformat() if row.complaint_date else '(空)')}"
                    f" 问题={(row.problem or '')[:16]}"
                )

        if not args.apply:
            logger.info("(预览模式; 加 --apply 才会写库)")
            return
        # ⚠️ 写库前二次确认(2026-09-10 审查): 避免误触 --apply 直接改数。
        if not args.yes:
            logger.warning(
                f"即将回填 {len(matched)} 行 handle_time(不可自动回滚)。"
                f"确认请重跑并加 --yes。"
            )
            return

        n = 0
        for row, new_ht, _ in matched:
            row.handle_time = new_ht
            n += 1
        db.commit()
        logger.info(f"✅ 已回填 {n} 行")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
