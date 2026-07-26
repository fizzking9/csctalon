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
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '工作台', icon: 'HomeFilled' },
      },
      // 数据中心
      {
        path: 'data/upload',
        name: 'DataUpload',
        component: () => import('@/views/data/Upload.vue'),
        meta: { title: '数据上传', icon: 'Upload' },
      },
      {
        path: 'data/records',
        name: 'DataRecords',
        component: () => import('@/views/data/Records.vue'),
        meta: { title: '上传记录', icon: 'Document' },
      },
      {
        path: 'data/match',
        name: 'DataMatch',
        component: () => import('@/views/data/Match.vue'),
        meta: { title: '关联匹配', icon: 'Connection' },
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
        meta: { title: '策略管理', icon: 'EditPen', manager: true },
      },
      // 报表中心(规划占位,manager 可见)
      {
        path: 'report/center',
        name: 'ReportCenter',
        component: () => import('@/views/report/Center.vue'),
        meta: { title: '报表中心', icon: 'Printer', manager: true },
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
        name: 'SystemDataSources',
        component: () => import('@/views/system/DataSources.vue'),
        meta: { title: '数据源管理', icon: 'Coin', admin: true },
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
        path: 'system/operation-logs',
        name: 'SystemOperationLogs',
        component: () => import('@/views/system/OperationLogs.vue'),
        meta: { title: '操作日志', icon: 'Tickets', admin: true },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫:登录校验 + 角色分流
router.beforeEach((to) => {
  const authStore = useAuthStore()
  document.title = to.meta.title ? `${to.meta.title} - 客服中心管理系统` : '客服中心管理系统'
  if (!to.meta.public && !authStore.isLoggedIn) {
    return { name: 'Login', query: { redirect: to.fullPath } }
  }
  if (to.meta.admin && !authStore.isAdmin) {
    return { name: 'Dashboard' }
  }
  // manager 仅 admin/supervisor 可访问(如策略管理)
  if (to.meta.manager && !authStore.isManager) {
    return { name: 'Dashboard' }
  }
})

export default router
