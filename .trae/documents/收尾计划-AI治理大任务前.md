# 收尾计划：AI 治理大任务前的尾巴清理

## Context

用户即将启动 AI 数据治理框架（#162~#165，四张工单、体量较大）。开工前需要把前面工作留下的尾巴收干净，避免"旧账+新账"混在一起。

用户原话：**"因为后续的任务比较大，前面的工作要及时收一下尾巴，然后持续执行"**。

本轮已确认范围（用户拍板）：
- **只收尾巴，然后转大任务**（不做 C-P1-4 权限矩阵、不做 C-P1-8 容器非 root）
- wallet 备份：**表 + dump 都留到 09-26 到期日统一清**

## 收尾前的现状核查（本轮已完成，均为只读）

| 事项 | 核查结论 |
|---|---|
| wallet 备份 dump 可用性 | ✅ 已验证：`wallet_withdrawals_20260919.sql.gz` 解出 69691 行，其中 `COPY` 数据段 **69455 行**，与 `wallet_withdrawals_bak_20260919` 表行数**完全一致** → 备份可恢复 |
| #161 修复后是否仍有新重复 | ✅ 主表按 `(account_id,op_type,op_time,operator,op_amount) WHERE device_sn IS NULL` 分组重复组 = **0**；部分索引 `uq_wallet_withdrawals_null_sn` 已生效 |
| 同类风险（可空列进唯一键） | ✅ 已扫描生产库 59 个唯一索引（含可空列 19 个），报出 4 处"重复"经**逐组甄别全部为 NULL 组**（PG 对 NULL 不去重是规范行为，非违约）→ **生产无真违约**，无需修复 |
| 生产定时备份（C-P1-2） | ❌ **真实缺口**：root/tanzk crontab 均空、无 systemd timer、`/etc/cron.d` 无 csctalon 项 → **生产当前没有任何自动备份** |
| 备份目录占用 | ⚠️ `/opt/csctalon/backups` = **5.2G**（10 个文件，多为升级时 `upgrade.sh` 自动产生的全量包 760M+/个）；磁盘可用 184G |

## 任务 1：补生产每日定时备份（C-P1-2 核心缺口）

**为什么**：`deploy/backup.sh` 早已写好且支持 `--env-file/--output/--keep`，`deploy/README.md §5.1` 也明确建议配 cron 每日跑 —— 但生产**从未配置**。当前仅靠 `upgrade.sh` 升级时顺带备份，意味着"不升级就没有备份"，且无异地留存。这是审计 C-P1-2「无备份/恢复方案」的实际缺口。

**做法**（不新增脚本，复用既有 `backup.sh`）：

1. 在生产服务器 `/etc/cron.d/csctalon-backup` 落一个 cron 文件（比 `crontab -e` 更可审计、可版本化）：
   ```
   # 每日 03:30 备份数据库+附件+登录态, 保留最近 14 份
   30 3 * * * root /opt/csctalon/current/deploy/backup.sh --env-file /opt/csctalon/.env --output /opt/csctalon/backups --keep 14 >> /var/log/csctalon-backup.log 2>&1
   ```
   - 用 `current` 软链 → 随版本升级自动用最新脚本
   - `--keep 14`（README 推荐值）：每日一份 × 14 天
2. 同步 `deploy/README.md §5.1`：把"建议配置"改为"已配置"的现状说明（一行），并补 `/etc/cron.d/` 方式（原文档只写了 `crontab -e`）。
3. 加 logrotate（可选但推荐）：`/etc/logrotate.d/csctalon-backup` 轮转 `/var/log/csctalon-backup.log`，防日志无限增长。

**验证**：
- `sudo /opt/csctalon/current/deploy/backup.sh --env-file /opt/csctalon/.env --output /opt/csctalon/backups --keep 14` 手动跑一次 → 产出新包且健康
- `sudo -n cat /etc/cron.d/csctalon-backup` 确认落盘
- 检查 `run-parts`/cron 生效：`sudo systemctl status cron` + 次日看 `/var/log/csctalon-backup.log`
- 抽验备份包可恢复：`tar tzf <新包>` 列出 `csctalon.dump / uploads / browser_states / backup.info`

**风险控制**：
- `--keep 14` 会清理超出的 `csctalon-backup-*.tar.gz`（不匹配该前缀的 `pre-org-*.dump`、`wallet_*.sql.gz`、`ai_reports_*.dump` **不受影响**，因 `ls -1t csctalon-backup-*.tar.gz` 只匹配该前缀）→ 需在验证时确认手工 dump 仍在
- 5.2G 现状：新 cron 跑起来后靠 `--keep 14` 自然收敛（每日增量约 770M，14 份约 10G，磁盘 184G 可用，可接受）

## 任务 2：wallet 备份到期清理机制

**用户决定**：表 + dump 都留到 **2026-09-26** 统一清。

**做法**：
- 不在本轮删除任何东西
- 在 `/etc/cron.d/csctalon-backup` 同文件追加一条**一次性到期清理**，或在 `docs/审查行动项登记.md` 登记"09-26 清理"待办
- 推荐：**登记到行动项台账**（C-P1-7 备注补一行），比藏一条 cron 更可见；到期由人确认后删（备份删除是不可逆操作，人工确认更稳）

清理对象（09-26 执行）：
```sql
DROP TABLE IF EXISTS wallet_withdrawals_bak_20260919;   -- 69455 行
```
```bash
sudo rm -f /opt/csctalon/backups/wallet_withdrawals_20260919.sql.gz   # 2.5M
sudo rm -f /opt/csctalon/backups/ai_reports_pre166_20260920.dump      # 195K (#166 清理的备份)
sudo rm -f /opt/csctalon/backups/pre-org-20260914.dump                # 108M (09-14 组织调整, 已过期)
```

## 任务 3：登记同类风险排查结论（闭环 C-P1-7 的"同类排查"尾巴）

**做法**：更新 `docs/审查行动项登记.md` 的 C-P1-7 行，备注补：
- 同类风险已全库扫描（59 个唯一索引 / 19 个含可空列）
- 4 处报警经甄别**全为 NULL 组**（PG 规范允许），**无真违约**
- 扫描脚本方法论（v2 复现索引真实表达式 + 部分条件，修正 v1 裸列名误报）

同时在 `docs/design/AI数据治理框架-20260919.md` 的探测器设计（D1）中，把本次扫描脚本沉淀为 D1 的实现依据（D1 = 唯一索引重复探测器，正好复用 v2 逻辑）。

## 任务 4：清理文档/工单尾巴

- `docs/审查行动项登记.md`：C-P1-7 状态由「待处理」→「✅ 已完成(2026-09-19~20)」，引用 #160/#161
- 确认 #162~#165 工单正文齐备（已建，open），本轮不动

## 不做（明确记录取舍）

| 项 | 为什么不做 |
|---|---|
| C-P1-4 权限矩阵收紧 | 跨后端 7 类端点 + 前端角色控制 + 测试，属独立工作包；用户选择先转大任务 |
| C-P1-8 容器非 root | Playwright/Chromium/Xvfb 非 root 运行需 `--no-sandbox` 与写盘权限验证，风险最高，需单独排期 |
| 删除 wallet 备份 | 未到 09-26 到期日，且删除不可逆 |
| 异地 rsync 备份 | README 有建议但需目标机；本轮先保证"有本地每日备份"，异地另议 |

## 验证清单

1. 手动跑一次 `backup.sh` → 新包生成、`tar tzf` 内容齐全、手工 dump 文件未被误删
2. `/etc/cron.d/csctalon-backup` 落盘且语法正确（`sudo run-parts --test /etc/cron.d` 或等次日日志）
3. `docs/审查行动项登记.md` C-P1-7 已更新为已完成并含同类排查结论
4. 提交（携带 `#161`/`#160` 关联）+ 推送
5. 转 #162 开始治理框架 A（两表 + D1 探测器）

## 关键文件

- [deploy/backup.sh](file:///Users/tanzk/dev/csctalon/deploy/backup.sh) —— 既有备份脚本（复用，不改逻辑）
- [deploy/README.md](file:///Users/tanzk/dev/csctalon/deploy/README.md) —— §5.1 定时备份章节（补 `/etc/cron.d/` 方式 + 现状）
- [docs/审查行动项登记.md](file:///Users/tanzk/dev/csctalon/docs/审查行动项登记.md) —— C-P1-7 状态与备注
- [docs/design/AI数据治理框架-20260919.md](file:///Users/tanzk/dev/csctalon/docs/design/AI数据治理框架-20260919.md) —— D1 探测器实现依据（沉淀扫描脚本）
- `/tmp/scan_dup_v2.py` + `/tmp/triage_dup.py` —— 本轮扫描脚本（D1 参考实现）
