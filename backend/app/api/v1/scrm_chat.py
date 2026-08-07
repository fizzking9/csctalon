"""SCRM 聊天记录独立查询接口 (预留)

- 用户:「聊天记录应该是偶尔需要调用,并不需要全部同步过来查询」
- 策略: 默认不爬不抓; 只有当筛选条件达到最低要求(日期范围 + 至少一个额外条件)或 force_crawl 时,
  才预留后续接入 Playwright 抓取 / 本地 scrm_sessions 表查询的能力。
- 当前实现: 返回 placeholder + skipped=True, 后续接入真实抓取只要改 `_resolve_records` 一个函数即可。
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DBSession
from app.schemas.scrm_chat import (
    ScrmChatQueryParams,
    ScrmChatQueryResponse,
)

router = APIRouter(prefix="/scrm-chat-records", tags=["SCRM 聊天记录(预留)"])

log = logging.getLogger(__name__)


def _resolve_records(
    db: Session,
    q: ScrmChatQueryParams,
) -> ScrmChatQueryResponse:
    """真实查询入口 —— 当前预留,后续在此接入:

    方案 A (网页抓取): 复用 ScrmWebFetcher.fetch_sessions → 填 session_type 下拉 + 客服/客户筛选 → 点查询 → 返回页
    方案 B (本地库): 先查本地 scrm_sessions 表, 本地没命中再按需触发一次增量抓取回写
    """
    t0 = time.perf_counter()
    skipped = True
    reason: str | None = None

    if not q.has_minimum_filter():
        skipped = True
        missing = []
        if not (q.start_date and q.end_date):
            missing.append("start_date/end_date 日期范围")
        if not any([
            q.session_type, q.customer_name, q.customer_phone,
            q.staff_name, q.staff_id, q.person_id, q.session_status,
            q.enterprise_suffix,
        ]):
            missing.append("至少一个额外筛选条件(客服/客户/类型/状态 等)")
        reason = "筛选条件不足(需日期范围 + 至少一个额外筛选,或 force_crawl=True); 已跳过抓取。缺: " + "; ".join(missing)
    else:
        skipped = True
        reason = "预留接口占位 —— 已满足最低筛选,但未接入真实抓取逻辑(后续在 _resolve_records 接入 Playwright 或 本地库)"

    return ScrmChatQueryResponse(
        query=q,
        skipped=skipped,
        skip_reason=reason,
        total=0,
        data_source="placeholder",
        items=[],
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )


@router.get("", response_model=ScrmChatQueryResponse)
def query_scrm_chat_records(
    db: DBSession,
    _user: CurrentUser,
    q: ScrmChatQueryParams = Depends(),
):
    return _resolve_records(db, q)


@router.post("", response_model=ScrmChatQueryResponse)
def query_scrm_chat_records_post(
    q: ScrmChatQueryParams,
    db: DBSession,
    _user: CurrentUser,
):
    return _resolve_records(db, q)
