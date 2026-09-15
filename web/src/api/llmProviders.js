import request from '@/api'

// LLM 多 Provider 管理(工单 #108)
// - 增删改查 Provider(本地 Talon / 云端 DeepSeek 等)
// - 测连通性(任意配置 / 已存 Provider)
// - 调用审计日志分页查询

export function listLlmProviders() {
  return request.get('/llm-providers')
}

export function createLlmProvider(payload) {
  return request.post('/llm-providers', payload)
}

export function updateLlmProvider(id, payload) {
  return request.patch(`/llm-providers/${id}`, payload)
}

export function deleteLlmProvider(id) {
  return request.delete(`/llm-providers/${id}`)
}

// 独立测任意配置(不依赖 Provider id, 用于新建前预检)
export function testLlmConnection(payload) {
  return request.post('/llm-providers/test-connection', payload)
}

// 用已存 Provider 测连通性
export function testExistingLlm(id) {
  return request.post(`/llm-providers/${id}/test`)
}

// 调用审计日志分页查询
export function listLlmCallLogs(params) {
  return request.get('/llm-providers/call-logs', { params })
}
