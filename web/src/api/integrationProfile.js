import request from '@/api'

// 接入配置包模板 (#44, A-20260910-002 LOCKED)
// 管理员可维护「提示词 + 字段规范 + 接口说明」模板, 渲染为可直接复制给调用方的 Markdown。
// ⚠️ 渲染出的内容中 token 位置为占位符, 系统不返回明文。

export function listProfiles(params) {
  return request.get('/integrations/profiles', { params })
}

export function createProfile(payload) {
  return request.post('/integrations/profiles', payload)
}

export function updateProfile(id, payload) {
  return request.put(`/integrations/profiles/${id}`, payload)
}

export function deleteProfile(id) {
  return request.delete(`/integrations/profiles/${id}`)
}

// 恢复默认内容(从内置 seed 读回; 自定义模板不支持)
export function resetProfile(id) {
  return request.post(`/integrations/profiles/${id}/reset`)
}

// #47: 回滚到上一版 content_md(请求体必须传 confirm=true 二次确认)
export function rollbackProfile(id, payload = { confirm: true }) {
  return request.post(`/integrations/profiles/${id}/rollback`, payload)
}

// 渲染配置包(纯函数, 不落库)
export function renderProfile(id, payload) {
  return request.post(`/integrations/profiles/${id}/render`, payload)
}
