import request from '@/api'

// #125 AI 模板与报告管理(admin)

// 模板列表(按 priority 升序)
export function aiTemplates() {
  return request.get('/admin/ai/templates')
}

// 新增模板
export function createAiTemplate(payload) {
  return request.post('/admin/ai/templates', payload)
}

// 编辑模板(部分字段)
export function updateAiTemplate(id, payload) {
  return request.patch(`/admin/ai/templates/${id}`, payload)
}

// 删除模板
export function deleteAiTemplate(id) {
  return request.delete(`/admin/ai/templates/${id}`)
}

// 手动触发预生成
export function triggerAiTemplate(id) {
  return request.post(`/admin/ai/templates/${id}/trigger`)
}

// 报告列表(admin, 分页)
export function aiAdminReports(params = {}) {
  return request.get('/admin/ai/reports', { params })
}

// 删除报告
export function deleteAiReport(id) {
  return request.delete(`/admin/ai/reports/${id}`)
}
