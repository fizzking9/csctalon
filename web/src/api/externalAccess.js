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
