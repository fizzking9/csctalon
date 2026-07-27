import request from '@/api'

// 同步源配置列表(凭证掩码)
export function listSyncSources() {
  return request.get('/system/sync-sources')
}

// 更新单个同步源配置(username/password 留空表示不改)
export function updateSyncSource(dataType, payload) {
  return request.put(`/system/sync-sources/${dataType}`, payload)
}
