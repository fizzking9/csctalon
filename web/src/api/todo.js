import request from '@/api'

export function listTodos(params) {
  return request.get('/todos', { params })
}

export function createTodo(payload) {
  return request.post('/todos', payload)
}

export function getTodo(id) {
  return request.get(`/todos/${id}`)
}

export function updateTodo(id, payload) {
  return request.put(`/todos/${id}`, payload)
}

export function deleteTodo(id) {
  return request.delete(`/todos/${id}`)
}

export function addFollowup(id, payload) {
  return request.post(`/todos/${id}/followups`, payload)
}
