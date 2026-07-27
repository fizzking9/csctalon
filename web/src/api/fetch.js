import request from '@/api'

// 获取可同步的数据源列表
export function listSyncDataSources() {
  return request.get('/fetch/data-sources')
}

// 一键同步所有 BMS 数据
export function syncAll(payload) {
  return request.post('/fetch/sync-all', payload, { timeout: 600000 })
}

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
