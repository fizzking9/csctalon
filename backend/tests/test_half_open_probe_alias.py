"""半开探测「派生别名」映射守护测试 (#94)

## 背景

半开探测（熔断源周期性单片试探）的已知名单在 #31 改为**从 DB 读取**
（`sync_source_configs.default_sync=true`），以对齐 UI 的「纳入周期采集」开关。

但 `ticket_finish` 是 `ticket` 的**派生别名** —— 它由
`bms_sync.sync_all` 在 `data_type == "ticket"` 时**派生**出同批任务
（按「完成时间」补抓，用于回填老工单的 handler），
**不对应 `sync_source_configs` 里的独立配置行**，因此永远不在 DB 名单里。

后果（生产实测 10 次告警）：
```
[调度器] 半开探测: 未知 data_type=ticket_finish, 无法定向试探
```
→ 该源一旦熔断，**半开探测这条恢复路径被堵死**，只能等下一个整点槽位，
恢复延迟从「约 2 小时」拉长到「最长 +1 小时以上」。

## 修法

把它**映射回宿主源** `ticket` 探测 —— 二者抓的是同一份工单数据
（`bms_sync` 导入时 `ticket_finish` 明确按 `ticket` 记账，
见 `gap_queue_service` 的 `if data_type == "ticket_finish"` 分支），
探测 ticket 可用即等价于 ticket_finish 可用。

## 本测试锁定

1. `_PROBE_ALIAS` 必须存在且把 `ticket_finish` 映射到 `ticket`；
2. 映射必须发生在**名单判定之前**（否则仍会走进 else 报"未知"）；
3. 未知源告警必须**可行动**（含修复指引），不能只报"未知"。
"""

from __future__ import annotations

import inspect


def _probe_source() -> str:
    from app.services import bms_scheduler

    return inspect.getsource(bms_scheduler.BmsScheduler._half_open_probe_once)


def test_ticket_finish_is_aliased_to_ticket():
    """`ticket_finish` 必须映射到宿主源 `ticket`。"""
    src = _probe_source()
    assert "_PROBE_ALIAS" in src, "缺少派生别名映射表（#94）"
    assert '"ticket_finish": "ticket"' in src, (
        "ticket_finish 必须映射到 ticket —— 二者抓同一份工单数据（#94）"
    )


def test_alias_mapping_happens_before_known_list_check():
    """映射必须在「是否在名单中」判定**之前**执行。

    回归风险：若先判 `if dt in bms_known` 再映射，`ticket_finish`
    仍会落入 else 分支报「未知」（#94 的原始形态）。
    """
    src = _probe_source()
    idx_alias = src.index("_PROBE_ALIAS.get")
    idx_check = src.index("if dt in bms_known")
    assert idx_alias < idx_check, (
        "别名映射必须早于 `if dt in bms_known` 判定，否则映射无效（#94）"
    )


def test_unknown_source_warning_is_actionable():
    """未知源告警必须给出**修复指引**，不能只说"未知"。"""
    src = _probe_source()
    assert "纳入周期采集" in src, (
        "告警须指引到「同步设置」开关（#94：原文案无法行动）"
    )
    assert "_PROBE_ALIAS" in src.split("else:")[-1] or "派生别名" in src, (
        "告警须提示派生别名应加入映射（#94）"
    )


def test_ticket_finish_is_not_in_db_config_table():
    """佐证：`ticket_finish` 并非 DB 里的独立配置源（这正是需要映射的原因）。

    若将来有人往 sync_source_configs 加了它的行，别名映射会变成冗余
    （仍无害，但本测试会提醒复核设计意图）。
    """
    from app.services.bms_sync import DEFAULT_SYNC_TYPES

    # 种子默认名单里应有 ticket（宿主源）
    assert "ticket" in DEFAULT_SYNC_TYPES, "ticket 应在默认同步名单中（#94）"
    # ticket_finish 是派生别名，不应作为独立种子出现
    assert "ticket_finish" not in DEFAULT_SYNC_TYPES, (
        "ticket_finish 是派生别名，不应作为独立种子源（#94 设计前提）"
    )


def test_gap_queue_maps_ticket_finish_to_ticket():
    """旁证：`gap_queue_service` 已把 ticket_finish 按 ticket 记账。

    这与半开探测的映射方向**一致**，说明「ticket_finish ≈ ticket」是
    系统内既有的统一约定，不是本次临时发明（#94）。
    """
    from app.services import gap_queue_service

    src = inspect.getsource(gap_queue_service)
    assert '"ticket_finish"' in src and '"ticket"' in src, (
        "gap_queue 应有 ticket_finish → ticket 的记账映射（#94 一致性）"
    )
