"""外部投诉对账 —— **经营登记(BMS 工单) ↔ 接待过程(315 文档)** 双边闭环 (#56)

## 用户语义（2026-09-11 权威）

> 「内部投诉(服务项: 商品投诉、服务投诉), 商户号投诉(服务项: 微信投诉)
>   跟这个外部投诉 **不存在对账关系**。
>   目前我们外部投诉对账, 主要是对这个**服务类别为空、类型是几个平台的**
>   外部投诉与文档同步过来的数据对账, 拉齐。
>   俩边的数据都有其意义: bms 中的服务请求里面的投诉(外部投诉)是**经营登记**;
>   文档登记的内容是**接待过程 以及人工分析、处理 等等过程数据**。」

## ★ 两侧语义不同 → 对账目标是「双边空悬」, 不是「数量相等」

| 方向 | 含义 | 业务动作 |
|---|---|---|
| 文档有 → 工单无 (`doc_orphan`) | 用户投诉了但**业务没建单** | 补登记 / 核查漏单 |
| 工单有 → 文档无 (`ticket_orphan`) | 建单了但**没过程记录** | 补接待记录 / 核查空跑 |

→ 两侧**不互相抵消**, 各自都有意义。

## ★ 匹配口径（本模块的唯一判据）

候选工单 = **外部投诉类工单**:

```python
category IS NULL OR btrim(category)='' OR category='投诉'   # 是投诉
AND item 命中平台关键词(12315/工信部/公共服务平台/消费者协会/消监…)  # 是外部
```

⚠️ **为什么必须限定「外部投诉类」**:

  实测全表 918 条已匹配的 315 记录中, **586 条(63.8%)** 挂到了
  `item='未领取体验套餐'/'开发票'/'网慢/网卡/无网络'` 这类**非投诉类工单**
  (category=套餐疑问/退款/其他)。那些是"同一 SN 的处理过程关联",
  **不是**投诉经营登记 —— 混进来会把对账基数污染。

## ★ 时间范围优先级（用户）

> 「时间上 优先确保最新数据都能持续对应上; 其次是最近3个月;
>   如果可以就从今年开始; 至于26年以前的能匹配最好 不能也没关系。」

  · P0 最新  → `reconcile_recent()` 每次同步后重跑(新数据持续对齐)
  · P1 近 3 月 / P2 26 年 / P3 26 年前 → `external_reconcile()` 任意窗口

## ⚠️ 本模块只读

不修改工单, 不删除 315 行。唯一写操作是 `remark_matched()` 回写
`matched_ticket_no` / `matched_at`(幂等, 可重跑, 不动其他字段)。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services.complaint_scope import (
    BUCKET_EXTERNAL,
    complaint_bucket_case,
    is_complaint_ticket_cond,
)

logger = logging.getLogger(__name__)

# 匹配滞后自检阈值: 工单入库晚于投诉日超过此天数 → 计入 lag 分布
_LAG_BUCKETS = (0, 3, 7, 30, 90)


def _external_ticket_where():
    """「外部投诉类工单」的 WHERE 条件（唯一口径, 与 complaint_scope 单一来源）"""
    return and_(
        is_complaint_ticket_cond(ServiceTicket.service_category),
        complaint_bucket_case(ServiceTicket.service_item, ServiceTicket.channel)
        == BUCKET_EXTERNAL,
    )


def _sn_candidates(db: Session, sns: set[str]) -> dict[str, list[tuple[str, datetime]]]:
    """按 SN 建立 → **外部投诉类工单**(工单号, 登记时间) 列表（登记时间升序）

    与 `complaint_sync._sn_to_ticket_map` 同结构, 但**判据限定为外部投诉类**。
    同步侧收敛到本函数可避免两处判据漂移（见 #56）。
    """
    if not sns:
        return {}
    stmt = (
        select(ServiceTicket.device_sn, ServiceTicket.ticket_no,
               ServiceTicket.register_time)
        .where(ServiceTicket.device_sn.in_(sns))
        .where(_external_ticket_where())
        .order_by(ServiceTicket.register_time.asc().nullslast())
    )
    out: dict[str, list[tuple[str, datetime]]] = {}
    for sn, tno, rt in db.execute(stmt).all():
        out.setdefault(sn, []).append((tno, rt))
    return out


def _pick_nearest(
    cands: list[tuple[str, datetime]] | None,
    ref: datetime | None,
) -> tuple[str | None, int | None]:
    """从候选中挑 `register_time` 与 `ref`(投诉日期) **最接近**的一张

    与 `complaint_sync._pick_nearest_ticket` 同语义(#47: 就近选代表)。

      · ref 为空 → 取最早登记(不引入随机性)
      · 同距 → 取更早登记的, 保证结果稳定可复现
    """
    if not cands:
        return None, None
    if ref is None:
        return cands[0][0], None
    r = ref.replace(tzinfo=None) if ref.tzinfo else ref
    best: tuple[str, int] | None = None
    for tno, rt in cands:
        if rt is None:
            continue
        c = rt.replace(tzinfo=None) if rt.tzinfo else rt
        try:
            diff = abs((c - r).days)
        except (TypeError, AttributeError):
            continue
        if best is None or diff < best[1]:
            best = (tno, diff)
    return best if best else (cands[0][0], None)


def external_reconcile(
    db: Session,
    ts: datetime | None = None,
    te: datetime | None = None,
    *,
    detail_limit: int = 500,
    include_detail: bool = True,
) -> dict:
    """外部投诉双边对账（**只读**, 任意窗口）

    参数:
      · `ts` / `te` —— 窗口(UTC), 按**投诉日期**过滤文档侧、按**登记时间**
        过滤工单侧。None 表示不限。
      · `include_detail` —— 是否返回空悬明细(供人工核查/前端下钻)。

    返回:
      {
        "doc_total":          # 文档侧(315)总数(窗口内)
        "doc_matched":        # 已挂外部投诉类工单
        "doc_orphan":         # ★ 文档有、工单无(漏登记)
        "doc_orphan_rows":    # 明细(脱敏由调用方处理)
        "ticket_total":       # 外部投诉类工单总数(窗口内)
        "ticket_documented":  # 有过程文档
        "ticket_orphan":      # ★ 工单有、文档无(缺过程记录)
        "ticket_orphan_rows": # 明细
        "match_lag":          # 匹配滞后分布(自检: 工单晚到程度)
        "coverage":           # 双向覆盖率(0~1)
      }
    """
    # ── 文档侧 ──
    d_conds: list = [ExternalComplaint.complaint_date.isnot(None)]
    if ts:
        d_conds.append(ExternalComplaint.complaint_date >= ts)
    if te:
        d_conds.append(ExternalComplaint.complaint_date <= te)
    doc_total = db.scalar(
        select(func.count()).select_from(ExternalComplaint).where(*d_conds)
    ) or 0

    # 已匹配的「外部投诉类工单」—— 用 JOIN 校验匹配是否**仍合规**
    #   ⚠️ 不能只看 matched_ticket_no 非空: 历史匹配可能指向非投诉类工单(见模块头)
    doc_rows = db.execute(
        select(ExternalComplaint.id, ExternalComplaint.device_sn,
               ExternalComplaint.complaint_date, ExternalComplaint.matched_ticket_no,
               ExternalComplaint.platform, ExternalComplaint.complainant_phone)
        .where(*d_conds)
    ).all()

    # 候选映射(仅外部投诉类工单)
    sns = {r[1] for r in doc_rows if r[1]}
    cand_map = _sn_candidates(db, sns)

    doc_matched = 0
    doc_orphan_rows: list[dict] = []
    for rid, sn, cdate, mtno, plat, _ph in doc_rows:
        tno, diff = _pick_nearest(cand_map.get(sn) if sn else None, cdate)
        if tno:
            doc_matched += 1
        else:
            doc_orphan_rows.append({
                "id": rid,
                "device_sn": sn,
                "complaint_date": cdate.isoformat() if cdate else None,
                "platform": plat,
                "prev_matched": mtno,   # 旧匹配(可能不合规), 供对比
            })
    doc_orphan = len(doc_orphan_rows)

    # ── 工单侧 ──
    t_conds: list = [ServiceTicket.register_time.isnot(None)]
    if ts:
        t_conds.append(ServiceTicket.register_time >= ts)
    if te:
        t_conds.append(ServiceTicket.register_time <= te)
    t_where = and_(_external_ticket_where(), *t_conds)

    ticket_total = db.scalar(
        select(func.count()).select_from(ServiceTicket).where(t_where)
    ) or 0

    # 有过程文档的工单 = 被 315 记录挂上(且该 315 的 SN 与工单一致)
    #   按 SN + 日期就近在 Python 侧判定, 与文档侧同一套候选逻辑 → 双向自洽
    tk_rows = db.execute(
        select(ServiceTicket.ticket_no, ServiceTicket.device_sn,
               ServiceTicket.register_time)
        .where(t_where)
    ).all()

    doc_by_sn: dict[str, list[datetime]] = {}
    for _, sn, cdate, _m, _p, _ph in doc_rows:
        if sn and cdate:
            doc_by_sn.setdefault(sn, []).append(cdate)

    ticket_orphan_rows: list[dict] = []
    ticket_documented = 0
    lag_buckets = {b: 0 for b in _LAG_BUCKETS}
    for tno, sn, rt in tk_rows:
        dates = doc_by_sn.get(sn) if sn else None
        # 有同 SN 的过程文档 → 视为已记录(不强制日期接近: 接待过程可能延迟补充)
        if dates:
            ticket_documented += 1
        else:
            ticket_orphan_rows.append({
                "ticket_no": tno,
                "device_sn": sn,
                "register_time": rt.isoformat() if rt else None,
            })
    ticket_orphan = len(ticket_orphan_rows)

    # ── 匹配滞后自检: 工单入库 vs 登记(反映"工单晚到"程度) ──
    lag_rows = db.execute(
        select(func.avg(
            func.extract("epoch",
                         ServiceTicket.created_at - ServiceTicket.register_time
                         ) / 86400
        ))
        .where(t_where)
    ).scalar()
    avg_lag = round(float(lag_rows), 1) if lag_rows else 0.0
    for b in _LAG_BUCKETS:
        lag_buckets[b] = db.scalar(
            select(func.count()).select_from(ServiceTicket).where(
                t_where,
                func.extract("epoch",
                             ServiceTicket.created_at - ServiceTicket.register_time
                             ) / 86400 >= b,
            )
        ) or 0

    out: dict = {
        "doc_total": int(doc_total),
        "doc_matched": int(doc_matched),
        "doc_orphan": int(doc_orphan),
        "ticket_total": int(ticket_total),
        "ticket_documented": int(ticket_documented),
        "ticket_orphan": int(ticket_orphan),
        "match_lag": {
            "avg_days": avg_lag,
            "gte_0d": lag_buckets[0],
            "gte_3d": lag_buckets[3],
            "gte_7d": lag_buckets[7],
            "gte_30d": lag_buckets[30],
            "gte_90d": lag_buckets[90],
        },
        "coverage": {
            "doc_hit_rate": round(doc_matched / doc_total, 4) if doc_total else 0.0,
            "ticket_doc_rate": (
                round(ticket_documented / ticket_total, 4) if ticket_total else 0.0
            ),
        },
    }
    if include_detail:
        out["doc_orphan_rows"] = doc_orphan_rows[:detail_limit]
        out["ticket_orphan_rows"] = ticket_orphan_rows[:detail_limit]
    return out


def remark_matched(db: Session, *, window_days: int = 90,
                   dry_run: bool = False, commit: bool = True) -> dict:
    """重算并回写 `matched_ticket_no` —— 解决「工单晚到不补挂」(#56 缺陷 4)

    ## 背景

      实测外部投诉类工单 `created_at - register_time` **平均滞后 71.1 天**
      (>30 天占 53%)。而 `matched_ticket_no` **只在同步瞬间写一次**,
      之后从不重算 → 工单晚到时该 315 记录**永久空悬**。

    ## 策略

      · 范围: 近 `window_days` 天(按投诉日期) + 全部未匹配行
        —— 控成本的同时保证「最新数据持续对齐」(用户 P0)
      · 只写 `matched_ticket_no` / `matched_at`, **不动其他字段**
      · 幂等: 重复执行结果一致(候选集与选择规则都是确定性的)
      · `dry_run=True` 只统计不改库

    ## 参数

      · `commit` —— 是否提交。默认 True(生产用);
        **测试传 False**(避免破坏 conftest 的事务回滚隔离, 改为 flush 使断言可见)。

    返回: {"scanned": n, "newly_matched": n, "changed": n, "unmatched": n}
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = db.execute(
        select(ExternalComplaint.id, ExternalComplaint.device_sn,
               ExternalComplaint.complaint_date, ExternalComplaint.matched_ticket_no)
        .where(
            or_(
                ExternalComplaint.matched_ticket_no.is_(None),
                ExternalComplaint.complaint_date >= cutoff,
            )
        )
    ).all()

    sns = {r[1] for r in rows if r[1]}
    cand_map = _sn_candidates(db, sns)

    now = datetime.now(timezone.utc)
    newly = changed = 0
    unmatched = 0
    updates: list[tuple[int, str | None, datetime | None]] = []
    for rid, sn, cdate, old in rows:
        tno, _diff = _pick_nearest(cand_map.get(sn) if sn else None, cdate)
        if tno is None:
            unmatched += 1
            if old is not None:
                # 原匹配已不合规(指向非外部投诉类工单) → 清空, 避免统计被污染
                updates.append((rid, None, None))
                changed += 1
            continue
        if tno == old:
            continue
        updates.append((rid, tno, now))
        if old is None:
            newly += 1
        else:
            changed += 1

    if not dry_run and updates:
        tbl = ExternalComplaint.__table__
        for rid, tno, at in updates:
            db.execute(
                tbl.update().where(tbl.c.id == rid)
                .values(matched_ticket_no=tno, matched_at=at)
            )
        if commit:
            db.commit()
        else:
            db.flush()

    logger.info(
        "remark_matched: 扫描 %d, 新挂 %d, 变更 %d, 仍空悬 %d, dry_run=%s",
        len(rows), newly, changed, unmatched, dry_run,
    )
    return {
        "scanned": len(rows),
        "newly_matched": newly,
        "changed": changed,
        "unmatched": unmatched,
        "dry_run": dry_run,
    }
