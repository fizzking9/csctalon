import request from '@/api'

// 首页汇总
export function getSummary() {
  return request.get('/analytics/summary')
}

// 运营分析
export const operation = {
  trend: (params) => request.get('/analytics/operation/trend', { params }),
  status: () => request.get('/analytics/operation/status'),
  reason: (params) => request.get('/analytics/operation/reason', { params }),
  method: () => request.get('/analytics/operation/method'),
}

// 现金流分析
export const cash = {
  trend: (params) => request.get('/analytics/cash/trend', { params }),
  breakdown: () => request.get('/analytics/cash/breakdown'),
  walletVsWithdraw: (params) => request.get('/analytics/cash/wallet-vs-withdraw', { params }),
  merchant: () => request.get('/analytics/cash/merchant'),
  walletBalance: (params) => request.get('/analytics/cash/wallet-balance', { params }),
}

// 退款分类分析
export const category = {
  serviceItem: () => request.get('/analytics/category/service-item'),
  merchant: (params) => request.get('/analytics/category/merchant', { params }),
  package: (params) => request.get('/analytics/category/package', { params }),
}

// 人员操作分析
export const operator = {
  ticket: (params) => request.get('/analytics/operator/ticket', { params }),
  audit: (params) => request.get('/analytics/operator/audit', { params }),
  refund: (params) => request.get('/analytics/operator/refund', { params }),
}

// 客服接待看板(对齐周报4模块)
export const reception = {
  overview: (params) => request.get('/analytics/reception/overview', { params }),
  categorySummary: (params) => request.get('/analytics/reception/category-summary', { params }),
  perCapita: (params) => request.get('/analytics/reception/per-capita', { params }),
  groupDetail: (params) => request.get('/analytics/reception/group-detail', { params }),
}

// 退款-提现全链路看板 (合并 SN对账 + 发起退款→实际到账时效分析)
export const refundWithdrawal = {
  overview: (params) => request.get('/analytics/refund-withdrawal/overview', { params }),
  timeDistribution: (params) => request.get('/analytics/refund-withdrawal/time-distribution', { params }),
  dailySplit: (params) => request.get('/analytics/refund-withdrawal/daily-split', { params }),
  dimensions: (params) => request.get('/analytics/refund-withdrawal/dimensions', { params }),
  detail: (params) => request.get('/analytics/refund-withdrawal/detail', { params }),
}

// 今日看板 (单日切面 + 实时节奏)
export const today = {
  kpi: (params) => request.get('/analytics/today/kpi', { params }),
  hourlyTrend: (params) => request.get('/analytics/today/hourly-trend', { params }),
  reception: (params) => request.get('/analytics/today/reception', { params }),
  refund: (params) => request.get('/analytics/today/refund', { params }),
  queue: (params) => request.get('/analytics/today/queue', { params }),
  staff: (params) => request.get('/analytics/today/staff', { params }),
  // 退款挽回专项分析 (独立视图, 不冲抵接待效率)
  refundRecovery: (params) => request.get('/analytics/today/refund-recovery', { params }),
}

