import request from '@/api'

// 周报10项框架模板(key/title/section_type)
export function getWeeklyFramework() {
  return request.get('/weekly-reports/framework')
}

// 周报看板列表(分页,支持 status)
export function listWeeklyReports(params) {
  return request.get('/weekly-reports', { params })
}

// 周报详情(含10项框架)
export function getWeeklyReport(id) {
  return request.get(`/weekly-reports/${id}`)
}

// 创建周报看板(草稿)
export function createWeeklyReport(payload) {
  return request.post('/weekly-reports', payload)
}

// 更新周报(仅草稿)
export function updateWeeklyReport(id, payload) {
  return request.put(`/weekly-reports/${id}`, payload)
}

// 确认周报(固化快照)
export function confirmWeeklyReport(id) {
  return request.post(`/weekly-reports/${id}/confirm`)
}

// 归档周报
export function archiveWeeklyReport(id) {
  return request.post(`/weekly-reports/${id}/archive`)
}

// 删除周报
export function deleteWeeklyReport(id) {
  return request.delete(`/weekly-reports/${id}`)
}

// 一键生成本周周报看板(自动周次+默认绑定)
// payload: { date?, name? } 可选指定周/覆盖名称
export function generateWeeklyReport(payload = {}) {
  return request.post('/weekly-reports/generate', payload)
}

// 周报分项默认绑定配置列表
export function listWeeklyBindings() {
  return request.get('/weekly-reports/bindings')
}

// 批量保存绑定配置(管理员配一次)
// payload: { bindings: [{ section_key, view_ids, owner_id, is_active }] }
export function saveWeeklyBindings(payload) {
  return request.put('/weekly-reports/bindings', payload)
}

// 专项下钻: 从周报某分项一键创建专项报告(指派负责人)
// payload: { name?, assignee_id?, due_date?, summary? }
export function createDrillReport(weeklyId, sectionKey, payload) {
  return request.post(`/weekly-reports/${weeklyId}/sections/${sectionKey}/drill-report`, payload)
}
