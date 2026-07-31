import request from '@/api'

// 设备审计时序流(跨表合并 7 张业务表)
export function getDeviceTimeline(sn, params) {
  return request.get(`/devices/${sn}/timeline`, { params })
}

// 人员审计时序流(跨表合并 6 张业务表)
export function getPersonTimeline(personId, params) {
  return request.get(`/persons/${personId}/timeline`, { params })
}

// 设备列表(供审计页搜索选择设备)
export function searchDevices(params) {
  return request.get('/devices', { params })
}
