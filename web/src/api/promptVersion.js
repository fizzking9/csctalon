import request from '@/api'

// 提示词版本管理 API（2026-09-23 全量版本化）
// governance = 治理研判提示词（judge_system/judge_user）
// onboarding = 接入助手起草提示词（draft_system/draft_user）
// 两类与 prompt_configs（AI 报告 gen/extract，在评估中心管理）同模式：
// 多版本行 + is_active 部分唯一索引 + change_reason/references 留痕，无金丝雀冒烟。
// 仅 admin；响应统一 {code, data, message}，拦截器已解包，业务侧取 res.data。

const BASE = {
  governance: '/admin/governance/prompts',
  onboarding: '/admin/onboarding/prompts',
}

// 版本列表（按 code；含生效标记/激活人时/change_reason）
export function listPromptVersions(kind, params) {
  return request.get(BASE[kind], { params })
}

// 新增版本（version+1 不激活；content 空=以生效版为底先改后升）
export function createPromptVersion(kind, payload) {
  return request.post(BASE[kind], payload)
}

// 激活版本（同 code 旧生效自动取消，账本留痕）
export function activatePromptVersion(kind, id) {
  return request.post(`${BASE[kind]}/${id}/activate`)
}

// 回滚到指定版本（= 激活历史版本，不删当前版本行）
export function rollbackPromptVersion(kind, id) {
  return request.post(`${BASE[kind]}/${id}/rollback`)
}
