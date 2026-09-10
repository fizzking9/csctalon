import request from '@/api'

// AI 与模型 - 统一管理入口 (#44, A-20260910-002 LOCKED)
// 🔒 安全约束: 模型配置接口为**脱敏只读**, 不返回 API Key 明文;
//    提示词预览为**只读**(只有 GET), 铁律/口径卡不可经 API 修改。

// 模型接入配置(脱敏: 只回是否配置 + 长度)
export function getAiModelConfig() {
  return request.get('/ai/model-config')
}

// 提示词四层总览(模型/铁律/口径卡/关注点)
export function getAiPromptLayers() {
  return request.get('/ai/prompt-layers')
}

// 单层正文预览(只读)
export function getAiPromptPreview(layer) {
  return request.get('/ai/prompt-preview', { params: { layer } })
}
