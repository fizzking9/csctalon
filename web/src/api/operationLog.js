import request from '@/api'

export function listOperationLogs(params) {
  return request.get('/operation-logs', { params })
}

export function listLogActions() {
  return request.get('/operation-logs/actions')
}

export function listLogTargetTypes() {
  return request.get('/operation-logs/targets')
}
