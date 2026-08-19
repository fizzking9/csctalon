import { reactive } from 'vue'

// 全局单例分析弹窗状态(跨组件共享, 点击任意 SN/人员即可唤起)
const state = reactive({
  visible: false,
  kind: 'device', // 'device' | 'person'
  id: null,       // device → sn; person → person_id(可选)
  name: '',       // person → 人员姓名(用于按名搜索)
})

export function useAudit() {
  function openDeviceAudit(sn) {
    if (!sn) return
    state.kind = 'device'
    state.id = sn
    state.name = ''
    state.visible = true
  }

  function openPersonAudit(name, id = null) {
    if (name == null || name === '') return
    state.kind = 'person'
    state.id = id
    state.name = String(name)
    state.visible = true
  }

  // 快捷分析入口(无预设ID, 打开弹窗供用户搜索)
  function openQuickAnalysis(kind = 'device') {
    state.kind = kind
    state.id = null
    state.name = ''
    state.visible = true
  }

  function closeAudit() {
    state.visible = false
  }

  return { state, openDeviceAudit, openPersonAudit, openQuickAnalysis, closeAudit }
}