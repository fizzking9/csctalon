import { reactive } from 'vue'

// AI 分析助手 - 全局单例状态(MainLayout 右上角按钮唤起, 跨组件共享)
const state = reactive({
  visible: false,
})

export function useAssistant() {
  function openAssistant() {
    state.visible = true
  }

  function closeAssistant() {
    state.visible = false
  }

  return { state, openAssistant, closeAssistant }
}
