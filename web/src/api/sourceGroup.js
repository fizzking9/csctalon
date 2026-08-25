import request from '@/api'

// 数据源分组视图(组 + 组内源 + 凭证状态)
export function listSourceGroups() {
  return request.get('/source-groups')
}

// 更新组配置(组名/域名/组级账号密码, 留空不改)
export function updateSourceGroup(groupKey, payload) {
  return request.put(`/source-groups/${groupKey}`, payload)
}

// 数据源归组
export function assignSourceGroup(dataType, groupKey) {
  return request.post(`/source-groups/${groupKey || 'x'}/assign`, { data_type: dataType, group_key: groupKey || null })
}

// 验证数据源登录(可传 username/password 覆盖配置值)
export function testSourceLogin(dataType, payload = {}) {
  return request.post(`/source-groups/${dataType}/test-login`, payload)
}
