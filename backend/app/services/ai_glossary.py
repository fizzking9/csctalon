"""AI 分析助手 - 业务口径卡(Business Glossary) (A-20260904-003 LOCKED)

把"模型必须遵守的统计口径"从《规划设计》/标准里抽取为一条条短句, 随 Prompt 分层注入,
避免把整篇规划文本灌给模型(稀释关键口径/抬高成本/带偏回答)。

每条 key 对应一个口径领域; 问答路由按问句关键词选子集, 未命中则全量。
口径与代码落地处一一对应(_complaint_source_type_case / _sources_reg_handled /
四路定义等), 未来口径变更只改对应 key, 不影响其它层。
一期放代码常量(可 git 版本化); 二期有后台需求再 DB 化。
"""

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
        "E=真实现金流(商户号退款 SUCCESS, 仅作对账基准, 不并入四路)。金额单位=元。"
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
        "环比=前移等长窗口; 趋势默认按日。"
    ),
    "money_unit": "金额单位一律=元(小数2位), 不要报成分/或加换算。",
}

# 问答路由: 关键词 -> 需注入的口径卡子集(未命中任一域则全量注入)
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
}


def glossary_for_question(question: str) -> str:
    """按问句关键词挑选口径卡子集; 未命中任何域则全量注入(自由问答兜底)。

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
        picked = list(GLOSSARY.keys())
    lines = [GLOSSARY[k] for k in picked if k in GLOSSARY]
    return "\n".join(f"- {line}" for line in lines)


def glossary_all() -> str:
    """全量口径卡(兜底/调试)。"""
    return "\n".join(f"- {GLOSSARY[k]}" for k in GLOSSARY)
