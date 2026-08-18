import request from '@/api'

// 发票
export function listInvoices(params) {
  return request.get('/invoices', { params })
}
export function createInvoice(payload) {
  return request.post('/invoices', payload)
}
export function updateInvoice(id, payload) {
  return request.put(`/invoices/${id}`, payload)
}
export function deleteInvoice(id) {
  return request.delete(`/invoices/${id}`)
}
// 报销单
export function listReimbursements(params) {
  return request.get('/invoices/reimbursements', { params })
}
export function createReimbursement(payload) {
  return request.post('/invoices/reimbursements', payload)
}
export function approveReimbursement(id, payload) {
  return request.post(`/invoices/reimbursements/${id}/approve`, payload)
}
export function exportReimbursement(id) {
  return request.get(`/invoices/reimbursements/${id}/export`, { responseType: 'blob' })
}
