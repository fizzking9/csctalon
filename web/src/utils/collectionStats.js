/**
 * 采集结果展示辅助(#42 abc-c)
 *
 * 统一「拒收行 / 入库前校验」两类信息的展示口径, 供推送页与采集日志页复用。
 *
 * ## 为什么把这两个指标单独成列
 * 语义完全不同, 混在一起会误导:
 *   - **rejected(拒收)**: 行**未进入业务表**, 落 ingest_rejected_rows 待人工判定,
 *     默认不纳入统计 → 是"数据缺口", 需要人工处理。
 *   - **validation(校验)**: 行**已进入业务表**且可用于统计, 只是被打了
 *     quality_status=pending 标记, 进入治理队列 → 是"待治理", 不影响口径完整性。
 * 两者都不是「同步失败」(failed), 因此也不能写进 error 列/合并进"失败"统计
 * (#42 复审 N-2 曾因把校验写进 error 列而回退)。
 */

/**
 * 生成「拒收」标签的展示信息
 * @param {number|null|undefined} rejected 拒收行数
 * @returns {{show: boolean, text: string, tip: string}}
 */
export function rejectedTag(rejected) {
  const n = Number(rejected || 0)
  if (!n) {
    return { show: false, text: '', tip: '' }
  }
  return {
    show: true,
    text: `拒收 ${n}`,
    tip: `${n} 行未采纳，已落「数据质量-拒收数据」待人工判定（不纳入统计）`,
  }
}

/**
 * 生成「校验」标签的展示信息(含 block/warn 分级)
 * @param {number|null|undefined} count    校验问题总条数
 * @param {number|null|undefined} block    需优先处理(block)条数
 * @param {number|null|undefined} warn     常规处理(warn)条数
 * @returns {{show: boolean, text: string, tip: string}}
 */
export function validationTag(count, block, warn) {
  const n = Number(count || 0)
  if (!n) {
    return { show: false, text: '', tip: '' }
  }
  const b = Number(block || 0)
  const w = Number(warn || 0)
  return {
    show: true,
    text: `校验 ${n}`,
    tip:
      `入库前校验告警 ${n} 条：需优先处理 ${b} 条(block)、常规 ${w} 条(warn)。` +
      `数据已入库并打标，可在「数据质量」页治理`,
  }
}

/**
 * 是否需要显示"无"占位(两者都没有时)
 * @returns {boolean}
 */
export function showRejectPlaceholder(rejected, validationCount) {
  return !Number(rejected || 0) && !Number(validationCount || 0)
}
