import request from '@/api'

// 异常事件列表(分页,支持 status/start/end)
export function listIncidents(params) {
  return request.get('/incidents', { params })
}

// 异常事件详情
export function getIncident(id) {
  return request.get(`/incidents/${id}`)
}

// 登记异常事件
export function createIncident(payload) {
  return request.post('/incidents', payload)
}

// 更新异常事件
export function updateIncident(id, payload) {
  return request.put(`/incidents/${id}`, payload)
}

// 转为预期事项(复盘后纳入提前部署)
export function convertIncident(id, payload) {
  return request.post(`/incidents/${id}/convert`, payload)
}

// 删除异常事件
export function deleteIncident(id) {
  return request.delete(`/incidents/${id}`)
}

// ---- 跟进时间线 ----
export function listFollowups(id) {
  return request.get(`/incidents/${id}/followups`)
}
export function addFollowup(id, payload) {
  return request.post(`/incidents/${id}/followups`, payload)
}
