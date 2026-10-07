import request from '@/api'

// #208 外部投诉同源案件复核（判重 v5：同 device_sn + 内容相似 ≥0.7 + 14 天窗）
// 口径单源在后端 app.services.complaint_same_source，本文件只做 HTTP 转发。

// 同源组分页（status: auto/needs_review/confirmed/rejected；含成员/证据/④C 取值）
export function listSameSourceGroups(params) {
  return request.get('/admin/complaint-same-source/groups', { params })
}

// 同源组详情
export function getSameSourceGroup(id) {
  return request.get(`/admin/complaint-same-source/groups/${id}`)
}

// 人工终审：status=confirmed(确认同源) / rejected(驳回，非同源)；note 可选
export function reviewSameSourceGroup(id, payload) {
  return request.patch(`/admin/complaint-same-source/groups/${id}`, payload)
}

// 手动重算：dry_run=true 只返回统计不落库；false 落库（幂等，保留人工已审组）
export function runSameSourceGroups(dryRun = false) {
  return request.post('/admin/complaint-same-source/run', { dry_run: dryRun })
}
