import request from '@/api'

// 外部管理台入口（仅系统管理员，工单 #187）。
// 只返回「有没有配、地址是什么」，票据在点击时单独签发。
export function getSsoEntries() {
  return request.get('/auth/sso/entries', { silentError: true })
}

// 签发免密进入 netmgr 网络管理面板的一次性票据。
// 返回 { url, expires_in }；url 内含 60 秒一次性票据（放在 URL fragment）。
// ⚠️ 调用方应直接 window.location 跳转；url/ticket 不得写入任何日志或埋点（票据即凭证）。
export function issueNetmgrTicket() {
  return request.post('/auth/sso/ticket')
}
