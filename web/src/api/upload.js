import request from '@/api'

// 解析文件(返回预览)
// formData 可以是 FormData 对象(上传文件)或普通对象(已有文件,提供 stored_path)
export function parseUpload(formData) {
  const isFormData = formData instanceof FormData
  const opts = {
    timeout: 120000,
  }
  // 后端使用 Form 参数,必须用 multipart/form-data
  if (!isFormData) {
    const fd = new FormData()
    for (const [key, value] of Object.entries(formData)) {
      if (value !== undefined && value !== null) {
        fd.append(key, value)
      }
    }
    formData = fd
  }
  opts.headers = { 'Content-Type': 'multipart/form-data' }
  return request.post('/uploads/parse', formData, opts)
}

// diff 预览(导入前查看 existing/new/abnormal 分类)
export function previewDiff(payload) {
  return request.post('/uploads/preview-diff', payload, { timeout: 300000 })
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
