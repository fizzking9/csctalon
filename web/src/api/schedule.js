import request from '@/api'

// 班次模板
export function listShifts() {
  return request.get('/schedules/shifts')
}
export function createShift(payload) {
  return request.post('/schedules/shifts', payload)
}
// 排班
export function listSchedules(params) {
  return request.get('/schedules', { params })
}
export function bulkSchedule(payload) {
  return request.post('/schedules/bulk', payload)
}
export function updateSchedule(id, payload) {
  return request.put(`/schedules/${id}`, payload)
}
export function deleteSchedule(id) {
  return request.delete(`/schedules/${id}`)
}
export function scheduleComparison(params) {
  return request.get('/schedules/comparison', { params })
}
// #256 B3-8 镜像: 导入企微排班表(前置 parseUpload 存档, stored_path 寻址)
export function importSchedule(payload) {
  return request.post('/schedules/import', payload)
}
