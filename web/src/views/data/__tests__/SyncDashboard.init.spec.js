// SyncDashboard 默认勾选初始化竞态测试（工单 #99）
//
// ⚠️ 本文件的存在理由（真实事故，2026-09-15 #97 实测时发现）：
//
//   BMS 与 SCRM 的「默认勾选」曾共用同一个 `typesInited` 标记，而 onMounted 用
//   Promise.all **并发**加载两者 → 谁先返回谁把标记置真，后者跳过自己的默认勾选赋值。
//   实测 SCRM 恒先返回（响应体远小于 BMS 的 11 源状态聚合），于是：
//     手动同步面板**每次打开都是 0 勾选**，主按钮灰显「共 0 个」，使用者被迫手点 11 次。
//   —— 连续刷新 5 次 100% 复现，是个"看起来只是没默认勾选、实则永远不勾"的死结。
//
//   修复 = 拆成两个独立标记（bmsInited / scrmInited）。
//
// 本测试用「SCRM 立即返回、BMS 延迟返回」**精确复现原竞态时序** ——
// 只断言最终 DOM 结果，不读组件内部变量，因此不依赖实现细节（改内部写法仍应通过）。

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

// ── 接口 mock：BMS 延迟返回，SCRM 立即返回（= 生产实测的时序）──
const BMS_DELAY_MS = 120

const BMS_SOURCES = [
  { data_type: 'refund', name: '退费单管理', schedule_tier: 'high', default_sync: true, enabled: true, has_credentials: true },
  { data_type: 'ticket', name: '服务请求管理', schedule_tier: 'high', default_sync: true, enabled: true, has_credentials: true },
  { data_type: 'device_combo', name: '设备套餐', schedule_tier: 'low', default_sync: true, enabled: true, has_credentials: true },
  { data_type: 'shanmao_refund', name: '闪猫退款管理', schedule_tier: 'off', default_sync: false, enabled: true, has_credentials: true },
]

const SCRM_ROWS = [
  { data_type: 'scrm_operator', name: 'SCRM · 客服操作员', default_sync: true, has_credentials: true },
  { data_type: 'scrm_session_group', name: 'SCRM · 群聊会话', default_sync: false, has_credentials: true },
]

vi.mock('@/api/fetch', () => ({
  // BMS：延迟返回 → 在 SCRM 之后完成
  listSyncDataSources: vi.fn(() => new Promise(resolve => {
    setTimeout(() => resolve({ data: BMS_SOURCES }), BMS_DELAY_MS)
  })),
  // SCRM：立即返回 → 先完成
  getScrmSyncStatus: vi.fn(() => Promise.resolve({ data: SCRM_ROWS })),
  taskPreflight: vi.fn(() => Promise.resolve({ data: { busy: false, cooldown_remaining: 0, time_ranges: [] } })),
  taskStatus: vi.fn(() => Promise.resolve({ data: { busy: false, task: null } })),
  listSyncLogs: vi.fn(() => Promise.resolve({ data: [] })),
  listScrmSyncLogs: vi.fn(() => Promise.resolve({ data: [] })),
  getScheduleStatus: vi.fn(() => Promise.resolve({
    data: { enabled: false, schedule_hours: [], low_freq_hour: 6, has_credentials: { bms: true, scrm: true } },
  })),
  getScheduleConfig: vi.fn(() => Promise.resolve({ data: {} })),
  updateScheduleConfig: vi.fn(() => Promise.resolve({ data: {} })),
  startSchedule: vi.fn(() => Promise.resolve({ data: {} })),
  stopSchedule: vi.fn(() => Promise.resolve({ data: {} })),
  testBrowser: vi.fn(() => Promise.resolve({ data: true })),
  captchaLoginCheck: vi.fn(() => Promise.resolve({ data: { has_storage_state: true } })),
  captchaLoginStart: vi.fn(() => Promise.resolve({ data: {} })),
  captchaLoginSubmit: vi.fn(() => Promise.resolve({ data: {} })),
  captchaLoginLogout: vi.fn(() => Promise.resolve({ data: {} })),
  taskSubmit: vi.fn(() => Promise.resolve({ data: { task_id: 't1', message: 'ok' } })),
  submitScrmSync: vi.fn(() => Promise.resolve({ data: { task_id: 's1', message: 'ok' } })),
}))
vi.mock('@/api/system', () => ({
  listSyncSources: vi.fn(() => Promise.resolve({ data: [] })),
  updateSyncSource: vi.fn(() => Promise.resolve({ data: {} })),
}))
// 部分 mock：只替换 useRoute（本页只需 query），保留 createRouter 等真实导出 ——
// 否则 `@/api/index.js → router/index.js` 的 createRouter 调用会因缺少导出而炸。
vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, useRoute: () => ({ query: {} }) }
})

import SyncDashboard from '@/views/data/SyncDashboard.vue'
import { useAuthStore } from '@/stores/auth'

/** 挂载页面并等两个接口都落地（含 BMS 的延迟） */
async function mountAndSettle() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = { role: 'agent', username: 'u' }  // 非管理员：少拉一路配置

  const wrapper = mount(SyncDashboard, {
    global: { plugins: [pinia, ElementPlus] },
  })
  // 等过 BMS 的延迟 + 微任务队列
  await new Promise(r => setTimeout(r, BMS_DELAY_MS + 80))
  await flushPromises()
  return wrapper
}

/** 从页面读取「已选 N / M」与三个分组的计数（纯 DOM 断言，不碰内部状态） */
function readPicked(wrapper) {
  const txt = wrapper.find('.tier-selected').text()
  const m = txt.match(/已选\s*(\d+)\s*\/\s*(\d+)/)
  const groups = wrapper.findAll('.tier-group').map(g => {
    const tag = g.find('.tier-group-head .el-tag').text()
    const count = g.find('.tier-group-count').text()
    return `${tag}=${count}`
  })
  return { picked: Number(m?.[1]), total: Number(m?.[2]), groups }
}

function syncButtonText(wrapper) {
  return wrapper.findAll('button').map(b => b.text()).find(t => t.includes('开始同步')) || ''
}

let wrapper
beforeEach(() => { wrapper = null })
afterEach(() => { if (wrapper) wrapper.unmount() })

describe('SyncDashboard · 默认勾选初始化竞态（#99）', () => {
  it('SCRM 先返回、BMS 后返回时，BMS 默认勾选**不丢**（回归原竞态）', async () => {
    wrapper = await mountAndSettle()
    const r = readPicked(wrapper)

    // 高频 2 个（refund/ticket）+ 低频 1 个（device_combo）默认勾选；off 档不勾
    expect(r.groups).toContain('高频档=2 / 2')
    expect(r.groups).toContain('低频档=1 / 1')
    expect(r.groups).toContain('未纳入定时=0 / 1')

    // 修复前此处恒为 0 —— 这正是原缺陷的判据
    expect(r.picked).toBeGreaterThan(0)
    expect(r.picked).toBe(3)
  })

  it('SCRM 默认勾选同样生效（两者互不吞并）', async () => {
    wrapper = await mountAndSettle()
    // scrm_operator default_sync=true → 勾；scrm_session_group false → 不勾
    const rows = wrapper.findAll('.el-form-item')
      .filter(f => f.text().includes('SCRM 数据源'))
    expect(rows.length).toBeGreaterThan(0)
    const checked = rows[0].findAll('.el-checkbox.is-checked')
    expect(checked.length).toBe(1)
    expect(checked[0].text()).toContain('客服操作员')
  })

  it('主按钮不再显示「共 0 个」（原缺陷的首屏症状）', async () => {
    wrapper = await mountAndSettle()
    const t = syncButtonText(wrapper)
    // 按钮统计的是 BMS + SCRM 合计：BMS 3（refund/ticket/device_combo）+ SCRM 1（scrm_operator）
    expect(t).toContain('共 4 个')
    expect(t).toContain('BMS 3 + SCRM 1')
    // 原缺陷症状：按钮灰显「共 0 个」，使用者无从下手
    expect(t).not.toContain('共 0 个')
  })

  it('两个初始化标记必须独立 —— 不得合并为单一标记（防后人重构合回去）', async () => {
    wrapper = await mountAndSettle()
    // 若两者共用标记，SCRM 先返回会把标记置真 → BMS 归 0。
    // 因此「BMS 有默认勾选」与「SCRM 有默认勾选」**同时成立**即证明标记独立。
    const bms = readPicked(wrapper)
    const scrmItem = wrapper.findAll('.el-form-item').find(f => f.text().includes('SCRM 数据源'))
    expect(bms.picked).toBeGreaterThan(0)
    expect(scrmItem.findAll('.el-checkbox.is-checked').length).toBeGreaterThan(0)
  })
})
