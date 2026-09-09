import request from '@/api'

// 对账回填 + 五路对账复核(A-20260905-002 / A-20260906-002, 工单#17/#22)
export const reconcile = {
  // D 路 op_time 回填(幂等, Admin/Supervisor)
  backfillDOpTime: (params) => request.post('/reconcile/backfill-d-op-time', null, { params }),
  // E 路水单逐笔勾稽视图
  waterStatement: (params) => request.get('/reconcile/water-statement', { params }),
  // ── 五路对账复核(A-20260906-002 / #22) ──
  checkRun: (params) => request.get('/reconcile/check/run', { params }),             // 预览(不落库)
  checkConfirm: (params) => request.post('/reconcile/check/confirm', null, { params }), // 落库(Admin/Supervisor)
  checkList: (params) => request.get('/reconcile/check/list', { params }),
  checkDetail: (params) => request.get('/reconcile/check/detail', { params }),
  checkConclusion: (checkId, params) => request.post(`/reconcile/check/${checkId}/conclusion`, null, { params }),
  // ── 逐笔勾稽明细核对(A-20260906-002 v1.1 / #22) ──
  checkLines: (checkId, params) => request.get(`/reconcile/check/${checkId}/lines`, { params }),               // 批次逐笔列表
  checkLinesSummary: (checkId) => request.get(`/reconcile/check/${checkId}/lines-summary`),                    // 逐笔确认进度
  checkLineCandidates: (params) => request.get('/reconcile/check/line-candidates', { params }),                 // E笔候选
  checkLineConfirm: (checkId, params) => request.post(`/reconcile/check/${checkId}/line-confirm`, null, { params }), // 打标(Admin/Supervisor)
  // ── 逐路对齐(A-20260909-002): E 为基准调校 ABCD ──
  alignment: (params) => request.get('/reconcile/alignment', { params }),                              // 汇总(每路对齐/差异 + issues)
  alignmentDetail: (params) => request.get('/reconcile/alignment/detail', { params }),                 // 差异明细下钻(A/B/C/D/orphan)
}

export default reconcile
