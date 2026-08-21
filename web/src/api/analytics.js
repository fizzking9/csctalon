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
  sleeping: (params) => request.get('/analytics/refund-withdrawal/sleeping', { params }),
}

// 今日看板 (单日切面 + 实时节奏)
// 第二个参数 config 可传 { signal } 用于 AbortController 取消请求
export const today = {
  kpi: (params, config) => request.get('/analytics/today/kpi', { params, ...config }),
  hourlyTrend: (params, config) => request.get('/analytics/today/hourly-trend', { params, ...config }),
  reception: (params, config) => request.get('/analytics/today/reception', { params, ...config }),
  refund: (params, config) => request.get('/analytics/today/refund', { params, ...config }),
  queue: (params, config) => request.get('/analytics/today/queue', { params, ...config }),
  staff: (params, config) => request.get('/analytics/today/staff', { params, ...config }),
  // 退款挽回专项分析 (独立视图, 不冲抵接待效率)
  refundRecovery: (params, config) => request.get('/analytics/today/refund-recovery', { params, ...config }),
}

// 退款看板深度分析 (2026-08-12 新增)
export const refundDeep = {
  personnelAnalysis: (params) => request.get('/analytics/refund/personnel-analysis', { params }),
  personDevices: (params) => request.get('/analytics/refund/person-devices', { params }),
  personnelTrend: (params) => request.get('/analytics/refund/personnel-trend', { params }),
  personReasonMatrix: (params) => request.get('/analytics/refund/person-reason-matrix', { params }),
  deviceAnalysis: (params) => request.get('/analytics/refund/device-analysis', { params }),
  deviceDetail: (params) => request.get('/analytics/refund/device-detail', { params }),
  typeTrend: (params) => request.get('/analytics/refund/type-trend', { params }),
  problemTypeDetail: (params) => request.get('/analytics/refund/problem-type-detail', { params }),
  groupComparison: (params) => request.get('/analytics/refund/group-comparison', { params }),
  decisionOverview: (params) => request.get('/analytics/refund/decision-overview', { params }),
}

// 退款明细导出 Excel(blob)
export function refundExport(params) {
  return request.get('/analytics/refund/export', { params, responseType: 'blob', timeout: 120000 })
}

