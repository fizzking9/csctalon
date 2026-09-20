import request from '@/api'

// AI 提示词评估中心 API（工单 #173）
// 契约见 docs/design/AI提示词评估中心-20260920.md §四 + backend/app/api/v1/admin_ai_prompt_eval.py。
// 评估端点全部同步 GET（聚合读）；suggest 产建议并落库为 proposed 行（去重）；
// confirm/apply 走 prompt_iterations 状态机（proposed→confirmed→applied/rejected）。
// 仅 admin；响应统一 {code, data, message}，拦截器已解包，业务侧取 res.data。

// ── 一、评估（只读聚合）───────────────────────────────────────────────
// 总览：命中分布 / 命中率 / live 占比 / 模板命中热度 / 规则健康度 / 高频兜底问句
export function getPromptEvalSummary(params) {
  return request.get('/admin/ai/prompt-eval/summary', { params })
}

// 模板级评估：命中率升序（问题模板在前）
export function getPromptEvalTemplates(params) {
  return request.get('/admin/ai/prompt-eval/templates', { params })
}

// 跨链路锚点一致性：生成侧 vs 摘取侧静态比对 + 报告实际 H2 漂移
export function getAnchorCheck(params) {
  return request.get('/admin/ai/prompt-eval/anchor-check', { params })
}

// 治理规则健康度：hit + 效果回填 + fp_ratio
export function getPromptEvalRules() {
  return request.get('/admin/ai/prompt-eval/governance-rules')
}

// ── 二、半自动迭代（写，全留痕）──────────────────────────────────────
// 生成建议并落库（同 target+新值去重），返回本次新增列表
export function createSuggestions(params) {
  return request.post('/admin/ai/prompt-eval/suggest', null, { params })
}

// 迭代建议列表（status / scope 过滤）
export function listIterations(params) {
  return request.get('/admin/ai/prompt-eval/iterations', { params })
}

// 人工拍板：approved=true → confirmed（可应用）；false → rejected
export function confirmIteration(id, approved = true) {
  return request.post(`/admin/ai/prompt-eval/iterations/${id}/confirm`, null, {
    params: { approved },
  })
}

// 应用建议（仅 template_keyword / template_question / rule_adjust）
export function applyIteration(id) {
  return request.post(`/admin/ai/prompt-eval/iterations/${id}/apply`)
}

// ── 三、人工意见（#174，只记录不自动执行）──────────────────
// 输入对分析结果的异常/不合理判断、业务逻辑理解调整与深化意见
// payload: {content, category(枚举), target_name?}
export function createOpinion(payload) {
  return request.post('/admin/ai/prompt-eval/opinions', payload)
}
