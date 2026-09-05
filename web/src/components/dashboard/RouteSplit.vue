<template>
  <div class="route-split">
    <div v-if="!hideHeader" class="rs-header">
      <span class="rs-title">
        {{ compact ? '四路经营 vs 实际现金流' : '现金流出四路线拆分 & 对账偏差' }}
      </span>
      <el-tag size="small" type="info" effect="plain">A/B/C/D 四路 → 经营估算 vs 商户号实际</el-tag>
      <el-tooltip effect="dark" placement="top">
        <template #content>
          <div style="max-width:420px; line-height:1.85; font-size:12px">
            <div style="font-weight:600; margin-bottom:4px">四路经营侧出款 (经营报表导出, data_source=business)</div>
            <div>A. 退费单管理 → refund_method='线上'</div>
            <div>B. 钱包提现记录 → op_type='用户提现'</div>
            <div>C. 闪猫退款 → 独立业务表 (shanmao_refunds, status=已退款 计入)</div>
            <div>D. 其他退款 → 暂无数据 (=0)</div>
            <hr style="margin:8px 0; opacity:.3">
            <div style="font-weight:600; color:#409eff">经营估算 = A + B + C + D</div>
            <div style="font-weight:600; color:#f56c6c">实际现金流 (对账基准) = 退款记录报表(商户号退款)合计</div>
            <div style="font-weight:600">对账偏差 = 经营 − 实际</div>
          </div>
        </template>
        <el-icon style="color:#909399; cursor:help"><QuestionFilled /></el-icon>
      </el-tooltip>
    </div>

    <!-- 3 合计卡 -->
    <el-row :gutter="compact ? 8 : 12" class="sum-row">
      <el-col :xs="24" :sm="8">
        <div class="rs-sum rs-sum-op">
          <div class="rs-sum-head">经营估算</div>
          <div class="rs-sum-body">
            <span class="rs-sum-val">{{ fmtMoney(op.amount) }}</span>
            <span class="rs-sum-sub">{{ fmtNum(op.count) }}笔 · A+B+C+D</span>
          </div>
        </div>
      </el-col>
      <el-col :xs="24" :sm="8">
        <div class="rs-sum rs-sum-fc">
          <div class="rs-sum-head">实际现金流 <span class="tip">（对账基准）</span></div>
          <div class="rs-sum-body">
            <span class="rs-sum-val">{{ fmtMoney(fc.amount) }}</span>
            <span class="rs-sum-sub">{{ fmtNum(fc.count) }}笔 · 商户号退款</span>
          </div>
        </div>
      </el-col>
      <el-col :xs="24" :sm="8">
        <div class="rs-sum" :class="diff.amount >= 0 ? 'rs-sum-diff-pos' : 'rs-sum-diff-neg'">
          <div class="rs-sum-head">对账偏差 <span class="tip">（经营 − 实际）</span></div>
          <div class="rs-sum-body">
            <span class="rs-sum-val">{{ diff.amount >= 0 ? '+' : '' }}{{ fmtMoney(diff.amount) }}</span>
            <span class="rs-sum-sub">{{ diff.count >= 0 ? '+' : '' }}{{ fmtNum(diff.count) }}笔 · 差值</span>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 四路堆叠条 -->
    <div v-if="!compact || stackSegs.length" class="stack-wrap mt-8">
      <div class="stack-bar">
        <template v-for="(seg, k) in stackSegs" :key="k">
          <div class="stack-seg"
               :class="seg.cls"
               :style="{ width: seg.pct + '%', minWidth: seg.amount > 0 ? (seg.pct < 1.2 ? '48px' : 'auto') : '0' }"
               :title="seg.title">
            <span v-if="seg.pct >= 8 || (seg.amount > 0 && seg.pct >= 3)" class="seg-in">
              <b>{{ seg.label }}</b> {{ fmtMoney(seg.amount) }}
            </span>
            <span v-else-if="seg.amount > 0" class="seg-in seg-mini">{{ seg.label }}</span>
          </div>
        </template>
        <div v-if="stackSegs.every(s => s.amount === 0 || s.amount == null)"
             class="stack-seg seg-empty" style="width:100%">
          <span class="seg-in">暂无数据</span>
        </div>
      </div>
      <div class="stack-legend mt-6">
        <div v-for="(seg, k) in stackSegs" :key="'lg-'+k" class="lg-item">
          <i class="lg-dot" :class="seg.dotCls"></i>
          <span class="lg-name">{{ seg.name }}</span>
          <span class="lg-val">{{ fmtMoney(seg.amount) }}</span>
          <span class="lg-sub">{{ fmtNum(seg.count) }}笔</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps({
  // 支持两种数据结构:
  //  1) cash_routes: { operational, actual_cashflow/financial, diff, route_a/b/c/d } — 首页汇总
  //  2) cash_routes_today: 同上 — 今日看板单日切面
  routes: { type: Object, required: true },
  compact: { type: Boolean, default: false },
  // 隐藏内置头部 (当外层已提供卡头和说明时使用)
  hideHeader: { type: Boolean, default: false },
})

const op = computed(() => props.routes?.operational || { amount: 0, count: 0 })
const fc = computed(() => props.routes?.actual_cashflow ?? props.routes?.financial ?? { amount: 0, count: 0 })
const diff = computed(() => ({
  amount: Number(props.routes?.diff?.amount ?? 0),
  count: Number(props.routes?.diff?.count ?? 0),
}))

const stackSegs = computed(() => {
  const r = props.routes || {}
  const defs = [
    { key: 'route_a', label: 'A', name: 'A. 线上直退', cls: 'seg-a', dotCls: 'd-a' },
    { key: 'route_b', label: 'B', name: 'B. 用户取现', cls: 'seg-b', dotCls: 'd-b' },
    { key: 'route_c', label: 'C', name: 'C. 闪猫退款', cls: 'seg-c', dotCls: 'd-c' },
    { key: 'route_d', label: 'D', name: 'D. 手动退款', cls: 'seg-d', dotCls: 'd-d' },
  ]
  const totalAmt = Math.max(0, Number(op.value.amount ?? 0))
  return defs.map(d => {
    const row = r[d.key] || { amount: 0, count: 0 }
    const amt = Math.max(0, Number(row.amount ?? 0))
    const cnt = Math.max(0, Number(row.count ?? 0))
    const pct = totalAmt > 0 ? Math.min(100, amt / totalAmt * 100) : 0
    return {
      ...d,
      amount: amt,
      count: cnt,
      pct,
      title: `${d.name}\n退款金额: ¥${amt.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}\n退款笔数: ${cnt}笔\n占经营估算: ${pct.toFixed(1)}%`,
    }
  })
})

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
.route-split {
  padding: 10px 12px;
  background: linear-gradient(180deg,#fbfcfe,#f6f8fb);
  border: 1px solid #ebeef5;
  border-radius: 10px;
}
.rs-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.rs-title { font-weight: 600; font-size: 14px; }
.tip { font-size: 12px; color: #909399; font-weight: 400; }

.sum-row { margin-top: 6px; }
.rs-sum {
  position: relative;
  padding: 6px 10px 6px 12px;
  border-radius: 8px;
  background: #fff;
  margin-bottom: 4px;
}
.rs-sum-op { background:#ecf5ff; }
.rs-sum-fc { background:#fdf6ec; }
.rs-sum-diff-pos { background:#fef0f0; }
.rs-sum-diff-neg { background:#f0f9eb; }

.rs-sum-head {
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 6px;
}
.rs-sum-op .rs-sum-head { color:#409eff; }
.rs-sum-diff-pos .rs-sum-head { color:#f56c6c; }
.rs-sum-diff-neg .rs-sum-head { color:#67c23a; }

.rs-sum-body {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.rs-sum-val {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
}
.rs-sum-diff-pos .rs-sum-val { color:#f56c6c; }
.rs-sum-diff-neg .rs-sum-val { color:#67c23a; }
.rs-sum-sub {
  font-size: 12px;
  color: #606266;
  margin-top: 4px;
}

/* 堆叠条 */
.stack-wrap { padding: 0 2px; }
.stack-bar {
  display: flex;
  width: 100%;
  height: 32px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid #ebeef5;
  background: #fff;
}
.stack-seg {
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.3s;
  min-width: 0;
}
.seg-in { font-size: 12px; color:#fff; font-weight: 500; white-space: nowrap; padding: 0 6px; }
.seg-mini { font-size: 10px; opacity: 0.92; }
.seg-empty {
  background: #f5f7fa !important;
  color: #c0c4cc !important;
}
.seg-empty .seg-in { color: #909399 !important; }

.seg-a { background: linear-gradient(180deg,#409eff,#2563eb); }
.seg-b { background: linear-gradient(180deg,#67c23a,#3a9d2e); }
.seg-c { background: linear-gradient(180deg,#e6a23c,#d48806); opacity: .95; }
.seg-d { background: linear-gradient(180deg,#909399,#6b7280); opacity: .92; }

.stack-legend {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 6px 12px;
}
.lg-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.lg-dot { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.d-a { background: #409eff; }
.d-b { background: #67c23a; }
.d-c { background: #e6a23c; }
.d-d { background: #909399; }
.lg-name { color: #606266; flex-shrink: 0; }
.lg-val { font-weight: 600; color:#303133; }
.lg-sub { color: #909399; margin-left: auto; }

.mt-6 { margin-top: 6px; }
.mt-8 { margin-top: 8px; }
</style>
