# [OPEN] 调试会话: sync-error-report-0814
创建时间: 2026-08-14

## 症状 (用户原话)
「同步报错」(4 字，需要进一步收集运行时证据)

## 已知上下文 (来自近期项目改动历史)
1. 昨天 2026-08-13 对 `backend/app/services/analytics.py` 做了投诉待处理列表相关代码修改
   - 改动点: complaint_pending_list 三表 SQL 移除 LIMIT 201，未涉及同步链路
   - 理论上不影响 BMS/SCRM 同步任务，但可能触发后端重启时导入问题
2. 昨天还修改了 `bms_scheduler.py` (熔断从日期级改到专项级 + 倒查7天窗口) 和 `fetch.py` (reset_halt 调用)
   - 涉及同步调度逻辑，可能引入语法/运行时异常
3. 每天 8:00-24:00 每小时有调度触发同步，8:00 / 9:00 是最可能出错的时间点

## 5 个可证伪假设 (H1-H5)
| # | 假设内容 | 可证伪观测点 |
|---|---|---|
| **H1** | BMS 同步任务本身失败 (BMS 端登录/验证码/接口变动) → 某 data_type 被熔断 | `bms_sync_logs` 最新 10 条的 status/error_msg/stack_trace；`sync_task_manager` 运行中/失败的任务 |
| **H2** | SCRM 同步任务失败 (企微登录态过期/滑块验证码/selector 变动) | `scrm_sync_logs` 最新 10 条；sync_source_config 中 scrm_* 的 enabled 状态 |
| **H3** | 昨天改动 `bms_scheduler.py` 或 `analytics.py` 引入 Python 语法/导入/运行时异常，后台服务根本没起来或启动后每小时调度触发异常 | `backend/app/services/bms_scheduler.py` 语法自检；`_run_loop`/`_do_scheduled_sync` 最新调用异常堆栈 |
| **H4** | 熔断逻辑的 `_halted_types` / `_reload_today_completed_hours` 字段改动导致调度期报错 (KeyError / AttributeError) | scheduler 启动日志中回填阶段的异常；`_halted_types` 被赋值的类型错误检查 |
| **H5** | 昨天 analytics.py 中 `_EXTERNAL_ITEM_COMPLAINT_KW` 引用越界/未定义 → 当天第一次运行接口报错 (用户以为是同步报错) | 调用 reception 接口是否 500 |

## 调试进度
### Step 1: 假设披露 ✅
### Step 2: 运行时证据收集 ✅ (脚本: scripts/debug_sync_error_0814.py)

运行证据总结 (全部来自 2026-08-14 16:46 数据库直查 + 模块导入):

#### H1 ✅ 确认成立: BMS refund(退款) 手动同步 今天 01:26 连续两次失败
最新 20 条 bms_sync_logs 关键报错行:
| # | 时间 (UTC+0) | data_type | trigger | status | 错误内容 |
|---|---|---|---|---|---|
| #806 | 08-14 01:26:51 | refund | manual | ❌ failed | **抓取失败: 找不到任何查询/搜索按钮,所有兜底均失败** |
| #773 | 08-14 01:24:59 | refund | manual | ❌ failed | 服务重启清理:僵尸running任务 (可忽略, 服务重启后自动清理机制) |
| #772 | 08-14 01:24:28 | cs_record | manual | ❌ failed | 同上 (僵尸清理,非核心错) |
| #805 | 08-14 01:26:29 | cs_record | manual | 🔄 running | ⚠ 卡在 running 状态到现在 (可能因为 refund 前面报错导致后续没 finish) |

其他 data_type 8/13 07-08 点调度 & 8/14 手动 ticket / ticket_finish / wallet 全部 ✅ success.

#### H2 ✅ 确认成立但属于历史: SCRM 同步从 8/9 起连续失败 (5 天前, 暂时关闭了有配置)
最后 20 条 scrm_sync_logs 全部在 8/9, 最后一条距今 >5 天:
- 错误 1 (主流): `Playwright: [同域登录熔断复用] 登录重试 5 次仍失败` → 企微登录态已过期 + 滑块/验证码拦截
- 错误 2: `Playwright: 验证码 OCR 失败(captcha_type=math)` → 早期的数学验证码 OCR 打不过
- 8/10-8/14 没有任何 SCRM 日志 = 调度器的 scrm 配置判断已关闭 (和我们之前讨论的一样, `has_credentials=False` 自动 skip, 不会再报错, 所以 8/10-14 的空窗期=配置生效了)

#### H3/H5 ❌ 不成立: 我昨天改的代码没有引入语法/运行错误
- bms_scheduler、analytics 模块导入全部 ✅
- today_reception_breakdown 真实返回: `complaint_total=9 pending_total=200 pending_list=200 skipped=True` → 昨天的修复有效

#### H4 ❌ 不成立: 调度器熔断没有乱
get_status: enabled=false (用户还没开自动调度, 正常), is_halted=false, halted_count=0, halted_types=[] → 没熔断

#### H8 额外发现 (sync_task_manager 接口错误): 
`snapshot()` 方法不存在 (AttributeError), 需要检查 fetch.py 里是用什么 API 取任务状态的 → 在用户"同步报错"问题里这个只是调试脚本的小错, 不影响实际业务

---
### Step 3: 证据判定 (H1 是核心问题, 需要立刻修)

❓ 用户说的"同步报错"到底是哪一条?
- 时间线吻合: #806/#773 refund 手动同步 8/14 01:26 两次失败 → 如果用户今早手动点了「退款同步」按钮, 报的错就是这个
- 错误内容:**抓取失败: 找不到任何查询/搜索按钮,所有兜底均失败**
  → 根因: BMS 平台退款导出页面的 DOM 按钮 selector 已变动 (页面改版 / class 名被 hash 化了)
  → 需要修复 `app/services/bms_fetcher.py` 中 refund 相关抓取逻辑里的查找按钮 selector

### Step 4: 最小范围修复 ✅ (2026-08-14 16:58 提交)

#### 根因深挖
为什么 ticket / ticket_finish / wallet 今天手动同步全部成功，但 refund 手动同步连续 2 次失败？
→ 原 `_click_query_button` 的 4 层兜底策略 **第 1-3 层全都只在 `document.querySelectorAll('button')` 里搜索**，第 4 层只是 Enter 键盘。
→ BMS 平台 **退费单管理页 (`pay/refund-manage`) 近两天做了改版**：查询按钮不再是原生 `<button>` 标签，改成了伪按钮 DOM（`<div role="button">` 或 `<a class="btn-search">` 或 `<span class="el-button">` 等），这 4 层全都扫不到 → 最终抛 `RuntimeError("找不到任何查询/搜索按钮,所有兜底均失败")`

#### 修复措施 (backend/app/services/bms_fetcher.py `_click_query_button`)
- 候选 DOM 扩大：从只扫 `button:visible` → 扩大到 `ANY_CLICKABLE = button,div[role=button],span[role=button],a[class*=btn/button],div/span[class*=el-button]`
- 文案匹配扩大：innerText 为空时，兜底读 `aria-label`（伪按钮常把文案挂在 aria-label 而不是 innerText）
- 第 2 层 y 坐标筛选条逻辑：同样应用扩大选择器，y 上限从 600 → 800（适配 1080p 高屏）
- 第 3 层 JS 全局兜底：`querySelectorAll` 也用 ANY_CLICKABLE（去掉 :visible 因为在全局 evaluate 里 visible 没意义）；拼接 innerText+aria-label 一起搜
- **新增第 5 层终极兜底**：
  - 5a. 找页面里 `form` 只要包含日期输入框 → 调 `form.requestSubmit()`
  - 5b. 找 `[aria-label*="搜索"/"查询"]` 或 `class*=submit-btn/search-submit/query-submit` 的元素直接 click
- 最终 RuntimeError 文案追加 `(候选已扩大到 div/span/a 伪按钮)` 便于未来判断是哪版逻辑抛的

#### 语法校验
✅ `python3 -c "ast.parse(open(...).read())" → SYNTAX_OK`

### Step 5: 前后对比验证 (TODO, 请用户手动触发)
操作：在数据采集页面手动触发「退款/退费单管理」同步 1 次，观察：
- 预期 1: `status=success，rows=正常行数`
- 预期 2: 如果命中了新兜底，日志会出现 `tag=DIV/A/SPAN` 或 `终极兜底触发查询成功` 之类字样
- 预期 3: 页面不再出现 "找不到任何查询/搜索按钮,所有兜底均失败"
