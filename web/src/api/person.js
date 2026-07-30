import request from '@/api'

// 人员列表(分页)
export function listPersons(params) {
  return request.get('/persons', { params })
}

// 人员详情
export function getPerson(id) {
  return request.get(`/persons/${id}`)
}

// 更新人员
export function updatePerson(id, payload) {
  return request.put(`/persons/${id}`, payload)
}

// 把多个别名归并到该人员
export function mergeAliases(personId, aliasIds) {
  return request.post(`/persons/${personId}/merge`, { alias_ids: aliasIds })
}

// 新建人员并归并别名
export function mergeToNew(displayName, aliasIds, extra = {}) {
  return request.post('/persons/merge', { display_name: displayName, alias_ids: aliasIds, ...extra })
}

// 从人员拆出别名(各自建新 person)
export function splitAliases(personId, aliasIds) {
  return request.post(`/persons/${personId}/split`, { alias_ids: aliasIds })
}

// 待归并别名列表
export function listUnmergedAliases() {
  return request.get('/persons/unmerged-aliases')
}

// 归并建议(normalized 重复的别名组)
export function listMergeSuggestions() {
  return request.get('/persons/suggestions')
}

// 手动触发别名提取
export function refreshAliases() {
  return request.post('/persons/refresh-aliases')
}

// 全量重建未归并别名 + 重算归一化(清历史脏残留)
export function rebuildAliases() {
  return request.post('/persons/rebuild-aliases', {}, { timeout: 180000 })
}

// 批量按建议归并别名到对应 HR 人员(alias_ids 为空=全部待归并有建议的)
export function batchMergeBySuggestion(aliasIds = []) {
  return request.post('/persons/batch-merge', { alias_ids: aliasIds }, { timeout: 120000 })
}

// 批量按归一名(alias_normalized)新建人员 - 用于无建议目标的别名一键归一
export function batchCreateByNormalized(aliasIds = []) {
  return request.post('/persons/batch-create-by-normalized', { alias_ids: aliasIds }, { timeout: 120000 })
}

// 批量按前缀纳入人员(前缀-XXX → 建 Person,指定部门)
export function batchImportByPrefix(prefix, department) {
  return request.post('/persons/batch-import-by-prefix', { prefix, department }, { timeout: 120000 })
}

// 批量更新人员字段(部门/角色/在职状态/启用/确认/入职/分组/专项)
export function batchUpdatePersons(payload) {
  return request.post('/persons/batch-update', payload, { timeout: 120000 })
}

// 导出 HR 主表 xlsx(blob)
export function exportPersons() {
  return request.get('/persons/export', { responseType: 'blob', timeout: 120000 })
}

// 停用人员
export function deletePerson(id) {
  return request.delete(`/persons/${id}`)
}
