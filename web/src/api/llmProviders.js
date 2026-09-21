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

// ── 角色（能力档位）与出域策略（2026-09-21）─────────────────────────
// 见 docs/design/20260921-模型角色分组与出域管控设计.md
// 设计说明: 替系统管理员回答「出域策略怎么设」「每个角色现在会用谁」
//   「某个模型挂了会退到谁」—— 按角色分区而非扁平列表，因为提问是
//   「这个角色够用吗」而不是「系统里有哪些模型」。

// 角色列表（含成员与实际生效调用链）
export function listLlmRoles() {
  return request.get('/llm-providers/roles')
}

// 更新角色（开关 / 是否允许出域 / 文案）
export function updateLlmRole(roleKey, payload) {
  return request.put(`/llm-providers/roles/${roleKey}`, payload)
}

// 读全局出域策略（含选项说明与云端 provider 清单）
export function getCloudPolicy() {
  return request.get('/llm-providers/cloud-policy')
}
