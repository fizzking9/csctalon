"""纯工信部投诉视图 (#63)

## 用户需求（2026-09-11）

> 「纯粹工信部的**优先级不高**，有统计记录就好了。
>   回头**单列一页**展示即可。**工单+文档 合并去重 统计展示**。」

> 「纯工信部的 可以建工单 留待后面处理。先把外部投诉 这部分对账完成。」

## 判据（★ 关键：必须用「SN 或 电话」）

```
纯工信部 = 工信部工单 ∧ NOT (卡商表 SN 或 电话 命中)
                      ∧ NOT (315表  SN 或 电话 命中)
```

### 为什么不能只用 SN 精确匹配

用户人工核对 6 条「SN 无匹配」的工信部工单后，暴露了 SN 精确匹配的**盲区**：

| 现实情况 | 实例 |
|---|---|
| 同一客户**多台设备**：工单管 A 机，卡商单登记 B 机 | ① `...0103` vs `...2464` |
| 同客户**换机**，SN 变了 | ② `...6449` vs `...0957` |
| **处理意见里提到另一台** SN | ⑥ 工单 SN `...9896`，意见里是 `...4195` |

**而 `phone` 三条全部一致** → **电话才是可靠的关联键**。

> 印证 #55 的原则：**SN = 客户身份，但一个客户可能有多台设备**。

### 实测（2026-09-11，全量 320 条工信部工单）

| 判据 | 命中 |
|---|---|
| SN 精确命中（卡商或 315） | 314 |
| **SN 或 电话命中** | **318** |
| **纯工信部**（两者都无） | **2** |

用户人工核对的 6 条候选，判据验证：

| # | SN | 结果 | 命中路径 |
|---|---|---|---|
| ① | `68032504010203` | 有对账 ✅ | 卡商**电话** ×2 |
| ② | `67492609646449` | 有对账 ✅ | 卡商 SN ×5 + **电话** ×15 |
| ③ | `67482512338896` | **纯工信部** | — |
| ④ | `67482610416034` | **纯工信部** | — |
| ⑤ | `69212602132363` | 有对账 ✅ | 315 **电话** ×2 |
| ⑥ | `69212612179896` | 有对账 ✅ | 卡商**电话** ×1 |

★ ①②⑥ 正是「SN 查不到、电话能查到」的典型 → 用 SN 精确匹配会**误判为纯工信部**。

## 「合并去重」的含义

```
工单侧: service_item='工信部投诉'（经营登记）
文档侧: 315/卡商表中「无对应工单」的记录
  ↓ 按 (SN 或 电话) 关联后
合并为一份「纯工信部」视图, 同一投诉只计一次
```

## ★ 纪律

- **不删数据**：本模块只做**只读视图**，不改任何表。
- 纯工信部工单**保持原样**（`service_item='工信部投诉'` 不动）。
- 与 #57/#65 的卡商口径**不冲突**：卡商桶仍包含全部工信部工单；
  本视图只是从中**再筛出「两边都无对账」的那一小撮**。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.models.ticket import ServiceTicket
from app.services.complaint_scope import (
    complaint_bucket_case,
    is_complaint_ticket_cond,
)

logger = logging.getLogger(__name__)

# 工信部工单的判据（与 complaint_scope 单一来源保持一致）
BUCKET_CARD_TICKET = "(外)卡商"


def _card_ticket_stmt():
    """工信部工单（= 卡商桶）的 WHERE 条件（不含 SN 软纠正，因本视图针对原登记）"""
    return and_(
        is_complaint_ticket_cond(ServiceTicket.service_category),
        complaint_bucket_case(ServiceTicket.service_item, ServiceTicket.channel)
        == BUCKET_CARD_TICKET,
    )


def _hit_sets(db: Session) -> dict[str, set[str]]:
    """取两张文档表的 SN / 电话集合（一次性载入，供内存比对）

    ⚠️ 为什么载入内存而非写 SQL `NOT IN`：
      · 文档表 SN/电话**含 NULL** → `NOT IN` 三值逻辑会整表返回空
      · 需要「SN **或** 电话」的**跨列** OR 匹配，SQL 写法冗长易错
      · 两张表规模可控（卡商 764 / 315 2643 行）→ 内存比对更快
    """
    out: dict[str, set[str]] = {}
    for name, model, sn_col, ph_col in (
        ("card", CardComplaint, CardComplaint.device_sn, CardComplaint.complainant_phone),
        ("ext", ExternalComplaint, ExternalComplaint.device_sn, ExternalComplaint.complainant_phone),
    ):
        out[f"{name}_sn"] = {
            v for v in db.scalars(select(sn_col).where(sn_col.isnot(None)).distinct()).all() if v
        }
        out[f"{name}_ph"] = {
            v for v in db.scalars(select(ph_col).where(ph_col.isnot(None)).distinct()).all() if v
        }
    return out


def _is_hit(sn: str | None, phone: str | None, hits: dict[str, set[str]]) -> tuple[bool, bool, list[str]]:
    """判断该 (SN, 电话) 是否命中任一张文档表

    返回 `(是否命中, SN是否命中, 命中路径列表)`。
    命中路径形如 `卡商SN` / `315电话` —— 供页面展示"为什么不算纯工信部"。
    """
    paths: list[str] = []
    sn_hit = False
    if sn:
        if sn in hits["card_sn"]:
            sn_hit = True
            paths.append("卡商SN")
        if sn in hits["ext_sn"]:
            sn_hit = True
            paths.append("315SN")
    if phone:
        if phone in hits["card_ph"]:
            paths.append("卡商电话")
        if phone in hits["ext_ph"]:
            paths.append("315电话")
    return bool(paths), sn_hit, paths


def pure_card_tickets(
    db: Session,
    ts: datetime | None = None,
    te: datetime | None = None,
    *,
    detail_limit: int = 2000,
    include_checked: bool = False,
) -> dict:
    """**纯工信部投诉视图**（#63）

    参数:
      · `ts` / `te` —— 按 `register_time` 过滤（None = 全量）
      · `detail_limit` —— 清单返回上限
      · `include_checked` —— 是否连「已对账」的工信部工单一并返回
        （默认 False, 只返回纯工信部; True 时清单含全部工信部工单并带 `is_pure` 标记）

    返回:
      ```
      {
        "total_tickets": 320,              # 工信部工单总数
        "matched": 318,                    # SN 或 电话命中文档表的
        "pure": 2,                         # ★ 纯工信部（两边都无）
        "sn_exact_only": 314,              # 仅按 SN 精确匹配时的命中数（对比用）
        "by_hit_path": {"卡商SN": n, ...}, # 命中路径分布
        "monthly": [{"month": "2026-08", "total": n, "pure": n}],
        "items": [ {...} ],                # 纯工信部清单
      }
      ```
    """
    stmt = (
        select(
            ServiceTicket.ticket_no,
            ServiceTicket.device_sn,
            ServiceTicket.phone,
            ServiceTicket.register_time,
            ServiceTicket.finish_time,
            ServiceTicket.status,
            ServiceTicket.handle_opinion,
            ServiceTicket.service_item,
            ServiceTicket.remark,
        )
        .where(_card_ticket_stmt())
        .order_by(ServiceTicket.register_time.desc().nullslast())
    )
    if ts:
        stmt = stmt.where(ServiceTicket.register_time >= ts)
    if te:
        stmt = stmt.where(ServiceTicket.register_time <= te)

    rows = db.execute(stmt).all()
    hits = _hit_sets(db)

    items: list[dict] = []
    matched = 0
    pure_count = 0
    sn_exact_only = 0
    path_counter: dict[str, int] = {}
    monthly: dict[str, dict[str, int]] = {}

    for r in rows:
        (tno, sn, ph, reg, fin, status, opinion, _item, remark) = r
        is_hit, sn_hit, paths = _is_hit(sn, ph, hits)
        if is_hit:
            matched += 1
        else:
            pure_count += 1
        if sn_hit:
            sn_exact_only += 1
        for p in paths:
            path_counter[p] = path_counter.get(p, 0) + 1

        m = _month_key(reg)
        if m:
            b = monthly.setdefault(m, {"total": 0, "pure": 0})
            b["total"] += 1
            if not is_hit:
                b["pure"] += 1

        if is_hit and not include_checked:
            continue
        items.append({
            "ticket_no": tno,
            "device_sn": sn,
            "phone": ph,
            "register_time": _iso(reg),
            "finish_time": _iso(fin),
            "status": status,
            # 处理意见: handle_opinion 优先, 回退 remark (#63 需要供人工核查)
            "handle_opinion": (opinion or remark or "").strip() or None,
            "service_item": _item,
            "is_pure": not is_hit,
            "hit_paths": paths,
        })

    truncated = len(items) > detail_limit
    return {
        "total_tickets": len(rows),
        "matched": matched,
        "pure": pure_count,
        # 对比项: 仅 SN 精确匹配的命中数 —— 用于体现「电话兜底」的价值
        "sn_exact_only": sn_exact_only,
        "by_hit_path": dict(sorted(path_counter.items(), key=lambda x: -x[1])),
        "monthly": [
            {"month": k, **v} for k, v in sorted(monthly.items())
        ],
        "items": items[:detail_limit],
        "truncated": truncated,
    }


def _month_key(dt_val: datetime | None) -> str | None:
    """按北京时间归月（YYYY-MM）

    ⚠️ 库里 `register_time` 是 `timestamptz`, 但驱动取出可能是 naive UTC
       → 先补 UTC 再转 Asia/Shanghai（项目强制时区规范）。
    """
    if not dt_val:
        return None
    from zoneinfo import ZoneInfo

    aware = dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=ZoneInfo("UTC"))
    return aware.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")


def _iso(dt_val: datetime | None) -> str | None:
    """→ 北京时间 ISO 串（与前端其它明细一致的 'YYYY-MM-DD HH:MM'）"""
    if not dt_val:
        return None
    from zoneinfo import ZoneInfo

    aware = dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=ZoneInfo("UTC"))
    return aware.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
