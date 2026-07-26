import request from '@/api'

// 数据源列表
export function listDataSources() {
  return request.get('/data-sources')
}

// 数据源详情
export function getDataSource(code) {
  return request.get(`/data-sources/${code}`)
}

// 数据源字段列表
export function listDataSourceFields(code) {
  return request.get(`/data-sources/${code}/fields`)
}

// 更新数据源
export function updateDataSource(code, payload) {
  return request.patch(`/data-sources/${code}`, payload)
}

// 更新字段
export function updateDataSourceField(fieldId, payload) {
  return request.patch(`/data-sources/fields/${fieldId}`, payload)
}

// 触发反射重建数据源元数据
export function refreshDataSources() {
  return request.post('/data-sources/refresh')
}
