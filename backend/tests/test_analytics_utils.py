# -*- coding: utf-8 -*-
"""分析引擎纯逻辑函数测试(analytics.py)。

覆盖无 DB 依赖的纯函数(手机号清洗/日期范围/失败备注判断/按人归并),
锁定与业务规范一致的关键口径。
"""

from datetime import datetime, timedelta, timezone

from app.services.analytics import (
    _clean_phone,
    _date_range,
    _is_withdraw_fail_remark,
    _merge_person_rows,
)

# ---- _clean_phone: 手机号归一化 ----

def test_clean_phone_normal():
    """正向: 11 位 1 开头的手机号原样返回。"""
    assert _clean_phone("13812345678") == "13812345678"


def test_clean_phone_with_separators():
    """正向: 去除空格/括号/连字符后归一化。"""
    assert _clean_phone("138-1234-5678") == "13812345678"
    assert _clean_phone("(138) 1234 5678") == "13812345678"


def test_clean_phone_strip_country_code():
    """正向: 去掉 +86/86/0086 前缀。"""
    assert _clean_phone("+8613812345678") == "13812345678"
    assert _clean_phone("8613812345678") == "13812345678"
    assert _clean_phone("008613812345678") == "13812345678"


def test_clean_phone_none_empty():
    """边界: None / 空串 / 纯空白 → None。"""
    assert _clean_phone(None) is None
    assert _clean_phone("") is None
    assert _clean_phone("   ") is None


def test_clean_phone_masked():
    """边界: 含掩码(****/XXXX) → None(不进去重)。"""
    assert _clean_phone("138****5678") is None
    assert _clean_phone("138XXXX5678") is None


def test_clean_phone_invalid_length_or_prefix():
    """边界: 长度非 11 或非 1 开头 → None。"""
    assert _clean_phone("23812345678") is None  # 2 开头
    assert _clean_phone("1381234567") is None   # 10 位
    assert _clean_phone("138123456789") is None  # 12 位


# ---- _date_range: 默认时间范围 ----
# ⚠️ 2026-09-06 审查修复(#20): naive 入参按北京时间解释转 UTC aware(消除 API naive
#    窗口偏 8h); aware 入参统一转 UTC。

def test_date_range_both_provided():
    """正向: naive start/end 视为北京时间 → 转 UTC aware。"""
    s, e = datetime(2026, 9, 1), datetime(2026, 9, 30)
    start, end = _date_range(s, e)
    # 北京 9/1 00:00 = UTC 8/31 16:00; 北京 9/30 00:00 = UTC 9/29 16:00
    assert start == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 29, 16, 0, tzinfo=timezone.utc)


def test_date_range_default_end_now():
    """边界: 只给 start(naive 北京), end 默认当前时间。"""
    s = datetime(2026, 9, 1)
    start, end = _date_range(s, None)
    # start 被解释为北京 9/1 0:00 → UTC aware
    assert start == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)
    assert end is not None
    assert end.tzinfo is not None  # aware
    assert end > start


def test_date_range_default_both():
    """边界: 都不给, 默认近 30 天(aware)。"""
    start, end = _date_range(None, None)
    assert end is not None
    assert end.tzinfo is not None
    assert start == end - timedelta(days=30)


# ---- _is_withdraw_fail_remark: 失败/冻结备注判断 ----

def test_withdraw_fail_remark_hits_keywords():
    """正向: 命中失败关键词(冻结/退款失败/驳回等) → True。"""
    assert _is_withdraw_fail_remark("账户冻结") is True
    assert _is_withdraw_fail_remark("退款失败, 余额不足") is True
    assert _is_withdraw_fail_remark("驳回: 超期订单不允许退款") is True


def test_withdraw_fail_remark_plain():
    """边界: 正常提现备注 → False。"""
    assert _is_withdraw_fail_remark("提现成功到账") is False
    assert _is_withdraw_fail_remark("") is False
    assert _is_withdraw_fail_remark(None) is False


# ---- _merge_person_rows: 首页效率同一人多行按 person_id 归并 (F-P1-1, 2026-09-04) ----

def _mk_staff(op, name, pid, reception_count=1, reception_device_count=1,
              sp_internal=1, pp_internal=1, qw_user_id=None, department="企微"):
    """构造 today_staff_efficiency 输出风格的 record(真人员工, 非 virtual)。

    work_score/eff_score 与 breakdown 自洽: 内部投诉桶 系数 work=3.0/eff=5.0。
    """
    return {
        "operator": op, "name": name, "person_id": pid,
        "department": department, "group": "未分组", "position": None,
        "current_specialty": None, "hr_category": "内部投诉", "qw_user_id": qw_user_id,
        "weights": {}, "transfer_penalty": 0.2, "rank": None, "is_virtual": False,
        "reception_count": reception_count, "reception_device_count": reception_device_count,
        "refund_op_count": 0,
        "sp_zixun": 0, "sp_taocan": 0, "sp_recovery": 0, "sp_processing": 0,
        "sp_internal": sp_internal, "sp_external": 0,
        "pp_zixun": 0, "pp_taocan": 0, "pp_recovery": 0, "pp_processing": 0,
        "pp_internal": pp_internal, "pp_external": 0,
        "breakdown": {
            "内部投诉": {"sessions": sp_internal, "people": pp_internal,
                        "weight": 3.0, "coef": 3.0, "is_own": True,
                        "work_contrib": sp_internal * 3.0, "eff_contrib": pp_internal * 5.0},
        },
        "work_score": round(sp_internal * 3.0, 2),
        "eff_score": round(pp_internal * 5.0, 2),
        "_calc_note": {},
    }


def test_merge_person_rows_merges_same_person_id():
    """正向: 同一 person_id 的多行(跨表写法/别名不同) → 合并成一行且数值加总。"""
    rows = [
        _mk_staff("董梦玲", "董梦玲", 1001, reception_count=3, sp_internal=2, pp_internal=1),
        _mk_staff("董梦玲 ", "董梦玲", 1001, reception_count=2, sp_internal=1, pp_internal=2),
    ]
    out = _merge_person_rows(rows)
    assert len(out) == 1
    assert out[0]["name"] == "董梦玲"
    assert out[0]["person_id"] == 1001
    # 数值加总
    assert out[0]["reception_count"] == 5
    assert out[0]["sp_internal"] == 3
    assert out[0]["pp_internal"] == 3
    # work/eff 加总后 round
    assert out[0]["work_score"] == 9.0
    assert out[0]["eff_score"] == 15.0


def test_merge_person_rows_keeps_distinct_person():
    """边界: 不同 person_id 不合并。"""
    rows = [
        _mk_staff("甲", "甲", 1, reception_count=1),
        _mk_staff("乙", "乙", 2, reception_count=1),
    ]
    out = _merge_person_rows(rows)
    assert len(out) == 2


def test_merge_person_rows_fallback_by_name_when_no_pid():
    """边界: person_id 均为 None 时按归一 name 兜底归并(同名未登记场景)。"""
    rows = [
        _mk_staff("董梦玲", "董梦玲", None, reception_count=2, qw_user_id=None),
        _mk_staff("董梦玲", "董梦玲", None, reception_count=3, qw_user_id=None),
    ]
    out = _merge_person_rows(rows)
    assert len(out) == 1
    assert out[0]["reception_count"] == 5


def test_merge_person_rows_attr_prefer_qw():
    """正向: qw_user_id 优先保留; department 占位值(未设置)被真实部门覆盖。"""
    rows = [
        _mk_staff("董梦玲", "董梦玲", 1001, qw_user_id=None, department="未设置"),
        _mk_staff("企微-董梦玲", "董梦玲", 1001, qw_user_id="wx_123", department="企微"),
    ]
    out = _merge_person_rows(rows)
    assert len(out) == 1
    assert out[0]["qw_user_id"] == "wx_123"
    assert out[0]["department"] == "企微"


def test_merge_person_rows_breakdown_coef_weighted_avg():
    """正向: 归并后 breakdown.coef = 加权平均系数(work_contrib/sessions)。"""
    rows = [
        # 内部投诉系数 3.0: 2 次(sp_internal=2 → work=6.0)
        _mk_staff("a", "A", 1, reception_count=2, sp_internal=2, pp_internal=1),
    ]
    # 咨询系数 1.0: 1 次
    r2 = _mk_staff("a2", "A", 1, reception_count=1)
    r2["breakdown"] = {
        "咨询接待": {"sessions": 1, "people": 0, "weight": 1.0, "coef": 1.0, "is_own": False,
                     "work_contrib": 1.0, "eff_contrib": 0.0},
    }
    r2["work_score"] = 1.0
    r2["eff_score"] = 0.0
    out = _merge_person_rows([rows[0], r2])
    assert len(out) == 1
    bb = out[0]["breakdown"]
    # 内部投诉桶 2 次贡献 6.0, 咨询桶 1 次贡献 1.0 → work_score=7.0
    assert out[0]["work_score"] == 7.0
    # 内部投诉 coef = 6.0/2 = 3.0; 咨询 coef = 1.0/1 = 1.0
    assert bb["内部投诉"]["coef"] == 3.0
    assert bb["咨询接待"]["coef"] == 1.0
    assert bb["内部投诉"]["sessions"] == 2
    assert bb["咨询接待"]["sessions"] == 1


def test_merge_person_rows_empty():
    """边界: 空列表原样返回。"""
    assert _merge_person_rows([]) == []


# ---- 问题3 回归(2026-09-04): 商户号投诉(微信)不得并入外部桶 ----
def test_two_bucket_simple_case_removed():
    """两桶简化版(_complaint_source_type_simple_case, 微信归外部)已删除, 防死代码回归。

    唯一调用点(today_reception_breakdown.complaint_by_source)已改三源
    _complaint_source_type_case()。若未来有人再 import 该函数应失败。
    """
    import app.services.analytics as A
    assert not hasattr(A, "_complaint_source_type_simple_case"), \
        "两桶 helper 应已删除(微信并入外部是问题3 要消除的口径)"


def test_three_source_case_keyword_priority():
    """三源 CASE 关键词优先级常量: 微信→商户号桶(独立), 不并入外部。"""
    import app.services.analytics as A
    # 商户号=微信关键词桶(独立于外部)
    assert "微信" in A._MERCHANT_COMPLAINT_KEYWORDS
    # 外部平台词不含"微信"(避免平台词误吞微信)
    assert "微信" not in A._EXTERNAL_PLATFORM_KEYWORDS
    # 平台词优先级在微信之前(12315微信投诉 → 外部, 监管更重, 既有口径)
    assert "12315" in A._EXTERNAL_PLATFORM_KEYWORDS


# ---- 质量排除口径统一 (A-20260909-001 §2.3 / abc-a) ----

def test_quality_exclude_set_is_single_source():
    """★口径单一来源: 排除集合只在此处定义(dup_residual=已治理重复, ignored=人工忽略)"""
    import app.services.analytics as A
    assert set(A._D_OTHER_EXCLUDE_QSTATUS_SET) == {"dup_residual", "ignored"}


def test_excl_sql_matches_exclude_set():
    """★SQL 片段必须与排除集合一一对应(加状态时不得只改一处)"""
    import app.services.analytics as A
    sql = A._EXCL_QSTATUS_SQL
    for st in A._D_OTHER_EXCLUDE_QSTATUS_SET:
        assert f"'{st}'" in sql, f"SQL 排除片段漏了 {st}"
    # 也必须放过 NULL(历史行 quality_status 为空 = 未标记, 属正常业务行)
    assert "COALESCE" in sql, "NULL 行必须被放行, 否则历史数据全被排除"


def test_quality_include_cond_is_list():
    """ORM 侧统一入口返回条件列表(供 where(*cond) 展开)"""
    import app.services.analytics as A
    cond = A._quality_include_cond()
    assert isinstance(cond, list) and len(cond) == 1


def test_quality_exclude_sql_delegates_to_gate():
    """统一入口在"无 quality 列"的旧环境必须返回 None(让调用方跳过条件, 不报错)"""
    import app.services.analytics as A

    class _Dummy:
        pass

    # 打桩: 模拟旧表无 quality_status 列
    orig = A._manual_refunds_has_quality_col
    try:
        A._manual_refunds_has_quality_col = lambda db: False
        assert A._quality_exclude_sql(_Dummy()) is None
        A._manual_refunds_has_quality_col = lambda db: True
        assert A._quality_exclude_sql(_Dummy()) == A._EXCL_QSTATUS_SQL
    finally:
        A._manual_refunds_has_quality_col = orig


def test_no_scattered_gate_expressions():
    """★回归锁: 不得再出现散落的 `X if _manual_refunds_has_quality_col(db) else None` 门控。

    历史问题: 8 处各自内联该表达式, 其中一处误把**字符串常量本身**当布尔标记使用,
    且新增排除状态时需要逐处修改(易漏)。现收敛为 _quality_exclude_sql() 单一入口。

    判定: 用 AST 找出所有「以 _manual_refunds_has_quality_col(...) 为条件的三元表达式」,
    仅允许出现在 _quality_exclude_sql 函数体内。语言级判定, 不受注释/文档字符串干扰。
    """
    import ast
    import inspect

    import app.services.analytics as A

    src = inspect.getsource(A)
    tree = ast.parse(src)

    # 记录唯一入口的函数体行号区间
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_quality_exclude_sql":
            allowed.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.IfExp):
            continue
        cond = node.test
        # 匹配 _manual_refunds_has_quality_col(...) 调用
        calls = [n for n in ast.walk(cond) if isinstance(n, ast.Call)]
        if any(getattr(c.func, "id", None) == "_manual_refunds_has_quality_col" for c in calls):
            if node.lineno not in allowed:
                offenders.append(node.lineno)

    assert not offenders, (
        f"仍有散落门控表达式(行 {offenders}), 请改用 _quality_exclude_sql(db)"
    )
