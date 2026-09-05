import request from '@/api'

// 拉取设备月流量并入库(单 SN, 可指定月份; 缺省月份=该SN全量有流量月份)
export function fetchDeviceFlow(sn, month = null, opts = {}) {
  const params = { sn }
  if (month) params.month = month
  return request.post('/device-flow/fetch', null, { params, timeout: 120000, ...opts })
}

// 本地历史查询(分页)
export function listDeviceFlowUsage(params = {}) {
  return request.get('/device-flow/usage', { params })
}

// 单设备月流量明细+汇总
export function getDeviceFlowBySn(sn) {
  return request.get(`/device-flow/usage/${sn}`)
}
