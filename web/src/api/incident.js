import request from '@/api'

// 影响事件列表(分页,支持 status/start/end)
export function listIncidents(params) {
  return request.get('/incidents', { params })
}

// 影响事件详情
export function getIncident(id) {
  return request.get(`/incidents/${id}`)
}

// #233 B1.5-2 时间轴事件标记(供 ECharts markPoint/markLine 消费)
// 入参: { start: 'YYYY-MM-DD', end: 'YYYY-MM-DD', event_type?: '经营事件'/'异常事故' }
// 返回: 数组 [{id, title, event_type, occurred_at, start_at, end_at, severity, ...}]
export function listIncidentMarkers(params) {
  return request.get('/incidents/markers/timeline', { params })
}

// #234 B1.5-3 事件影响对照: 事件前后窗口的接待/投诉/退款量变化
// 入参: { id, window_minutes?: 60 }
// 返回: { incident_id, anchor_at, pre_window, during_window, post_window, delta_pre_to_post }
export function getIncidentImpactComparison(id, params) {
  return request.get(`/incidents/${id}/impact-comparison`, { params })
}

// 登记影响事件
export function createIncident(payload) {
  return request.post('/incidents', payload)
}

// 更新影响事件
export function updateIncident(id, payload) {
  return request.put(`/incidents/${id}`, payload)
}

// 转为预期事项(复盘后纳入提前部署)
export function convertIncident(id, payload) {
  return request.post(`/incidents/${id}/convert`, payload)
}

// 删除影响事件
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
