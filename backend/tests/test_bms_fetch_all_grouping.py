# -*- coding: utf-8 -*-
"""fetch_all 按 (base_url, username) 分组测试(2026-09-07, #29)

背景: #28 治理遗留 WARNING-3 —— 原 fetch_all 仅按 base_url 分组, 同组共享一个
browser context(登录态)。若同 base_url 内某源配了与组账号不同的单源独立凭证
(own) 且作为组内首源登录成功, 后续用组账号的源会复用其登录态 → 串号(抓错账号
权限下的数据)。

修复(#29): 分组 key 从 base_url 改为 (base_url, username):
  - 同 base_url 同账号 → 同组共享登录态(省重复登录)
  - 同 base_url 不同账号 → 拆到独立组(独立 context/独立登录, 防串号)
  - username=None(未配置凭证) → 独立 (base_url, None) 组
  - ticket / ticket_finish 同账号 → 自然同组(不拆散双维度任务)
"""

from __future__ import annotations

from app.services.bms_fetcher import group_fetch_tasks, should_wait_between_groups


def _task(data_type: str, base_url: str = "https://wifi.cloud-cat.cn/bms",
          username: str | None = "YKM-TZK") -> dict:
    return {
        "data_type": data_type,
        "start": None,
        "end": None,
        "config": {"base_url": base_url, "username": username},
    }


# ── 分组: 同 base_url 同账号合并, 不同账号拆开 ─────────────────────────
def test_same_base_same_user_grouped_together():
    """同 base_url 同账号的多个源 → 归入同一组(共享登录态)。"""
    tasks = [
        _task("refund", username="YKM-TZK"),
        _task("ticket", username="YKM-TZK"),
        _task("wallet", username="YKM-TZK"),
    ]
    groups = group_fetch_tasks(tasks)
    assert len(groups) == 1
    base, user, ts = groups[0]
    assert base == "https://wifi.cloud-cat.cn/bms"
    assert user == "YKM-TZK"
    assert [t["data_type"] for t in ts] == ["refund", "ticket", "wallet"]


def test_same_base_diff_user_split_into_groups():
    """同 base_url 不同账号的源 → 拆到不同组(串号隔离核心)。"""
    tasks = [
        _task("device_combo", username="tanzk"),   # own 独立凭证(误填/特配)
        _task("refund", username="YKM-TZK"),       # 组账号
        _task("wallet", username="YKM-TZK"),       # 组账号
        _task("device_tag", username="tanzk"),     # 另一个 own 源同 tanzk
    ]
    groups = group_fetch_tasks(tasks)
    # 期望 2 组: (wifi, tanzk): device_combo/device_tag; (wifi, YKM-TZK): refund/wallet
    assert len(groups) == 2
    g0_base, g0_user, g0_ts = groups[0]
    g1_base, g1_user, g1_ts = groups[1]
    assert (g0_user, g1_user) == ("tanzk", "YKM-TZK")
    assert [t["data_type"] for t in g0_ts] == ["device_combo", "device_tag"]
    assert [t["data_type"] for t in g1_ts] == ["refund", "wallet"]


def test_ticket_and_ticket_finish_stay_same_group():
    """ticket 与 ticket_finish(双维度)同账号 → 必须同组, 不拆散。"""
    tasks = [
        _task("ticket", username="YKM-TZK"),
        _task("ticket_finish", username="YKM-TZK"),
    ]
    groups = group_fetch_tasks(tasks)
    assert len(groups) == 1
    assert [t["data_type"] for t in groups[0][2]] == ["ticket", "ticket_finish"]


def test_none_credential_source_isolated_group():
    """未配置凭证(username=None)的源 → 独立 (base_url, None) 组, 不乱入有凭证组。"""
    tasks = [
        _task("scrm_customer", username=None),
        _task("refund", username="YKM-TZK"),
    ]
    groups = group_fetch_tasks(tasks)
    assert len(groups) == 2
    assert groups[0][1] is None
    assert groups[1][1] == "YKM-TZK"


def test_different_base_url_separate_groups():
    """不同 base_url(如 wifi vs fast 闪猫)→ 天然不同组(跨域独立登录)。"""
    tasks = [
        _task("refund", base_url="https://wifi.cloud-cat.cn/bms", username="YKM-TZK"),
        _task("shanmao_refund", base_url="https://fast.cloud-cat.cn/sc_brm", username="TZK"),
    ]
    groups = group_fetch_tasks(tasks)
    assert len(groups) == 2
    assert groups[0][0].startswith("https://wifi")
    assert groups[1][0].startswith("https://fast")


def test_order_preserved_across_groups():
    """分组后的组间顺序 = 各 (base,user) 首次出现顺序; 组内顺序 = 任务原顺序。"""
    tasks = [
        _task("refund", username="YKM-TZK"),     # 组 A 首次出现
        _task("device_combo", username="tanzk"),  # 组 B 首次出现
        _task("wallet", username="YKM-TZK"),      # 归组 A(乱序出现也合并回 A)
        _task("device_tag", username="tanzk"),    # 归组 B
    ]
    groups = group_fetch_tasks(tasks)
    assert len(groups) == 2
    # 组间顺序: 先 A(YKM-TZK) 后 B(tanzk), 因为 YKM-TZK 先出现
    assert groups[0][1] == "YKM-TZK"
    assert groups[1][1] == "tanzk"
    # 组内保持原顺序
    assert [t["data_type"] for t in groups[0][2]] == ["refund", "wallet"]
    assert [t["data_type"] for t in groups[1][2]] == ["device_combo", "device_tag"]


# ── 组间冷却判定(should_wait_between_groups) ─────────────────────────
def test_wait_between_groups_same_base_url():
    """同 base_url(同域换账号) → 需等 65s(同一物理 BMS Web 系统 1 分钟限制)。"""
    assert should_wait_between_groups(
        "https://wifi.cloud-cat.cn/bms", "https://wifi.cloud-cat.cn/bms",
    ) is True


def test_wait_between_groups_diff_base_url():
    """不同 base_url(wifi → fast/闪猫)→ 不同 Web 系统, 无需等待。"""
    assert should_wait_between_groups(
        "https://wifi.cloud-cat.cn/bms", "https://fast.cloud-cat.cn/sc_brm",
    ) is False


def test_wait_between_groups_empty_and_real():
    """空 base_url(异常配置)与真实域 → 视为不同, 不等待(不阻塞)。"""
    assert should_wait_between_groups("", "https://wifi.cloud-cat.cn/bms") is False
    assert should_wait_between_groups("https://wifi.cloud-cat.cn/bms", "") is False


def test_empty_string_username_normalized_to_none_group():
    """空串 username(历史脏值) 与 None 归一化 → 归并同组(修复 WARNING: 防拆成两组)。"""
    tasks = [
        _task("scrm_customer", username=""),      # 空串
        _task("scrm_operator", username=None),    # None
    ]
    groups = group_fetch_tasks(tasks)
    assert len(groups) == 1
    assert groups[0][1] is None
    assert [t["data_type"] for t in groups[0][2]] == ["scrm_customer", "scrm_operator"]
