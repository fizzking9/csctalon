/**
 * 状态语义色板(规划设计 v0.5 §6.2 横切硬规范)
 *
 * 五大状态族 → 颜色(对齐 Element Plus el-tag type):
 *   黄 (warning)  待处理/待审/待回看 - 需要动作
 *   蓝 (primary)  进行中/执行中     - 已启动
 *   绿 (success)   已完成/生效/闭环   - 结束
 *   红 (danger)    异常/失效/超期     - 需关注
 *   灰 (info)      归档/废止/已读     - 只读留存
 *
 * 用法:
 *   import { statusColor, statusLabel } from '@/utils/statusColor'
 *   <el-tag :type="statusColor(row.status)">{{ statusLabel(row.status) }}</el-tag>
 *
 * 新页面统一照此拼装, 老页面渐进对齐, 目标「不培训就会用」。
 */

// 五大状态族色板
export const STATUS_PALETTE = {
  pending: 'warning',   // 黄:待处理/待审/待回看
  processing: 'primary', // 蓝:进行中/执行中
  done: 'success',      // 绿:已完成/生效/闭环
  error: 'danger',      // 红:异常/失效/超期
  archived: 'info',      // 灰:归档/废止/已读
}

// 通用状态键 → 状态族映射(覆盖所有模块的状态值)
// 各模块状态值(中英文混合)按语义归类, 同族用同色。
const STATUS_FAMILY = {
  // ─ 黄:待处理/待审/待回看 ─
  pending: 'pending', todo: 'pending', wait: 'pending', waiting: 'pending',
  draft: 'pending', '草稿': 'pending', '待处理': 'pending', '待办': 'pending',
  '待审': 'pending', '待审核': 'pending', '待核对': 'pending', '待识别': 'pending',
  '待确认': 'pending', 'pending_approval': 'pending', 'pending_review': 'pending',
  '未读': 'pending', unread: 'pending', registered: 'pending', '登记': 'pending',

  // ─ 蓝:进行中/执行中 ─
  processing: 'processing', running: 'processing', active: 'processing',
  '进行中': 'processing', '执行中': 'processing', '处理中': 'processing',
  in_progress: 'processing', executing: 'processing', '已发布': 'processing',
  published: 'processing', open: 'processing', '正常': 'processing',

  // ─ 绿:已完成/生效/闭环 ─
  done: 'done', completed: 'done', finished: 'done', closed: 'done',
  '已完成': 'done', '已闭环': 'done', '已生效': 'done', '已通过': 'done',
  '已确认': 'done', '已识别': 'done', '已入账': 'done', '已报销': 'done',
  approved: 'done', 'approved': 'done', 'calmed': 'done', '已平息': 'done',
  reviewed: 'done', '已复盘': 'done', success: 'done',

  // ─ 红:异常/失效/超期 ─
  error: 'error', failed: 'error', overdue: 'error', invalid: 'error',
  '异常': 'error', '失效': 'error', '超期': 'error', '过期': 'error',
  rejected: 'error', '驳回': 'error', '有问题': 'error', '失败': 'error',
  '请假': 'error', '脱岗': 'error', '高风险': 'error', high: 'error',

  // ─ 灰:归档/废止/已读 ─
  archived: 'archived', obsolete: 'archived', inactive: 'archived',
  '已归档': 'archived', '废止': 'archived', '已读': 'archived', read: 'archived',
  '关闭': 'archived', '取消': 'archived', cancelled: 'archived', '已忽略': 'archived',
  ignored: 'archived',
}

// 中文标签(状态值 → 友好显示文本)
const STATUS_LABELS = {
  // 通用英文状态
  draft: '草稿', pending: '待处理', pending_approval: '待审批', published: '已发布',
  archived: '已归档', registered: '已登记', processing: '处理中', executing: '执行中',
  calmed: '已平息', reviewed: '已复盘', done: '已完成', closed: '已闭环',
  pending_review: '待回看', open: '进行中', closed: '已关闭', read: '已读',
  unread: '未读', active: '生效中', inactive: '已失效', ignored: '已忽略',
  confirmed: '已确认', success: '成功', failed: '失败', rejected: '已驳回',
  // 通用中文状态(原样返回)
  '草稿': '草稿', '待处理': '待处理', '待办': '待办', '待审': '待审',
  '待审核': '待审核', '待核对': '待核对', '待识别': '待识别', '待确认': '待确认',
  '已发布': '已发布', '已归档': '已归档', '进行中': '进行中', '执行中': '执行中',
  '处理中': '处理中', '已完成': '已完成', '已闭环': '已闭环', '已生效': '已生效',
  '已通过': '已通过', '已确认': '已确认', '已识别': '已识别', '已入账': '已入账',
  '已报销': '已报销', '已平息': '已平息', '已复盘': '已复盘', '未读': '未读',
  '已读': '已读', '正常': '正常', '顶班': '顶班', '请假': '请假', '调班': '调班',
  '异常': '异常', '失效': '失效', '超期': '超期', '废止': '废止',
  '驳回': '驳回', '有问题': '有问题', '需改进': '需改进', '通过': '通过',
  '已忽略': '已忽略', '关闭': '关闭', '取消': '取消',
}

/**
 * 取状态色板(el-tag type)
 * @param {string} status 状态值(中英文)
 * @returns {string} el-tag type: success/warning/primary/danger/info
 */
export function statusColor(status) {
  if (!status) return 'info'
  const family = STATUS_FAMILY[String(status).toLowerCase()] || STATUS_FAMILY[status]
  return STATUS_PALETTE[family] || 'info'
}

/**
 * 取状态中文标签
 * @param {string} status 状态值(中英文)
 * @returns {string} 友好显示文本
 */
export function statusLabel(status) {
  if (!status) return '-'
  return STATUS_LABELS[status] || STATUS_LABELS[String(status).toLowerCase()] || status
}

/**
 * 优先级颜色(辅助)
 */
export function priorityColor(p) {
  const k = String(p || '中')
  if (k.includes('高') || k === 'H' || k.includes('紧急') || k.includes('urgent')) return 'danger'
  if (k.includes('低') || k === 'L') return 'info'
  return 'warning'
}

/**
 * 级别颜色(高/中/低)
 */
export function levelColor(l) {
  const k = String(l || '').toLowerCase()
  if (k.includes('高') || k === 'high') return 'danger'
  if (k.includes('中') || k === 'medium') return 'warning'
  if (k.includes('低') || k === 'low') return 'info'
  return 'info'
}
