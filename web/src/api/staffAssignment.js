import request from '@/api'

// 任职历史列表(按生效日期倒序)
export function listAssignments(personId) {
  return request.get(`/persons/${personId}/assignments`)
}

// 新增任职记录(自动关闭旧 current)
export function createAssignment(personId, payload) {
  return request.post(`/persons/${personId}/assignments`, payload)
}

// 编辑任职记录
export function updateAssignment(personId, aid, payload) {
  return request.put(`/persons/${personId}/assignments/${aid}`, payload)
}

// 删除任职记录
export function deleteAssignment(personId, aid) {
  return request.delete(`/persons/${personId}/assignments/${aid}`)
}

// 点查询(某日有效任职)
export function getAssignmentAt(personId, date) {
  return request.get(`/persons/${personId}/assignment-at`, { params: { date } })
}

// HR 人力快照 xlsx 导入(确认合并)
export function importSnapshot(payload) {
  return request.post('/persons/import-snapshot', payload, { timeout: 300000 })
}

// HR 快照导入预览(dry-run,只读,返回 diff)
export function previewSnapshot(payload) {
  return request.post('/persons/import-snapshot/preview', payload, { timeout: 120000 })
}

// 导出 HR 主表 xlsx
export function exportPersons() {
  return request.get('/persons/export', { responseType: 'blob', timeout: 120000 })
}
