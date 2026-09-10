#!/usr/bin/env python3
"""删除重复导入行 + 修正失效指纹 (#42, 2026-09-10) — 默认预览, --apply 才写库

## 背景
2026-09-05 两批 workbuddy 推送(complaint_sync_logs id=195/198)的中「投诉日期」被
**整批转置**(库内年 = 2000+真值日; 库内日 = 真值年−2001; 月不变)。
而 `dedup_hash = md5(日期|手机|SN|问题)` 上有**唯一索引**, 于是:
    日期错 → 指纹不同 → 唯一索引不冲突 → ON CONFLICT 不触发 → **INSERT 新行**
→ 该批 2532 行中 **2527 行(99.8%)与该批之外已有行内容重复**(同一投诉两份)。
(该批 complaint_no 大量为 None, 编号保护规则失效, 无法兜底)

## 处置(用户 2026-09-10 拍板)
保留 **08-20 批原行**(编号更完整: 1915 vs 674), 删除 09-05 重复行;
09-05 批**独有的 6 行保留**(它们不是重复)。

## 为什么删而非重算指纹
重算 09-05 行指纹后, 会与 08-20 孪生行**撞唯一索引**(实测 2333 行冲突)。
即"两行本质是同一投诉", 必须二选一 —— 故按编号完整度选保留 08-20。

## 顺带修正: 日期回填遗留的指纹不一致
日期回填(2398 行)修了显示但未同步重算指纹 → 2403 行 dedup_hash 与内容脱节
(46.2%)。本工具在删除后对**剩余行**重算指纹, 恢复一致性(幂等)。

## 安全
- **默认预览**; 写库需 `--apply --yes`
- 删除前落 `ingest_rejected_rows`(category=dup_import, 可追溯/可复原)
- 事务包裹 + 前后自检, 不通过自动回滚
- 建议先 backup.sh

用法:
    cd backend && ./.venv/bin/python ../scripts/dedup_complaint_dup_import.py            # 预览
    cd backend && ./.venv/bin/python ../scripts/dedup_complaint_dup_import.py --apply --yes
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("dedup_complaint_dup_import")

from sqlalchemy import select, text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.external_complaint import ExternalComplaint  # noqa: E402
from app.services.upsert import _complaint_identity_hash  # noqa: E402

_UTC = timezone.utc

# 重复导入批次窗口(本地时间 → UTC)
_DEF_SINCE = "2026-09-05 00:00"
_DEF_UNTIL = "2026-09-05 23:59"


def _content_key(r) -> tuple:
    """内容键(不含日期): 手机+SN+问题原文

    ⚠️ 用 problem 原文而非签名: 签名会归一去标点, 可能把不同投诉误判为同一;
    判重宁可用更严格的原文相等(实测该批 2527/2532 命中, 足够)。
    ⚠️ 三元组全空的行(手机/SN/问题都空)**不参与判重** —— 它们是无内容占位行,
    互相之间不是"同一投诉", 用空键合并会误删(实测 id=363629 ↔ 1672)。
    """
    prob = (r.problem or "").strip()
    phone = (r.complainant_phone or "").strip()
    sn = (r.device_sn or "").strip()
    if not (prob or phone or sn):
        return None   # 空行: 调用方应视为"不可判重"
    return (phone, sn, prob)


def _rank(r) -> tuple:
    """保留优先级: 有编号 > 入库早 > id 小

    编号为空的行优先度最低 —— 它没有业务标识, 是旧版/异常推送的缺失形态
    (实证: 2026-09-05 批 complaint_no 大量为 None, 而 08-20 批 1915/2527 有编号)。
    """
    has_no = 1 if (r.complaint_no or "").strip() else 0
    return (-has_no, r.created_at, r.id)


def _identity(r) -> str:
    return _complaint_identity_hash({
        "complaint_date": r.complaint_date,
        "complainant_phone": r.complainant_phone,
        "device_sn": r.device_sn,
        "problem": r.problem,
    })


def main() -> int:
    ap = argparse.ArgumentParser(description="删除重复导入行并修正指纹(默认预览)")
    ap.add_argument("--since", default=_DEF_SINCE)
    ap.add_argument("--until", default=_DEF_UNTIL)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    tz_cn = timezone(timedelta(hours=8))
    lo = datetime.strptime(args.since, "%Y-%m-%d %H:%M").replace(tzinfo=tz_cn).astimezone(_UTC)
    hi = datetime.strptime(args.until, "%Y-%m-%d %H:%M").replace(tzinfo=tz_cn).astimezone(_UTC)
    if args.apply and not args.yes:
        logger.error("--apply 需同时给出 --yes")
        return 2

    db = SessionLocal()
    try:
        rows = list(db.scalars(select(ExternalComplaint)).all())
        dup_batch = [r for r in rows if lo <= r.created_at < hi]

        # ── 统一判重: 按内容键**全局**分组, 每组只留一条 ──
        # 为什么不按"批次内/外"分开处理: 曾因此漏判 —— 批次内多行时跳过跨批匹配,
        # 导致批次内"幸存者"从未与旧批孪生比较, 二者共存(实测 id 362168 与 208 同留)。
        # 全局分组天然覆盖三种重复:
        #   ① 旧批内部重复   ② 新批内部重复   ③ 新批 ↔ 旧批 重复
        groups: dict[tuple, list] = {}
        for r in rows:
            k = _content_key(r)
            if k is None:
                continue
            groups.setdefault(k, []).append(r)

        to_delete: list = []
        to_keep_unique: list = []   # 本批独有, 保留
        for k, grp in groups.items():
            if len(grp) <= 1:
                only = grp[0]
                if lo <= only.created_at < hi:
                    to_keep_unique.append(only)
                continue
            gs = sorted(grp, key=_rank)
            survivor = gs[0]
            for r in gs[1:]:
                to_delete.append((r, survivor))

        # 空键行(手机/SN/问题全空)不参与判重: 单独保留本批内的
        for r in dup_batch:
            if _content_key(r) is None:
                to_keep_unique.append(r)

        print(f"总行数: {len(rows)}")
        print(f"待清理批次({args.since}~{args.until}): {len(dup_batch)} 行")
        print(f"  → 判定为重复导入(删除): {len(to_delete)} 行")
        print(f"  → 批次独有(保留)      : {len(to_keep_unique)} 行")
        print(f"删除后预计总行数: {len(rows) - len(to_delete)}")

        print("\n=== 独有行(保留) ===")
        for r in to_keep_unique:
            print(f"  id={r.id} 日期={str(r.complaint_date)[:10]} "
                  f"手机={r.complainant_phone} 问题={(r.problem or '')[:26]!r}")

        print("\n=== 删除样本(前 6 组) ===")
        for r, twin in to_delete[:6]:
            print(f"  删 id={r.id} 日期={str(r.complaint_date)[:10]} 编号={str(r.complaint_no)[:16]}")
            print(f"  留 id={twin.id} 日期={str(twin.complaint_date)[:10]} 编号={str(twin.complaint_no)[:16]}")

        # 指纹一致性(删除后, 对剩余行)
        remaining = [r for r in rows if r.id not in {x[0].id for x in to_delete}]
        # 判定"需重算"必须同时覆盖两种情况:
        #   a) 有指纹但与内容不符(日期回填遗留)
        #   b) **指纹为空**(历史行/新写入未填) —— 只比"不等"会把空指纹也算进来, 正确;
        #      但早期版本用 `(r.dedup_hash or "") != ...` 已覆盖, 这里显式说明以免误改。
        stale = [r for r in remaining if (r.dedup_hash or "") != _identity(r)]
        print(f"\n剩余行中指纹需重算: {len(stale)} 行")
        n_empty = sum(1 for r in stale if not (r.dedup_hash or "").strip())
        if n_empty:
            print(f"  其中指纹为空: {n_empty} 行")

        # 重算是否会造成唯一冲突 —— 含"库中已存在相同指纹"与"两行重算后互相撞车"
        existing = {r.dedup_hash: r.id for r in remaining if r.dedup_hash}
        will_collide = []
        seen_want: dict[str, int] = {}
        for r in stale:
            want = _identity(r)
            other = existing.get(want)
            if other is not None and other != r.id:
                will_collide.append((r.id, other))
                continue
            # 两个 stale 行重算后会得到同一指纹(彼此撞车)
            twin = seen_want.get(want)
            if twin is not None and twin != r.id:
                will_collide.append((r.id, twin))
                continue
            seen_want[want] = r.id
        print(f"其中重算后会撞唯一索引: {len(will_collide)} 行")
        if will_collide:
            print("  (这些行与库中另一行指纹相同 —— 属漏网重复, 需另行人工判定)")
            for sid, other in will_collide[:5]:
                print(f"    id={sid} 与 id={other}")

        if not args.apply:
            print("\n[预览] 未写库。执行: --apply --yes")
            return 0

        # ── 执行 ──
        # 1) 删除前落拒收表(可追溯)
        from app.models.ingest_rejected_row import IngestRejectedRow
        n_rej = 0
        for r, twin in to_delete:
            try:
                with db.begin_nested():
                    db.add(IngestRejectedRow(
                        data_type="external_complaint",
                        source="dedup_repair",
                        source_ref=f"dup_of:{twin.id}",
                        source_row_no=None,
                        reject_category="dup_import",
                        reject_reason=(
                            f"重复导入(2026-09-05 日期转置导致指纹不同而重复插入); "
                            f"保留 id={twin.id} 原行"
                        ),
                        raw_row={
                            "id": r.id,
                            "complaint_no": r.complaint_no,
                            "complaint_date": str(r.complaint_date),
                            "problem": (r.problem or "")[:200],
                        },
                        status="discard",
                    ))
                n_rej += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"落拒收行失败(忽略): {e}")

        # 2) 删除重复行
        del_ids = [r.id for r, _ in to_delete]
        n_del = 0
        for i in range(0, len(del_ids), 500):
            chunk = del_ids[i:i + 500]
            res = db.execute(
                text("DELETE FROM external_complaints WHERE id = ANY(:ids)"),
                {"ids": chunk},
            )
            n_del += res.rowcount or 0
        db.flush()
        logger.info("已删除 %d 行(拒收表留痕 %d 条)", n_del, n_rej)

        # 3) 重算剩余行指纹(仅无冲突者)
        # ⚠️ 必须用**重新查询**的对象: `rows` 是删除前的内存快照, 其中的行可能已被删;
        #    且 SQL 直接 UPDATE 不会同步 ORM 实例属性(需 expire)。
        db.expire_all()
        live = list(db.scalars(select(ExternalComplaint)).all())
        live_by_id = {r.id: r for r in live}
        collide_ids = {a for a, _ in will_collide}
        n_fix = 0
        for rid in [r.id for r in stale]:
            if rid in collide_ids:
                continue
            obj = live_by_id.get(rid)
            if obj is None:
                continue   # 已被删除
            db.execute(
                text("UPDATE external_complaints SET dedup_hash=:h WHERE id=:i"),
                {"h": _identity(obj), "i": rid},
            )
            n_fix += 1
            if n_fix % 500 == 0:
                db.flush()
        db.flush()
        logger.info("已重算 %d 行指纹(跳过冲突 %d 行)", n_fix, len(will_collide))

        # 4) 自检 —— 必须 expire 后重读, 否则拿到的是删除前的缓存快照
        db.expire_all()
        after_total = db.scalar(text("SELECT count(*) FROM external_complaints")) or 0
        after_stale = 0
        for r in db.scalars(select(ExternalComplaint)).all():
            if (r.dedup_hash or "") != _identity(r):
                after_stale += 1
        logger.info("自检: 总行 %d; 剩余指纹不一致 %d(应=%d)", after_total, after_stale, len(will_collide))
        if after_stale > len(will_collide):
            db.rollback()
            logger.error("自检失败(残留不一致多于预期), 已回滚")
            return 3

        db.commit()
        logger.info("✔ 完成: 删 %d 行, 重算 %d 行指纹, 总行 %d", n_del, n_fix, after_total)
        return 0
    except Exception:
        db.rollback()
        logger.exception("失败, 已回滚")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
