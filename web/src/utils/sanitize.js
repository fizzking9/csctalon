import { marked } from 'marked'
import createDOMPurify from 'dompurify'

marked.setOptions({ breaks: true, gfm: true })

/**
 * DOMPurify 实例: 浏览器下默认绑定全局 window;
 * node(测试/SSR)下用 createDOMPurify + 最小 window stub 初始化, 保证 renderMarkdown 可用。
 */
let DOMPurify
try {
  DOMPurify = createDOMPurify(globalThis.window)
} catch {
  // 无 window 环境兜底: 不初始化(renderMarkdown 会走 escapeHtml 安全分支)
  DOMPurify = null
}

/**
 * HTML 转义: 用于把后端返回的任意文本安全嵌入 HTML(P0-5 tooltip 注入防护)。
 * 例: 人员名/岗位/分组/专项 等字段来自同步数据, 可能含 < > " '
 */
export function escapeHtml(s) {
  if (s == null) return ''
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * 安全渲染 Markdown(P0-5 存储型 XSS 防护)。
 * 策略正文由管理员写入、对所有登录用户展示, marked.parse 后必须经 DOMPurify 消毒。
 * @param {string} md Markdown 源文
 * @returns {string} 消毒后的 HTML(可直接 v-html)
 */
export function renderMarkdown(md) {
  if (!md) return ''
  try {
    const raw = marked.parse(md)
    // DOMPurify 不可用时(无 window 环境)退化为纯转义, 保证永不注入可执行 HTML
    if (!DOMPurify) {
      return `<pre>${escapeHtml(md)}</pre>`
    }
    return DOMPurify.sanitize(raw, {
      // 允许常见 Markdown 渲染所需标签; 禁 script/iframe/on* 事件/ style 等
      ALLOWED_TAGS: [
        'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'pre', 'code', 'em', 'strong', 'del', 's', 'u', 'mark', 'sub', 'sup',
        'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'a', 'img', 'span', 'div',
      ],
      ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'target', 'rel', 'colspan', 'rowspan'],
      ALLOW_DATA_ATTR: false,
    })
  } catch {
    return `<pre>${escapeHtml(md)}</pre>`
  }
}
