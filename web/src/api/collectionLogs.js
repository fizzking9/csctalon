import request from '@/api'

/** 统一采集日志列表 */
export function listCollectionLogs(params) {
  return request.get('/collection/logs', { params })
}

/** 采集日志来源选项 */
export function listLogSources() {
  return request.get('/collection/logs/sources')
}

/** 采集日志数据类型选项 */
export function listLogDataTypes() {
  return request.get('/collection/logs/data-types')
}