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
  // ── 1. 运营看板(全员, 最高频) ──
  {
    key: 'dashboard',
    title: '运营看板',
    icon: 'DataLine',
    buildMode: 'always',
    roles: null,
    children: [
      { path: '/dashboards/today', title: '今日看板', icon: 'DataLine' },
      { path: '/dashboards/operation', title: '运营看板', icon: 'TrendCharts' },
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

  // ── 6. 系统管理(admin) — 三层分组 ──
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
      {
        key: 'system-data',
        title: '数据与接入',
        icon: 'Coin',
        children: [
          { path: '/data/ingest', title: '数据采集' },
          // ★ #67 P3: 数据治理类工具统一收敛到「系统管理」(用户 2026-09-11 决策)
          { path: '/system/complaint-quality', title: '投诉采集质量' },
          { path: '/system/access-center', title: '接入中心' },
          { path: '/system/cache', title: '缓存管理' },
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
