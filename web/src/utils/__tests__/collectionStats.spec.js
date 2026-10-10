import { describe, it, expect } from 'vitest'
import { rejectedTag, validationTag, showRejectPlaceholder } from '../collectionStats'

// #42 abc-c: 采集结果展示口径(拒收 / 入库前校验)
//
// 两个指标语义不同, 必须分开且文案明确:
//   rejected  = 行未进业务表(落拒收表待人工判定, 默认不纳入统计) → 数据缺口
//   validation= 行已进业务表可用(仅打标 pending 进治理队列)      → 待治理
// 二者都不是"同步失败"(failed), 也不应写进 error 列
// (复审 N-2 曾因把校验写进 error 列而回退)。

describe('rejectedTag', () => {
  it('未采纳行数大于 0 时显示, 文案含行数', () => {
    const t = rejectedTag(7)
    expect(t.show).toBe(true)
    expect(t.text).toBe('拒收 7')
  })

  it('提示语说明"未采纳/待人工判定/不纳入统计"三个关键语义', () => {
    const t = rejectedTag(3)
    expect(t.tip).toContain('未采纳')
    expect(t.tip).toContain('待人工判定')
    expect(t.tip).toContain('不纳入统计')
  })

  it('为 0 时不显示', () => {
    expect(rejectedTag(0).show).toBe(false)
  })

  it('null/undefined 当 0 处理(存量日志该列为 NULL)', () => {
    expect(rejectedTag(null).show).toBe(false)
    expect(rejectedTag(undefined).show).toBe(false)
    expect(rejectedTag(0).text).toBe('')
  })

  it('字符串数字也能识别(接口 JSON 可能返回字符串)', () => {
    expect(rejectedTag('5').show).toBe(true)
    expect(rejectedTag('5').text).toBe('拒收 5')
  })
})

describe('validationTag', () => {
  it('有告警时显示总数', () => {
    const t = validationTag(12, 3, 9)
    expect(t.show).toBe(true)
    expect(t.text).toBe('校验 12')
  })

  it('提示语含 block/warn 分级明细', () => {
    const t = validationTag(12, 3, 9)
    expect(t.tip).toContain('需优先处理 3 条(block)')
    expect(t.tip).toContain('常规 9 条(warn)')
  })

  it('提示语强调"已入库"以区别于拒收(避免误认为丢数据)', () => {
    const t = validationTag(2, 0, 2)
    expect(t.tip).toContain('已入库')
  })

  it('分级缺省时按 0 兜底(存量日志无 severity 列值)', () => {
    const t = validationTag(4)
    expect(t.tip).toContain('需优先处理 0 条(block)')
    expect(t.tip).toContain('常规 0 条(warn)')
  })

  it('为 0 时不显示', () => {
    expect(validationTag(0, 0, 0).show).toBe(false)
    expect(validationTag(null).show).toBe(false)
    expect(validationTag(undefined).show).toBe(false)
  })
})

describe('showRejectPlaceholder', () => {
  it('两者都为 0/NULL 时显示占位', () => {
    expect(showRejectPlaceholder(0, 0)).toBe(true)
    expect(showRejectPlaceholder(null, null)).toBe(true)
    expect(showRejectPlaceholder(undefined, undefined)).toBe(true)
  })

  it('任一有值时不显示占位', () => {
    expect(showRejectPlaceholder(1, 0)).toBe(false)
    expect(showRejectPlaceholder(0, 1)).toBe(false)
    expect(showRejectPlaceholder(3, 5)).toBe(false)
  })

  it('字符串 "0" 视为无值(接口可能返回字符串)', () => {
    expect(showRejectPlaceholder('0', '0')).toBe(true)
  })
})
