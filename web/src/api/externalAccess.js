import request from '@/api'

// 外部只读开放平台 - token 管理(管理员)

// 接口启用状态 + 默认限频 + 可用 scope
export function externalConfig() {
  return request.get('/external/config')
}

// token 列表(不含明文)
export function listExternalTokens() {
  return request.get('/external/tokens')
}

// 生成 token(明文仅此一次返回)
export function createExternalToken(payload) {
  return request.post('/external/tokens', payload)
}

// 更新 token(name/scopes/rate_limit/enabled)
export function updateExternalToken(id, payload) {
  return request.patch(`/external/tokens/${id}`, payload)
}

// 吊销 token
export function revokeExternalToken(id) {
  return request.post(`/external/tokens/${id}/revoke`)
}

// 恢复已吊销 token (#43: 吊销可逆, 误吊销无需重建)
export function enableExternalToken(id) {
  return request.post(`/external/tokens/${id}/enable`)
}

// 换钥: 覆盖式重新生成明文, 旧 token 立即失效 (#43)
export function rotateExternalToken(id) {
  return request.post(`/external/tokens/${id}/rotate`)
}

// 接入配置包(token+地址+提示词+规范, 一次复制带走) (#43)
// ⚠️ 返回内容中 token 位置为占位符, 不返回明文
export function getSetupPackage(id, params) {
  return request.get(`/external/tokens/${id}/setup-package`, { params })
}
