import request from '@/api'

// 跟进注释列表(周报/报告通用)
// params: { target_type: 'weekly'|'report', target_id, section_key? }
export function listComments(params) {
  return request.get('/report-comments', { params })
}

// 添加跟进注释(可派生措施)
// payload: { target_type, target_id, section_key?, content, create_measure? }
export function addComment(payload) {
  return request.post('/report-comments', payload)
}

// 删除跟进注释
export function deleteComment(id) {
  return request.delete(`/report-comments/${id}`)
}
