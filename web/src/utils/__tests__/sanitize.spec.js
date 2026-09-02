// XSS 防护工具测试
// 覆盖 escapeHtml(纯函数, 存储型 XSS 防护) + renderMarkdown(DOMPurify 消毒)
import { describe, it, expect } from 'vitest'
import { escapeHtml, renderMarkdown } from '../sanitize.js'

describe('escapeHtml', () => {
  it('null/undefined → 空字符串', () => {
    expect(escapeHtml(null)).toBe('')
    expect(escapeHtml(undefined)).toBe('')
  })

  it('转义 HTML 特殊字符', () => {
    expect(escapeHtml('<script>')).toBe('&lt;script&gt;')
    expect(escapeHtml('a&b')).toBe('a&amp;b')
    expect(escapeHtml('"quote"')).toBe('&quot;quote&quot;')
    expect(escapeHtml("it's")).toBe('it&#39;s')
  })

  it('普通文本原样返回', () => {
    expect(escapeHtml('普通文本 123')).toBe('普通文本 123')
  })
})

describe('renderMarkdown', () => {
  it('空输入 → 空字符串', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null)).toBe('')
  })

  it('正常 Markdown → 渲染为 HTML 标签', () => {
    const html = renderMarkdown('**加粗** 和 _斜体_')
    expect(html).toContain('<strong>')
    expect(html).toContain('<em>')
  })

  it('存储型 XSS 注入被消毒(无可执行 script 标签)', () => {
    const html = renderMarkdown('<script>alert(1)</script>')
    // 不可含未转义的可执行 <script 标签(转义后的 &lt;script&gt; 是安全的)
    expect(html).not.toContain('<script')
  })

  it('事件属性(onerror)不被注入为可执行属性', () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)">')
    // 不可含未转义的 onerror 属性(转义文本中的 onerror 字符串是安全的)
    expect(html).not.toMatch(/<img[^>]*\sonerror=/)
  })
})
