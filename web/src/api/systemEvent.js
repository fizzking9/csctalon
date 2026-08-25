import request from '@/api'

// 系统事务通知列表(分页, 可筛选)
export function listSystemEvents(params) {
  return request.get('/system-events', { params })
}

// 角标概要: 未读数 + 最近24h结果分布
export function systemEventSummary() {
  return request.get('/system-events/summary')
}

// 标记单条已读
export function markSystemEventRead(id) {
  return request.post(`/system-events/${id}/read`)
}

// 全部标记已读
export function markAllSystemEventsRead() {
  return request.post('/system-events/read-all')
}
