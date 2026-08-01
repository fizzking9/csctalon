/**
 * 金额/数字格式化工具
 */

/**
 * 格式化金额为"万"为单位(≥1万时),2位小数
 * - ¥1234.5 → "1,234.50"
 * - ¥12345.67 → "1.23 万"
 * - ¥1234567 → "123.46 万"
 * 用于卡片数值、表格、tooltip 等展示
 */
export function formatMoney(v) {
  if (v == null || v === '') return '-'
  const n = Number(v)
  if (Number.isNaN(n)) return '-'
  const abs = Math.abs(n)
  if (abs >= 10000) {
    return (n / 10000).toFixed(2) + ' 万'
  }
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/**
 * 格式化 ECharts 坐标轴刻度值为"万"为单位
 * 用于 yAxis.axisLabel.formatter,数值除以 10000 并加"万"后缀
 */
export function formatAxisWan(v) {
  if (v == null) return ''
  const n = Number(v)
  if (Number.isNaN(n) || n === 0) return '0'
  return (n / 10000).toFixed(0) + '万'
}
