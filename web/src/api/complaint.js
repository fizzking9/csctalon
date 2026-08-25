import request from '@/api'

// 投诉决策分析(管理摘要/平台分布/时效/处理人效率)
export function complaintAnalysis(params) {
  return request.get('/complaints/analysis', { params })
}

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

// 积压未处理投诉审核分析(统一口径: 归一化状态非已完成)
export function complaintBacklog(params) {
  return request.get('/complaints/backlog', { params })
}

// 被投诉主体维度分析(主体统计/严重性分布/升级链/退款关联)
export function complaintSubjectAnalysis(params) {
  return request.get('/complaints/subject-analysis', { params })
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
