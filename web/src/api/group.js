import request from '@/api'

// 分组列表
export function listGroups(params = {}) {
  return request.get('/groups', { params })
}

// 分组详情
export function getGroup(id) {
  return request.get(`/groups/${id}`)
}

// 组长/副主管轮岗
export function transferGroupLeadership(groupId, params) {
  return request.post(`/groups/${groupId}/transfer-leadership`, null, { params })
}
