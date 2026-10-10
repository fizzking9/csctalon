import request from '@/api'

// AI 分析助手 - 自由问答 (A-20260904-003, ManagerUser)

// 助手启用状态 + period 选项 + 快捷问
export function aiConfig() {
  return request.get('/ai/config')
}

// 提交分析任务(返回 {task_id}); status=success 时直接带 result_md
// payload 支持: { question, period, date?, start?, end? } —— start/end 为纯日期区间
export function aiAnalyze(payload) {
  return request.post('/ai/analyze', payload)
}

// 轮询任务结果(终态含 result_md)
// config: 可传 { silentError: true } 关闭拦截器的通用错误弹窗 —— 轮询失败由
//   助手面板给出更具体的文案(区分"任务已过期"与"网络抖动"), 避免双重提示。
export function aiTaskStatus(taskId, config) {
  return request.get(`/ai/tasks/${taskId}`, config)
}

// 最近一次任务(调试/展示)
export function aiTaskLatest() {
  return request.get('/ai/tasks')
}

// #116 AI 报告历史(本人; admin 可查全部)
// 列表: 支持 start/end/period 筛选, 默认按 created_at DESC 分页
export function aiReports(params = {}) {
  return request.get('/ai/reports', { params })
}

// KPI + 7 日柱状(供"我的待办·AI 报告历史" tab 头部)
export function aiReportsSummary() {
  return request.get('/ai/reports/summary')
}

// 单条详情(含 answer_md 全文)
export function aiReportDetail(reportId) {
  return request.get(`/ai/reports/${reportId}`)
}

// #126 P0-2 LLM 调度队列状态(忙时提示)
// 返回 { active_count, queue_length, est_wait_s, is_busy, timestamp }
export function aiQueueStatus() {
  return request.get('/ai/queue-status')
}
