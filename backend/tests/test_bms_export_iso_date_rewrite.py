# -*- coding: utf-8 -*-
"""#335 导出请求 ISO 日期改写测试(2026-10-19)

背景: BMS 后端 2026-10-11 起收紧导出接口日期反序列化, 只收 "yyyy-MM-dd HH:mm:ss";
return_tracking 页面导出把日期选择器 v-model(JS Date)直接 JSON 序列化为
ISO-8601 UTC 串(实证 payload: "2026-10-19T01:41:07.000Z") → BMS 400。
修法: page.route 拦截导出请求, _rewrite_iso_dates_in_json 把 ISO 日期改写为
北京时格式化串后放行。

本测试锁定:
1. ISO-8601(Z / 带偏移 / 带毫秒) → 北京时 "yyyy-MM-dd HH:mm:ss"(含跨日换算)
2. 嵌套 dict/list 递归改写 + 改写处数统计
3. 非 ISO 形态(纯日期/空格分隔/普通字符串/数字/None)一律不改写(防误伤)
4. return_tracking 在改写白名单内(防配置漂移回归)
"""

from __future__ import annotations

from app.services.bms_fetcher import (
    _EXPORT_REWRITE_ISO_DATE_TYPES,
    _rewrite_iso_dates_in_json,
)


def test_iso_z_to_beijing():
    """ISO Z(UTC) → 北京时(+8), 秒级对齐生产实证 payload"""
    out, n = _rewrite_iso_dates_in_json("2026-10-19T01:41:07.000Z")
    assert out == "2026-10-19 09:41:07"
    assert n == 1


def test_iso_z_cross_day():
    """UTC 16:00 = 北京次日 00:00(跨日换算, 对应 10-11 失败样本)"""
    out, n = _rewrite_iso_dates_in_json("2026-10-11T16:00:00.000Z")
    assert out == "2026-10-12 00:00:00"
    assert n == 1


def test_iso_with_offset():
    """带 +08:00 偏移的 ISO 同样改写(不重复加时差)"""
    out, n = _rewrite_iso_dates_in_json("2026-10-19T09:41:07+08:00")
    assert out == "2026-10-19 09:41:07"
    assert n == 1


def test_nested_dict_and_list():
    """生产 payload 形态: 窗口起终点嵌在请求体字段里, 递归改写并计数"""
    body = {
        "startTime": "2026-10-11T16:00:00.000Z",
        "endTime": "2026-10-19T01:41:07.000Z",
        "pageNo": 1,
        "ext": [{"signTime": "2026-10-15T00:00:00Z"}],
    }
    out, n = _rewrite_iso_dates_in_json(body)
    assert out == {
        "startTime": "2026-10-12 00:00:00",
        "endTime": "2026-10-19 09:41:07",
        "pageNo": 1,
        "ext": [{"signTime": "2026-10-15 08:00:00"}],
    }
    assert n == 3


def test_non_iso_untouched():
    """非 ISO 形态一律原样保留, 改写处数=0"""
    body = {
        "plainDate": "2026-10-19",                 # 纯日期不改
        "already": "2026-10-19 09:41:07",          # 已是目标格式不改
        "text": "导出时间2026-10-19T01:41:07.000Z内嵌",  # 长文本内嵌不改(防误伤)
        "num": 20261019,
        "none": None,
        "bool": True,
    }
    out, n = _rewrite_iso_dates_in_json(body)
    assert out == body
    assert n == 0


def test_return_tracking_in_whitelist():
    """return_tracking / refund_record_report 必须在改写白名单(防配置漂移回归)"""
    assert "return_tracking" in _EXPORT_REWRITE_ISO_DATE_TYPES
    assert "refund_record_report" in _EXPORT_REWRITE_ISO_DATE_TYPES  # #336


# ── #336: GET query string 改写(refund_record_report 导出走 GET+query) ──
from urllib.parse import parse_qsl, urlsplit

import pytest

from app.services.bms_fetcher._browser import _rewrite_export_request_iso_dates


class _FakeRequest:
    def __init__(self, method: str, url: str = "", post_data: str | None = None):
        self.method = method
        self.url = url
        self.post_data = post_data


class _FakeRoute:
    def __init__(self):
        self.kwargs = None

    async def continue_(self, **kwargs):
        self.kwargs = kwargs  # 无 kwargs = 原样放行


class _FakeLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


@pytest.mark.asyncio
async def test_get_query_iso_rewritten():
    """生产实证形态: downLoadExcel GET query 带 ISO → 改写为北京时格式"""
    url = (
        "https://wifi.cloud-cat.cn/bms-portal/wxRefundBill/downLoadExcel/v1.01"
        "?page=1&rows=20&sn=&refundStartTime=2026-10-11T16:00:00.000Z"
        "&refundEndTime=2026-10-14T15:59:59.000Z"
    )
    route = _FakeRoute()
    await _rewrite_export_request_iso_dates(route, _FakeRequest("GET", url), _FakeLogger(), "refund_record_report")
    assert route.kwargs is not None and "url" in route.kwargs
    params = dict(parse_qsl(urlsplit(route.kwargs["url"]).query, keep_blank_values=True))
    assert params["refundStartTime"] == "2026-10-12 00:00:00"
    assert params["refundEndTime"] == "2026-10-14 23:59:59"
    assert params["page"] == "1" and params["sn"] == ""  # 其它参数原样(含空值保留)


@pytest.mark.asyncio
async def test_get_query_normal_format_passthrough():
    """GET query 已是 yyyy-MM-dd HH:mm:ss(页面默认形态) → 原样放行"""
    url = (
        "https://wifi.cloud-cat.cn/bms-portal/wxRefundBill/findPageList"
        "?page=1&refundStartTime=2026-10-13+15:20:05&refundEndTime=2026-10-20+15:20:05"
    )
    route = _FakeRoute()
    await _rewrite_export_request_iso_dates(route, _FakeRequest("GET", url), _FakeLogger(), "refund_record_report")
    assert route.kwargs == {}  # continue_() 无参 = 原样


@pytest.mark.asyncio
async def test_post_body_iso_still_rewritten():
    """POST+JSON 路径回归(#335 形态): body 改写, url 不动"""
    import json as _json
    body = _json.dumps({"startTime": "2026-10-11T16:00:00.000Z", "pageNo": 1})
    route = _FakeRoute()
    req = _FakeRequest("POST", "https://wifi.cloud-cat.cn/x/downLoadExcel", post_data=body)
    await _rewrite_export_request_iso_dates(route, req, _FakeLogger(), "return_tracking")
    assert route.kwargs is not None and "post_data" in route.kwargs
    out = _json.loads(route.kwargs["post_data"])
    assert out["startTime"] == "2026-10-12 00:00:00" and out["pageNo"] == 1
