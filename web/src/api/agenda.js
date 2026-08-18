import request from '@/api'

// 统一待办中心: 聚合「该我处理的事」
export function getAgenda(params) {
  return request.get('/agenda', { params })
}

// ---- 通知中心 ----
export function listNotifications(params) {
  return request.get('/notifications', { params })
}
export function unreadCount() {
  return request.get('/notifications/unread-count')
}
export function markRead(id) {
  return request.post(`/notifications/${id}/read`)
}
export function markAllRead() {
  return request.post('/notifications/read-all')
}
