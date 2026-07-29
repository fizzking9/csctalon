import request from '@/api'

// 管理措施列表(分页,支持 status/measure_type/source/owner_id)
export function listMeasures(params) {
  return request.get('/measures', { params })
}

// 到期待回看措施队列
export function listDueReviewMeasures(params) {
  return request.get('/measures/due-review', { params })
}

// 措施详情
export function getMeasure(id) {
  return request.get(`/measures/${id}`)
}

// 创建措施
export function createMeasure(payload) {
  return request.post('/measures', payload)
}

// 更新措施
export function updateMeasure(id, payload) {
  return request.put(`/measures/${id}`, payload)
}

// 措施到期回看(review_result/review_note → 流转状态)
export function reviewMeasure(id, payload) {
  return request.post(`/measures/${id}/review`, payload)
}

// 删除措施
export function deleteMeasure(id) {
  return request.delete(`/measures/${id}`)
}
