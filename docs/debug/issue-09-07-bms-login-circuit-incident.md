# [ISSUE] 09-07 BMS 六源采集连锁登录失败 — 止损闭环

> 类型：事故处理 / 止损闭环
> 状态：已创建为 Gitea 工单 `tanzk/csctalon#27`
> 工单链接：http://192.168.28.11:3000/tanzk/csctalon/issues/27
> 责任人：运维 / 数据负责人
> 关联治理：`tanzk/csctalon#28`（docs/debug/issue-09-07-bms-login-circuit-governance.md）
> 日期：2026-09-07

## 一、背景

2026-09-07 13:34 手动采集 BMS 域 6 源全部失败：
- `device_combo`(设备套餐) 报「登录失败: 用户名不存在或密码错误!」（5 秒即失败）
- 其余 5 源（设备标签打标 / 退款记录报表 / 订单管理 / 退货跟踪 / 设备管理）报「[同域登录熔断复用]」被跳过
- 仅独立域名 `shanmao_refund`(闪猫, fast.cloud-cat.cn) 成功(1/0)

同期 12:00/13:00 定时任务 `refund/ticket/wallet`（同 BMS 域、同账号）均正常 → 凭证本身未整体失效。

## 二、影响范围

- 时间窗口：2026-09-07 13:34 ~ 数据修复完成(14:20)
- 影响对象：BMS 域 6 个非定时源（`default_sync=false`，仅手动采集触发）
- 数据影响：上述 6 源当日窗口未采集，需凭证修复后补采
- 定时任务不受影响

## 三、根因判断（事实与证据）

1. **直接根因**：`device_combo` 单源凭证被误填成本系统登录账号 `tanzk`（非 BMS 平台账号）。
   生产库核对：`device_combo.username` 解密明文 = `tanzk`；组凭证(`bms-wifi`) = `YKM-TZK`。
2. **凭证解析放大**：`resolve_credentials` 单源凭证优先于组凭证 → `tanzk` 覆盖 `YKM-TZK`。
3. **熔断连坐**：`bms_fetcher.fetch_all` 将任意「登录类错误」写入组级熔断记忆 `group_login_failed_err`
   → 同组其余 5 个本可用 `YKM-TZK` 的源被连带跳过。
4. **误填来源**：2026-09-06 23:01 UI 批量配置凭证时误填（operation_log 100925-100931）。
5. **为何此前未暴露**：`device_combo` `default_sync=false` 不进定时任务，手动勾选才触发且排序靠前。

## 四、处理动作（止损，已完成）

- [x] 2026-09-07 14:20 清空 `device_combo` / `device_tag` 单源凭证（置 NULL）→ 走组凭证 `YKM-TZK`
  - `UPDATE sync_source_configs SET username=NULL,password=NULL WHERE data_type IN ('device_combo','device_tag')`
  - 原密文备份：`/tmp/cred_backup_20260907_142001.txt`
- [x] 复核 BMS 域全部源账号统一为 `YKM-TZK`（单源或继承组）
- [ ] 补采当日窗口数据（凭证修复后手动跑，由治理 #28 部署后统一验证）

## 五、关闭条件

- [ ] 生产 DB：BMS 域无任何源单源凭证为 `tanzk` / 脏密文
- [ ] 手动采集 `device_combo` + `refund_record_report` 不再连锁失败（同批至少 `refund_record_report` 成功）
- [ ] 补采数据入库，当日窗口无缺口

## 六、风险说明

- 本工单属「止损与闭环」，不做生产逻辑变更；
- 系统级修复（熔断按凭证来源区分、凭证管理加固）在治理工单 #28 中另行评审部署。
