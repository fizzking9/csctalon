import request from '@/api'

export function listRegistrations(params) {
  return request.get('/wechat/registrations/pending', { params })
}

export function approveRegistration(id, payload) {
  return request.post(`/wechat/registrations/${id}/approve`, payload)
}