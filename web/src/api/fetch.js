import request from '@/api'

// 获取可同步的数据源列表
export function listSyncDataSources() {
  return request.get('/fetch/data-sources')
}

// 一键同步所有 BMS 数据(旧版同步阻塞,兼容保留)
export function syncAll(payload) {
  return request.post('/fetch/sync-all', payload, { timeout: 600000 })
}

// ========== 2026-08-02 新版异步 Task 模式 ==========

// 预检: 是否可执行、冷却剩余、增量时间范围、凭证状态
export function taskPreflight(dataTypes = []) {
  const params = {}
  if (dataTypes && dataTypes.length) params.data_types = dataTypes.join(',')
  return request.get('/fetch/task/preflight', { params })
}

// 提交异步同步任务(立即返回 task_id,请轮询 taskStatus)
export function taskSubmit(payload) {
  return request.post('/fetch/task/submit', payload, { timeout: 15000 })
}

// 查询任务进度
export function taskStatus(taskId = null) {
  const params = {}
  if (taskId) params.task_id = taskId
  return request.get('/fetch/task/status', { params })
}

// ====================================================

// 获取同步日志
export function listSyncLogs(params = {}) {
  return request.get('/fetch/sync-logs', { params })
}

// 获取定时同步状态
export function getScheduleStatus() {
  return request.get('/fetch/schedule/status')
}

// 启动定时同步
export function startSchedule() {
  return request.post('/fetch/schedule/start')
}

// 停止定时同步
export function stopSchedule() {
  return request.post('/fetch/schedule/stop')
}

// 测试浏览器可用性
export function testBrowser() {
  return request.get('/fetch/test')
}

// ========== 验证码交互登录(闪猫等 requires_captcha 数据源) ==========

// 检查登录态文件是否存在 + 保存时间
export function captchaLoginCheck(dataType) {
  return request.get('/fetch/captcha-login/check', { params: { data_type: dataType } })
}

// 第 1 步: 启动浏览器,截图验证码,返回 session_id + captcha_image(base64 data URI)
export function captchaLoginStart(dataType) {
  return request.post('/fetch/captcha-login/start', { data_type: dataType }, { timeout: 90000 })
}

// 第 2 步: 用户提交验证码 → 填账号密码+验证码提交登录 → 成功保存 storage_state
export function captchaLoginSubmit(sessionId, captcha) {
  return request.post('/fetch/captcha-login/submit', { session_id: sessionId, captcha }, { timeout: 60000 })
}

// 清除登录态(退出登录,强制下次重新登录)
export function captchaLoginLogout(dataType) {
  return request.post('/fetch/captcha-login/logout', { data_type: dataType })
}
