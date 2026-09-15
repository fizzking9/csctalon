"""AI 分析助手 - 业务口径卡(Business Glossary) (A-20260904-003 LOCKED)

把"模型必须遵守的统计口径"从《规划设计》/标准里抽取为一条条短句, 随 Prompt 分层注入,
避免把整篇规划文本灌给模型(稀释关键口径/抬高成本/带偏回答)。

每条 key 对应一个口径领域; 问答路由按问句关键词选子集, 未命中则全量。
口径与代码落地处一一对应(_complaint_source_type_case / _sources_reg_handled /
四路定义等), 未来口径变更只改对应 key, 不影响其它层。
一期放代码常量(可 git 版本化); 二期有后台需求再 DB 化。
"""

import re

# 每张卡: key -> 中文口径短句(喂给模型)
GLOSSARY: dict[str, str] = {
    "complaint_sources": (
        "投诉三源分类: 内部投诉=商品投诉/服务投诉; "
        "商户号投诉=微信投诉(独立单列, 绝不并入内部或外部); "
        "外部投诉=外部平台投诉(工信部/12315/消协/市监/12345等) + 315/卡商群补充推送。"
        "分类优先级: 外部平台关键词 > 微信 > 商品/服务投诉 > 其余归外部。"
    ),
    "complaint_reg_handled": (
        "投诉'登记'=窗口内新增(register_time 落在窗口); "
        "'处理'=窗口内真实完成(finish_time 落在窗口 且 finish−register≥3分钟, "
        "含窗口外登记、窗口内才办结的历史单); '积压'=窗口内登记且尚未闭环。"
    ),
    "refund_routes": (
        "退款金额口径=四路经营合计: A线上直退 + B钱包提现 + C闪猫退款 + D手动退款(按创建时间); "
        "E=真实现金流(退款记录报表 refund_record_reports 退款成功, 仅作对账基准, 不并入四路; "
        "2026-09-05 起替代旧 merchant_refunds 快照)。金额单位=元。"
    ),
    "refund_litigation": (
        "涉诉率=涉诉金额 / A路(线上直退)总金额 ×100%; "
        "涉诉=退款 SN 关联投诉工单(外部>商户号>内部 优先级) 或审核备注/备注含投诉标识; 清退/计划退款不算涉诉。"
    ),
    "reception_metric": (
        "接待量=客服处理记录数(cs_record 落窗口); 服务设备数=去重设备SN; "
        "会话数=智能助手接待条数 + 对应专项工单完工条数; 接待个数=SN 跨表去重。"
    ),
    "timezone": (
        "所有统计按北京时间(Asia/Shanghai)窗口; '今天/当日'=北京 00:00 至当前时刻(实时); "
        "环比=前移等长窗口(单日→前一天同时段; 区间→向前平移相同天数), 趋势默认按日。"
        "【对比期语义, 2026-09-11 #66】数据里的 yoy_value/yoy_pct 只覆盖'上一个等长周期'的"
        "汇总值, 不含该周期的明细字段; 因此 yoy 可用于说'环比涨跌', 但不能当作'昨天/上周的"
        "完整数据'来展开陈述。用户要某天或某区间的独立明细时, 必须让系统按该窗口取数"
        "(窗口由问句中的时间表达自动识别, 如'昨天''这两天''上周'), 不要用 yoy 拼凑。"
    ),
    "money_unit": "金额单位一律=元(小数2位), 不要报成分/或加换算。",
    # ── #91 (2026-09-14): 「负责人」有两个体系, 必须区分 ──────────────
    # 为何要单独一张卡：
    #   本仓同时存在 **专项负责人**（业务）与 **行政组长**（管理）两套独立体系。
    #   用户在界面上看到"负责人"时很容易理解成同一个概念，而数据源的差异
    #   会造成看似矛盾的陈述（如某人在专项目录里是 lead、却不是行政组长）。
    #   不给模型这张卡，它会把专项目录里的 lead 直接当成"组长"复述出去。
    "lead_systems": (
        "「负责人」有两套**相互独立**的体系，回答时必须区分，不得混用：\n"
        "① **专项负责人**（业务口径）= reception.by_specialty[].lead，"
        "role_label=「专项负责人」；一个专项一个负责人，管的是「这类业务谁负责」。\n"
        "② **行政组长/主管**（管理口径）= groups 体系（current_leader_id）；"
        "一个行政组一个组长，管的是「这群人日常管理谁负责」。\n"
        "两者可以不同人：同一人可在某专项任 lead 而在行政组仅为成员，反之亦然。\n"
        "因此：不要用「某专项的 lead」推断「他是哪个组的组长」；也不要看到两份"
        "「负责人」不同就认为数据矛盾 —— 它们本就是两个口径。"
        "若要回答「组长是谁」，请以行政组（groups）为准；"
        "若要回答「专项谁负责」，以专项 lead 为准。"
    ),
}

# 问答路由: 关键词 -> 需注入的口径卡子集(未命中任一域则全量注入)
# 注: timezone 卡含"对比期语义"(#66), 故所有涉及时间对比的域都要带上它。
_COMPARE_WORDS = ("对比", "环比", "同比", "昨天", "昨日", "前天", "上一天", "前一天",
                  "上周", "本月", "上月", "这几天", "这两天", "趋势")
# 出现"具体日期"也算时间对比场景(如 "9月1日到9月5日"), 用正则识别, 否则
# 「会话数 9月1日到9月5日」会命中"会话"域而漏掉 timezone 卡(对比期语义)。
_COMPARE_DATE_RE = re.compile(
    r"(\d{4}\s*[-年]\s*\d{1,2}\s*[-月]\s*\d{1,2})"      # 2026-09-10 / 2026年9月10日
    r"|(\d{1,2}\s*月\s*\d{1,2}\s*[日号])"                # 9月10日
    r"|(近|最近|过去|这)\s*\d{1,3}\s*[天日]"              # 近3天
)

GLOSSARY_ROUTE: dict[str, tuple[str, ...]] = {
    # 投诉域
    "投诉": ("complaint_sources", "complaint_reg_handled", "timezone"),
    "内部投诉": ("complaint_sources", "complaint_reg_handled"),
    "商户号": ("complaint_sources", "complaint_reg_handled"),
    "外部投诉": ("complaint_sources", "complaint_reg_handled"),
    "积压": ("complaint_reg_handled", "complaint_sources"),
    "登记": ("complaint_reg_handled",),
    "处理": ("complaint_reg_handled",),
    # 退款域
    "退款": ("refund_routes", "refund_litigation", "money_unit", "timezone"),
    "提现": ("refund_routes", "money_unit"),
    "涉诉": ("refund_litigation", "refund_routes", "money_unit"),
    "退费": ("refund_routes", "money_unit"),
    # 接待域
    "接待": ("reception_metric", "timezone"),
    "会话": ("reception_metric",),
    "咨询": ("reception_metric",),
    # 负责人/组长域 (#91): 问"负责人/组长/谁负责"必须带上双体系说明，
    # 否则模型会把专项 lead 当行政组长复述（用户实测反馈"说法不一致"）
    "负责人": ("lead_systems", "reception_metric"),
    "组长": ("lead_systems",),
    "专项": ("lead_systems", "reception_metric"),
}


# ── #115 (2026-09-15): 未命中泛问的极简兜底口径(替代原全量 8 卡 1287 字符) ──────
# 场景: 问句未命中 GLOSSARY_ROUTE 任一域关键词(如「今天整体怎么样」「帮我看看今天」),
#   此时 feed 是**全量**的(四路退款/三源投诉/接待都在)。但 feed._spec
#   (external._spec_brief) 已覆盖: 登记/处理/积压定义、涉诉率、金额单位、lead 双体系。
#   故兜底只补 _spec **缺失**的四类判据, 不重复、不新增口径、语义不漂移;
#   实测 327 字符(含 yoy_value/SN/12315 等英文术语, 中文正文约 250 字), 较全量
#   1287 字符降 75%。字数让位于判据完整, 不再强压 300。
# 第 4 条恒含【对比期语义】+ yoy_value 字样: 泛问常带"昨天/对比"等词, 必须保留
#   #66 防幻觉约束(不得拿同比汇总值当昨日完整明细), 与原 timezone 卡同义。
_FALLBACK_MINIMAL: tuple[str, ...] = (
    "投诉三源: 内部=商品/服务投诉; 商户号=微信投诉(独立单列,不并入内/外部); "
    "外部=工信部/12315/消协/市监/12345+315/卡商群; "
    "优先级:外部关键词>微信>商品/服务>其余归外部。",
    "退款总额=四路经营合计:A线上直退+B钱包提现+C闪猫退款+D手动退款(按创建时间);"
    "E=真实现金流仅对账、不并入;金额单位=元。",
    "接待量=客服处理记录数(cs_record落窗口);服务设备数=去重SN;"
    "会话数=智能助手接待+专项工单完工条数。",
    "窗口按北京时间。【对比期语义】yoy_value/yoy_pct 仅为「上一等长周期」汇总值、"
    "非完整明细:可讲环比,不得当昨日/上周明细展开;需独立明细按对应窗口另取,"
    "禁止用yoy拼凑。",
)


def glossary_for_question(question: str) -> str:
    """按问句关键词挑选口径卡子集; 未命中任何域则注入极简全域卡(#115)。

    返回拼好的口径说明文本(Prompt 的 Business Context 段)。
    """
    q = question or ""
    picked: list[str] = []
    # 简单关键词匹配: 遍历路由表, 命中即并入(保序去重)
    for kw, keys in GLOSSARY_ROUTE.items():
        if kw.lower() in q.lower():
            for k in keys:
                if k not in picked:
                    picked.append(k)
    if not picked:
        # #115: 泛问兜底不再全量灌 8 卡(1287 字符), 改极简全域卡(327 字符),
        # 补 feed._spec 缺失的三源/四路/接待/yoy 四类判据。第 4 条已含对比期语义,
        # 故无需再走下方的对比词追加 timezone 逻辑, 直接返回。
        return "\n".join(f"- {line}" for line in _FALLBACK_MINIMAL)
    # 对比类问句必须带上 timezone 卡(#66): 否则模型不知道 yoy 不是独立明细,
    # 会拿同比值当"昨天的数据"陈述, 这正是生产里"答昨日无数据/硬凑对比"的成因之一。
    if "timezone" not in picked and (any(w in q for w in _COMPARE_WORDS) or _COMPARE_DATE_RE.search(q)):
        picked.append("timezone")
    lines = [GLOSSARY[k] for k in picked if k in GLOSSARY]
    return "\n".join(f"- {line}" for line in lines)


def glossary_all() -> str:
    """全量口径卡(兜底/调试)。"""
    return "\n".join(f"- {GLOSSARY[k]}" for k in GLOSSARY)
