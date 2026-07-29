import request from '@/api'

// 报告列表(分页,支持 report_type/status)
export function listReports(params) {
  return request.get('/reports', { params })
}

// 报告详情(含完整快照)
export function getReport(id) {
  return request.get(`/reports/${id}`)
}

// 创建报告(草稿)
export function createReport(payload) {
  return request.post('/reports', payload)
}

// 更新报告(仅草稿)
export function updateReport(id, payload) {
  return request.put(`/reports/${id}`, payload)
}

// 确认报告(固化快照)
export function confirmReport(id, payload) {
  return request.post(`/reports/${id}/confirm`, payload)
}

// 归档报告
export function archiveReport(id) {
  return request.post(`/reports/${id}/archive`)
}

// 删除报告
export function deleteReport(id) {
  return request.delete(`/reports/${id}`)
}

// 上传审计贴图(返回 {path,url})
export function uploadAuditAttachment(file, reportId) {
  const data = new FormData()
  data.append('file', file)
  return request.post('/reports/attachments', data, {
    params: reportId ? { report_id: reportId } : {},
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// 报告审计附注列表
export function listAuditNotes(reportId) {
  return request.get(`/reports/${reportId}/audit-notes`)
}

// 添加审计附注(可派生措施)
export function addAuditNote(reportId, payload) {
  return request.post(`/reports/${reportId}/audit-notes`, payload)
}

// 删除审计附注
export function deleteAuditNote(reportId, noteId) {
  return request.delete(`/reports/${reportId}/audit-notes/${noteId}`)
}
