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

// ── 质量处置 + 治理规则 A-20260909-001 ─────────────────────────
/** 彻底忽略(quality_status→ignored, 从经营排除) */
export function dispositionIgnore(table, pk, reason) {
  return request.post(`/data-quality/disposition/${table}/${pk}/ignore`, { reason })
}

/** 恢复(清除 ignored/dup_residual) */
export function dispositionRestore(table, pk) {
  return request.post(`/data-quality/disposition/${table}/${pk}/restore`)
}

/** 单行治理建议(规则命中 + E匹配提示) */
export function dispositionSuggestion(table, pk) {
  return request.get(`/data-quality/disposition/${table}/${pk}/suggestion`)
}

/** 治理规则列表 */
export function listDispositionRules(params) {
  return request.get('/data-quality/disposition/rules', { params })
}

/** 新建治理规则 */
export function createDispositionRule(payload) {
  return request.post('/data-quality/disposition/rules', payload)
}

/** 更新治理规则 */
export function updateDispositionRule(id, payload) {
  return request.put(`/data-quality/disposition/rules/${id}`, payload)
}

/** 删除治理规则 */
export function deleteDispositionRule(id) {
  return request.delete(`/data-quality/disposition/rules/${id}`)
}

/** 规则试匹配(预览命中行) */
export function dispositionRuleApplyTest(id, limit) {
  return request.post(`/data-quality/disposition/rules/${id}/apply-test`, null, { params: { limit } })
}

/** 扫描 A/D 真重复候选 */
export function scanAdOverlapDup(limit) {
  return request.get('/data-quality/disposition/scan/ad-overlap-dup', { params: { limit } })
}
