import request from '@/api'

/**
 * 流量与充值查询 API 封装(占位,对应后端 recharges.py)
 *
 * 分两类:
 * 1) /recharges/device-combos/local* : 基于本库 device_combos 的本地查询
 *    (无上游时仍可用,数据来自 BMS/导出同步)
 * 2) /recharges/upstream + /recharges/traffic/upstream : 实时上游接口占位
 *    (需要你提供 BMS/运营商/支付侧的接口文档或抓包后,后端再填充)
 *
 * TODO: 真实上游接入后,前端无需改调用,仅后端占位内部 _upstream_* 补全即可。
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

// ---------- 上游实时(占位,未接入真实数据源) ----------

/**
 * 上游-实时充值订单查询
 * 后端已返回 _hint 提醒接入点。
 * 待提供:接口URL/鉴权(token/sign/mTLS)/参数签名/响应字段样例 或 抓包HAR。
 */
export function queryUpstreamRecharges(params) {
  return request.get('/recharges/upstream', { params })
}

/**
 * 上游-实时流量余额/使用量查询
 * 同上:未接入真实凭据,当前返回占位 _hint。
 */
export function queryUpstreamTraffic(params) {
  return request.get('/recharges/traffic/upstream', { params })
}
