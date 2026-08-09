import request from '@/api'

/**
 * 部门间信息同步 API 封装(占位,对应后端 dept_info_syncs.py)
 *
 * 所有写操作自动落库 operation_logs,中文标签形如:
 *   「部门同步.创建/更新/分类/登记/已读/回复/闭环/删除」。
 *
 * TODO: 待 RegistryCenter/DeptSync 前端页面接入。
 */

// ---------- 基础 CRUD ----------

/** 分页列表(支持 mine_only 只看跟我相关的:发送人/接收人) */
export function listDeptInfoSyncs(params) {
  return request.get('/dept-info-syncs', { params })
}

/** 详情 */
export function getDeptInfoSync(id) {
  return request.get(`/dept-info-syncs/${id}`)
}

/** 新建登记(草稿) */
export function createDeptInfoSync(payload) {
  return request.post('/dept-info-syncs', payload)
}

/** 更新(调整内容/分类/优先级/状态/截止/接收人/关联异常或措施等) */
export function updateDeptInfoSync(id, payload) {
  return request.put(`/dept-info-syncs/${id}`, payload)
}

/** 删除(仅草稿允许) */
export function deleteDeptInfoSync(id) {
  return request.delete(`/dept-info-syncs/${id}`)
}

// ---------- 动作 ----------

/** 分类/优先级调整 → 「部门同步.分类」 */
export function classifyDeptInfoSync(id, payload) {
  return request.post(`/dept-info-syncs/${id}/classify`, payload)
}

/** 发布/正式登记(草稿→已发布sent) → 「部门同步.登记」 */
export function registerDeptInfoSync(id) {
  return request.post(`/dept-info-syncs/${id}/register`)
}

/** 标记已读(阅读回执) → 「部门同步.已读」 */
export function readDeptInfoSync(id) {
  return request.post(`/dept-info-syncs/${id}/read`)
}

/** 接收方回复/协调结果 → 「部门同步.回复」 */
export function respondDeptInfoSync(id, payload) {
  return request.post(`/dept-info-syncs/${id}/respond`, payload)
}

/** 闭环(完成确认) → 「部门同步.闭环」 */
export function closeDeptInfoSync(id, payload) {
  return request.post(`/dept-info-syncs/${id}/close`, payload)
}

// ---------- 枚举元数据 ----------

export function listSyncCategories() {
  return request.get('/dept-info-syncs/meta/categories')
}

export function listSyncPriorities() {
  return request.get('/dept-info-syncs/meta/priorities')
}

export function listSyncStatuses() {
  return request.get('/dept-info-syncs/meta/statuses')
}
