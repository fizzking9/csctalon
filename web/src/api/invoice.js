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
/**
 * 上传发票图片本地解码二维码(规划设计 §4.5)
 * 返回 {decoded, qr_texts, used_text, parsed, ...}
 * parsed 含 invoice_no/invoice_code/total_amount/invoice_date 等字段, 可回填表单
 */
export function decodeInvoiceImage(file) {
  const form = new FormData()
  form.append('file', file)
  return request.post('/invoices/decode-image', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 30000,
  })
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
