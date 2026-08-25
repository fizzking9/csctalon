import request from '@/api'

// 缓存管理信息: 可用重建范围 + Redis/预热/同步状态
export function cacheInfo() {
  return request.get('/system/cache/info')
}

// 强制重建/失效缓存(仅管理员)
// scope: today/week/reports/all ; invalidateOnly=true 仅失效不预热
export function rebuildCache(scope, invalidateOnly = false) {
  return request.post('/system/cache/rebuild', {
    scope,
    invalidate_only: invalidateOnly,
  })
}
