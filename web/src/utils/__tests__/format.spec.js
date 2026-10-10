// 金额格式化工具测试
// 覆盖 formatMoney / formatAxisWan 的展示口径(万元换算/千分位/边界)
import { describe, it, expect } from 'vitest'
import { formatMoney, formatAxisWan } from '../format.js'

describe('formatMoney', () => {
  it('null/空字符串 → "-"', () => {
    expect(formatMoney(null)).toBe('-')
    expect(formatMoney('')).toBe('-')
    expect(formatMoney(undefined)).toBe('-')
  })

  it('非数字 → "-"', () => {
    expect(formatMoney('abc')).toBe('-')
  })

  it('小于 1 万 → 千分位 + 2 位小数', () => {
    expect(formatMoney(1234.5)).toBe('1,234.50')
  })

  it('达到 1 万 → 万元换算 2 位小数', () => {
    expect(formatMoney(12345.67)).toBe('1.23 万')
  })

  it('大金额 → 万元换算', () => {
    expect(formatMoney(1234567)).toBe('123.46 万')
  })

  it('边界: 恰好 1 万 → 万元', () => {
    expect(formatMoney(10000)).toBe('1.00 万')
  })
})

describe('formatAxisWan', () => {
  it('null → 空字符串', () => {
    expect(formatAxisWan(null)).toBe('')
  })

  it('0 → "0"', () => {
    expect(formatAxisWan(0)).toBe('0')
  })

  it('坐标轴刻度 → 除以 1 万 + 万后缀(整数)', () => {
    expect(formatAxisWan(50000)).toBe('5万')
  })

  it('不足 1 万 → 0万', () => {
    expect(formatAxisWan(500)).toBe('0万')
  })
})
