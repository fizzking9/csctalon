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
