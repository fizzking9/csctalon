/**
 * 周区间计算单测（#78 顺带修复的存量 bug）
 *
 * 回归目标：`dayjs().startOf('week')` 的周起始是**周日**，
 * 旧写法 `.add(1, 'day')` 在「今天恰好是周日」时会把「本周」算成**下周（未来）**
 * → 运营看板指标全为 0。
 */
import { describe, it, expect } from 'vitest'
import dayjs from 'dayjs'
import { getWeekRange, getWeekRangeStr, weekLabel } from '../weekRange'

describe('getWeekRange（中国习惯：周一起始）', () => {
  it('周三 → 周一~周日', () => {
    // 2026-09-09 是周三
    const { start, end } = getWeekRange('2026-09-09')
    expect(start.format('YYYY-MM-DD')).toBe('2026-09-07')
    expect(end.format('YYYY-MM-DD')).toBe('2026-09-13')
  })

  it('★ 周日 → 本周仍是「刚过去的周一~今天所在周日」（回归：旧实现在此算成下周）', () => {
    // 2026-09-13 是周日
    const { start, end } = getWeekRange('2026-09-13')
    expect(start.format('YYYY-MM-DD')).toBe('2026-09-07')
    expect(end.format('YYYY-MM-DD')).toBe('2026-09-13')
    // 明确否定旧实现的错误结果
    expect(start.format('YYYY-MM-DD')).not.toBe('2026-09-14')
  })

  it('周一 → 本周起始就是当天', () => {
    const { start, end } = getWeekRange('2026-09-07')
    expect(start.format('YYYY-MM-DD')).toBe('2026-09-07')
    expect(end.format('YYYY-MM-DD')).toBe('2026-09-13')
  })

  it('起始为周一 00:00、结束为周日 23:59', () => {
    const { start, end } = getWeekRange('2026-09-09')
    expect(start.day()).toBe(1) // 1 = 周一
    expect(start.format('HH:mm:ss')).toBe('00:00:00')
    expect(end.format('HH:mm:ss')).toBe('23:59:59')
  })

  it('offsetWeeks=1 → 上一周', () => {
    const { start, end } = getWeekRange('2026-09-09', 1)
    expect(start.format('YYYY-MM-DD')).toBe('2026-08-31')
    expect(end.format('YYYY-MM-DD')).toBe('2026-09-06')
  })

  it('offsetWeeks 跨年不出错', () => {
    const { start } = getWeekRange('2026-01-05', 0) // 周一
    expect(start.format('YYYY-MM-DD')).toBe('2026-01-05')
    const prev = getWeekRange('2026-01-05', 1)
    expect(prev.start.format('YYYY-MM-DD')).toBe('2025-12-29')
  })
})

describe('getWeekRangeStr', () => {
  it('返回 YYYY-MM-DD 字符串', () => {
    const r = getWeekRangeStr('2026-09-09')
    expect(r.start).toBe('2026-09-07')
    expect(r.end).toBe('2026-09-13')
  })

  it('clampToNow=true 时，未来周的 end 不晚于今天', () => {
    // 用「未来一周」触发截断
    const r = getWeekRangeStr(undefined, -1, true)
    expect(dayjs(r.end).isAfter(dayjs().endOf('day'))).toBe(false)
  })

  it('clampToNow=false（历史周）不截断', () => {
    const r = getWeekRangeStr(undefined, 1, false)
    // 上一周整天都属于过去，无需截断
    expect(dayjs(r.end).isBefore(dayjs().endOf('day'))).toBe(true)
  })
})

describe('weekLabel', () => {
  it('本周 / 上周 / 日期区间', () => {
    expect(weekLabel(0)).toBe('本周')
    expect(weekLabel(1)).toBe('上周')
    expect(weekLabel(2)).toMatch(/^\d{2}-\d{2}~\d{2}-\d{2}$/)
  })
})
