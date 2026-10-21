"""投诉明细/纯工信部/补充明细: complaint_detail / complaint_pure_card / complaint_supplementary。"""

from fastapi import Query
from sqlalchemy import and_, column, func, or_, select, table

from app.api.deps import CurrentUser, DBSession

# 跨子模块引用(拆分自 complaints_src.py, 原同文件内直接调用)
from app.api.v1.complaints._helpers import (
    _EXTERNAL_DONE_RESULTS,
    _external_done_sns,
    _handler_fallback_cols,
    _is_real_finish,
    _norm_external_result_status,
    _norm_handler,
    _norm_status,
    _norm_subject_entity,
    _parse_dt,
    _parse_dt_end,
    _to_sh_iso,
)
from app.api.v1.complaints._router import router
from app.core.redis_client import cache_ttl_for, cached_json
from app.models.ticket import ServiceTicket

# #338: 投诉明细读 std_all 镜像(展示用全列)
_CC = table("std_card_complaint_all",
            column("id"), column("complaint_date"), column("complaint_group"),
            column("complainant"), column("complainant_phone"), column("device_sn"),
            column("problem"), column("problem_category"), column("result"),
            column("solution"), column("handler"), column("handle_result"),
            column("matched_ticket_no"), column("created_at"))
_EC = table("std_external_complaint_all",
            column("id"), column("complaint_no"), column("complaint_date"),
            column("platform"), column("complainant"), column("complainant_phone"),
            column("device_sn"), column("problem"), column("complaint_category"),
            column("solution"), column("result"), column("follower"),
            column("handle_time"), column("handle_detail"), column("matched_ticket_no"),
            column("company_entity"), column("complaint_shop"), column("created_at"))
from app.schemas.common import R
from app.services.analytics import (
    _complaint_source_type_case,
    _ticket_category_case,
)
from app.services.complaint_scope import (
    _CARD_TICKET_KEYWORDS,
)


@router.get("/detail", response_model=R, summary="投诉明细列表(三源合并: 内部/商户号/外部)")
@cached_json(lambda *a, **kw: f"complaint:detail:{kw.get('start')}:{kw.get('end')}:{kw.get('source_type')}:{kw.get('subject')}:{kw.get('limit')}:{int(bool(kw.get('include_supplementary')))}",
             ttl=lambda **kw: cache_ttl_for(kw.get('start'), kw.get('end'), 60))
def complaint_detail(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="内部投诉/商户号投诉/外部投诉/(外)卡商"),
    subject: str | None = Query(None, description="按被投诉主体(归一化名, 如 云客猫)过滤明细"),
    limit: int = Query(500, ge=1, le=2000),
    include_supplementary: bool = Query(False, description="为true时按设备SN附加315/卡商补充明细(关联合并)"),
):
    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    src_expr = _complaint_source_type_case()
    handler_expr = _handler_fallback_cols()

    # 被投诉商户主体名集合(registrant 去重): 用于过滤上游把商户号名称误写入 handler 的脏数据
    # 商户号投诉的"处理人"必须是客服人员, 不能是商户主体名。
    entity_set: set[str] = set()
    if not source_type or source_type == "商户号投诉":
        entity_set = set(
            db.scalars(
                select(ServiceTicket.registrant)
                .where(_ticket_category_case() == "投诉")
                .where(src_expr == "商户号投诉")
                .where(ServiceTicket.registrant.isnot(None), ServiceTicket.registrant != "")
                .distinct()
            ).all()
        )

    records: list[dict] = []

    # 1. 服务工单投诉 (主台账) → is_primary=True, 前端排序在前
    #    查询范围: register_time在区间内(今日新增) OR finish_time在区间内(今日完成)
    #    解决历史登记但今日才处理完成的工单不出现在明细里的问题
    stmt = (
        select(
            ServiceTicket.ticket_no,
            ServiceTicket.device_sn,
            src_expr,
            ServiceTicket.service_item,
            ServiceTicket.remark,
            handler_expr,        # 工单 handler: 多级 fallback, 不再只看 handler 字段
            ServiceTicket.register_time,
            ServiceTicket.status,
            ServiceTicket.finish_time,
            ServiceTicket.registrant,  # 被投诉商户主体名(商户号投诉场景; 内部/外部投诉时多为处理人或空)
        )
        .where(_ticket_category_case() == "投诉")
    )
    # finish_time 在区间内
    # 说明: 如果finish_time ≈ register_time(系统默认值),二者几乎相同,
    # 此时 register_time 和 finish_time 要么同时在范围内,要么同时不在,不会漏掉也不会重复计算.
    finish_conds = [ServiceTicket.finish_time.isnot(None)]
    time_conds = []
    if ts:
        time_conds.append(ServiceTicket.register_time >= ts)
        finish_conds.append(ServiceTicket.finish_time >= ts)
    if te:
        time_conds.append(ServiceTicket.register_time <= te)
        finish_conds.append(ServiceTicket.finish_time <= te)
    finish_in_range = and_(*finish_conds)
    if time_conds:
        reg_in_range = and_(*time_conds)
        stmt = stmt.where(or_(reg_in_range, finish_in_range))
    else:
        stmt = stmt.where(finish_in_range)
    if source_type:
        stmt = stmt.where(src_expr == source_type)
    # 按"动作时间"排序: 用 GREATEST(register_time, COALESCE(finish_time, register_time)) 近似最近动作时间
    action_time = func.greatest(
        ServiceTicket.register_time,
        func.coalesce(ServiceTicket.finish_time, ServiceTicket.register_time),
    )
    # 315登记已办结的SN: 外部投诉工单状态未同步但实际已办结 → 明细状态显示已完成(与积压口径一致)
    ext_done_sns = _external_done_sns(db)
    for r in db.execute(stmt.order_by(action_time.desc().nulls_last())).all():
        source = r[2] or "内部投诉"
        raw_status = r[7]
        raw_handler = r[5]
        finish = r[8]
        register = r[6]
        norm_status = _norm_status(source, raw_status, finish, register, raw_handler)
        if source == "外部投诉" and r[1] in ext_done_sns:
            norm_status = "已完成"  # 积压闭环: 315登记已办结 → 工单视为已完成
        # ★★ #65: 软纠正标注 ★★
        #   工单 `service_item='工信部投诉'` 但被 #58 纠正为外部投诉时,
        #   明细表若只显示 category='工信部投诉' 会与 source='外部投诉' **矛盾**
        #   → 显式标出原登记值, 让运营知道「分类是错的, 已按监所口径归入外部」。
        #   ⚠️ 仅标注, **不改 BMS 原数据**(用户: "不能乱删除 所以还是慎重")。
        _raw_item = r[3] or ""
        _is_soft_corrected = (
            source == "外部投诉"
            and any(k in _raw_item for k in _CARD_TICKET_KEYWORDS)
        )
        # 展示时间:取最近动作时间(完成优先于登记,但如果完成时间不合理则用登记时间)
        display_dt = register
        if _is_real_finish(finish, register):
            # finish 在查询区间内(今日完成)或register也在区间内(今日登记今日完成):取较晚者
            if finish and register and finish > register:
                display_dt = finish
        records.append({
            "source": source,
            "source_role": "primary",
            "is_primary": True,
            "no": r[0],
            "device_sn": r[1],
            "category": r[3],
            "problem": r[4],
            # ★ #65: 软纠正标注(前端展示为「原登记: 工信部投诉」小标签)
            "soft_corrected": _is_soft_corrected,
            # 商户号投诉: handler 若落在被投诉主体名集合 → 该行无真实处理人(上游脏数据)
            "handler": "未分配" if (source == "商户号投诉" and raw_handler in entity_set) else _norm_handler(raw_handler),
            # 被投诉主体: 仅商户号投诉的 registrant 是主体; 内部/外部投诉 registrant 多为处理人或系统值, 由补充表回填
            "entity": r[9] if source == "商户号投诉" else None,
            "time": _to_sh_iso(display_dt),
            "register_time": _to_sh_iso(register),
            "finish_time": _to_sh_iso(finish) if _is_real_finish(finish, register) else None,
            "status": norm_status,
        })

    # ---- 关联合并: 按设备SN附加 315外部投诉/卡商群投诉 补充明细 ----
    # 两张补充表(workbuddy推送)是对工单口径的更详细描述(投诉问题/处理明细/跟进人等),
    # 核心匹配键为 device_sn, 一个SN可对应多条补充记录(用户可能多次投诉)。
    # 默认不附加(避免大页负载), 显式 include_supplementary=true 时附加。
    # 已关联工单的按SN附加到对应工单行; 未关联工单(或SN为空)的生成独立行,
    # 使运营看板/今日看板也能看到 workbuddy 推送但尚未落成工单的投诉线索。
    if include_supplementary:
        extra_rows: list[dict] = []

        def _collect(model, sn_field_name: str, source_name, limit_per_sn=10):
            # ★★ #65 修复: `source_type` 筛选必须**同时约束补充表的独立行** ★★
            #   Bug: `source_type=(外)卡商` 时, 315 补充表**全量**被当成卡商独立行返回
            #        (实测 473 条, 其中 472 条 source=外部投诉) —— 因为原实现
            #        仅在工单主行 `stmt.where(src_expr == source_type)` 筛选。
            #
            #   ⚠️ 但**不能**在函数开头就 return —— 补充记录若 SN 命中工单行,
            #      它是该行的**附注(supplementary)**, 不改变主行所属桶,
            #      且 `subject` 过滤依赖它(W2 场景: 商户号行 + 挂载的 315 主体)。
            #      → 只对**独立行** (`extra_rows`) 施加桶筛选。
            _bucket = "外部投诉" if source_name == "315外部投诉" else "(外)卡商"
            _keep_extra = (not source_type) or (_bucket == source_type)
            stmt = select(model)
            if ts:
                stmt = stmt.where(model.c.complaint_date >= ts)
            if te:
                stmt = stmt.where(model.c.complaint_date <= te)
            rows = db.execute(stmt.order_by(model.c.complaint_date.desc().nullslast())).mappings().all()
            for c in rows:
                sn = getattr(c, sn_field_name)
                suppl_item = {
                    "source": source_name,
                    "complaint_no": getattr(c, "complaint_no", None),
                    "complaint_date": _to_sh_iso(c.complaint_date),
                    "complainant": c.complainant,
                    "complainant_phone": c.complainant_phone,
                    "platform": getattr(c, "platform", None),
                    "complaint_group": getattr(c, "complaint_group", None),
                    "company_entity": getattr(c, "company_entity", None),
                    "complaint_shop": getattr(c, "complaint_shop", None),
                    "problem": c.problem,
                    "solution": c.solution,
                    "result": c.result,
                    "handler": getattr(c, "handler", None) or getattr(c, "follower", None),
                    "handle_detail": getattr(c, "handle_detail", None) or getattr(c, "handle_result", None),
                    "handle_time": _to_sh_iso(c.handle_time) if hasattr(c, "handle_time") else None,
                    "matched_ticket_no": c.matched_ticket_no,
                }
                # 被投诉主体(仅315外部投诉有: 公司主体 → 投诉店铺兜底)
                entity = None
                if source_name == "315外部投诉":
                    ce = (suppl_item.get("company_entity") or "").strip()
                    cs = (suppl_item.get("complaint_shop") or "").strip()
                    entity = ce or (cs if cs and cs not in ("/", "-") else None)
                # 命中工单SN → 附加到对应工单行(补充说明)
                targets = [r for r in records if r.get("device_sn") == sn] if sn else []
                if targets:
                    for rec in targets:
                        rec.setdefault("supplementary", [])
                        if len(rec["supplementary"]) >= limit_per_sn:
                            continue
                        rec["supplementary"].append(suppl_item)
                    continue
                # 未关联工单(或SN为空) → 独立行, 归入对应桶
                #   ⚠️ 2026-09-10 (#47): 卡商**独立成桶**「(外)卡商」,
                #      原先 `main_source = "外部投诉"`(卡商被并入外部) → 已拆开。
                #   ⚠️ #65: `source_type` 筛选时跳过**非目标桶**的独立行,
                #      否则会把 315 外部投诉全量当成卡商返回(见函数头说明)。
                if not _keep_extra:
                    continue
                is_315 = source_name == "315外部投诉"
                main_source = "外部投诉" if is_315 else "(外)卡商"
                extra_rows.append({
                    "source": main_source,
                    "source_role": "supplementary",
                    "is_primary": False,
                    "no": suppl_item["complaint_no"] or suppl_item["matched_ticket_no"],
                    "device_sn": sn,
                    # 分类: 315表=平台(全国12315平台/公共服务平台等, 属外部投诉) /
                    #      卡商表=投诉群名无分类意义(群/主体名, 如"小皮丘&深圳联通"), 统一用「(外)卡商」
                    #      避免明细/看板分类筛选出现主体群名
                    "category": getattr(c, "platform", None) if is_315 else "(外)卡商",
                    "problem": c.problem,
                    "handler": _norm_handler(suppl_item["handler"]),
                    "entity": entity,
                    "time": suppl_item["handle_time"] or suppl_item["complaint_date"],
                    "register_time": suppl_item["complaint_date"],
                    "finish_time": suppl_item["handle_time"],
                    # 315行状态归一化(办结类→已完成, 与积压口径一致); 卡商行保持原文
                    "status": _norm_external_result_status(c.result) if is_315 else (c.result or "补充记录"),
                    "supplementary": [suppl_item],
                    "suppl_finish_time": suppl_item["handle_time"],
                    "suppl_problem": c.problem,
                    "suppl_count": 1,
                })

        _collect(_EC, "device_sn", "315外部投诉")
        # #47: 统计桶名用「(外)卡商」(与数据源字面名"卡商群投诉"区分)
        _collect(_CC, "device_sn", "(外)卡商")

        # 工单行便捷合并字段: 补充表里更详细的 完成时间/投诉内容(供明细表直接展示)
        for r in records:
            suppl = r.get("supplementary") or []
            r["supplementary"] = suppl
            if suppl:
                times = [s.get("handle_time") for s in suppl if s.get("handle_time")]
                r["suppl_finish_time"] = max(times) if times else None
                r["suppl_problem"] = next((s.get("problem") for s in suppl if s.get("problem")), None)
                r["suppl_count"] = len(suppl)
                # 外部投诉工单 registrant 多为处理人(无主体), 用补充表的公司主体回填
                if not r.get("entity"):
                    r["entity"] = next((s.get("company_entity") for s in suppl if s.get("company_entity")), None)

        records.extend(extra_rows)

    # 按动作时间降序排列
    records.sort(key=lambda x: x.get("time") or "", reverse=True)
    # F-P2-4 (问题4) + 审查 W2 修订(2026-09-04): 按被投诉主体过滤(归一化比较)。
    # subject_ranking 的口径 = 商户号工单主体(registrant) + 315/卡商补充表主体
    # (company_entity/投诉店铺)。detail 中同一投诉可能有两种落点:
    #   ① 主行 entity: 商户号行=registrant; 315 未匹配工单→独立行 entity=company_entity
    #   ② 挂靠: include_supplementary 时 315/卡商记录若 SN 匹配到工单, 挂在主行
    #      supplementary 下(主行 entity 可能是商户号/内部主体, 非该 315 的主体)
    # 故 subject 过滤需同时看 主行 entity 与 supplementary[] 里各补充项的 company_entity,
    # 任一归一命中 subject 即保留 —— 避免"315 挂靠到其它主体工单行"导致漏出(W2)。
    def _record_subject_entities(r: dict) -> set:
        """该明细行涉及的所有归一被投诉主体(主行 entity + 挂靠 315 的 company_entity/店铺)。"""
        out = set()
        ent = r.get("entity")
        if ent:
            norm = _norm_subject_entity(ent)
            if norm:
                out.add(norm)
        for s in (r.get("supplementary") or []):
            ce = (s.get("company_entity") or "").strip()
            cs = (s.get("complaint_shop") or "").strip()
            for v in (ce, cs):
                if v and v not in ("/", "-"):
                    norm = _norm_subject_entity(v)
                    if norm:
                        out.add(norm)
        return out

    if subject:
        _norm_subject = _norm_subject_entity(subject)
        if _norm_subject:
            records = [r for r in records if _norm_subject in _record_subject_entities(r)]
    # F-P1-2(审查 W3 修订 2026-09-04): registered/handled 计数统一基于"过滤后明细行"
    # 计算, 与 items 集合同口径(消除与全窗口 _sources_reg_handled 的口径差):
    #   registered = 行 register_time 落在窗口(start~end 北京日);  hand handled = 行 finish_time 非空
    #   (detail 仅在 _is_real_finish 真实完成时写 finish_time)且落在窗口。
    # register_time/finish_time 为 _to_sh_iso 输出(YYYY-MM-DD HH:MM), 取前10位与 start/end 比较。
    _ds = (start[:10] if start else "")
    _de = (end[:10] if end else "")
    def _in_window(dt_str: str | None) -> bool:
        if not dt_str or len(dt_str) < 10:
            return False
        d10 = dt_str[:10]
        return (not _ds or d10 >= _ds) and (not _de or d10 <= _de)
    _reg_cnt = sum(1 for r in records if _in_window(r.get("register_time")))
    _hdl_cnt = sum(1 for r in records if r.get("finish_time") and _in_window(r.get("finish_time")))
    # 保持顶层 reg_handled 三源结构向后兼容(全窗口三源; 前端明细标题主要用顶层
    # registered/handled, 无需三源细分时无需关心)。为不增加额外 6~10 次 count SQL,
    # 这里 total 直接用上面按行统计值(与 items 一致), 三源键保留默认空(前端不消费)。
    _reg_handled = {"total": {"registered": _reg_cnt, "handled": _hdl_cnt}}
    return R(data={
        "items": records[:limit],
        "total": len(records),
        "reg_handled": _reg_handled,
        "registered": _reg_cnt,
        "handled": _hdl_cnt,
    })

@router.get("/pure-card", response_model=R,
            summary="纯工信部投诉·单列视图(工单+文档合并去重) (#63)")
def complaint_pure_card(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    detail_limit: int = Query(2000, ge=1, le=5000, description="清单返回上限"),
    include_checked: bool = Query(False, description="连已对账的工信部工单一并返回(带 is_pure 标记)"),
):
    """**纯工信部投诉**单列视图（#63）。

    ★ 用户需求（2026-09-11）:
      > 「纯粹工信部的**优先级不高**，有统计记录就好了。回头**单列一页**展示即可。
      >   工单+文档 **合并去重** 统计展示。」

    ★★★ 判据必须用「SN **或** 电话」★★★

    ```
    纯工信部 = 工信部工单 ∧ NOT (卡商表 SN 或 电话 命中)
                        ∧ NOT (315表  SN 或 电话 命中)
    ```

    ## 为什么不能用 SN 精确匹配（用户人工核对后发现的盲区）

    | 现实情况 | 实例 |
    |---|---|
    | 同一客户**多台设备**：工单管 A 机，卡商单登记 B 机 | ① |
    | 同客户**换机**，SN 变了 | ② |
    | **处理意见里提到另一台** SN | ⑥ |

    而 `phone` 三条**全部一致** → **电话才是可靠的关联键**。
    （印证 #55: SN = 客户身份, 但一个客户可能有多台设备。）

    ## 实测（2026-09-11, 全量 320 条工信部工单）

    | 判据 | 命中 |
    |---|---|
    | SN 精确命中 | 314 |
    | **SN 或 电话命中** | **318** |
    | **纯工信部** | **2** |

    用户核对的 6 条候选中，①②⑥ 正是「SN 查不到、电话能查到」
    → 用 SN 精确匹配会**误判为纯工信部**（应归卡商）。

    ## 「合并去重」的含义

    工单侧（经营登记）按 `(SN 或 电话)` 关联文档侧（接待过程）后
    合并为一份视图，同一投诉只计一次；两边都无 = **纯工信部**。

    ⚠️ 本接口**只读** —— 不删不改任何数据；原 `service_item` 保持原样。
    """
    from app.services.pure_card_complaint import pure_card_tickets

    ts = _parse_dt(start)
    te = _parse_dt_end(end)
    data = pure_card_tickets(
        db, ts, te, detail_limit=detail_limit, include_checked=include_checked,
    )
    return R(data=data)

@router.get("/supplementary", response_model=R, summary="投诉补充明细列表(315/卡商,workbuddy推送)")
def complaint_supplementary(
    db: DBSession,
    _: CurrentUser,
    start: str | None = None,
    end: str | None = None,
    source_type: str | None = Query(None, description="external=315外部投诉/card=(外)卡商, 缺省查全部"),
    matched: bool | None = Query(None, description="true=仅已关联工单, false=仅未关联"),
    limit: int = Query(500, ge=1, le=2000),
):
    """查询两张投诉补充表明细(按投诉日期过滤), 支持按来源/关联状态筛选。

    数据由 workbuddy 定期推送, 是对 service_tickets 工单口径的更详细补充。
    关联 = 按 device_sn 匹配到外部投诉类工单(matched_ticket_no 非空)。
    """
    ts = _parse_dt(start)
    te = _parse_dt_end(end)

    def _items(model, sn_field, source_name):
        stmt = select(model).where(True)
        if ts:
            stmt = stmt.where(model.c.complaint_date >= ts)
        if te:
            stmt = stmt.where(model.c.complaint_date <= te)
        if matched is True:
            stmt = stmt.where(model.c.matched_ticket_no.isnot(None))
        elif matched is False:
            stmt = stmt.where(model.c.matched_ticket_no.is_(None))
        rows = db.execute(stmt.order_by(model.c.complaint_date.desc().nullslast())).mappings().all()
        return [
            {
                "source": source_name,
                "complaint_no": getattr(c, "complaint_no", None),
                "complaint_date": _to_sh_iso(c.complaint_date),
                "complaint_group": getattr(c, "complaint_group", None),
                "complainant": c.complainant,
                "complainant_phone": c.complainant_phone,
                "device_sn": getattr(c, sn_field, None),
                "platform": getattr(c, "platform", None),
                "problem": c.problem,
                "problem_category": getattr(c, "problem_category", None) or getattr(c, "complaint_category", None),
                "company_entity": getattr(c, "company_entity", None),
                "solution": c.solution,
                "result": c.result,
                "handler": getattr(c, "handler", None) or getattr(c, "follower", None),
                "handle_detail": getattr(c, "handle_detail", None) or getattr(c, "handle_result", None),
                "handle_time": _to_sh_iso(c.handle_time) if hasattr(c, "handle_time") else None,
                "matched_ticket_no": c.matched_ticket_no,
                "created_at": _to_sh_iso(c.created_at) if hasattr(c, "created_at") else None,
            }
            for c in rows
        ]

    items: list[dict] = []
    if source_type in (None, "external", "315"):
        items.extend(_items(_EC, "device_sn", "315外部投诉"))
    if source_type in (None, "card", "卡商"):
        items.extend(_items(_CC, "device_sn", "(外)卡商"))
    # 按投诉日期降序(主排序), 无日期的排末尾
    items.sort(key=lambda x: (x.get("complaint_date") is None, x.get("complaint_date") or ""), reverse=True)
    # F-P1-2: 补充记录 登记/处理 计数(明细标题展示)
    #   registered = 窗口内投诉日期记录数(= 查询已按 complaint_date 过滤, 即全部 items)
    #   handled    = 已办结记录数: 315 result∈办结类, 或 handle_time 落在窗口内
    _day_start = start[:10] if start else None
    _day_end = end[:10] if end else None

    def _is_handled(it: dict) -> bool:
        if (it.get("result") or "").strip() in _EXTERNAL_DONE_RESULTS:
            return True
        ht = it.get("handle_time")
        if ht and len(ht) >= 10:
            hd = ht[:10]
            if _day_start and hd < _day_start:
                return False
            if _day_end and hd > _day_end:
                return False
            return True
        return False

    _registered = len(items)
    _handled = sum(1 for it in items if _is_handled(it))
    return R(data={
        "items": items[:limit],
        "total": len(items),
        "registered": _registered,
        "handled": _handled,
    })
