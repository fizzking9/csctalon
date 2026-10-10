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
// opts.silentError=true 可在轮询/后台刷新场景下抑制全局 ElMessage 错误弹层
export function taskPreflight(dataTypes = [], opts = {}) {
  const params = {}
  if (dataTypes && dataTypes.length) params.data_types = dataTypes.join(',')
  return request.get('/fetch/task/preflight', { params, ...opts })
}

// 提交异步同步任务(立即返回 task_id,请轮询 taskStatus)
export function taskSubmit(payload) {
  return request.post('/fetch/task/submit', payload, { timeout: 15000 })
}

// 查询任务进度 — 默认 silentError(轮询 2s 一次,避免失败时循环弹错误)
export function taskStatus(taskId = null, opts = {}) {
  const params = {}
  if (taskId) params.task_id = taskId
  return request.get('/fetch/task/status', { params, silentError: true, ...opts })
}

// ====================================================

// 获取同步日志
export function listSyncLogs(params = {}, opts = {}) {
  return request.get('/fetch/sync-logs', { params, silentError: true, ...opts })
}

// 获取定时同步状态 — 默认 silentError(由轮询调用)
export function getScheduleStatus(opts = {}) {
  return request.get('/fetch/schedule/status', { silentError: true, ...opts })
}

// 高频采集节拍配置 (#51) — 时段 + 间隔
export function getScheduleConfig() {
  return request.get('/fetch/schedule/config')
}

// 更新高频节拍配置(管理员) — 改后自下一槽位生效, 无需重启
export function updateScheduleConfig(payload) {
  return request.put('/fetch/schedule/config', payload)
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

// ========== SCRM 微信聚合平台 ==========

// SCRM: 查询 4 个数据源的最近同步状态 — 默认 silentError(被轮询调用)
export function getScrmSyncStatus(opts = {}) {
  return request.get('/fetch/scrm/status', { silentError: true, ...opts })
}

// SCRM: 手动提交同步任务(走全局 SyncTaskManager,与 BMS 共享锁/冷却)
export function submitScrmSync(payload) {
  return request.post('/fetch/scrm/sync', payload, { timeout: 15000 })
}

// SCRM: 查询同步日志 — 默认 silentError(被轮询调用)
export function listScrmSyncLogs(params = {}, opts = {}) {
  return request.get('/fetch/scrm/logs', { params, silentError: true, ...opts })
}
