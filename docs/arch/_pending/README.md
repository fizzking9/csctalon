# `_pending/` 待审核设计文档 — 锁定候选清单

**生成日期**：2026-09-01
**更新**：2026-09-04（`A-20260904-001` 已锁定移出）
**说明**：本目录原存放从 `.trae/documents/` 迁移的 20 份历史设计文档，现**已全部锁定**移入 `docs/arch/`。

> 全部 20 份文档已按 `A-20260902-001` ~ `-020` 编号锁定（标 LOCKED + 打 arch tag）。
> 其中 `export-feature-design`、`refund-dashboard-redesign` 已按实际实现修订状态后锁定。

## ✅ 已全部锁定（2026-09-02，20 份移入 docs/arch/）

见 `docs/arch/` 目录下 `A-20260902-*.md`（20 份）与对应 `arch/A-20260902-00X` tag（20 个）。

## ✅ 新增锁定（2026-09-03）

- `A-20260903-001-issue-automation-gates.md`（工单自动化门禁与 CI 联动设计）已审核通过 → 标 LOCKED v1.0 → 移入 `docs/arch/` → 打 `arch/A-20260903-001` tag。

## ✅ 新增锁定（2026-09-04）

- `A-20260904-001-ingest-health-alert-design.md`（采集数据健康告警与事故自动登记设计，A-20260902-004 的 M5 扩展）已人工终审通过 → 标 LOCKED v1.0 → 移入 `docs/arch/` → 打 `arch/A-20260904-001` tag。关联工单 `#5`。
- `A-20260904-001-ingest-health-alert-design.md` 升级至 **LOCKED v1.2**（判定改"数据到达心跳" + 注册表驱动自动发现）→ 关联工单 `#6`。v1.1 为过渡版本已并入。

> 当前 `_pending/` 目录为空。



