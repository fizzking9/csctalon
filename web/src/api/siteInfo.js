import request from '@/api'

// 站点备案信息(公开接口, 无需登录)
export function getSiteInfo() {
  return request.get('/public/site-info', { silentError: true })
}
