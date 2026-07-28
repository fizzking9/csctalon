import request from '@/api'

// 看板列表(分页,支持 category/status/keyword)
export function listDashboards(params) {
  return request.get('/dashboards', { params })
}

// 看板详情
export function getDashboard(id) {
  return request.get(`/dashboards/${id}`)
}

// 创建看板
export function createDashboard(payload) {
  return request.post('/dashboards', payload)
}

// 更新看板
export function updateDashboard(id, payload) {
  return request.put(`/dashboards/${id}`, payload)
}

// 删除看板(预设不可删)
export function deleteDashboard(id) {
  return request.delete(`/dashboards/${id}`)
}
