import request from '@/api'

export function listUpstreamTickets(params) {
  return request.get('/upstream-tickets', { params })
}
export function createUpstreamTicket(payload) {
  return request.post('/upstream-tickets', payload)
}
export function updateUpstreamTicket(id, payload) {
  return request.put(`/upstream-tickets/${id}`, payload)
}
export function deleteUpstreamTicket(id) {
  return request.delete(`/upstream-tickets/${id}`)
}
export function upstreamStats() {
  return request.get('/upstream-tickets/stats/summary')
}
