# [ISSUE] 09-07 同域多源登录熔断防复发 + 凭证管理加固

> 类型：治理改进
> 状态：已创建为 Gitea 工单 `tanzk/csctalon#28`
> 工单链接：http://192.168.28.11:3000/tanzk/csctalon/issues/28
> 责任人：后端 / 采集
> 关联事故：`tanzk/csctalon#27`（docs/debug/issue-09-07-bms-login-circuit-incident.md）
> 日期：2026-09-07

## 一、背景

BMS 六源采集连锁失败事故（#27）暴露两类系统缺陷：

1. **熔断粒度缺陷**：同组(base_url 相同)某源配了与组凭证不同的单源独立凭证，该源登录失败后，
   `fetch_all` 组级熔断把「登录类错误」记忆化 → 同组其余本可用组凭证的源被连带跳过。
   即：**单点凭证误填被放大为全组故障**。
2. **凭证管理缺陷**：UI 保存单源凭证时无「与组账号不一致 / 疑似密文」提示，误填无法及时发现；
   且「清空单源凭证以继承组」在 UI 上无法操作（空串=不改语义）。

## 二、根因判断（代码层）

| # | 缺陷 | 位置 |
|---|---|---|
| 1 | `resolve_credentials` 只返回凭证值，不带「来源(own/group)」标记 → 熔断无法区分"本源凭证坏"vs"共享凭证坏" | `source_group_service.py` |
| 2 | `fetch_all` 任意「登录类错误」→ 写 `group_login_failed_err` → 同组无条件跳过 | `bms_fetcher.py` |
| 3 | 保存单源凭证不校验与组账号一致性、不识别粘贴 Fernet 密文 | `system.py` PUT |
| 4 | `update_sync_source_config` 空串=不改 → UI 无法清除误填单源凭证 | `sync_source_config_service.py` |
| 5 | 前端不展示凭证来源状态（独立/继承/失效），误填无感知 | `SyncSettings.vue` |

## 三、设计决策

### 3.1 凭证来源标记（own / group / none）

`resolve_credentials_with_source(db, row) -> (user, pwd, source)`：
- `own`：单源凭证解密有效 → 本源独立使用
- `group`：继承组级凭证（同域共享登录态）
- `none`：无可用凭证
旧 `resolve_credentials` 保持为薄封装（返回前两元），不破坏既有调用与测试。

config 透传 `credential_source` / `credential_is_own` / `group_username`；
列表接口透传 `credential_source` / `own_cred_differs_group`（布尔，不泄露明文）。

### 3.2 熔断细化（核心）

纯函数：
- `is_login_class_error(err_txt, page_crashed)`：登录类错误判定（排除浏览器崩溃）
- `should_circuit_break_group_on_login_error(err_txt, credential_is_own, page_crashed)`：
  - `credential_is_own=False`（组/共享凭证失败）→ `True` 熔断整组（同组同账号必失败）
  - `credential_is_own=True`（本源独立凭证失败）→ `False` 不熔断；仅 `logged_in_ok=False` 让下一源用组凭证重新登录

组循环另加「同组账号不一致」诊断日志（不阻断，暴露隐患）。

### 3.3 保存校验 + 显式清空

- PUT 保存：单源账号与组账号不同 → 返回 warning（不阻断）；识别 `gAAAA` 密文粘贴 → warning；SCRM 源豁免一致性比较（username 语义=client_id）
- 新增 `clear_credentials` 显式清空语义：`update_sync_source_config` 中 username/password key 存在且为空 → 置 NULL（回退继承组）
- 前端：单源编辑弹窗「清空独立凭证」checkbox（仅当用独立凭证且组有凭证）；保存 warning 弹框确认

### 3.4 已知限制（不阻塞，单独评估）

- **own 源登录成功后被同组复用的串号隐患**：同组共享一个 browser context，若 own 源账号有效但权限不同，
  后续同组源会复用其登录态抓取。彻底解决需为独立凭证源拆分独立 context（改动大），
  建议后续单独评估；本次误填场景 own 账号登录即失败，已被熔断细化覆盖。

## 四、实现清单

- [x] `source_group_service.py`：`resolve_credentials_with_source`
- [x] `sync_source_config_service.py`：`_row_effective_source` / `_own_cred_differs_group` / config+list 新字段 / 显式清空
- [x] `bms_fetcher.py`：`is_login_class_error` / `should_circuit_break_group_on_login_error` / fetch_all 熔断细化 / 账号一致性日志 / 登录态守卫
- [x] `system.py`：PUT warning（组不一致/密文/SCRM 豁免）+ `clear_credentials`
- [x] `SyncSettings.vue`：凭证列标签、ownCredWarnVisible 警示、清空 checkbox、保存 warning 弹框
- [x] 测试：`test_bms_circuit_break_refine.py`(+8) / `test_credential_dirty_cipher.py`(+7) / `test_device_profile.py`(+2)

## 五、验收标准 / 关闭条件

- [x] 本地全量 pytest 352 passed；ruff 通过；eslint 0 error；vite build 通过
- [ ] Reviewer 审查报告无 CRITICAL（已初审：无 CRITICAL；WARNING 已处理 3/4，1 条记为已知限制）
- [ ] CI run #316(test.yml) 通过（或本地证据替代 + 说明）
- [ ] 生产部署后实测：手动勾 `device_combo` + `refund_record_report` 同批跑，即使 device_combo 再失败也不连锁
- [ ] 代码提交已关联 #28（61abb07 / 4f4670c）

## 六、部署

- `sudo ./deploy/upgrade.sh --git`（从 gitea 拉最新 main；部署会重启 backend，需无采集任务窗口）
- 部署后回填本工单部署记录与生产实测结果
