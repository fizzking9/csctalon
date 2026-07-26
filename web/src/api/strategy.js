import request from '@/api'

export function listStrategy(params) {
  return request.get('/strategy', { params })
}

export function getStrategy(id) {
  return request.get(`/strategy/${id}`)
}

export function createStrategy(payload) {
  return request.post('/strategy', payload)
}

export function updateStrategy(id, payload) {
  return request.put(`/strategy/${id}`, payload)
}

export function deleteStrategy(id) {
  return request.delete(`/strategy/${id}`)
}

export function publishStrategy(id) {
  return request.post(`/strategy/${id}/publish`)
}

export function newVersion(id) {
  return request.post(`/strategy/${id}/new-version`)
}

export function markRead(id) {
  return request.post(`/strategy/${id}/read`)
}

export function getReceipts(id) {
  return request.get(`/strategy/${id}/receipts`)
}
