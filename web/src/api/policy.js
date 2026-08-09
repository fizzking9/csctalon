import request from '@/api'

/**
 * 管理制度 API 封装(占位,对应后端 policies.py)
 *
 * 所有写操作(POST/PUT/DELETE)均由 operation_log 中间件自动落库,
 * action 中文标签 = 「管理制度.创建/更新/分类/审核/批/驳/归档/删除」。
 *
 * TODO: 待 RegistryCenter/PolicyManage 前端页面接入时使用。
 */

// ---------- 基础 CRUD ----------

/** 分页列表(支持分类/状态/关键词/只看已发布) */
export function listPolicies(params) {
  return request.get('/policies', { params })
}

/** 详情(自动 view_count+1) */
export function getPolicy(id) {
  return request.get(`/policies/${id}`)
}

/** 新建草稿 */
export function createPolicy(payload) {
  return request.post('/policies', payload)
}

/** 更新(调整内容/分类/版本/生效期/状态/备注) */
export function updatePolicy(id, payload) {
  return request.put(`/policies/${id}`, payload)
}

/** 删除草稿(非草稿会被后端拒绝,需要先归档) */
export function deletePolicy(id) {
  return request.delete(`/policies/${id}`)
}

// ---------- 动作(子动作路径 → 自动生成中文操作标签) ----------

/** 分类调整: /policies/{id}/classify  → 「管理制度.分类」 */
export function classifyPolicy(id, payload) {
  return request.post(`/policies/${id}/classify`, payload)
}

/** 提交审批 → 「管理制度.审核」(状态:draft→pending_approval) */
export function submitPolicyReview(id) {
  return request.post(`/policies/${id}/review`)
}

/** 审批通过并发布 → 「管理制度.批准」 */
export function approvePolicy(id, payload) {
  return request.post(`/policies/${id}/approve`, payload)
}

/** 驳回审批 → 「管理制度.驳回」 */
export function rejectPolicy(id, payload) {
  return request.post(`/policies/${id}/reject`, payload)
}

/** 归档 → 「管理制度.归档」 */
export function archivePolicy(id, payload) {
  return request.post(`/policies/${id}/archive`, payload)
}

// ---------- 枚举元数据(下拉源) ----------

/** 分类枚举(建议下拉缓存) */
export function listPolicyCategories() {
  return request.get('/policies/meta/categories')
}

/** 状态枚举 */
export function listPolicyStatuses() {
  return request.get('/policies/meta/statuses')
}
