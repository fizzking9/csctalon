import request from '@/api'

export function listUsers(params) {
  return request.get('/users', { params })
}

export function createUser(payload) {
  return request.post('/users', payload)
}

export function updateUser(id, payload) {
  return request.put(`/users/${id}`, payload)
}

export function deleteUser(id) {
  return request.delete(`/users/${id}`)
}
