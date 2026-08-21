import request from '@/api'

export function listChannels() {
  return request.get('/channels')
}
export function createChannel(payload) {
  return request.post('/channels', payload)
}
export function updateChannel(id, payload) {
  return request.put(`/channels/${id}`, payload)
}
export function deleteChannel(id) {
  return request.delete(`/channels/${id}`)
}
export function testChannel(code, payload) {
  return request.post(`/channels/${code}/test`, payload)
}
