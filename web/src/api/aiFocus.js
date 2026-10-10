import request from '@/api'

// AI 助手 - 关注点/分析视角配置管理(管理员)

// 关注点列表
export function listAiFocus() {
  return request.get('/ai/focus-configs')
}

// 新建关注点
export function createAiFocus(payload) {
  return request.post('/ai/focus-configs', payload)
}

// 更新关注点(部分)
export function updateAiFocus(id, payload) {
  return request.patch(`/ai/focus-configs/${id}`, payload)
}

// 删除关注点
export function deleteAiFocus(id) {
  return request.delete(`/ai/focus-configs/${id}`)
}
