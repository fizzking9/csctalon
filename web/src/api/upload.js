import request from '@/api'

// 解析文件(返回预览)
export function parseUpload(formData) {
  return request.post('/uploads/parse', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

// 确认导入
export function confirmImport(payload) {
  return request.post('/uploads/import', payload, { timeout: 300000 })
}

// 上传记录列表
export function listUploadRecords(params) {
  return request.get('/uploads/records', { params })
}

// 上传记录详情
export function getUploadRecord(id) {
  return request.get(`/uploads/records/${id}`)
}

// 关联匹配 - 统计
export function matchSummary() {
  return request.get('/match/summary')
}

// 关联匹配 - 手动触发
export function runMatch(onlyUnmatched = true) {
  return request.post('/match/run', null, { params: { only_unmatched: onlyUnmatched } })
}

// 关联关系列表
export function listRelations(params) {
  return request.get('/match/relations', { params })
}

// 人工补关联
export function manualMatch(payload) {
  return request.post('/match/manual', payload)
}

// 未关联退费单列表
export function listUnmatchedRefunds(params) {
  return request.get('/match/unmatched-refunds', { params })
}
