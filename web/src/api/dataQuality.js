import request from '@/api'

/** 数据质量规则列表 */
export function listRules() {
  return request.get('/data-quality/rules')
}

/** 新建规则 */
export function createRule(payload) {
  return request.post('/data-quality/rules', payload)
}

/** 更新规则 */
export function updateRule(id, payload) {
  return request.put(`/data-quality/rules/${id}`, payload)
}

/** 删除规则 */
export function deleteRule(id) {
  return request.delete(`/data-quality/rules/${id}`)
}

// ── 通用数据质量审核(L3) ──────────────────────────────────────
/** 支持审核的表列表(含各状态统计) */
export function listQualityTables() {
  return request.get('/data-quality/tables')
}

/** 表单质量记录列表 */
export function qualityList(table, params) {
  return request.get(`/data-quality/${table}/list`, { params })
}

/** 人工确认通过 */
export function qualityConfirm(table, pk) {
  return request.post(`/data-quality/${table}/${pk}/confirm`)
}

/** 人工修正字段 */
export function qualityFix(table, pk, payload) {
  return request.post(`/data-quality/${table}/${pk}/fix`, payload)
}

/** 存量数据重检(管理员) */
export function qualityScan(table) {
  return request.post(`/data-quality/${table}/scan`)
}

// ── 拒收行(未采纳数据) A-20260908-003 ─────────────────────────
/** 拒收行列表 */
export function listRejectedRows(params) {
  return request.get('/data-quality/rejected', { params })
}

/** 拒收行统计 */
export function rejectedStats() {
  return request.get('/data-quality/rejected/stats')
}

/** 人工判定单行 keep/discard */
export function reviewRejectedRow(id, action) {
  return request.post(`/data-quality/rejected/${id}/review`, { action })
}

/** 批量判定 */
export function batchReviewRejected(ids, action) {
  return request.post('/data-quality/rejected/batch-review', { row_ids: ids, action })
}
