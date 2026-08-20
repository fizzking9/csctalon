import request from '@/api'

// 三源投诉统计汇总
export function complaintSummary(params) {
  return request.get('/complaints/summary', { params })
}

// 投诉明细列表(三源合并)
export function complaintDetail(params) {
  return request.get('/complaints/detail', { params })
}

// 投诉补充明细(315外部投诉 / 卡商群投诉, workbuddy推送, 按设备SN关联工单)
export function complaintSupplementary(params) {
  return request.get('/complaints/supplementary', { params })
}

// workbuddy 投诉推送同步日志(内部查看)
export function listComplaintSyncLogs(params) {
  return request.get('/sync/complaints/logs', { params })
}

// ---- 单个投诉审计 ----
export function listAudits(params) {
  return request.get('/complaints/audits', { params })
}
export function createAudit(payload) {
  return request.post('/complaints/audits', payload)
}
