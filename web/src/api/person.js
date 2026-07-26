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
export function mergeToNew(displayName, aliasIds) {
  return request.post('/persons/merge', { display_name: displayName, alias_ids: aliasIds })
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

// 停用人员
export function deletePerson(id) {
  return request.delete(`/persons/${id}`)
}
