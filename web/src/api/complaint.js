import request from '@/api'

// ★ #67 P1: 投诉决策概览(单请求出一屏: 结论条/KPI/四趋势/积压)
//   替代原先并发 8 个请求(summary/analysis/detail/backlog/subject/…),
//   且统一了时间口径 —— 各接口原口径不一致会让同屏数字互相打架。
export function complaintOverview(params) {
  return request.get('/complaints/overview', { params })
}

// #67 投诉设备维度汇总(按渠道可切: 内部/商户号/外部/卡商)
//   与概览 device_dims 同源自洽(source_type=None ≡ 概览A6), 支持渠道过滤。
export function complaintDeviceDims(params) {
  return request.get('/complaints/device-dims', { params })
}

// 投诉决策分析(管理摘要/平台分布/时效/处理人效率)
export function complaintAnalysis(params) {
  return request.get('/complaints/analysis', { params })
}

// 三源投诉统计汇总
export function complaintSummary(params) {
  return request.get('/complaints/summary', { params })
}

// ★ #87 投诉健康度指标（活跃SN / 投诉率三维 / 三岗位负荷 / 重复投诉率）
//   落点: 运营分析看板「投诉专项分析」区
//   ⚠️ 其中「重复投诉率」是**升级的代理信号**（同SN 30天内重复），
//      不是真实升级率 —— 真实升级率需升级投诉表（申报/化解字段）到位后才可算。
export function complaintHealthMetrics(params) {
  return request.get('/complaints/health-metrics', { params })
}

// 投诉明细列表(三源合并)
export function complaintDetail(params) {
  return request.get('/complaints/detail', { params })
}

// 投诉补充明细(315外部投诉 / 卡商群投诉, workbuddy推送, 按设备SN关联工单)
export function complaintSupplementary(params) {
  return request.get('/complaints/supplementary', { params })
}
// ★ #55: 投诉事件归并「待人工审核」列表(同设备同日多电话; 只读, 不删数据)
export function complaintDedupReview(params) {
  return request.get('/complaints/dedup-review', { params })
}

// ★ #60: SN 后补残影清单(采集质量问题; 已从统计排除, 原始行保留)
export function complaintSnBackfill(params) {
  return request.get('/complaints/sn-backfill', { params })
}
// 积压未处理投诉审核分析(统一口径: 归一化状态非已完成)
export function complaintBacklog(params) {
  return request.get('/complaints/backlog', { params })
}

// ★ #63: 纯工信部投诉单列视图(工单+文档 合并去重; 判据 = SN 或 电话)
export function complaintPureCard(params) {
  return request.get('/complaints/pure-card', { params })
}

// 积压未处理投诉明细导出Excel(同口径, 不受分页限制)
export function complaintBacklogExport(params) {
  return request.get('/complaints/backlog/export', { params, responseType: 'blob', timeout: 120000 })
}

// ★ #273: 积压筛选-设备维度候选项(设备类型/套餐/商户号, 与退款明细 facets 同口径)
export function complaintBacklogDimOptions() {
  return request.get('/complaints/backlog/device-dim-options')
}

// 被投诉主体维度分析(主体统计/严重性分布/升级链/退款关联)
export function complaintSubjectAnalysis(params) {
  return request.get('/complaints/subject-analysis', { params })
}

// workbuddy 投诉推送同步日志(内部查看)
export function listComplaintSyncLogs(params) {
  return request.get('/sync/complaints/logs', { params })
}

// ---- 单个投诉审计 ----
export function listAudits(params) {
  return request.get('/complaints/audits', { params })
}
export function createAudit(payload) {
  return request.post('/complaints/audits', payload)
}
