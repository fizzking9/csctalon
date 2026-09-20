"""采集质量与事件归并: complaint_sn_backfill(#60) / complaint_dedup_review(#55)。"""

import re

from fastapi import Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import _parse_dt, _parse_dt_end
from app.api.v1.complaints._router import router
from app.models.card_complaint import CardComplaint
from app.models.external_complaint import ExternalComplaint
from app.schemas.common import R
from app.services.complaint_scope import (
    dedup_events,
)


@router.get("/sn-backfill", response_model=R,
            summary="采集质量·SN后补残影清单(已从统计排除) (#60)")
def complaint_sn_backfill(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(
        None, description="external=315 / card=(外)卡商, 缺省全部"
    ),
    limit: int = Query(500, ge=1, le=2000),
):
    """**SN 后补残影**清单 —— 采集质量问题可视化（#60）

    ## 这是什么

      源文档存在「**先建行、后补 SN**」的采集习惯 →
      `dedup_hash` 含 SN, 补 SN 后指纹变化 → **又 INSERT**
      → 同一投诉产生两条：

      | 行 | 特征 | created_at |
      |---|---|---|
      | **残影** | 缺 SN（字段不全） | **较早** |
      | 完整行 | 有 SN（字段齐全） | 较晚 |

      #59 已把残影标记为 `quality_status='dup_sn_backfill'`，
      并从统计口径**排除**（避免同一条投诉被算两次）。

    ## 本接口的用途

      · 采集质量**可视化** —— 让运营看到上游「先建行后补 SN」的规模与趋势
      · 提供**配对关系**（残影 ↔ 完整行），便于人工核对
      · ★ 明确标注「**已排除 / 原行保留**」，避免误解为数据丢失

    ## 数据安全

      ★ **不删除任何行** —— 残影保留, 仅打标 + 统计排除。
        可通过把 `quality_status` 置空**完整回滚**。
    """
    from app.models.card_complaint import CardComplaint
    from app.services.complaint_scope import QSTATUS_DUP_SN_BACKFILL

    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    out: list[dict] = []
    by_month: dict[str, int] = {}

    def _collect(model, label_col, src: str):
        conds: list = [model.quality_status == QSTATUS_DUP_SN_BACKFILL]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        ghosts = db.execute(
            select(model.id, model.device_sn, model.complaint_date,
                   model.complainant_phone, label_col, model.created_at,
                   model.quality_note)
            .where(*conds)
            .order_by(model.complaint_date.desc().nullslast())
            .limit(limit)
        ).all()
        for gid, sn, cdate, ph, lbl, cat, note in ghosts:
            # 从 note 里解析出「被哪条完整行取代」(格式: 被 id=NNN 取代)
            full_id = None
            if note:
                m = re.search(r"id=(\d+)", note)
                if m:
                    full_id = int(m.group(1))
            # ★ 取**完整行**的 SN —— 残影本身缺 SN, 用户要看的是"补齐的那个SN"
            full_sn = full_created = None
            if full_id:
                frow = db.execute(
                    select(model.device_sn, model.created_at)
                    .where(model.__table__.c.id == full_id)
                ).first()
                if frow:
                    full_sn, full_created = frow[0], frow[1]
            out.append({
                "ghost_id": gid,
                "source_type": src,
                "device_sn": sn,              # 残影的 SN(通常为空)
                "full_device_sn": full_sn,    # ★ 完整行的 SN(即补齐的那个)
                "complaint_date": cdate.isoformat() if cdate else None,
                "complainant_phone": ph,
                "platform": lbl,
                "ghost_created_at": cat.isoformat() if cat else None,
                "full_created_at": full_created.isoformat() if full_created else None,
                "full_id": full_id,
                "quality_note": note,
                "table": model.__tablename__,
            })
            if cdate:
                k = cdate.strftime("%Y-%m")
                by_month[k] = by_month.get(k, 0) + 1

    if source_type in (None, "", "external"):
        _collect(ExternalComplaint, ExternalComplaint.platform, "external")
    if source_type in (None, "", "card"):
        _collect(CardComplaint, CardComplaint.complaint_group, "card")

    out.sort(key=lambda x: str(x.get("complaint_date") or ""), reverse=True)
    return R(data={
        "items": out[:limit],
        "total": len(out),
        "by_month": dict(sorted(by_month.items(), reverse=True)),
        "excluded_from_stats": True,   # ★ 明确: 已从统计排除
        "rows_preserved": True,        # ★ 明确: 原始行保留未删
    })

@router.get("/dedup-review", response_model=R, summary="投诉事件归并·待人工审核列表(#55)")
def complaint_dedup_review(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(
        None, description="external=315外部投诉 / card=(外)卡商, 缺省全部"
    ),
):
    """★ (#55) **待人工判定**的同设备同日多电话组 —— 供前端「人工审核」Tab。

    ## 为什么需要人工

      用户（2026-09-11）:
      > 「手机不同 默认 统计为不同, 但是提供一个人工再审核更合适点」

      `dedup_events` 的自动归并只在 **(SN, 归一化电话)** 一致时生效;
      电话不同 → **默认算不同投诉**(不自动归并), 但可能是:
        · 同一人换了号码 / 用家人号码登记
        · 联系人字段被业务填错
        · 确实就是两起不同的投诉
      → 无法程序判定, 必须人工。

    ## 归并组 `reason` 取值

      · `same_day_multi_phone` —— 同 SN 同投诉日 但电话 ≥2 个(最需人工)
      · `phone_missing`         —— 有 SN 但电话缺失

    ⚠️ 本接口**只读**, 不修改任何数据。人工结论目前落 `complaint_audits`
       (见 `POST /complaints/audits`), **不删原始行**。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    def _collect(model, ch_col) -> list[dict]:
        conds: list = [model.complaint_date.isnot(None)]
        if ts:
            conds.append(model.complaint_date >= ts)
        if te:
            conds.append(model.complaint_date <= te)
        rows = db.execute(
            select(model.id, model.device_sn, model.complaint_date,
                   model.complainant_phone, ch_col)
            .where(*conds)
        ).all()
        payload = [
            {"id": r[0], "device_sn": r[1], "complaint_date": r[2],
             "complainant_phone": r[3], "channel": r[4]}
            for r in rows
        ]
        _, _, review = dedup_events(payload)
        # 补 `source_type`(前端区分卡商/315) 与 `record_count`(组内条数, 排序用)
        for g in review:
            g["source_type"] = "card" if model is CardComplaint else "external"
            g["record_count"] = len(g.get("record_ids") or [])
        return review

    out: list[dict] = []
    if source_type in (None, "", "external"):
        out += _collect(ExternalComplaint, ExternalComplaint.platform)
    if source_type in (None, "", "card"):
        out += _collect(CardComplaint, CardComplaint.complaint_group)

    # 影响面大的优先(组内条数降序), 再按投诉日倒序(近期更值得处理)
    out.sort(key=lambda g: (-int(g.get("record_count") or 0),
                            str(g.get("complaint_day") or "")),
             reverse=False)
    return R(data={"items": out, "total": len(out)})
