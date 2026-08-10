import request from '@/api'

// 专项列表
export function listSpecialties(params = {}) {
  return request.get('/specialties', { params })
}

// 专项详情
export function getSpecialty(id) {
  return request.get(`/specialties/${id}`)
}

// 创建专项(支持临时专项)
export function createSpecialty(params) {
  return request.post('/specialties', null, { params })
}

// 专项负责人轮岗
export function transferSpecialtyLead(specialtyId, params) {
  return request.post(`/specialties/${specialtyId}/transfer-lead`, null, { params })
}

// 分配人员到专项
export function assignPersonsToSpecialty(specialtyId, params) {
  return request.post(`/specialties/${specialtyId}/assign`, null, { params })
}

// 归档专项
export function archiveSpecialty(id) {
  return request.post(`/specialties/${id}/archive`)
}
