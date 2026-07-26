import request from '@/api'

// 专项任务 CRUD
export function listTasks(params) {
  return request.get('/tasks', { params })
}

export function createTask(payload) {
  return request.post('/tasks', payload)
}

export function getTask(id) {
  return request.get(`/tasks/${id}`)
}

export function updateTask(id, payload) {
  return request.put(`/tasks/${id}`, payload)
}

export function deleteTask(id) {
  return request.delete(`/tasks/${id}`)
}

// 设备批次
export function listTaskDevices(id, params) {
  return request.get(`/tasks/${id}/devices`, { params })
}

export function addTaskDevices(id, payload) {
  return request.post(`/tasks/${id}/devices`, payload)
}

export function updateTaskDevice(taskId, deviceId, payload) {
  return request.put(`/tasks/${taskId}/devices/${deviceId}`, payload)
}

export function removeTaskDevice(taskId, deviceId) {
  return request.delete(`/tasks/${taskId}/devices/${deviceId}`)
}

// 跟进记录
export function addTaskFollowup(id, payload) {
  return request.post(`/tasks/${id}/followups`, payload)
}
