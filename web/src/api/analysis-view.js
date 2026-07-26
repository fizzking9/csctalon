import request from '@/api'

// ---------- 分析视图 ----------

// 视图列表(分页)
export function listAnalysisViews(params) {
  return request.get('/analysis-views', { params })
}

// 视图详情
export function getAnalysisView(id) {
  return request.get(`/analysis-views/${id}`)
}

// 创建视图
export function createAnalysisView(payload) {
  return request.post('/analysis-views', payload)
}

// 更新视图
export function updateAnalysisView(id, payload) {
  return request.put(`/analysis-views/${id}`, payload)
}

// 删除视图(预设不可删)
export function deleteAnalysisView(id) {
  return request.delete(`/analysis-views/${id}`)
}

// 执行视图(已存视图或 inline 预览)
// payload: { view_id?, inline_config?, params? }
export function executeAnalysisView(payload) {
  return request.post('/analysis-views/execute', payload)
}

// ---------- 分类规则 ----------

// 规则列表
export function listClassificationRules(params) {
  return request.get('/classification-rules', { params })
}

// 创建规则
export function createClassificationRule(payload) {
  return request.post('/classification-rules', payload)
}

// 更新规则
export function updateClassificationRule(id, payload) {
  return request.put(`/classification-rules/${id}`, payload)
}

// 删除规则
export function deleteClassificationRule(id) {
  return request.delete(`/classification-rules/${id}`)
}

// 预览规则效果
export function previewClassificationRule(payload) {
  return request.post('/classification-rules/preview', payload)
}
