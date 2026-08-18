import request from '@/api'

// 设备列表
export function listDevices(params) {
  return request.get('/devices', { params })
}
// 设备详情
export function getDevice(sn) {
  return request.get(`/devices/${sn}`)
}
// 设备审计时序
export function deviceTimeline(sn, params) {
  return request.get(`/devices/${sn}/timeline`, { params })
}

// ---- 设备专项标签 ----
export function listTags() {
  return request.get('/devices/tags/list')
}
export function createTag(payload) {
  return request.post('/devices/tags', payload)
}
export function tagStats() {
  return request.get('/devices/tags/stats')
}
// 批量导入 SN 打标
export function batchImport(payload) {
  return request.post('/devices/tags/batch-import', payload)
}
// 标签批次列表
export function listTagBatches(params) {
  return request.get('/devices/tags/batches', { params })
}
