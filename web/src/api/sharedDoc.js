import request from '@/api'

// 共享文档在线采集(doc_sync_sources)管理 —— 多平台共享文档 → 导出xlsx → 复用业务管线入库

// 列出全部采集源(ManagerUser+)
export function listSharedDocSources() {
  return request.get('/sync/shared-docs')
}

// 新建采集源(AdminUser)
export function createSharedDocSource(payload) {
  return request.post('/sync/shared-docs', payload)
}

// 更新采集源(AdminUser)
export function updateSharedDocSource(id, payload) {
  return request.put(`/sync/shared-docs/${id}`, payload)
}

// 删除采集源(AdminUser)
export function deleteSharedDocSource(id) {
  return request.delete(`/sync/shared-docs/${id}`)
}

// 启停采集源(AdminUser)
export function toggleSharedDocSource(id, enabled) {
  return request.post(`/sync/shared-docs/${id}/toggle`, { enabled })
}

// 手动触发同步某源(AdminUser)
export function syncSharedDocNow(sourceId) {
  return request.post('/sync/shared-docs/sync-now', { source_id: sourceId })
}
