#!/usr/bin/env python3
"""complaint_date 回填工具 (#42, 2026-09-10) — 默认预览, --apply 才写库

## 背景
2026-09-05 的两批 workbuddy 推送(log id=195/198)落库时「投诉日期」被转置:
    库内年 = 2000 + 真值"日" ; 库内"日" = 真值年份 − 2001 ; 月份不变
共 **2532 行整批可疑**(不止年份阈值能检出的 1556+128 —— 该批次连"看似正常
(2020~now)"的行同样错: 563 行有编号内嵌日期的行, 与库内一致 = 0)。

⚠️ 成因**不在本仓库代码**: 已证明 `_to_datetime`/`_sanitize_date` 对源文件
663 个真实日期解析 661 个完全正确(另 2 个是源表年份笔误 2225 被正确纠错);
6 种候选格式 + Excel 序列号均不产生该转置。故本工具只做数据修复,
写入侧成因(上游 workbuddy 批次的 JSON)需另行溯源。

## 真值来源(两级键, 覆盖 94.9%)
1. **complaint_no 内嵌 8 位日期**(主键, 最可靠)
   如 `1440306002025030183693297` → `2025-03-01`(编号可能带 \n 前缀, 已清洗)
2. **(投诉手机号, 问题签名) → 源表 xlsx 反查**(次键, 仅当源表该键日期唯一)
   源表: `01样本数据/315投诉处理登记表新.xlsx` 的「监督局-315投诉」sheet
   2562 个键中 2530 个日期唯一

不可回填的 130 行(源表无此键/缺手机或问题)保持原值, 由校验打标留在治理队列。

## 安全性
- **默认预览**, 不写库; 写库需显式 `--apply --yes`
- 仅更新 `complaint_date`, 不触碰 dedup_hash(避免触发去重键重算)
- 幂等: 只更新"与真值不同"的行, 可重复执行
- 事务包裹 + 前后自检; 自检不通过自动回滚
- 建议先 `deploy/backup.sh` 备份

用法:
    cd backend && ./.venv/bin/python ../scripts/backfill_complaint_date.py            # 预览
    cd backend && ./.venv/bin/python ../scripts/backfill_complaint_date.py --apply --yes
    # 指定批次窗口(默认 2026-09-05 两批)
    ... --since '2026-09-05 00:00' --until '2026-09-05 23:59'
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_complaint_date")

import pandas as pd  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.external_complaint import ExternalComplaint  # noqa: E402
from app.services.upsert import _complaint_problem_signature  # noqa: E402

_UTC = timezone.utc
_TZ_CN = timezone(timedelta(hours=8))

# 默认批次窗口(#42 定位到的两批污染, 本地时间语义)
_DEF_SINCE = "2026-09-05 00:00"
_DEF_UNTIL = "2026-09-05 23:59"
_DEF_XLSX = _ROOT / "01样本数据" / "315投诉处理登记表新.xlsx"
_SHEET_KEYWORDS = ("315", "监督局")

# 编号内嵌 8 位日期(月日必须合法)
_NO8_RE = re.compile(r"(20[0-9]{2})(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])")
# 真值合理窗口(源表实证区间), 超出即弃用该真值
_MIN_TRUTH_DATE = "2023-01-01"


def _is_plausible_truth(d: str) -> bool:
    """真值是否落在业务合理窗口(源表实证 2023~现在)

    源表本身含年份笔误(如 2225-03-28 / 2225-04-12), 直接采信会把库内写成 2225。
    这类值应交给 `upsert._sanitize_date` 做 2xxx→20xx 纠错, 而非在此回填。
    """
    return _MIN_TRUTH_DATE <= d <= datetime.now().strftime("%Y-%m-%d")


def _extract_no_date(no: str | None) -> str | None:
    """从投诉编号提取内嵌日期 → 'YYYY-MM-DD'; 不可靠则 None"""
    if not no:
        return None
    s = str(no).replace("\n", "").replace("\r", "").replace(" ", "")
    hits = list(_NO8_RE.finditer(s))
    if not hits:
        return None
    m = hits[-1]  # 取最后一个(前缀可能含干扰数字)
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if not _is_plausible_truth(dt.strftime("%Y-%m-%d")):
        return None
    return dt.strftime("%Y-%m-%d")


def _load_source_map(xlsx: Path) -> dict[tuple[str, str], str]:
    """读源表 → {(手机号, 问题签名): 'YYYY-MM-DD'}(仅保留日期唯一的键)

    ⚠️ 必须用 calamine 引擎: openpyxl 对 WPS 导出文件报 `Fill() takes no arguments`
    (见 repo memory: complaint-315-export-analysis-20260910)。
    """
    if not xlsx.exists():
        logger.warning("源表不存在, 跳过第二键: %s", xlsx)
        return {}
    xl = pd.ExcelFile(str(xlsx), engine="calamine")
    sheet = next((s for s in xl.sheet_names if any(k in str(s) for k in _SHEET_KEYWORDS)),
                 xl.sheet_names[0])
    df = pd.read_excel(str(xlsx), sheet_name=sheet, dtype=str, engine="calamine")
    df.columns = [str(c).replace("\t", "").strip() for c in df.columns]
    logger.info("源表 %s [%s]: %d 行", xlsx.name, sheet, len(df))

    multi: set[tuple[str, str]] = set()
    out: dict[tuple[str, str], str] = {}
    implausible = 0
    for _, r in df.iterrows():
        ph = str(r.get("投诉手机号") or "").strip()
        prob = _complaint_problem_signature(r.get("投诉问题"))
        dt = str(r.get("投诉日期") or "")[:10]
        if not ph or not prob or len(dt) != 10:
            continue
        # ⚠️ 源表本身含年份笔误(2225-03-28 等), 直接采信会把库内写成 2225。
        #    这类值应由 upsert._sanitize_date 做 2xxx→20xx 纠错, 不在回填层处理。
        if not _is_plausible_truth(dt):
            implausible += 1
            continue
        key = (ph, prob)
        if key in out and out[key] != dt:
            multi.add(key)   # 同键多日期 → 不可作真值
            continue
        out[key] = dt
    for k in multi:
        out.pop(k, None)
    logger.info("源表可作真值的键: %d (剔除多日期冲突 %d / 窗口外 %d)",
                len(out), len(multi), implausible)
    return out


def _to_local_date(dt: datetime | None) -> str | None:
    """timestamptz → 北京日期字符串(库内按 UTC 存, 必须转北京再比)"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_TZ_CN).strftime("%Y-%m-%d")


def main() -> int:
    ap = argparse.ArgumentParser(description="complaint_date 回填(默认预览)")
    ap.add_argument("--since", default=_DEF_SINCE, help="批次起始(北京时间, YYYY-MM-DD HH:MM)")
    ap.add_argument("--until", default=_DEF_UNTIL, help="批次结束(北京时间, YYYY-MM-DD HH:MM)")
    ap.add_argument("--xlsx", type=Path, default=_DEF_XLSX, help="源表(第二键用)")
    ap.add_argument("--apply", action="store_true", help="实际写库(默认仅预览)")
    ap.add_argument("--yes", action="store_true", help="确认执行(与 --apply 同用)")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 行(调试用)")
    args = ap.parse_args()

    # 北京时间窗口 → UTC
    since = datetime.strptime(args.since, "%Y-%m-%d %H:%M").replace(tzinfo=_TZ_CN).astimezone(_UTC)
    until = datetime.strptime(args.until, "%Y-%m-%d %H:%M").replace(tzinfo=_TZ_CN).astimezone(_UTC)

    if args.apply and not args.yes:
        logger.error("--apply 需同时给出 --yes 以确认(防误操作)")
        return 2

    logger.info("批次窗口(北京时间): %s ~ %s", args.since, args.until)
    src_map = _load_source_map(args.xlsx)

    db = SessionLocal()
    try:
        rows = list(db.scalars(
            select(ExternalComplaint).where(
                ExternalComplaint.created_at >= since,
                ExternalComplaint.created_at < until,
            ).order_by(ExternalComplaint.id)
        ).all())
        if args.limit:
            rows = rows[:args.limit]
        logger.info("批次内行数: %d", len(rows))
        if not rows:
            return 0

        # ── 计算每行真值(两级键) ──
        plans: list[tuple[int, str, str, str]] = []   # (id, 当前值, 真值, 来源)
        no_truth = src_truth = missing = 0
        for r in rows:
            cur = _to_local_date(r.complaint_date)
            t = _extract_no_date(r.complaint_no)
            src = "编号"
            if t is None:
                ph = str(r.complainant_phone or "").strip()
                prob = _complaint_problem_signature(r.problem)
                t = src_map.get((ph, prob)) if (ph and prob) else None
                src = "源表(手机+问题)"
                if t is None:
                    missing += 1
                    continue
                src_truth += 1
            else:
                no_truth += 1
            if cur == t:
                continue   # 已正确, 幂等跳过
            plans.append((r.id, cur or "NULL", t, src))

        logger.info("真值来源: 编号键 %d / 源表键 %d / 无法确定 %d", no_truth, src_truth, missing)
        logger.info("待更新行数: %d", len(plans))

        print("\n=== 变更预览(前 15 行) ===")
        print(f"{'id':>8}  {'当前(北京)':<12} {'回填真值':<12} {'来源'}")
        for sid, cur, t, src in plans[:15]:
            print(f"{sid:>8}  {cur:<12} {t:<12} {src}")
        print(f"... 共 {len(plans)} 行")

        # 变更前后年份分布(让审核者可判断合理性)
        print("\n=== 变更前 年份分布 ===")
        from collections import Counter
        print(" ", Counter((c[:4] if c != "NULL" else "NULL") for _, c, _, _ in plans).most_common())
        print("=== 变更后 年份分布 ===")
        print(" ", Counter(t[:4] for _, _, t, _ in plans).most_common())

        if not args.apply:
            print("\n[预览模式] 未写库。确认无误后加 --apply --yes 执行。")
            return 0

        # ── 执行(事务 + 前后自检) ──
        before_bad = db.scalar(text(
            "SELECT count(*) FROM external_complaints "
            "WHERE created_at >= :s AND created_at < :e "
            "AND (complaint_date < '2020-01-01' OR complaint_date > now() + interval '1 day')"
        ), {"s": since, "e": until}) or 0
        logger.info("更新前 该批次可检出异常行: %d", before_bad)

        n = 0
        for sid, _cur, t, _src in plans:
            # 真值按北京 00:00 → 转 UTC 存储(与既有口径一致)
            local = datetime.strptime(t, "%Y-%m-%d").replace(tzinfo=_TZ_CN)
            db.execute(
                text("UPDATE external_complaints SET complaint_date = :d WHERE id = :i"),
                {"d": local.astimezone(_UTC), "i": sid},
            )
            n += 1
            if n % 500 == 0:
                db.flush()
                logger.info("  已更新 %d/%d", n, len(plans))
        db.flush()

        after_bad = db.scalar(text(
            "SELECT count(*) FROM external_complaints "
            "WHERE created_at >= :s AND created_at < :e "
            "AND (complaint_date < '2020-01-01' OR complaint_date > now() + interval '1 day')"
        ), {"s": since, "e": until}) or 0
        logger.info("更新后 该批次可检出异常行: %d", after_bad)

        # 自检: 更新后该类异常应显著下降(不必为 0 —— 无法确定真值的行保留原值)
        if after_bad >= before_bad:
            db.rollback()
            logger.error("自检失败(异常行未下降): before=%d after=%d, 已回滚", before_bad, after_bad)
            return 3

        db.commit()
        logger.info("✔ 回填完成: 更新 %d 行; 该批次异常行 %d → %d", n, before_bad, after_bad)
        return 0
    except Exception:
        db.rollback()
        logger.exception("回填失败, 已回滚")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
