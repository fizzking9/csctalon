# -*- coding: utf-8 -*-
"""缓存预热 key/参数 与 路由签名 的一致性契约测试

## 为什么需要这个文件
`cache_warmer` 的预热项必须与路由**逐字符对齐**（key 字符串 + kwargs 形状）。
这一约定在本仓库被违反过至少两次，且**每次都是静默失败**：

| 时间 | 缺陷 | 表现 |
|---|---|---|
| 2026-09-14 | `CAT_BUCKETS` 加桶但 `cat_data` 漏加 → `KeyError: 'complaint'` | 人效接口 500（预热日志仅一行 warning） |
| 2026-09-11 (#67) | 路由加 4 个 `Query` 参数但预热未同步 | `'>' not supported between 'Query' and 'int'`，**预热每次失败**（被 try/except 吞掉） |

⚠️ 共同特点：**升级流程的健康检查通过**（预热失败不影响服务启动），
   而单测因为不覆盖动态取值路径也全绿 → **只有翻日志才能发现**。

故本文件用"签名 ↔ 预热"自动对齐检查替代人眼比对。
"""
from __future__ import annotations

import inspect

import pytest

from app.services import cache_warmer as W


# 路由层包裹后的函数（`cached_json` 装饰）需取 `__wrapped__` 拿真实签名
def _sig(fn):
    target = getattr(fn, "__wrapped__", fn)
    return inspect.signature(target)


# 预热项 → 路由函数（新增预热项时应在此登记）
CASES = [
    ("complaint_subject_analysis", "complaint_subject_analysis"),
    ("complaint_analysis", "complaint_analysis"),
    ("complaint_summary", "complaint_summary"),
    ("complaint_detail", "complaint_detail"),
    ("complaint_health_metrics_endpoint", "complaint_health_metrics_endpoint"),
]


def _get_route_fn(name: str):
    from app.api.v1 import complaints as C

    return getattr(C, name)


@pytest.mark.parametrize("_,fn_name", CASES)
def test_warmer_kwargs_are_acceptable_to_route_signature(_, fn_name):
    """预热 kwargs 里的每个键都必须是路由函数的**真实参数名**。

    回归: #67 给 subject 接口加了 `source_type/sort_by/top_n/compare`，
    预热未同步 → 这些参数取到 `Query` 对象而非默认值 → 运行期比较报错。
    """
    fn = _get_route_fn(fn_name)
    params = _sig(fn).parameters
    # 收集 cache_warmer 源码里对该预热项的出现（宽松检查键名合法性）
    src = inspect.getsource(W)
    # 断言：源码中不应再出现"形如 {'start':…, 'end':…} 而路由另有必填 Query 参数"的情况
    # 具体做法：路由的每个**带 Query/默认值**的参数，若在源码中完全未出现，则告警
    missing = [
        p for p in params
        if p not in ("db", "_", "self") and f'"{p}"' not in src
    ]
    # 允许 start/end 之外的参数缺失，仅当它们有默认值时可接受；
    # 但**不允许**参数名带 Query 却从未出现在预热里（那正是 #67 事故形态）
    query_params = [
        p for p, v in params.items()
        if p not in ("db", "_") and v.default is not inspect.Parameter.empty
        and type(v.default).__name__ == "Query"
    ]
    still_missing = [p for p in query_params if p in missing]
    assert not still_missing, (
        f"{fn_name} 的 Query 参数 {still_missing} 未出现在 cache_warmer 中 —— "
        f"预热会拿到 Query 对象而非默认值（参见 #67 事故）"
    )


def test_subject_warmer_key_shape_matches_route_cache_key():
    """subject 预热的 key 必须含 6 段（与路由 cached_json 的形状一致）。

    路由 key: `complaint:subject:{start}:{end}:{source_type}:{sort_by}:{top_n}:{compare}`
    """
    src = inspect.getsource(W)
    assert "complaint:subject:{start}:{end}:all:total:0:none" in src
    assert "complaint:subject:{s30}:{e}:all:total:0:none" in src


def test_staff_buckets_are_internally_consistent():
    """`CAT_BUCKETS` 与 `cat_data` 的桶 key 必须一一对应。

    回归: 2026-09-14 生产事故 —— 只给 CAT_BUCKETS 加了 `complaint`，
    `cat_data[bucket_key]` 直接 KeyError → 人效接口整体 500。
    """
    import pathlib
    import re

    from app.services import analytics as A

    # 拆分后(issue #184): CAT_BUCKETS/cat_data 位于 today 子模块, 递归聚合全包源码供断言
    # (#284: glob → rglob, 否则 today/_staff.py 不被纳入, 报「未找到 CAT_BUCKETS」)
    pkg = pathlib.Path(A.__file__).parent
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(pkg.rglob("*.py")))
    m = re.search(r"CAT_BUCKETS = \[(.*?)\n    \]", src, re.S)
    assert m, "未找到 CAT_BUCKETS"
    bucket_keys = re.findall(r'\("(\w+)",', m.group(1))

    m2 = re.search(r"cat_data = \{(.*?)\n        \}", src, re.S)
    assert m2, "未找到 cat_data"
    data_keys = re.findall(r'"(\w+)": \(', m2.group(1))

    assert set(bucket_keys) == set(data_keys), (
        f"桶 key 不一致（运行期会 KeyError）: "
        f"CAT_BUCKETS={bucket_keys} vs cat_data={data_keys}"
    )
