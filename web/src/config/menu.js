/**
 * 导航菜单唯一配置源 (#46, A-20260910-004 LOCKED v1.0)
 *
 * ⚠️⚠️ 本文件是**全站菜单的唯一定义处** ⚠️⚠️
 *   不要在 MainLayout.vue / 移动端抽屉里直接写 <el-menu-item> —— 那会导致
 *   桌面端与移动端菜单再次漂移(历史问题: 两份硬编码互不同步)。
 *   要增删菜单项, 只改这里的 MENU_GROUPS。
 *
 * 设计要点:
 *  - **零 path 变更**: 本配置只决定"菜单怎么摆", 不新增/修改任何路由 path,
 *    历史书签与深链 100% 保留。
 *  - buildMode 三档:
 *      'always'     所有构建模式都显示(核心菜单)
 *      'debug_only' 仅 debug 构建显示(内测/低频管理功能)
 *      'hidden'     任何构建都不显示(占位页暂时下线, 路由仍可达)
 *  - roles: null=全员; ['admin'] / ['admin','supervisor'] 等精确白名单
 *  - 支持两层嵌套: 顶层 group → 子 group(二级 sub-menu) → 叶子项
 */

/** 构建模式可见性档位 */
export const BUILD_MODES = {
  ALWAYS: 'always',
  DEBUG_ONLY: 'debug_only',
  HIDDEN: 'hidden',
}

/**
 * 菜单树。
 *
 * 组织原则(对应 A-20260910-004 §二):
 *  一级按「业务域」，系统管理内部按「功能域 → 权限 → 使用频度」三层分组。
 */
export const MENU_GROUPS = [
  // ── 1. 看板(全员, 最高频) ──
  // ★ #80 命名修正（用户 2026-09-13 拍板）:
  //   #76 曾把 `/today` 改名「决策分析看板」+ 本组仍叫「运营看板」，但两页**内容错位**:
  //     · `/today`  实际是**值班台**（实时KPI + 值班组长 + 影响事件 + 待办 + 政策），
  //                 是「平时每天看」的那一个，**没有归因/目标/异常分解能力**，
  //                 称「决策分析」属过度拔高。
  //     · `/operation` 才是**分析页**（周/月/季/年四粒度 + 全量人效 + 原因Top10），
  //                 是「复盘时才看」的那一个。
  //   → 正名: today → 「今日值班看板」; operation → 「运营分析看板」。
  //   ⚠️ 仅改显示名，path 保持不动（书签/权限/menu.spec.js 均按 path 断言）。
  //   ⚠️ 改名须 **4 处同步**（漏一处即打架）: 本文件 · router/index.js meta.title ·
  //      两页的 pageTitle 常量 · Dashboard.vue 快捷入口文案。
  {
    key: 'dashboard',
    title: '看板',
    icon: 'DataLine',
    buildMode: 'always',
    roles: null,
    children: [
      { path: '/dashboards/today', title: '今日值班看板', icon: 'DataLine' },
      { path: '/dashboards/operation', title: '运营分析看板', icon: 'TrendCharts' },
    ],
  },

  // ── 2. 专项分析(管理层) ──
  {
    key: 'analysis',
    title: '专项分析',
    icon: 'DataAnalysis',
    buildMode: 'always',
    roles: ['admin', 'supervisor', 'specialty_lead'],
    children: [
      { path: '/dashboards/refund', title: '退款分析' },
      { path: '/dashboards/refund-recovery', title: '挽回分析' },
      { path: '/dashboards/complaint-analysis', title: '投诉分析' },
      { path: '/dashboards/complaint-audit', title: '投诉核查', buildMode: 'debug_only' },
      // ★ #63: 纯工信部投诉单列页 —— 核查类工具, 与「投诉核查」同档(debug_only)
      { path: '/dashboards/pure-card-complaint', title: '纯工信部投诉', buildMode: 'debug_only' },
      { path: '/dashboards/reception', title: '接待分析', buildMode: 'debug_only' },
      { path: '/dashboards/performance', title: '绩效分析', buildMode: 'debug_only' },
      { path: '/dashboards/special', title: '专项跟进', buildMode: 'debug_only' },
    ],
  },

  // ── 3. 运营工具(把原先散落的一级菜单收敛进来) ──
  {
    key: 'tools',
    title: '运营工具',
    icon: 'Tools',
    buildMode: 'debug_only',
    roles: null,
    children: [
      { path: '/todos', title: '事项跟进', icon: 'Bell' },
      { path: '/tasks', title: '专项任务', icon: 'List' },
      { path: '/management/device-tags', title: '设备专项标签', icon: 'CollectionTag' },
      { path: '/management/invoices', title: '发票登记', icon: 'Tickets' },
      { path: '/management/kb-articles', title: '知识库', icon: 'Reading' },
      {
        path: '/strategy/publish',
        title: '策略发布',
        icon: 'Document',
        // 管理层额外能看到「策略管理」, 由 child 级 roles 控制
        altChildren: [
          { path: '/strategy/manage', title: '策略管理', roles: ['admin', 'supervisor'] },
        ],
      },
    ],
  },

  // ── 4. 管理闭环(管理层) ──
  {
    key: 'management',
    title: '管理闭环',
    icon: 'Flag',
    buildMode: 'debug_only',
    roles: ['admin', 'supervisor', 'specialty_lead'],
    children: [
      { path: '/management/reports', title: '报告索引' },
      { path: '/management/weekly', title: '周报看板' },
      { path: '/management/measures', title: '管理措施' },
      { path: '/management/policies', title: '政策措施' },
      { path: '/management/incidents', title: '影响事件' },
      { path: '/management/schedule', title: '排班管理' },
      { path: '/management/alerts', title: '预警中心' },
      { path: '/management/upstream-tickets', title: '跨部门协作' },
      {
        path: '/management/templates',
        title: '看板模板',
        roles: ['admin', 'supervisor'],
      },
    ],
  },

  // ── 5. 数据分析 ──
  //  #46 决策: 从「系统管理 / admin-only」移出并放宽到 manager,
  //  解决审计项 U3「数据分析能力入口错位」(主管/组长此前无法消费分析视图)。
  //  ⚠️ 写操作仍在 API 侧锁 AdminUser(见 A-20260910-004 §四)。
  {
    key: 'data-analysis',
    title: '数据分析',
    icon: 'PieChart',
    buildMode: 'debug_only',
    roles: ['admin', 'supervisor', 'specialty_lead'],
    children: [
      { path: '/analytics/views', title: '分析视图' },
      { path: '/analytics/builder', title: '视图构建器', roles: ['admin'] },
      { path: '/data/device-flow-audit', title: '设备流量审计' },
    ],
  },

  // ── 5.5. AI 与分析(admin) ──
  // ── 设计说明（Design Rationale, #140）──────────────────────────────
  // 目的: 替 admin 把「AI 报告/模型/提示词/事务通知」全链路收敛到一处入口,
  //         避免 admin 在「系统管理」杂项里翻找 AI 配置(原 #137 漏改菜单导致
  //         路由可达但侧栏看不到, 用户反馈「左边菜单没有看到这个 AI 管理」)。
  // 逻辑: AI 报告链路(模板 → 预生成 → 模型节点 → 提示词 → 事务通知) 是完整闭环,
  //         与「数据分析」(主管/组长也可见的复盘视图)语义不同 → 独立顶级分组,
  //         不挤进「数据分析」也避免与「系统管理」运维杂项混淆。
  // 口径: roles=['admin'] (与原 system/ai-templates admin-only 一致);
  //         buildMode='always'(核心配置入口, 不做 debug_only);
  //         icon='MagicStick'(与 router/index.js #137 路由 meta 一致)。
  // 取舍:
  //   ① 不挤进「数据分析」分组: 数据分析 roles 含 supervisor/specialty_lead,
  //      AI 管理 admin-only, 混在一起会出现「组长看到入口但点进去 403」;
  //   ② 不并入「系统管理」子分组: 系统管理下已分三层(组织/数据/审计),
  //      AI 链路是完整闭环不宜切成三块塞进去;
  //   ③ 暂不下挂「分析视图构建器」等既有项: 避免与「数据分析」分组重复展示,
  //      违反「禁止重复展示」铁律; 后续若 AI 视图拆出可再扩。
  // 工单: #140 (修复 #137 菜单漏改 + 重组「投诉采集质量」到「数据质量」分组)。
  {
    key: 'ai-analysis',
    title: 'AI 与分析',
    icon: 'MagicStick',
    buildMode: 'always',
    roles: ['admin'],
    children: [
      // #141: 3 Tab 整合入口(模板/模型/提示词, 配置侧)
      { path: '/system/ai-admin', title: 'AI 管理' },
      // #125: 模板管理独立页(老路由保留可达, 不破坏书签)
      { path: '/system/ai-templates', title: 'AI 报告模板' },
      // #141: AI 报告历史(查看侧) — 含完整 content 查看抽屉
      { path: '/system/ai-reports', title: 'AI 报告历史' },
      // #173: 提示词评估中心(评估侧) — 命中/兜底/锚点/规则健康 + 半自动迭代
      { path: '/system/ai-prompt-eval', title: '提示词评估中心' },
      // 2026-09-23: 提示词全量版本化 —— 治理研判/接入起草提示词版本账本
      // （gen/extract 两类含金丝雀冒烟, 仍在评估中心区块⑤管理, 不重复展示）
      { path: '/system/prompt-versions', title: '提示词版本管理' },
    ],
  },

  // ── 6. 系统管理(admin) — 四层分组 ──
  {
    key: 'system',
    title: '系统管理',
    icon: 'Setting',
    buildMode: 'always',
    roles: ['admin'],
    children: [
      // ① 组织与权限: 低频, 改一次用很久
      {
        key: 'system-org',
        title: '组织与权限',
        icon: 'OfficeBuilding',
        children: [
          { path: '/system/users', title: '用户管理' },
          { path: '/system/persons', title: '人员管理' },
          { path: '/system/classification-rules', title: '分类规则' },
        ],
      },
      // ② 数据与接入: 中频, 运维日常(按使用频度降序)
      //    ★ #140 取舍: 「投诉采集质量」从这里移出到独立「数据质量」分组,
      //      因「数据采集/接入中心/缓存管理」都是"与外部系统接入相关"的功能,
      //      而「投诉采集质量」是数据治理域概念, 与接入无关 → 拆出避免语义混淆。
      {
        key: 'system-data',
        title: '数据与接入',
        icon: 'Coin',
        children: [
          { path: '/data/ingest', title: '数据采集' },
          { path: '/system/access-center', title: '接入中心' },
          // ★ #157: 新数据源接入的「起草→校验→确认→落地包」闭环工具。
          //   归此组而非「AI 与分析」: 它的产物是接入声明与落地包(数据接入域),
          //   与「AI 报告/模型/提示词」那套报告链路不是同一条流水线。
          { path: '/system/ai-onboarding', title: '数据源接入助手' },
          { path: '/system/cache', title: '缓存管理' },
        ],
      },
      // ②.5 数据质量: 中频, 数据治理域
      //    ── 设计说明（Design Rationale, #140）──────────────────────
      //    目的: 替 admin 把"数据好不好"的治理类工具单独成组,
      //         与"数据怎么接入"的接入类工具分开, 避免运营把"采集质量"
      //         当成"接入配置"误改。
      //    逻辑: 数据治理(质量/完整性/对账)与数据接入(采集/缓存/接入中心)
      //         是两个正交维度, 混在一起会让运营误以为它们是同一条流水线。
      //    口径: 暂只含「投诉采集质量」(#67 P3 收敛到此), 后续可扩
      //         「退款对账质量」「接待数据完整性」等同语义项。
      //    取舍: 不重命名「数据与接入」组(其语义本就是"接入侧",
      //         数据采集/接入中心/缓存管理都属于"与外部系统接入相关");
      //         不把「操作日志/事务通知」并入此处(审计语义不同)。
      //    工单: #140。
      {
        key: 'system-quality',
        title: '数据质量',
        icon: 'CircleCheck',
        children: [
          // ★ #67 P3: 数据治理类工具统一收敛到「系统管理」(用户 2026-09-11 决策)
          // ★ #140: 从「数据与接入」移到独立「数据质量」分组(用户 2026-09-17 决策)
          { path: '/system/complaint-quality', title: '投诉采集质量' },
          // ★ #165: AI 数据治理(体检→AI 研判→人工确认→分级执行)闭环工具。
          //   归此组而非「AI 与分析」: 它的产物是治理规则与执行凭证(数据治理域),
          //   与「AI 报告/模板/提示词」那套报告链路不是同一条流水线;
          //   归此组而非「数据与接入」: 它不改接入配置, 只判定数据本身好不好。
          { path: '/system/ai-data-governance', title: 'AI 数据治理', roles: ['admin'] },
        ],
      },
      // ③ 审计与配置: 中频
      {
        key: 'system-audit',
        title: '审计与配置',
        icon: 'Tickets',
        children: [
          { path: '/system/operation-logs', title: '操作日志' },
          { path: '/system/system-events', title: '事务通知', badge: 'eventUnread' },
          { path: '/dashboards/designer', title: '看板配置' },
        ],
      },
    ],
  },
]

// ── 以下为派生工具(渲染层用) ──

/** 收集菜单树中出现的全部 path(供"菜单项必须存在于 router"的防悬空测试用)。 */
export function collectMenuPaths(groups = MENU_GROUPS) {
  const out = []
  const walk = (nodes) => {
    for (const n of nodes || []) {
      if (n.path) out.push(n.path)
      if (n.altChildren) walk(n.altChildren)
      if (n.children) walk(n.children)
    }
  }
  walk(groups)
  return out
}
