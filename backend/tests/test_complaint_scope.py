# -*- coding: utf-8 -*-
"""投诉统计口径测试 (#47, 2026-09-10)

覆盖 `services/complaint_scope.py` 的四组能力（**纯函数，不依赖 DB**）：
1. 分桶: 工单 service_item → 内部/商户号/外部/其他（优先级）
2. 去重: 实体级 (SN, 手机) + 时间窗；无键不误合
3. 状态合并: 任一完成即已处理
4. 主体归一: 三源归一 + 卡商内部群过滤

## 业务口径（用户 2026-09-10 澄清）
- 工单"投诉"判据 = `service_category` **空白**（非 ILIKE '%投诉%'）
- 内部（商品/服务）与商户号（微信）**不参与**外部匹配
- 需匹配的只有: 外部投诉（工单平台类 + 315 表）、(外)卡商（card_complaints）
- 去重键 = SN + 手机（**两者都命中**）；窗口 30 天
- 同一笔多渠道 → 算 1 笔，保留明细
- 状态: **任一完成即已处理**（因 79.9% 的 315 记录无处理时间戳）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.complaint_scope import (
    ALL_BUCKETS,
    BUCKET_CARD,
    BUCKET_EXTERNAL,
    BUCKET_INTERNAL,
    BUCKET_MERCHANT,
    BUCKET_OTHER,
    DEDUP_WINDOW_DAYS,
    STATUS_DONE,
    STATUS_PENDING,
    classify_bucket_from_item,
    complaint_dedup_key,
    dedup_rows_by_entity,
    is_internal_group,
    merge_complaint_status,
    normalize_phone,
    normalize_subject,
    resolve_subject,
    status_evidence,
)

_TZ = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════════════════════════
# 1. 分桶
# ═══════════════════════════════════════════════════════════════════
class TestBucket:
    """分桶覆盖实测的全部 service_item 取值（生产数据）"""

    def test_real_values(self):
        """真实取值逐一核对（来自 2026-09-10 生产数据）

        ⚠️ 2026-09-11 (#53): 「其他」桶已移除 —— 四类完备
        （用户: "还有部分 似乎应该归类为 内部投诉"）。
        """
        cases = {
            "商品投诉": BUCKET_INTERNAL,     # 17,148
            "服务投诉": BUCKET_INTERNAL,     # 9,696
            "微信投诉": BUCKET_MERCHANT,     # 4,384
            "工信部投诉": BUCKET_EXTERNAL,   # 320
            "全国12315平台": BUCKET_EXTERNAL,  # 278
            "公共服务平台": BUCKET_EXTERNAL,  # 87
            "消费者协会": BUCKET_EXTERNAL,   # 50
            "套餐扣费申诉": BUCKET_INTERNAL,  # 14  ★#53: 归内部(用户要求)
        }
        for item, exp in cases.items():
            assert classify_bucket_from_item(item) == exp, item

    def test_priority_external_over_merchant(self):
        """★优先级: 外部平台关键词 > 微信

        "12315微信投诉" 应归**外部**（监管渠道更重，既有口径 2026-08-25 固化）。
        """
        assert classify_bucket_from_item("12315微信投诉") == BUCKET_EXTERNAL
        assert classify_bucket_from_item("工信部转微信") == BUCKET_EXTERNAL

    def test_priority_merchant_over_internal(self):
        """微信 > 商品/服务（"微信投诉"不含"商品投诉"字样，此处验证不误归）"""
        assert classify_bucket_from_item("微信投诉") == BUCKET_MERCHANT

    def test_channel_fallback(self):
        """★★ #53 新增: channel 回退 —— 救回 81 行空 item

        实测: item 空共 81 行 → channel=微信投诉 78 行→商户号; channel=H5 3 行→内部。
        """
        # item 为空时, channel 提供兜底
        assert classify_bucket_from_item("", "微信投诉") == BUCKET_MERCHANT
        assert classify_bucket_from_item(None, "微信投诉") == BUCKET_MERCHANT
        assert classify_bucket_from_item("", "H5") == BUCKET_INTERNAL
        assert classify_bucket_from_item(None, "H5") == BUCKET_INTERNAL
        # item 有值时, channel 不覆盖 item
        assert classify_bucket_from_item("工信部投诉", "微信投诉") == BUCKET_EXTERNAL

    def test_no_other_bucket(self):
        """★★ #53: **无「其他」桶** —— else 兜底为内部投诉（四类完备）

        实测空白 category 32,063 行完全可分:
          内部 26,866 + 商户号 4,462 + 外部 735 = 32,063 ✓
        故 `ALL_BUCKETS` 只有三类, 且任何输入都必落到三类之一。
        """
        assert BUCKET_OTHER not in ALL_BUCKETS
        assert len(ALL_BUCKETS) == 3
        # 任何输入都不会产生「其他」
        for item, ch in (("投诉处理", None), ("未知类型", None), (None, None),
                         ("", ""), ("abc", "xyz")):
            got = classify_bucket_from_item(item, ch)
            assert got in ALL_BUCKETS, f"item={item!r} ch={ch!r} → {got} 不在三类内"


# ═══════════════════════════════════════════════════════════════════
# 2. 去重
# ═══════════════════════════════════════════════════════════════════
class TestDedupKey:
    def test_both_required(self):
        """★SN 与手机**两者都必须有值**，任一缺失返回 None（不参与去重）"""
        assert complaint_dedup_key("SN1", "13800138000") == ("SN1", "13800138000")
        assert complaint_dedup_key("SN1", None) is None
        assert complaint_dedup_key(None, "13800138000") is None
        assert complaint_dedup_key(None, None) is None
        assert complaint_dedup_key("", "") is None

    def test_phone_normalized(self):
        """手机归一后才能比对（否则 +86/横线 会漏配）"""
        assert complaint_dedup_key("SN1", "+8613800138000") == ("SN1", "13800138000")
        assert complaint_dedup_key("SN1", "138-0013-8000") == ("SN1", "13800138000")
        assert complaint_dedup_key("SN1", "(138) 0013 8000") == ("SN1", "13800138000")

    def test_sn_stripped(self):
        assert complaint_dedup_key("  SN1  ", "13800138000") == ("SN1", "13800138000")

    def test_window_default_is_30(self):
        """窗口 30 天（用户拍板；实测 87% 的关联对落在 30 天内）"""
        assert DEDUP_WINDOW_DAYS == 30


class TestNormalizePhone:
    def test_variants(self):
        for v in ("+8613800138000", "8613800138000", "138-0013-8000",
                  "(138) 0013 8000", "138 0013 8000"):
            assert normalize_phone(v) == "13800138000", v

    def test_invalid(self):
        for v in (None, "", "abc", "---"):
            assert normalize_phone(v) is None, v

    def test_0086_prefix(self):
        assert normalize_phone("008613800138000") == "13800138000"


class TestDedupRows:
    """实体级去重（模拟"一个投诉分多渠道不同日期"）"""

    def _rows(self):
        base = datetime(2026, 5, 1, tzinfo=_TZ)
        return [
            {"id": 1, "device_sn": "SN1", "complainant_phone": "13800138000",
             "complaint_date": base},                                    # 保留(最早)
            {"id": 2, "device_sn": "SN1", "complainant_phone": "13800138000",
             "complaint_date": base + timedelta(days=3)},                # 窗内 → 合并
            {"id": 3, "device_sn": "SN1", "complainant_phone": "13800138000",
             "complaint_date": base + timedelta(days=60)},               # 窗外 → 新一笔
            {"id": 4, "device_sn": "SN2", "complainant_phone": None,
             "complaint_date": base},                                    # 缺键 → 保留
            {"id": 5, "device_sn": None, "complainant_phone": "13800138000",
             "complaint_date": base},                                    # 缺键 → 保留
        ]

    def test_merge_within_window(self):
        kept, merged = dedup_rows_by_entity(self._rows(), window_days=30)
        assert merged == 1, "仅 id=2 应被合并（3 天内）"
        assert len(kept) == 5, "★保留而非删除：行数不变"

    def test_dedup_of_recorded(self):
        """被合并的行必须标 dedup_of（供明细回溯）"""
        kept, _ = dedup_rows_by_entity(self._rows(), window_days=30)
        dup = [r for r in kept if r.get("dedup_of") is not None]
        assert len(dup) == 1
        assert dup[0]["id"] == 2
        assert dup[0]["dedup_of"] == 1

    def test_no_key_never_merged(self):
        """★缺 SN 或手机的行**一律保留**（宁可重复不可误合）"""
        kept, merged = dedup_rows_by_entity(self._rows(), window_days=30)
        for r in kept:
            if r["id"] in (4, 5):
                assert r.get("dedup_of") is None, f"id={r['id']} 不应被合并"

    def test_outside_window_is_new(self):
        """超窗视为新一笔（同 SN 不同年份的多次独立投诉）"""
        kept, merged = dedup_rows_by_entity(self._rows(), window_days=30)
        id3 = next(r for r in kept if r["id"] == 3)
        assert id3.get("dedup_of") is None

    def test_empty_input(self):
        assert dedup_rows_by_entity([]) == ([], 0)

    def test_earliest_kept(self):
        """乱序输入时保留最早的一条"""
        base = datetime(2026, 5, 1, tzinfo=_TZ)
        rows = [
            {"id": 2, "device_sn": "S", "complainant_phone": "138", "complaint_date": base + timedelta(days=1)},
            {"id": 1, "device_sn": "S", "complainant_phone": "138", "complaint_date": base},
        ]
        kept, merged = dedup_rows_by_entity(rows, window_days=30)
        survivor = next(r for r in kept if r.get("dedup_of") is None and r["id"] in (1, 2))
        assert survivor["id"] == 1, "应保留最早的一条"


# ═══════════════════════════════════════════════════════════════════
# 3. 状态合并
# ═══════════════════════════════════════════════════════════════════
class TestStatusMerge:
    def test_any_done_means_done(self):
        """★用户拍板: 任一为完成态即已处理"""
        assert merge_complaint_status("已完成", None, None) == STATUS_DONE
        assert merge_complaint_status(None, "和解结案", None) == STATUS_DONE
        assert merge_complaint_status(None, None, "已退款完成") == STATUS_DONE

    def test_none_done_is_pending(self):
        assert merge_complaint_status("待处理", None, None) == STATUS_PENDING
        assert merge_complaint_status(None, None, None) == STATUS_PENDING
        assert merge_complaint_status("", "", "") == STATUS_PENDING

    def test_pending_but_channel_done(self):
        """★实测场景: 315 已办结 但工单仍"待处理"（224 行）→ 判已处理"""
        assert merge_complaint_status("待处理", "和解结案", None) == STATUS_DONE
        assert merge_complaint_status("处理中", "暂行办结", None) == STATUS_DONE

    def test_evidence_source(self):
        """必须能追溯"已处理"依据来自哪一侧"""
        assert status_evidence("已完成", None, None) == "工单"
        assert status_evidence(None, "和解结案", None) == "315"
        assert status_evidence(None, None, "已解决") == "卡商"
        assert status_evidence("待处理", None, None) is None
        assert status_evidence(None, None, "电话联系不上") is None

    def test_ticket_done_statuses(self):
        """工单完成态全集 —— 对齐 `complaints._BACKLOG_DONE_SET`(单一来源)"""
        for s in ("已结案", "已完成", "完成", "已解决", "已回复", "已反馈",
                  "已关闭", "终止", "已终止"):
            assert merge_complaint_status(s, None, None) == STATUS_DONE, s
        # 实测 status 只有 5 种取值: 已完成 190428 / 处理中 41466 / 待处理 23584
        #                              / 终止 9966 / None 5
        for s in ("处理中", "待处理"):
            assert merge_complaint_status(s, None, None) == STATUS_PENDING, s

    def test_external_done_results(self):
        """★315 办结类 —— 必须含 `未达成一致方案`/`暂行办理`

        这两项在 `ingest_validator._EXTERNAL_RESULT_SET`(上游合法枚举) 里,
        但既有 `complaints._EXTERNAL_DONE_RESULTS`(9 项) 漏了它们 → 实测
        done 判定 2,460(漏) vs 2,558(正确), 差 98 行。
        """
        for r in ("和解结案", "撤销结案", "暂行办结", "调解结案", "调节结案",
                  "投诉无效", "重复投诉", "终止调节", "退回消委会",
                  "未达成一致方案", "暂行办理"):
            assert merge_complaint_status(None, r, None) == STATUS_DONE, r

    def test_external_non_done(self):
        """315 非办结态: 需跟进(16) / 不予受理(4) / 空(77) —— 实测全量仅这几种非办结"""
        for r in ("需跟进", "不予受理", None, ""):
            assert merge_complaint_status(None, r, None) == STATUS_PENDING, r

    def test_card_done_by_prefix(self):
        """★卡商 `result` 是**混合语义**字段 → 用前缀锚定(非精确匹配/非子串包含)

        实测 873 行 / 117 种取值, 短枚举与自由文本诉求混杂。
        前缀锚定覆盖 `已退余额`/`已短信告知`/`已全额退款` 等 30+ 种变体
        (精确 `in_()` 会漏掉它们)。
        """
        for r in ("已解决", "已退款完成", "已电话告知", "电话加V沟通解决",
                  "已退余额", "已短信告知", "已全额退款", "退款赔偿",
                  "退货退款", "已注销实名解绑", "已补回丢失金额", "补偿30天100G流量",
                  "已退款319元", "退了319", "已联系店铺同意退货退款"):
            assert merge_complaint_status(None, None, r) == STATUS_DONE, r

    def test_card_substring_false_positive(self):
        """★★实测反例: 自由文本里含'退款'二字但**实际未解决**

        `赔偿损失，订单号：... 但设备费用商家拒绝了退货退款申请`
        → 子串包含会把这条判成已解决(错误); 前缀锚定不会(正确)。
        """
        for r in ("赔偿损失，订单号：259 和顾客联系，但设备费用商家拒绝了退货退款申请",
                  "换套餐和退款及补偿流量都 不同意",
                  "跟商家多次沟通无果",
                  "退套餐，退设备，不接受",
                  "自动续费的套餐已退回消费者设备余额"):
            assert merge_complaint_status(None, None, r) == STATUS_PENDING, r

    def test_card_process_action_not_done(self):
        """★过程动作不算办结（我最初版本把它们误判为已处理, 已修正）

        `已上传凭证`(6) / `已提交服告`(6) / `已报交报告说明`(3) 只是提交/上报,
        `已解释说明`(1) 只是解释 —— 都**未**解决。
        `换货`(5) / `换设备`(1) 是消费者诉求, 非处理结果。
        """
        for r in ("已上传凭证", "已提交服告", "已报交报告说明", "已解释说明",
                  "换货", "换设备", "退套餐，退设备"):
            assert merge_complaint_status(None, None, r) == STATUS_PENDING, r

    def test_card_non_done_values(self):
        """卡商真实非办结取值"""
        for r in ("需跟进", "电话联系不上", "投诉错误", "未达成一致意见",
                  "恶意投诉", "流量虚", "按月均扣费"):
            assert merge_complaint_status(None, None, r) == STATUS_PENDING, r

    def test_card_boundary_notified_no_reply(self):
        """◐ 口径边界（待用户拍板）: `已短信告知，未回复`

        客服已输出处理结论(与 `已短信告知` 同族) → 暂判**已处理**才自洽；
        但也可解读为"客户未回应 = 未闭环"。
        ⚠️ 实测仅 1 行, 两种口径差异可忽略; 此处记录选择依据, 便于事后调整。
        """
        assert merge_complaint_status(None, None, "已短信告知") == STATUS_DONE
        assert merge_complaint_status(None, None, "已短信告知，未回复") == STATUS_DONE

    def test_card_result_state_three_way(self):
        """★卡商需保留**三态**（未标注独立于未处理）

        否则区分不了"上游没填"与"真的没处理", 积压数会虚高且掩盖字段质量问题。
        """
        from app.services.complaint_scope import card_result_state
        assert card_result_state(None) == "未标注"
        assert card_result_state("") == "未标注"
        assert card_result_state("   ") == "未标注"
        assert card_result_state("已退款完成") == "已处理"
        assert card_result_state("电话联系不上") == "未处理"


# ═══════════════════════════════════════════════════════════════════
# 4. 主体归一
# ═══════════════════════════════════════════════════════════════════
class TestSubject:
    def test_external_prefers_company_entity(self):
        assert resolve_subject(
            source_bucket=BUCKET_EXTERNAL,
            company_entity="云客猫", complaint_shop="淘系智锐度") == "云客猫"

    def test_external_fallback_to_shop(self):
        """company_entity 为空时回退 complaint_shop（实测回退后覆盖 1353/2655 = 51.0%）"""
        assert resolve_subject(
            source_bucket=BUCKET_EXTERNAL, complaint_shop="淘系智锐度") == "淘系智锐度"

    def test_external_shop_placeholder_skipped(self):
        """★占位 `/` 不能当主体（实测 540 行 complaint_shop='/'）"""
        assert resolve_subject(
            source_bucket=BUCKET_EXTERNAL, company_entity="/", complaint_shop="/") is None
        assert resolve_subject(
            source_bucket=BUCKET_EXTERNAL, complaint_shop="/") is None

    def test_card_uses_group(self):
        assert resolve_subject(
            source_bucket=BUCKET_CARD, complaint_group="云骏售后") == "云骏售后"

    def test_card_internal_group_filtered(self):
        """★卡商内部群 → 未标注（非被投诉主体）

        实测: 投诉处理内部群(149)、航城投诉处理内部群(20)
        """
        for g in ("投诉处理内部群", "航城投诉处理内部群"):
            assert resolve_subject(source_bucket=BUCKET_CARD, complaint_group=g) is None, g

    def test_card_supervisor_channel(self):
        """★监督/受理渠道 → 独立分类（用户拍板 2026-09-10）

        实测: 消费者委员会(23)、市监局投诉群(2)、监局投诉处理群(1)
        这些是投诉被**受理**的路径, 不是被投诉对象。
        用户选择: 归入独立的"监督/受理渠道"分类 —— 既不混入被投诉主体排行,
        又保留"经消委会转办"这一事实(不静默丢弃)。
        """
        from app.services.complaint_scope import SUBJECT_SUPERVISOR
        for g in ("消费者委员会", "消费者委员会(私)", "市监局投诉群", "监局投诉处理群"):
            assert resolve_subject(source_bucket=BUCKET_CARD, complaint_group=g) == SUBJECT_SUPERVISOR, g
        assert SUBJECT_SUPERVISOR == "监督/受理渠道"

    def test_card_real_group_kept(self):
        """★反例防护: 真实卡商的群名**不能**被误杀

        `云骏售后群`/`橙果群`/`亿联群`/`日海通群` 含'群'字, 但确实是卡商主体
        → 不能用'含群字即噪声'的粗暴规则(我最初版本会误杀这些)。
        """
        for g, exp in (("云骏售后", "云骏售后"), ("云骏售后群", "云骏售后群"),
                       ("橙果群", "橙果群"), ("亿联群", "亿联群"),
                       ("小皮丘", "小皮丘")):
            assert resolve_subject(source_bucket=BUCKET_CARD, complaint_group=g) == exp, g

    def test_is_internal_group(self):
        assert is_internal_group("投诉处理内部群") is True
        assert is_internal_group("航城投诉处理内部群") is True
        assert is_internal_group("消费者委员会") is False, "消委会属监督渠道, 非内部群"
        assert is_internal_group("云骏售后") is False
        assert is_internal_group("云骏售后群") is False
        assert is_internal_group(None) is False

    def test_is_supervisor_channel(self):
        from app.services.complaint_scope import is_supervisor_channel
        assert is_supervisor_channel("消费者委员会") is True
        assert is_supervisor_channel("市监局投诉群") is True
        assert is_supervisor_channel("监局投诉处理群") is True
        assert is_supervisor_channel("云骏售后") is False
        assert is_supervisor_channel("投诉处理内部群") is False
        assert is_supervisor_channel(None) is False

    def test_normalize_strips_parenthesis(self):
        """主体名去括号补充说明（实测: 金斗云(天津德润) / 小皮丘&深圳联通）"""
        assert normalize_subject("金斗云(天津德润)") == "金斗云"
        assert normalize_subject("小皮丘&深圳联通") == "小皮丘&深圳联通"
        assert normalize_subject("  云客猫  ") == "云客猫"
        assert normalize_subject(None) is None
        assert normalize_subject("  ") is None

    def test_normalize_placeholder(self):
        """★占位空值必须归一为 None

        实测 315 `complaint_shop` 有 540 行是 `/`、`company_entity` 有 3 行是 `/`
        → 否则会出现"被投诉主体 = /"的脏排行。
        """
        for v in ("/", "-", "--", "无", "N/A", "null", "None"):
            assert normalize_subject(v) is None, v

    def test_ticket_submit_params_parsing(self):
        """工单侧从 submit_params 提取 complaintCompany（非标准 JSON，用正则）"""
        raw = ("{mobile: 1XXXXXXXXXX, remarks: 市民反映..., complaintNo: 08260718, "
               "complaintClass: 退费规则不认可, complaintCompany: 云客猫}")
        assert resolve_subject(source_bucket=BUCKET_EXTERNAL, submit_params=raw) is None
        # 注: 外部桶优先取 company_entity；工单侧主体走 ticket 桶语义
        # 这里直接验证解析函数行为
        from app.services.complaint_scope import _parse_complaint_company_from_submit_params as P
        assert P(raw) == "云客猫"
        assert P(None) is None
        assert P("{}") is None

    def test_verified_consistency_with_315(self):
        """★已验证: 工单 complaintCompany = 315 company_entity（样本均为 云客猫）"""
        from app.services.complaint_scope import _parse_complaint_company_from_submit_params as P
        raw = "{mobile: 1, remarks: x, complaintCompany: 云客猫}"
        assert P(raw) == "云客猫"
        assert resolve_subject(source_bucket=BUCKET_EXTERNAL, company_entity="云客猫") == "云客猫"


# ═══════════════════════════════════════════════════════════════════
# 5. 就近选代表工单 (#47 修复错配)
# ═══════════════════════════════════════════════════════════════════
class TestPickNearestTicket:
    """★原实现取 `candidates[0]`(最早登记), 与投诉日期无关 → 同 SN 多工单时错配

    实测: 多工单 SN 的关联平均差 21 天(旧) → 4 天(新), >30 天错配 4 条 → 0 条。
    """

    def test_picks_nearest(self):
        from datetime import datetime as D

        from app.services.complaint_sync import _pick_nearest_ticket
        cands = [
            ("GD_OLD", D(2026, 1, 1)),    # 187 天前
            ("GD_NEAR", D(2026, 7, 6)),   # 13 天前 ← 应选
            ("GD_FAR", D(2026, 12, 1)),
        ]
        tno, diff = _pick_nearest_ticket(cands, D(2026, 7, 19))
        assert tno == "GD_NEAR"
        assert diff == 13

    def test_exact_match(self):
        from datetime import datetime as D

        from app.services.complaint_sync import _pick_nearest_ticket
        cands = [("A", D(2026, 1, 1)), ("B", D(2026, 5, 5)), ("C", D(2026, 9, 9))]
        tno, diff = _pick_nearest_ticket(cands, D(2026, 5, 5))
        assert tno == "B"
        assert diff == 0

    def test_no_ref_date_falls_back_to_earliest(self):
        """无投诉日期 → 退化取最早(与原行为一致, 保证结果稳定)"""
        from datetime import datetime as D

        from app.services.complaint_sync import _pick_nearest_ticket
        cands = [("FIRST", D(2026, 1, 1)), ("LATER", D(2026, 6, 1))]
        tno, diff = _pick_nearest_ticket(cands, None)
        assert tno == "FIRST"
        assert diff is None

    def test_empty_candidates(self):
        from app.services.complaint_sync import _pick_nearest_ticket
        assert _pick_nearest_ticket(None, None) == (None, None)
        assert _pick_nearest_ticket([], None) == (None, None)

    def test_tie_prefers_earlier(self):
        """同距时取更早登记(保证可复现, 不随机)"""
        from datetime import datetime as D

        from app.services.complaint_sync import _pick_nearest_ticket
        cands = [("EARLY", D(2026, 5, 1)), ("LATE", D(2026, 5, 7))]
        tno, diff = _pick_nearest_ticket(cands, D(2026, 5, 4))  # 两侧都是 3 天
        assert tno == "EARLY"
        assert diff == 3

    def test_all_none_register_time(self):
        from datetime import datetime as D

        from app.services.complaint_sync import _pick_nearest_ticket
        cands = [("A", None), ("B", D(2026, 5, 5))]
        tno, diff = _pick_nearest_ticket(cands, D(2026, 5, 5))
        assert tno == "B"
        assert diff == 0

    def test_naive_and_aware_mixed(self):
        """naive(库内) 与 aware(参数) 混用不应抛错"""
        from datetime import datetime as D
        from datetime import timedelta, timezone

        from app.services.complaint_sync import _pick_nearest_ticket
        cands = [("A", D(2026, 1, 1))]
        tno, diff = _pick_nearest_ticket(
            cands, D(2026, 1, 3, tzinfo=timezone(timedelta(hours=8))))
        assert tno == "A"
        assert diff == 2
