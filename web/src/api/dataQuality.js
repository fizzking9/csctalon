import request from '@/api'

/** 数据质量规则列表 */
export function listRules() {
  return request.get('/data-quality/rules')
}

/** 新建规则 */
export function createRule(payload) {
  return request.post('/data-quality/rules', payload)
}

/** 更新规则 */
export function updateRule(id, payload) {
  return request.put(`/data-quality/rules/${id}`, payload)
}

/** 删除规则 */
export function deleteRule(id) {
  return request.delete(`/data-quality/rules/${id}`)
}
