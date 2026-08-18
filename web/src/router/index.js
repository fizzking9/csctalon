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
        meta: { title: '运营看板', icon: 'DataLine' },
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
        meta: { title: '数据采集', icon: 'Upload' },
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
      // 分析看板
      {
        path: 'analytics/views',
        name: 'AnalyticsViews',
        component: () => import('@/views/analytics/ViewList.vue'),
        meta: { title: '分析视图', icon: 'DataAnalysis' },
      },
      {
        path: 'analytics/builder',
        name: 'AnalyticsBuilder',
        component: () => import('@/views/analytics/ViewBuilder.vue'),
        meta: { title: '视图构建器', icon: 'EditPen' },
      },
      {
        path: 'analytics/views/:id',
        name: 'AnalyticsViewRender',
        component: () => import('@/views/analytics/ViewRender.vue'),
        meta: { title: '视图详情', hidden: true },
      },
      // 看板配置(数据中心): 仅 admin + supervisor 可配置全局看板
      {
        path: 'dashboards/designer',
        name: 'DashboardDesigner',
        component: () => import('@/views/dashboards/Designer.vue'),
        meta: { title: '看板配置', icon: 'Grid', manager: true, roles: ['admin', 'supervisor'] },
      },
      // 决策看板(新,manager 可见)
      {
        path: 'dashboards/refund',
        name: 'DashboardRefund',
        component: () => import('@/views/dashboards/RefundDashboard.vue'),
        meta: { title: '退款看板', icon: 'Wallet', manager: true },
      },
      {
        path: 'dashboards/complaint',
        name: 'DashboardComplaint',
        component: () => import('@/views/dashboards/DashboardView.vue'),
        meta: { title: '投诉看板', icon: 'Warning', manager: true },
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
        meta: { title: '投诉审计', icon: 'Warning', manager: true },
      },
      {
        path: 'dashboards/reception',
        name: 'DashboardReception',
        component: () => import('@/views/dashboards/Reception.vue'),
        meta: { title: '接待看板', icon: 'Service', manager: true },
      },
      {
        path: 'dashboards/refund-withdrawal',
        name: 'DashboardRefundWithdrawal',
        component: () => import('@/views/dashboards/RefundWithdrawal.vue'),
        meta: { title: '退款-提现全链路看板', icon: 'Money', manager: true },
      },
      {
        path: 'dashboards/performance',
        name: 'DashboardPerformance',
        component: () => import('@/views/dashboards/DashboardView.vue'),
        meta: { title: '绩效看板', icon: 'UserFilled', manager: true },
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
      // 审计时序页(隐藏入口,从设备/人员详情跳转或直接访问)
      {
        path: 'audit/device',
        name: 'AuditDevice',
        component: () => import('@/views/audit/DeviceAudit.vue'),
        meta: { title: '设备审计', hidden: true },
      },
      {
        path: 'audit/person',
        name: 'AuditPerson',
        component: () => import('@/views/audit/PersonAudit.vue'),
        meta: { title: '人员审计', hidden: true },
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
        meta: { title: '异常事件', icon: 'Warning', manager: true },
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
        name: 'SystemDataQuality',
        component: () => import('@/views/management/DataQuality.vue'),
        meta: { title: '数据质量', icon: 'CircleCheck', admin: true },
      },
      {
        path: 'system/operation-logs',
        name: 'SystemOperationLogs',
        component: () => import('@/views/system/OperationLogs.vue'),
        meta: { title: '操作日志', icon: 'Tickets', admin: true },
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
  document.title = to.meta.title ? `${to.meta.title} - 客服中心管理系统` : '客服中心管理系统'
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
