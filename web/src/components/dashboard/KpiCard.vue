<template>
  <el-card shadow="hover" class="kpi-card" :class="{ 'is-compact': compact }" :body-style="compact ? 'padding: 10px 14px' : 'padding: 14px 18px'">
    <div class="kpi-head">
      <div v-if="icon" class="kpi-icon" :style="{ background: bg || '#ecf5ff', color: color || '#409eff' }">
        <el-icon :size="compact ? 18 : 22"><component :is="icon" /></el-icon>
      </div>
      <div class="kpi-label" :style="{ cursor: tip ? 'help' : 'default' }">
        {{ label }}
        <el-tooltip v-if="tip" effect="dark" placement="top" :content="tip">
          <el-icon style="margin-left:4px; color:#909399; vertical-align:-1px"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
    </div>
    <div class="kpi-value">
      <template v-if="money">{{ fmtMoney(value) }}</template>
      <template v-else>{{ fmtNum(value) }}<span v-if="unit" class="kpi-unit">{{ unit }}</span></template>
    </div>
    <!-- 接待量专用追加行: 接待数(去重设备近似独立客户人数) -->
    <div v-if="deviceCount != null" class="kpi-device-sub">
      <span class="kpi-device-sub-label">接待数</span>
      <span class="kpi-device-sub-val">{{ fmtNum(deviceCount) }}<span class="kpi-device-sub-unit"> 个</span></span>
    </div>
    <div v-if="!hideYoy" class="kpi-yoy">
      <el-tag v-if="yoyPct != null" size="small" effect="plain" :type="yoyTagType(yoyPct)">
        {{ yoyPct > 0 ? '↑' : '↓' }} {{ Math.abs(yoyPct) }}%
      </el-tag>
      <el-tag v-else size="small" type="info" effect="plain">{{ yoyLabel }} 0</el-tag>
      <span class="kpi-yoy-val">
        <template v-if="money">{{ fmtMoney(yoyValue) }}</template>
        <template v-else>{{ fmtNum(yoyValue) }}{{ unit || '' }}</template>
        · {{ yoyLabel }}
      </span>
    </div>
    <slot name="extra" />
  </el-card>
</template>

<script setup>
import { QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [Number, String], default: 0 },
  unit: { type: String, default: '' },
  money: { type: Boolean, default: false },
  icon: { type: [String, Object], default: null },
  bg: { type: String, default: '#ecf5ff' },
  color: { type: String, default: '#409eff' },
  // 环比百分比数值,正数=上升,负数=下降; 配合 yoyKey 判断颜色方向
  yoyPct: { type: Number, default: null },
  // 对比期绝对值(用于显示)
  yoyValue: { type: [Number, String], default: 0 },
  // 对比期名称 (日=昨日/周=上周/月=上月/季度=上季度)
  yoyLabel: { type: String, default: '昨日' },
  // 当 yoyKey 在 BAD_UP_KEYS 时, 数值越大越差(退款金额/投诉/现金流出): 上升显示红
  yoyKey: { type: String, default: '' },
  // 接待量追加行: 去重设备近似人数
  deviceCount: { type: Number, default: null },
  tip: { type: String, default: null },
  // 隐藏同比/环比行 (用于工作台首页, 没有同比数据的简洁卡片)
  hideYoy: { type: Boolean, default: false },
  // 紧凑模式: 缩小padding/字体/间距,适合核心指标条
  compact: { type: Boolean, default: false },
})

const BAD_UP_KEYS = new Set(['refund_amount', 'cash_out', 'complaint', 'refund_count'])

function yoyTagType(pct) {
  if (pct == null) return 'info'
  const good = BAD_UP_KEYS.has(props.yoyKey) ? pct < 0 : pct > 0
  if (pct === 0) return 'info'
  return good ? 'success' : 'danger'
}

function fmtNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}
function fmtMoney(v) {
  if (v == null) return '0.00'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
</script>

<style scoped>
.kpi-card { position: relative; transition: transform 0.15s; }
.kpi-card:hover { transform: translateY(-2px); }
.kpi-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.kpi-icon {
  width: 42px; height: 42px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 10px; flex-shrink: 0;
}
.kpi-label { color: #909399; font-size: 13px; flex: 1; line-height: 1.4; }
.kpi-value { font-size: 24px; font-weight: 600; color: #303133; margin-top: 4px; line-height: 1.2; font-variant-numeric: tabular-nums; }
.kpi-unit { font-size: 13px; font-weight: 400; color: #909399; margin-left: 4px; }
.kpi-yoy { margin-top: 8px; display: flex; align-items: center; gap: 6px; font-size: 12px; color: #909399; }
.kpi-yoy-val { color: #c0c4cc; font-size: 12px; }
.kpi-device-sub { margin-top: 5px; display: flex; align-items: baseline; gap: 6px; }
.kpi-device-sub-label { font-size: 11px; color: #909399; }
.kpi-device-sub-val { font-size: 15px; font-weight: 600; color: #409eff; letter-spacing: -0.3px; }
.kpi-device-sub-unit { font-size: 11px; font-weight: 400; color: #909399; margin-left: 2px; }

/* 紧凑模式 */
.kpi-card.is-compact .kpi-icon {
  width: 32px; height: 32px; border-radius: 6px;
  margin-bottom: 6px;
}
.kpi-card.is-compact .kpi-label { font-size: 12px; }
.kpi-card.is-compact .kpi-value { font-size: 20px; margin-top: 2px; }
.kpi-card.is-compact .kpi-unit { font-size: 12px; }
.kpi-card.is-compact .kpi-yoy { margin-top: 5px; font-size: 11px; }
.kpi-card.is-compact .kpi-yoy-val { font-size: 11px; }
.kpi-card.is-compact .kpi-device-sub { margin-top: 3px; }
.kpi-card.is-compact .kpi-device-sub-val { font-size: 13px; }
</style>
