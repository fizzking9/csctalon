import request from '@/api'

/**
 * 流量与充值查询 API 封装
 *
 * 分两类:
 * 1) /recharges/device-combos/local* : 基于本库 device_combos 的本地查询
 *    (无上游时仍可用,数据来自 BMS/导出同步)
 * 2) /recharges/traffic/upstream : 实时流量查询(已接入真实 BMS 设备流量上游)
 *
 * #185 清理: 原 queryUpstreamRecharges(/recharges/upstream 充值订单上游查询)为长期
 *   未接入的占位接口, 已连同后端路由一并删除。
 */

// ---------- 本地 device_combos 查询(已可用) ----------

/** 本地充值套餐&流量记录 - 分页列表 */
export function listLocalCombos(params) {
  return request.get('/recharges/device-combos/local', { params })
}

/** 本地 - 汇总统计(笔数/金额/总流量) */
export function getLocalCombosSummary(params) {
  return request.get('/recharges/device-combos/local/summary', { params })
}

/** 本地 - 单 SN 充值时间线 + 汇总状态 */
export function getLocalComboBySn(sn, params) {
  return request.get(`/recharges/device-combos/local/${sn}`, { params })
}

// ---------- 上游实时(仅流量已接入真实 BMS) ----------

/**
 * 上游-实时流量余额/使用量查询
 * 套餐快照(本地 device_combos) + 月流量使用(本地 device_flow_usage);
 * refresh=true 或本地无该 SN 数据时触发一次 BMS 上游拉取(deviceFlow/sn/pageList)。
 */
export function queryUpstreamTraffic(params) {
  return request.get('/recharges/traffic/upstream', { params })
}
