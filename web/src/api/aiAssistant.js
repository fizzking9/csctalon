import request from '@/api'

// AI 分析助手 - 自由问答 (A-20260904-003, ManagerUser)

// 助手启用状态 + period 选项 + 快捷问
export function aiConfig() {
  return request.get('/ai/config')
}

// 提交分析任务(返回 {task_id})
export function aiAnalyze(payload) {
  return request.post('/ai/analyze', payload)
}

// 轮询任务结果(终态含 result_md)
export function aiTaskStatus(taskId) {
  return request.get(`/ai/tasks/${taskId}`)
}

// 最近一次任务(调试/展示)
export function aiTaskLatest() {
  return request.get('/ai/tasks')
}
