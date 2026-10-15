# Tasks：`#191 收口` + `#299 收口`

> 依赖 spec.md。**T1 之前需用户对「变更 A 是否放行」明示批准（铁律：未经确认不得修改采集核心）。**

## 阶段 0 · 用户拍板（阻塞门）

- [x] **T0** 用户确认两件事：
  - (a) 放行变更 A（`bms_scheduler.py` 低频分支补 `deadline_hour=now_cn.hour + 1`）；
  - (b) 变更 B 选「实施」还是「降级验收」。 → **批准 spec ⇒ (a) 放行、(b) 实施**

## 阶段 1 · #191 根因修复（变更 A）

- [x] **T1** 在 `backend/app/services/bms_scheduler.py` 低频档分支（L836–842）补
      `deadline_hour=now_cn.hour + 1`，并在该行上方补注释说明「窗口 = 该源配置小时起 1 小时」。
- [x] **T2** 新增/改单测（`backend/tests/test_source_freshness.py` 或 `test_bms_scheduler_t1.py`）：
  - 用例 1：`should_run_low_freq(15, low_freq_hour=15, deadline_hour=16, done=False, busy=False) is True`
  - 用例 2：`should_run_low_freq(15, low_freq_hour=15, deadline_hour=7, done=False, busy=False) is False`（锁根因）
  - 用例 3：源码漂移守卫 —— 断言调用点含 `deadline_hour=now_cn.hour + 1`
- [x] **T3** 本地回归：`backend/.venv/bin/python -m pytest tests/ -q`（全量，期望 0 失败）+ `ruff check`。
      → 全量 EXIT=0；`test_source_freshness.py` 19 passed；ruff 全绿。
- [x] **T4** 提交（commit message 带 `#191`）→ `git push origin main` → 节点标准入口
      `curl -fsSL http://10.147.17.41:3000/tanzk/csctalon/raw/branch/main/deploy/upgrade.sh | sudo bash -s -- --git`。
      → commit `a8b68f6`；已推送 main；发布 `git-20261015081123 → git-20261015090830`。
- [x] **T5** 发布后核验：`VERSION` == 新 release；容器 healthy；日志出现「低频档触发 …15:xx 低频源=['refund_record_report']」。
      → current=`git-20261015090830`、healthy、域名 200、部署树含修复行 L845。
      （「低频档触发」日志需等 10-16 15:00 档，见 T6）
- [ ] **T6** 观察窗口：次日起连续 2 个自然日 15:00 档 `bms_sync_logs(data_type=refund_record_report, trigger_type=scheduled)` 各 1 条且 `total_rows>0`。
- [ ] **T7** 跑核验脚本（scp + docker cp + `--issue 191 --allow-real-db`），期望 **EXIT=0**；若非 0 且原因在上游 → 按工单第 5 条降级并在凭证登记待观察项。
- [ ] **T8** 按工单纪律写 `## 关闭凭证核验 (YYYY-MM-DD)`（逐条对应关闭条件 1–5，给可复现命令/SQL），勾选正文 `[x]`，关闭 `#191`。

## 阶段 2 · #299 收口

- [x] **T9** （仅当 T0(b)=实施）在 `backend/app/services/source_freshness_service.py` 新增
      `check_device_combo_coverage()` 只读巡检 + 阈值告警（初值 5%），并接入现有每小时巡检入口；
      补单测；随阶段 1 一并发布。 → 提交 `a8b68f6`，2 个单测。
- [x] **T10** 写 `## 关闭凭证核验 (YYYY-MM-DD)` for `#299`（comment 3890）。
- [x] **T11** 勾选 `#299` 正文 `[ ]` → 关闭（closed_at 2026-10-15T17:19:29+08:00）。


## 明令不做

- 不改 `should_run_low_freq` 本体与 `:59` abandon 分支（避免扩大采集语义变更面）。
- 不改 E 路 fetch 窗口/tag 逻辑（09-24 实验结论已复核，不重做探针）。
- 不动 `docs/arch/A-20260907-001-*`（LOCKED）——本修复是把实现拉回该文档已锁定的「每天 1 次、窗口 1 小时」语义。
- 不为 #299 新增前端界面（原方案取舍：入口仅调度自动档 + admin API）。
