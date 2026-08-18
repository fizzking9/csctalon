import request from '@/api'

export function listRules() {
  return request.get('/alerts/rules')
}
export function createRule(payload) {
  return request.post('/alerts/rules', payload)
}
export function updateRule(id, payload) {
  return request.put(`/alerts/rules/${id}`, payload)
}
export function deleteRule(id) {
  return request.delete(`/alerts/rules/${id}`)
}
export function listAlerts(params) {
  return request.get('/alerts', { params })
}
export function setAlertStatus(id, payload) {
  return request.post(`/alerts/${id}/status`, payload)
}
export function scanAlerts() {
  return request.post('/alerts/scan')
}
