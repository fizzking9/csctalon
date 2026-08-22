import request from '@/api'

// 共享文档(腾讯文档)同步 —— OAuth 授权 + 持续监测同步

// 生成腾讯文档授权链接(管理员)
export function getTencentAuthorizeUrl() {
  return request.get('/sync/docs/tencent/authorize-url')
}

// 保存应用凭证 / 共享文档配置(管理员)
export function saveTencentDocConfig(payload) {
  return request.post('/sync/docs/tencent/config', payload)
}

// 接入状态(管理层)
export function getTencentDocStatus() {
  return request.get('/sync/docs/tencent/status')
}

// 立即手动同步一次(管理员)
export function syncTencentDocNow() {
  return request.post('/sync/docs/tencent/sync-now')
}

// 解除授权(管理员)
export function revokeTencentDoc() {
  return request.post('/sync/docs/tencent/revoke')
}

// 复用: 投诉推送同步日志
export function listComplaintSyncLogs(params) {
  return request.get('/sync/complaints/logs', { params })
}

// 被动推送模式: 推送接入配置状态(token 是否已配置)
export function getPushConfig() {
  return request.get('/sync/complaints/config')
}

// 采集方式开关: 开启哪些投诉接入模式
export function getIngestModes() {
  return request.get('/sync/complaints/ingest-modes')
}
export function setIngestMode(mode, enabled) {
  return request.post('/sync/complaints/ingest-modes', { mode, enabled })
}
