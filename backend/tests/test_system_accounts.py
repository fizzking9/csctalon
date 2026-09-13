"""#78 AI 接待口径分层 —— 系统账户识别 + 来源分类器单元测试

覆盖工单 #78「验收标准」中可单测的部分：
  1. 系统/AI 账户识别（含 WeChat 误判回归 —— 这是本工单的根因）
  2. cs_records 四类来源分类（对应用户描述的 6 类 AI 应用场景）
  3. service_tickets 第二套 AI 分类
  4. 真实人名不被误伤（回归保护）

架构: docs/arch/A-20260913-001-AI接待口径分层.md
"""

import pytest

from app.services.system_accounts import (
    SourceKind,
    classify_cs_record,
    classify_ticket,
    is_non_person_word,
    is_system_account,
    is_virtual_name,
)


class TestVirtualNameRegression:
    """★ 核心回归：`WeChat` 曾被 `_EMPLOYEE_ID_PATTERN` 判成「员工工号」→ 真人。

    后果（生产实测 2026-09-13）：WeChat 在人效榜 rank=1、is_virtual=false、
    work_score 2764.6。本组用例锁死该行为，防止回退。
    """

    @pytest.mark.parametrize("name", ["WeChat", "wechat", "WECHAT"])
    def test_wechat_is_virtual(self, name):
        """WeChat 必须判为虚拟账户（AI 全自动接待账户）。"""
        assert is_virtual_name(name) is True

    @pytest.mark.parametrize("name", ["SYS", "sys", "System", "system"])
    def test_sys_variants_are_virtual(self, name):
        """系统账户 4 种大小写写法必须全判虚拟（PG NOT IN 大小写敏感，需全列）。"""
        assert is_virtual_name(name) is True

    @pytest.mark.parametrize("name", ["客户", "智能客服", "AI客服", "机器人客服", "系统", "班牛"])
    def test_ai_and_system_accounts_are_virtual(self, name):
        assert is_virtual_name(name) is True

    @pytest.mark.parametrize("name", ["马微", "卢宁", "刘艺洋", "覃克泳", "邵晨雨", "谢松志"])
    def test_real_names_are_not_virtual(self, name):
        """中文人名（2-4 字）必须判真人 —— 防止过度排除伤及真实客服。"""
        assert is_virtual_name(name) is False

    @pytest.mark.parametrize("name", ["YKM-LN", "YKM-LJF", "hqq", "ZHY", "DML"])
    def test_employee_ids_are_real(self, name):
        """员工工号/拼音缩写是真人 —— 2026-08-11 曾误判为虚拟，此处锁死。"""
        assert is_virtual_name(name) is False

    def test_person_id_wins(self):
        """已通过 PersonAlias 归并到 Person 的，一律真人（即使名字带前缀）。"""
        assert is_virtual_name("财务-徐依婷", person_id=42) is False
        assert is_virtual_name("生产-刘思敏", person_id=43) is False

    def test_non_person_word_rejects_before_employee_id(self):
        """词干否决必须在「工号格式」判定之前 —— 否则 WeChat 会命中工号正则。"""
        assert is_non_person_word("WeChat") is True
        assert is_non_person_word("马微") is False


class TestSystemAccountExact:
    def test_empty_treated_as_non_human(self):
        """空值不是人（无操作人的记录一律不计真人）。"""
        assert is_system_account(None) is True
        assert is_system_account("") is True

    def test_case_insensitive(self):
        assert is_system_account("wechat") is True
        assert is_system_account("WECHAT") is True

    def test_real_name_not_system(self):
        assert is_system_account("企微-马微") is False


class TestClassifyCsRecord:
    """cs_records 四类来源分类（对应工单 #78 用户描述的 6 类场景）。"""

    def test_scene3_ai_auto(self):
        """场景 3: AI 识别需求后全自动响应，全程无人工。"""
        assert classify_cs_record("WeChat", "WeChat操作网络刷新") is SourceKind.AI_AUTO
        assert classify_cs_record("WeChat", "系统标记用户接受刷新网络方案") is SourceKind.AI_AUTO

    def test_scene1_human_send_script(self):
        """场景 1: 人工客服在 AI 辅助下生成话术，由人工点击发送。"""
        assert classify_cs_record("企微-刘艺洋", "企微-刘艺洋发送刷新网络说明") is SourceKind.HUMAN

    def test_scene2_human_uses_ai_tool(self):
        """场景 2: 人工客服识别需求后点 AI 智能助手做网络切换。"""
        assert classify_cs_record("企微-马微", "企微-马微操作网络刷新") is SourceKind.HUMAN

    def test_scene4_handover_counts_as_human(self):
        """场景 4: AI 识别后转人工操作 —— 本期**不做细分**，一律归真人。

        取舍理由见工单 #78: 平台无稳定标记列，BMS/智能客服仍在迭代，现在细分会返工。
        """
        assert classify_cs_record("企微-何琴", "企微-何琴操作设备限速") is SourceKind.HUMAN

    def test_scene5_customer_self_service(self):
        """场景 5: 客户在公众号手动操作（operator 为空，description 带 openid）。"""
        assert classify_cs_record(
            "", "olAFl6712SMJJHxyDyhCJqTVAE54操作设备限速"
        ) is SourceKind.CUSTOMER_SELF
        assert classify_cs_record(
            None, "ojb7F2PoSe4ydnxsHo-KOiRgVEDY操作网络刷新"
        ) is SourceKind.CUSTOMER_SELF

    def test_system_auto_marker(self):
        """无操作人的「系统标记」/「系统执行」= 系统内部自动化。"""
        assert classify_cs_record("", "系统标记用户接受刷新网络方案") is SourceKind.AI_SYSTEM
        assert classify_cs_record(None, "系统执行网络刷新失败") is SourceKind.AI_SYSTEM

    def test_null_safety(self):
        """NULL / 空串必须安全（历史数据两种都存在）。"""
        assert classify_cs_record(None, None) is SourceKind.AI_SYSTEM
        assert classify_cs_record("", "") is SourceKind.AI_SYSTEM

    def test_human_takes_priority_over_system_detection(self):
        """有操作人的真人不能被系统账户判定吞掉（判据顺序敏感）。"""
        assert classify_cs_record("企微-荣海伦", "系统标记用户接受刷新网络方案") is SourceKind.HUMAN


class TestClassifyTicket:
    """service_tickets 侧（第二套 AI：BMS 客服系统）—— 与 cs_records 共用枚举。"""

    def test_ai_registrant(self):
        assert classify_ticket("AI客服", None) is SourceKind.AI_AUTO

    def test_ai_handler(self):
        assert classify_ticket(None, "AI客服") is SourceKind.AI_AUTO

    def test_sys_handler(self):
        assert classify_ticket(None, "SYS") is SourceKind.AI_SYSTEM

    def test_customer_registrant(self):
        assert classify_ticket("客户", None) is SourceKind.CUSTOMER_SELF

    def test_real_handler(self):
        assert classify_ticket(None, "企微-马微") is SourceKind.HUMAN

    def test_all_empty(self):
        assert classify_ticket(None, "") is SourceKind.AI_SYSTEM


class TestSourceKindEnum:
    def test_enum_contract(self):
        """枚举值是与前端约定的契约（前端 SOURCE_KINDS 按此配色），不可随意改名。"""
        assert [k.value for k in SourceKind] == ["human", "ai_auto", "ai_system", "customer_self"]
