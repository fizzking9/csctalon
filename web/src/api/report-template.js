import request from '@/api'

// 模板列表(分页,支持 report_type/status/keyword)
export function listReportTemplates(params) {
  return request.get('/report-templates', { params })
}

// 按类型取默认模板(一键生成用)
export function getDefaultTemplate(reportType) {
  return request.get('/report-templates/default', { params: { report_type: reportType } })
}

// 模板详情
export function getReportTemplate(id) {
  return request.get(`/report-templates/${id}`)
}

// 实例化模板框架(供创建周报/报告用,返回补齐 summary/data/related_report_ids 的副本)
export function instantiateTemplate(id) {
  return request.get(`/report-templates/${id}/instantiate`)
}

// 创建模板(管理员)
export function createReportTemplate(payload) {
  return request.post('/report-templates', payload)
}

// 更新模板(管理员)
export function updateReportTemplate(id, payload) {
  return request.put(`/report-templates/${id}`, payload)
}

// 删除模板(预设软删归档,自定义硬删)
export function deleteReportTemplate(id) {
  return request.delete(`/report-templates/${id}`)
}

// 复制模板(另存为自定义)
// payload: { name?, description? }
export function copyReportTemplate(id, payload = {}) {
  return request.post(`/report-templates/${id}/copy`, payload)
}

// 设为该类型默认模板(同类型其他取消默认)
export function setDefaultTemplate(id) {
  return request.post(`/report-templates/${id}/set-default`)
}
