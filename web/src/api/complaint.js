import request from '@/api'

// 三源投诉统计汇总
export function complaintSummary(params) {
  return request.get('/complaints/summary', { params })
}

// ---- 单个投诉审计 ----
export function listAudits(params) {
  return request.get('/complaints/audits', { params })
}
export function createAudit(payload) {
  return request.post('/complaints/audits', payload)
}
