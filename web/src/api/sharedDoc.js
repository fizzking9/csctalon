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

// ── 页面内登录态管理 (#45, A-20260910-001 LOCKED) ──
// 替代「SSH 上服务器跑 scripts/tencent_doc_login.py」的唯一路径。

// 各平台登录态健康度(mtime / 是否超期 / 显示服务可用性)
export function getSharedDocStates() {
  return request.get('/sync/shared-docs/states')
}

// 启动登录会话(起浏览器截登录页/二维码)
export function startSharedDocLogin(sourceId) {
  return request.post(`/sync/shared-docs/${sourceId}/login/start`)
}

// 轮询会话状态: pending / scanned / authorized / expired / closed
export function getSharedDocLoginStatus(sessionId) {
  return request.get(`/sync/shared-docs/login/${sessionId}/status`)
}

// 确认扫码完成(后端会先轻量校验, 不通过则不覆盖旧登录态)
export function confirmSharedDocLogin(sessionId) {
  return request.post(`/sync/shared-docs/login/${sessionId}/confirm`)
}

// 取消登录会话(幂等)
export function cancelSharedDocLogin(sessionId) {
  return request.delete(`/sync/shared-docs/login/${sessionId}`)
}
