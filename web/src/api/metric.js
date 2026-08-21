import request from '@/api'

export function listMetrics() {
  return request.get('/metrics')
}
export function metricCurrent(code) {
  return request.get(`/metrics/${code}/current`)
}
export function updateThresholds(code, payload) {
  return request.put(`/metrics/${code}/thresholds`, payload)
}
export function thresholdHistory(code) {
  return request.get(`/metrics/${code}/thresholds/history`)
}
