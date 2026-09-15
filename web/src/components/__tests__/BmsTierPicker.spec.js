// BmsTierPicker 分档选择器测试（工单 #97）
//
// ⚠️ 本文件的存在理由：
//   #31 的原始事故是「UI 上开关看着是开的，调度器压根不跑」——
//   根因是**界面显示的档位与调度器实际消费的档位是两份独立定义**。
//   本组件把档位收敛到后端 schedule_tier（与 bms_scheduler 同源），
//   本测试则锁住「后端给什么档位 → 界面就归到哪一组、就不勾哪些源」这条链路，
//   防止后人为了"顺手"在前端补一份源名清单而重新制造漂移。

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'

import BmsTierPicker from '@/components/BmsTierPicker.vue'

/** 与生产 /fetch/data-sources 同形的夹具（default_sync=true 的 4 高频 + 3 低频 + 1 未纳入） */
const SOURCES = [
  { data_type: 'refund', name: '退费单管理', schedule_tier: 'high', has_credentials: true, default_sync: true, enabled: true },
  { data_type: 'ticket', name: '服务请求管理', schedule_tier: 'high', has_credentials: true, default_sync: true, enabled: true },
  { data_type: 'wallet', name: '提现操作记录', schedule_tier: 'high', has_credentials: true, default_sync: true, enabled: true },
  { data_type: 'cs_record', name: '智能助手处理记录', schedule_tier: 'high', has_credentials: true, default_sync: true, enabled: true },
  { data_type: 'device_combo', name: '设备套餐', schedule_tier: 'low', has_credentials: true, default_sync: true, enabled: true },
  { data_type: 'bms_order', name: '订单管理', schedule_tier: 'low', has_credentials: true, default_sync: true, enabled: true },
  { data_type: 'device_profile', name: '设备管理', schedule_tier: 'low', has_credentials: false, default_sync: true, enabled: true },
  { data_type: 'shanmao_refund', name: '闪猫退款管理', schedule_tier: 'off', has_credentials: true, default_sync: false, enabled: true },
]

function mountPicker(props = {}) {
  return mount(BmsTierPicker, {
    props: { modelValue: [], sources: SOURCES, lowFreqHour: 6, ...props },
    global: { plugins: [ElementPlus] },
  })
}

/** 取最近一次 update:modelValue 事件的值 */
function lastEmitted(wrapper) {
  const evts = wrapper.emitted('update:modelValue')
  return evts && evts.length ? evts[evts.length - 1][0] : undefined
}

/** 点某组里的按钮（按可见文本） */
function clickButtonIn(wrapper, groupSelector, text) {
  const btn = wrapper.findAll(`${groupSelector} button`).find(b => b.text().includes(text))
  expect(btn, `未找到按钮: ${text}`).toBeTruthy()
  return btn.trigger('click')
}

/** 取组内渲染出的复选框对应 data_type 列表（靠 :label 属性反查） */
function labelsIn(wrapper, groupSelector) {
  return wrapper.findAll(`${groupSelector} .el-checkbox input`).map(i => i.attributes('value'))
}

describe('BmsTierPicker · 按后端 schedule_tier 分组（#97）', () => {
  it('高频档 = schedule_tier=high 的源，且顺序与后端返回一致', () => {
    const w = mountPicker()
    expect(w.vm.highSources.map(d => d.data_type))
      .toEqual(['refund', 'ticket', 'wallet', 'cs_record'])
  })

  it('低频档 = schedule_tier=low 的源', () => {
    const w = mountPicker()
    expect(w.vm.lowSources.map(d => d.data_type))
      .toEqual(['device_combo', 'bms_order', 'device_profile'])
  })

  it('未纳入定时 = schedule_tier=off，且默认折叠（不参与"手滑全选"）', () => {
    const w = mountPicker()
    expect(w.vm.offSources.map(d => d.data_type)).toEqual(['shanmao_refund'])
    // 折叠状态下，off 档的复选框不应出现在 DOM 里
    expect(labelsIn(w, '.tier-group-off')).toEqual([])
  })

  it('缺少 schedule_tier 时兜底为 off（宁可不勾，不可误勾）', () => {
    const w = mountPicker({
      sources: [{ data_type: 'legacy_src', name: '旧后端源', default_sync: true }],
    })
    expect(w.vm.highSources).toEqual([])
    expect(w.vm.lowSources).toEqual([])
    expect(w.vm.offSources.map(d => d.data_type)).toEqual(['legacy_src'])
  })
})

describe('BmsTierPicker · 分组内批量（#97）', () => {
  it('「本组全选」只勾本组，不动其它组已勾选项', async () => {
    const w = mountPicker({ modelValue: ['device_combo'] })
    await clickButtonIn(w, '.tier-group-high', '本组全选')
    expect(lastEmitted(w)).toEqual(['device_combo', 'refund', 'ticket', 'wallet', 'cs_record'])
  })

  it('「本组清空」只清本组，保留其它组', async () => {
    const w = mountPicker({ modelValue: ['refund', 'ticket', 'device_combo'] })
    await clickButtonIn(w, '.tier-group-low', '本组清空')
    expect(lastEmitted(w)).toEqual(['refund', 'ticket'])
  })
})

describe('BmsTierPicker · 顶部总控（#97）', () => {
  it('全选 = 选中全部可选源（含 off 档，语义直观）', async () => {
    const w = mountPicker()
    await clickButtonIn(w, '.tier-head-actions', '全选')
    expect(lastEmitted(w)).toEqual(SOURCES.map(s => s.data_type))
  })

  it('清空 = 空数组', async () => {
    const w = mountPicker({ modelValue: ['refund'] })
    await clickButtonIn(w, '.tier-head-actions', '清空')
    expect(lastEmitted(w)).toEqual([])
  })

  it('反选 = 全量集合的补集', async () => {
    const w = mountPicker({ modelValue: ['refund', 'device_combo'] })
    await clickButtonIn(w, '.tier-head-actions', '反选')
    expect(lastEmitted(w)).toEqual(
      SOURCES.map(s => s.data_type).filter(dt => !['refund', 'device_combo'].includes(dt)))
  })
})

describe('BmsTierPicker · 防误跑的两个快捷动作（#97）', () => {
  it('「只选已配凭证」排除未配凭证的源', async () => {
    const w = mountPicker()
    await clickButtonIn(w, '.tier-head-actions', '只选已配凭证')
    // device_profile.has_credentials=false → 必须被排除
    expect(lastEmitted(w)).toEqual([
      'refund', 'ticket', 'wallet', 'cs_record', 'device_combo', 'bms_order', 'shanmao_refund',
    ])
  })

  it('「只选已配凭证」同时排除"需验证码但登录态未保存"的源（勾了必失败）', async () => {
    const w = mountPicker({
      sources: [
        { data_type: 'shanmao_refund', name: '闪猫退款管理', schedule_tier: 'low', requires_captcha: true, has_storage_state: false, has_credentials: true, default_sync: true, enabled: true },
        { data_type: 'refund', name: '退费单管理', schedule_tier: 'high', has_credentials: true, default_sync: true, enabled: true },
      ],
    })
    await clickButtonIn(w, '.tier-head-actions', '只选已配凭证')
    expect(lastEmitted(w)).toEqual(['refund'])
  })

  it('「恢复默认勾选」= 纳入定时（default_sync 且 enabled）的源', async () => {
    const w = mountPicker({ modelValue: ['shanmao_refund'] })
    await clickButtonIn(w, '.tier-head-actions', '恢复默认勾选')
    expect(lastEmitted(w)).toEqual([
      'refund', 'ticket', 'wallet', 'cs_record', 'device_combo', 'bms_order', 'device_profile',
    ])
  })
})

describe('BmsTierPicker · 节拍文案取自后端而非硬编码（#97）', () => {
  it('低频档标题用后端 low_freq_hour 渲染整点', () => {
    expect(mountPicker({ lowFreqHour: 6 }).text()).toContain('每天 06:00 跑一次')
    expect(mountPicker({ lowFreqHour: 3 }).text()).toContain('每天 03:00 跑一次')
  })

  it('低频档禁用（low_freq_hour=0）时给出明确提示', () => {
    expect(mountPicker({ lowFreqHour: 0 }).text()).toContain('当前低频档已禁用')
  })
})

describe('BmsTierPicker · 运行中禁用（#97）', () => {
  it('disabled=true 时全部批量按钮不可点', async () => {
    const w = mountPicker({ disabled: true })
    const btns = w.findAll('button:not([disabled])')
      .filter(b => /全选|清空|反选|只选已配凭证|恢复默认勾选/.test(b.text()))
    expect(btns).toEqual([])
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })
})
