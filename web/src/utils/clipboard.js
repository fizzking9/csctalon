// 剪贴板工具 - 统一复制行为与降级策略
//
// 背景: navigator.clipboard 在非 HTTPS / 非 localhost 环境不可用(内网 IP 访问常见),
// 且各页面此前各自写 `navigator.clipboard?.writeText(...)`, 静默失败无提示。
// 本模块提供统一封装: 优先 Clipboard API, 失败则降级 execCommand, 最后抛错由调用方提示。

import { ElMessage } from 'element-plus'

/**
 * 复制文本到剪贴板。
 *
 * @param {string} text 要复制的内容
 * @returns {Promise<boolean>} 是否成功
 */
export async function copyText(text) {
  const value = text == null ? '' : String(text)
  if (!value) return false

  // 1) 标准 Clipboard API(需 HTTPS 或 localhost)
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch (e) {
      // 继续尝试降级方案
    }
  }

  // 2) 降级: 隐藏 textarea + execCommand('copy')
  //    内网 HTTP 访问时 Clipboard API 不可用, 这是必需的回退路径
  try {
    const ta = document.createElement('textarea')
    ta.value = value
    // 避免页面滚动跳动: 固定在视口内且不可见
    ta.style.position = 'fixed'
    ta.style.top = '0'
    ta.style.left = '0'
    ta.style.width = '1px'
    ta.style.height = '1px'
    ta.style.padding = '0'
    ta.style.border = 'none'
    ta.style.outline = 'none'
    ta.style.boxShadow = 'none'
    ta.style.background = 'transparent'
    ta.setAttribute('readonly', '')
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, value.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch (e) {
    return false
  }
}

/**
 * 复制并给出统一提示(常用场景快捷方法)。
 *
 * @param {string} text 要复制的内容
 * @param {string} label 提示语中的对象名(如「配置包」)
 * @returns {Promise<boolean>}
 */
export async function copyWithToast(text, label = '内容') {
  const ok = await copyText(text)
  if (ok) {
    ElMessage.success(`${label}已复制到剪贴板`)
  } else {
    ElMessage.error('复制失败, 请手动选中文本复制')
  }
  return ok
}
