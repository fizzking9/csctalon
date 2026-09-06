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
}

export default reconcile
