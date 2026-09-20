import request from '@/api'

// AI 数据治理 API（工单 #165）
// 契约见 docs/design/AI数据治理框架-20260919.md §八 + backend/app/api/v1/governance.py。
// 「体检」与「研判」都是**异步 202 + 轮询**：POST 返回 scan_id / judge_id，
// 前端轮询对应 GET 直到 finished=true（与 AI 数据接入助手同一模式）。
// 全部端点仅 admin；响应统一 {code, data, message}，拦截器已解包，业务侧取 res.data。

// ── 一、体检 / 研判（异步 202 + 轮询）────────────────────────────────
// 触发一次数据体检（202，返回 { scan_id }）
export function createScan() {
  return request.post('/governance/scans')
}

// 轮询体检状态（finished=true 表示结束）；options 可传 { silentError: true } 抑制报错弹层
export function getScan(scanId, options = {}) {
  return request.get(`/governance/scans/${scanId}`, options)
}

// 触发一次研判（202，返回 { judge_id }）
export function createJudge() {
  return request.post('/governance/judge')
}

// 轮询研判状态
export function getJudge(judgeId, options = {}) {
  return request.get(`/governance/judges/${judgeId}`, options)
}

// ── 二、findings（探测发现）──────────────────────────────────────────
// 发现列表（detector/table_name/status/verdict 筛选 + 分页）
export function listFindings(params) {
  return request.get('/governance/findings', { params })
}

// 单条详情（证据 + 研判 + 建议 + 复现 SQL + 执行凭证）
export function getFinding(id) {
  return request.get(`/governance/findings/${id}`)
}

// 人工确认（L2 不可逆动作的执行前置；body: { note? }）
export function confirmFinding(id, payload) {
  return request.post(`/governance/findings/${id}/confirm`, payload)
}

// 执行建议动作（按服务端 auto_level 分级；L2 未确认 → 后端 400）
export function executeFinding(id) {
  return request.post(`/governance/findings/${id}/execute`)
}

// 判定「业务允许」并沉淀规则（body: { reason? }）
export function dismissFinding(id, payload) {
  return request.post(`/governance/findings/${id}/dismiss`, payload)
}

// ── 三、治理规则 ────────────────────────────────────────────────────
// 规则列表（detector / enabled 筛选 + 分页）
export function listRules(params) {
  return request.get('/governance/rules', { params })
}

// 新建规则（后端拒绝不可逆动作：auto_level 只允许 L0/L1、disposition 不得为 L2 动作）
export function createRule(payload) {
  return request.post('/governance/rules', payload)
}

// 改规则（任意子集，常用 { enabled }）
export function updateRule(id, payload) {
  return request.patch(`/governance/rules/${id}`, payload)
}

// ── 四、概览 ────────────────────────────────────────────────────────
// 治理概览（by_status / by_detector_open / by_verdict / 规则数 / 近期事件 / 分级表）
export function getSummary() {
  return request.get('/governance/summary')
}
