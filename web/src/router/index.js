import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboards/today',
    children: [
      {
        path: 'dashboards/today',
        name: 'DashboardToday',
        component: () => import('@/views/dashboards/Today.vue'),
        // ★ #80 正名: 「决策分析看板」→「今日值班看板」(path 不变)
        //   本页是值班台（实时/事件/待办），非能力意义上的决策分析页。
        meta: { title: '今日值班看板', icon: 'DataLine' },
      },
      {
        path: 'dashboards/operation',
        name: 'DashboardOperation',
        component: () => import('@/views/dashboards/OperationDashboard.vue'),
        // ★ #80 正名: 「运营看板」→「运营分析看板」(path 不变)
        meta: { title: '运营分析看板', icon: 'TrendCharts' },
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '工作台', icon: 'HomeFilled' },
      },
      // 我的待办(统一待办中心)
      {
        path: 'agenda',
        name: 'Agenda',
        component: () => import('@/views/agenda/Agenda.vue'),
        meta: { title: '我的待办', icon: 'Bell' },
      },
      // 数据中心
      {
        path: 'data/ingest',
        name: 'DataIngest',
        component: () => import('@/views/data/Ingest.vue'),
        meta: { title: '数据采集', icon: 'Upload', admin: true },
      },
      // 兼容旧路径
      { path: 'data/upload', redirect: '/data/ingest?tab=upload' },
      { path: 'data/fetch', redirect: '/data/ingest?tab=fetch' },
      { path: 'data/records', redirect: '/data/ingest?tab=records' },
      { path: 'data/browser', redirect: '/data/ingest?tab=browser' },
      {
        path: 'data/records',
        name: 'DataRecords',
        component: () => import('@/views/data/Records.vue'),
        meta: { title: '采集记录', icon: 'Document', hidden: true },
      },
      {
        path: 'data/browser',
        name: 'DataBrowser',
        component: () => import('@/views/data/Browser.vue'),
        meta: { title: '数据查看', icon: 'DataAnalysis', hidden: true },
      },
      {
        path: 'data/recharge-traffic',
        name: 'DataRechargeTraffic',
        component: () => import('@/views/data/RechargeTraffic.vue'),
        meta: { title: '流量与充值查询', icon: 'Coin' },
      },
      {
        path: 'data/device-flow-audit',
        name: 'DataDeviceFlowAudit',
        component: () => import('@/views/data/DeviceFlowAudit.vue'),
        meta: { title: '设备流量审计', icon: 'Odometer' },
      },
      // 分析看板
      {
        path: 'analytics/views',
        name: 'AnalyticsViews',
        component: () => import('@/views/analytics/ViewList.vue'),
        meta: { title: '分析视图', icon: 'DataAnalysis', admin: true },
      },
      {
        path: 'analytics/builder',
        name: 'AnalyticsBuilder',
        component: () => import('@/views/analytics/ViewBuilder.vue'),
        meta: { title: '视图构建器', icon: 'EditPen', admin: true },
      },
      {
        path: 'analytics/views/:id',
        name: 'AnalyticsViewRender',
        component: () => import('@/views/analytics/ViewRender.vue'),
        meta: { title: '视图详情', hidden: true },
      },
      // 看板配置(系统管理): 仅 admin 可配置全局看板
      {
        path: 'dashboards/designer',
        name: 'DashboardDesigner',
        component: () => import('@/views/dashboards/Designer.vue'),
        meta: { title: '看板配置', icon: 'Grid', admin: true },
      },
      // 决策看板(新,manager 可见)
      {
        path: 'dashboards/refund',
        name: 'DashboardRefund',
        component: () => import('@/views/dashboards/RefundDashboard.vue'),
        meta: { title: '退款分析', icon: 'Wallet', manager: true },
      },
      {
        path: 'dashboards/refund-recovery',
        name: 'DashboardRefundRecovery',
        component: () => import('@/views/dashboards/RefundRecovery.vue'),
        meta: { title: '挽回分析', icon: 'Wallet', manager: true },
      },
      {
        path: 'dashboards/refund-withdrawal',
        redirect: '/dashboards/refund',
      },
      {
        path: 'dashboards/complaint',
        redirect: '/dashboards/complaint-analysis',
      },
      {
        path: 'dashboards/complaint-analysis',
        name: 'ComplaintAnalysis',
        component: () => import('@/views/dashboards/ComplaintAnalysis.vue'),
        meta: { title: '投诉分析', icon: 'Warning', manager: true },
      },
      {
        path: 'dashboards/complaint-audit',
        name: 'ComplaintAudit',
        component: () => import('@/views/dashboards/ComplaintAudit.vue'),
        meta: { title: '投诉核查', icon: 'Warning', manager: true },
      },
      // ★ #63: 纯工信部投诉单列页（工单+文档 合并去重；判据 = SN 或 电话）
      {
        path: 'dashboards/pure-card-complaint',
        name: 'PureCardComplaint',
        component: () => import('@/views/dashboards/PureCardComplaint.vue'),
        meta: { title: '纯工信部投诉', icon: 'Warning', manager: true },
      },
      {
        path: 'dashboards/reception',
        name: 'DashboardReception',
        component: () => import('@/views/dashboards/Reception.vue'),
        meta: { title: '接待分析', icon: 'Service', manager: true },
      },
      {
        path: 'dashboards/performance',
        name: 'DashboardPerformance',
        component: () => import('@/views/dashboards/DashboardView.vue'),
        meta: { title: '绩效分析', icon: 'UserFilled', manager: true },
      },
      {
        path: 'dashboards/special',
        name: 'DashboardSpecial',
        component: () => import('@/views/dashboards/Special.vue'),
        meta: { title: '专项跟进', icon: 'Aim', manager: true },
      },
      {
        path: 'dashboards/by-code/:code',
        name: 'DashboardByCode',
        component: () => import('@/views/dashboards/DashboardView.vue'),
        meta: { title: '看板', hidden: true, manager: true },
      },
      {
        path: 'dashboards/view/:id',
        name: 'DashboardById',
        component: () => import('@/views/dashboards/DashboardView.vue'),
        meta: { title: '看板', hidden: true, manager: true },
      },
      {
        path: 'analytics/operation',
        name: 'AnalyticsOperation',
        component: () => import('@/views/analytics/Operation.vue'),
        meta: { title: '运营分析', icon: 'TrendCharts' },
      },
      {
        path: 'analytics/cash',
        name: 'AnalyticsCash',
        component: () => import('@/views/analytics/Cash.vue'),
        meta: { title: '现金流分析', icon: 'Money' },
      },
      {
        path: 'analytics/category',
        name: 'AnalyticsCategory',
        component: () => import('@/views/analytics/Category.vue'),
        meta: { title: '退款分类', icon: 'PieChart' },
      },
      {
        path: 'analytics/operator',
        name: 'AnalyticsOperator',
        component: () => import('@/views/analytics/Operator.vue'),
        meta: { title: '人员操作', icon: 'User' },
      },
      // 行为分析时序页(隐藏入口,从设备/人员详情或右上角快捷工具跳转)
      {
        path: 'audit/device',
        name: 'AuditDevice',
        component: () => import('@/views/audit/DeviceAudit.vue'),
        meta: { title: '设备分析', hidden: true },
      },
      {
        path: 'audit/person',
        name: 'AuditPerson',
        component: () => import('@/views/audit/PersonAudit.vue'),
        meta: { title: '人员分析', hidden: true },
      },
      // 待办
      {
        path: 'todos',
        name: 'Todos',
        component: () => import('@/views/todos/List.vue'),
        meta: { title: '事项跟进', icon: 'Bell' },
      },
      {
        path: 'todos/:id',
        name: 'TodoDetail',
        component: () => import('@/views/todos/Detail.vue'),
        meta: { title: '事项详情', hidden: true },
      },
      // 专项任务
      {
        path: 'tasks',
        name: 'Tasks',
        component: () => import('@/views/tasks/List.vue'),
        meta: { title: '专项任务', icon: 'List' },
      },
      {
        path: 'tasks/:id',
        name: 'TaskDetail',
        component: () => import('@/views/tasks/Detail.vue'),
        meta: { title: '专项详情', hidden: true },
      },
      // 策略发布
      {
        path: 'strategy/publish',
        name: 'StrategyPublish',
        component: () => import('@/views/strategy/Publish.vue'),
        meta: { title: '策略发布', icon: 'Document' },
      },
      {
        path: 'strategy/manage',
        name: 'StrategyManage',
        component: () => import('@/views/strategy/Manage.vue'),
        meta: { title: '策略管理', icon: 'EditPen', manager: true, roles: ['admin', 'supervisor'] },
      },
      // 报表中心(规划占位): admin + supervisor 可见(全量报表)
      {
        path: 'report/center',
        name: 'ReportCenter',
        component: () => import('@/views/report/Center.vue'),
        meta: { title: '报表中心', icon: 'Printer', manager: true, roles: ['admin', 'supervisor'] },
      },
      // 管理闭环(报告→审计→措施→跟进→回看)
      {
        path: 'management/reports',
        name: 'ManagementReports',
        component: () => import('@/views/management/ReportList.vue'),
        meta: { title: '报告索引', icon: 'Document', manager: true },
      },
      {
        path: 'management/reports/:id',
        name: 'ManagementReportDetail',
        component: () => import('@/views/management/ReportDetail.vue'),
        meta: { title: '报告详情', hidden: true, manager: true },
      },
      {
        path: 'management/weekly',
        name: 'ManagementWeekly',
        component: () => import('@/views/management/WeeklyList.vue'),
        meta: { title: '周报', icon: 'Calendar', manager: true },
      },
      {
        path: 'management/weekly/:id',
        name: 'ManagementWeeklyDetail',
        component: () => import('@/views/management/WeeklyDetail.vue'),
        meta: { title: '周报详情', hidden: true, manager: true },
      },
      {
        path: 'management/measures',
        name: 'ManagementMeasures',
        component: () => import('@/views/management/Measures.vue'),
        meta: { title: '管理措施', icon: 'Flag', manager: true },
      },
      {
        path: 'management/policies',
        name: 'ManagementPolicies',
        component: () => import('@/views/management/Policies.vue'),
        meta: { title: '政策措施', icon: 'Document', manager: true },
      },
      {
        path: 'management/incidents',
        name: 'ManagementIncidents',
        component: () => import('@/views/management/Incidents.vue'),
        meta: { title: '影响事件', icon: 'Warning', manager: true },
      },
      {
        path: 'management/device-tags',
        name: 'ManagementDeviceTags',
        component: () => import('@/views/management/DeviceTags.vue'),
        meta: { title: '设备专项标签', icon: 'CollectionTag' },
      },
      {
        path: 'management/schedule',
        name: 'ManagementSchedule',
        component: () => import('@/views/management/Schedule.vue'),
        meta: { title: '排班管理', icon: 'Calendar', manager: true },
      },
      {
        path: 'management/invoices',
        name: 'ManagementInvoices',
        component: () => import('@/views/management/Invoices.vue'),
        meta: { title: '发票登记', icon: 'Tickets' },
      },
      {
        path: 'management/kb-articles',
        name: 'ManagementKbArticles',
        component: () => import('@/views/management/KbArticles.vue'),
        meta: { title: '知识库', icon: 'Reading' },
      },
      {
        path: 'management/alerts',
        name: 'ManagementAlerts',
        component: () => import('@/views/management/Alerts.vue'),
        meta: { title: '预警中心', icon: 'AlarmClock', manager: true },
      },
      {
        path: 'management/upstream-tickets',
        name: 'ManagementUpstreamTickets',
        component: () => import('@/views/management/UpstreamTickets.vue'),
        meta: { title: '跨部门协作', icon: 'Connection', manager: true },
      },
      {
        path: 'management/registry',
        name: 'ManagementRegistry',
        component: () => import('@/views/management/RegistryCenter.vue'),
        meta: { title: '登记中心', icon: 'Form', manager: true },
      },
      {
        path: 'management/templates',
        name: 'ManagementTemplates',
        component: () => import('@/views/management/ReportTemplates.vue'),
        meta: { title: '看板模板', icon: 'Files', manager: true, roles: ['admin', 'supervisor'] },
      },
      // 系统
      {
        path: 'system/users',
        name: 'SystemUsers',
        component: () => import('@/views/system/Users.vue'),
        meta: { title: '用户管理', icon: 'UserFilled', admin: true },
      },
      {
        path: 'system/data-sources',
        redirect: '/data/ingest?tab=data-sources',
      },
      {
        path: 'system/data-sources',
        name: 'SystemDataSources',
        component: () => import('@/views/system/DataSources.vue'),
        meta: { title: '数据源管理', icon: 'Coin', admin: true, hidden: true },
      },
      {
        path: 'system/persons',
        name: 'SystemPersons',
        component: () => import('@/views/system/Persons.vue'),
        meta: { title: '人员管理', icon: 'UserFilled', admin: true },
      },
      {
        path: 'system/classification-rules',
        name: 'SystemClassificationRules',
        component: () => import('@/views/system/ClassificationRules.vue'),
        meta: { title: '分类规则', icon: 'Filter', admin: true },
      },
      {
        path: 'system/data-quality',
        redirect: '/data/ingest?tab=data-quality',
      },
      {
        // ★ #67 P3: 投诉采集质量(原「投诉分析」的人工审核#55 + 采集质量#60 外迁)。
        //   path 为**新增**(非搬迁) —— 原页面内的 tab 无独立 url, 故不破坏历史书签。
        path: 'system/complaint-quality',
        name: 'SystemComplaintQuality',
        component: () => import('@/views/management/ComplaintDataQuality.vue'),
        meta: { title: '投诉采集质量', icon: 'CircleCheck', admin: true },
      },
      {
        path: 'system/data-quality',
        name: 'SystemDataQuality',
        component: () => import('@/views/management/DataQuality.vue'),
        meta: { title: '数据质量', icon: 'CircleCheck', admin: true, hidden: true },
      },
      {
        path: 'system/operation-logs',
        name: 'SystemOperationLogs',
        component: () => import('@/views/system/OperationLogs.vue'),
        meta: { title: '操作日志', icon: 'Tickets', admin: true },
      },
      {
        path: 'system/system-events',
        name: 'SystemEvents',
        component: () => import('@/views/system/SystemEvents.vue'),
        meta: { title: '事务通知', icon: 'Bell', admin: true },
      },
      {
        path: 'system/cache',
        name: 'SystemCache',
        component: () => import('@/views/system/CacheAdmin.vue'),
        meta: { title: '缓存管理', icon: 'Cpu', admin: true },
      },
      {
        path: 'system/sync-settings',
        redirect: '/data/ingest?tab=sync-settings',
      },
      {
        path: 'system/sync-settings',
        name: 'SystemSyncSettings',
        component: () => import('@/views/system/SyncSettings.vue'),
        meta: { title: '同步设置', icon: 'Setting', admin: true, hidden: true },
      },
      {
        path: 'system/external-access',
        name: 'SystemExternalAccess',
        component: () => import('@/views/system/ExternalAccess.vue'),
        meta: { title: '外部接入', icon: 'Key', admin: true },
      },
      // #44 接入中心: 登录态 / 外部 Token / 推送配置包 / AI 与模型 统一入口
      // 旧路径(/system/external-access, /system/ai-focus-configs)保留可直达, 不破坏书签
      {
        path: 'system/access-center',
        name: 'SystemAccessCenter',
        component: () => import('@/views/system/AccessCenter.vue'),
        meta: { title: '接入中心', icon: 'Connection', admin: true },
      },
      {
        path: 'system/ai-focus-configs',
        name: 'SystemAiFocusConfigs',
        component: () => import('@/views/system/AiFocusConfigs.vue'),
        meta: { title: 'AI 助手提示词', icon: 'MagicStick', admin: true },
      },
      {
        // #108: LLM 多 Provider 管理(本地 Talon / 云端 DeepSeek 等)
        path: 'system/llm-providers',
        name: 'SystemLlmProviders',
        component: () => import('@/views/system/LlmProviders.vue'),
        meta: { title: 'LLM 模型接入', icon: 'Cpu', admin: true },
      },
      {
        // #125: AI 报告模板管理(admin)
        path: 'system/ai-templates',
        name: 'SystemAiTemplates',
        component: () => import('@/views/system/AiTemplates.vue'),
        meta: { title: 'AI 报告模板', icon: 'Document', admin: true },
      },
      {
        // #137: AI 管理页面(3 Tab 整合入口, #141 重组)
        // 模板管理 / 模型节点 / 提示词管理 (配置侧)
        // 报告列表拆出 → /system/ai-reports (查看侧)
        // 事务通知 → /system/system-events (审计侧)
        path: 'system/ai-admin',
        name: 'SystemAiAdmin',
        component: () => import('@/views/system/AiAdmin.vue'),
        meta: { title: 'AI 管理', icon: 'MagicStick', admin: true },
      },
      {
        // #141: AI 报告历史(查看侧) — 独立成页, 含完整 content 查看抽屉
        path: 'system/ai-reports',
        name: 'SystemAiReports',
        component: () => import('@/views/system/AiReports.vue'),
        meta: { title: 'AI 报告历史', icon: 'Document', admin: true },
      },
      {
        // #157: 数据源接入助手(起草→校验→确认→落地包 闭环, 归「数据与接入」组)
        path: 'system/ai-onboarding',
        name: 'SystemAiOnboarding',
        component: () => import('@/views/system/AiOnboarding.vue'),
        meta: { title: '数据源接入助手', icon: 'Connection', admin: true },
      },
      {
        // #165: AI 数据治理(体检→研判→确认→分级执行 闭环, 归「数据质量」组)
        path: 'system/ai-data-governance',
        name: 'SystemAiDataGovernance',
        component: () => import('@/views/system/AiDataGovernance.vue'),
        meta: { title: 'AI 数据治理', icon: 'CircleCheck', admin: true },
      },
      {
        // #173: AI 提示词评估中心(评估侧: 命中/兜底/锚点/规则健康 + 半自动迭代)
        path: 'system/ai-prompt-eval',
        name: 'SystemAiPromptEval',
        component: () => import('@/views/system/AiPromptEval.vue'),
        meta: { title: '提示词评估中心', icon: 'DataAnalysis', admin: true },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫:登录校验 + 角色分流(五角色: admin/supervisor/specialty_lead/specialist/agent)
router.beforeEach((to) => {
  const authStore = useAuthStore()
  document.title = to.meta.title ? `${to.meta.title} - 数据分析工具应用` : '数据分析工具应用'
  if (!to.meta.public && !authStore.isLoggedIn) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  // 1. admin 专属(系统管理): 仅 admin
  if (to.meta.admin && !authStore.isAdmin) {
    return { name: 'Dashboard' }
  }
  // 2. manager 专属(决策看板/管理闭环): admin + supervisor + specialty_lead
  if (to.meta.manager && !authStore.isManager) {
    return { name: 'Dashboard' }
  }
  // 3. 精确角色白名单(meta.roles 数组): 优先级最高, 命中即校验
  //    例: meta.roles: ['admin', 'supervisor'] → 仅主管+管理员可见
  //    例: meta.roles: ['specialty_lead', 'supervisor'] → 主管+组长可见
  if (Array.isArray(to.meta.roles) && to.meta.roles.length && !to.meta.roles.includes(authStore.role)) {
    return { name: 'Dashboard' }
  }
})

export default router
