import request from '@/api'

export function listArticles(params) {
  return request.get('/kb-articles', { params })
}
export function listCategories() {
  return request.get('/kb-articles/categories')
}
export function getArticle(id) {
  return request.get(`/kb-articles/${id}`)
}
export function createArticle(payload) {
  return request.post('/kb-articles', payload)
}
export function updateArticle(id, payload) {
  return request.put(`/kb-articles/${id}`, payload)
}
export function deleteArticle(id) {
  return request.delete(`/kb-articles/${id}`)
}
