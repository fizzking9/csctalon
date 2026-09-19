import request from '@/api'

// AI 数据接入助手 API（工单 #157）
// 契约见 docs/design/AI数据接入助手设计-20260919.md §七（7 个端点）。
// 起草为「异步 202 + 轮询」：createDraft 返回 draft_id，前端轮询 getDraft 直到
// status 不再是 drafting。

// 提交样例，异步起草（返回 { draft_id }）
export function createDraft(payload) {
  return request.post('/ai-onboarding/drafts', payload)
}

// 草稿列表（status / issue_no 筛选）
export function listDrafts(params) {
  return request.get('/ai-onboarding/drafts', { params })
}

// 草稿详情（轮询起草进度 / 详情展示）
export function getDraft(id) {
  return request.get(`/ai-onboarding/drafts/${id}`)
}

// 人工编辑声明主体字段（改后自动重跑校验）
export function updateDraft(id, payload) {
  return request.put(`/ai-onboarding/drafts/${id}`, payload)
}

// 确认接入声明（fail 清零 + warn 逐条处置；仅 admin）
export function confirmDraft(id, payload) {
  return request.post(`/ai-onboarding/drafts/${id}/confirm`, payload)
}

// 生成落地包（只产 JSON，不写库不改码；仅 admin）
export function applyDraft(id) {
  return request.post(`/ai-onboarding/drafts/${id}/apply`)
}

// registry 快照（校验器对照物，用于前端提示 code/name_cn 冲突）
export function getRegistrySnapshot() {
  return request.get('/ai-onboarding/registry-snapshot')
}
