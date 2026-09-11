<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：客服主管打开页面后 10 秒内知道「该先处理什么」，不需要自己看图找结论。           │
  │ 逻辑：投诉分析页原本全是数字与图形，没有一个字回答"所以呢" → 本组件把后端算好的      │
  │       归因结论排成一行，按严重度(danger→warn→info→ok)排序，每条可点击下钻到对应      │
  │       明细页 —— **结论必须指向一个动作**，否则只是换个地方堆字。                      │
  │ 口径：结论文案由**后端生成**（`/complaints/overview` 的 `conclusions`），前端只渲染。 │
  │       原因：① 文案含口径判断(环比阈值/基数护栏/超期阈值)，放后端才能单测；            │
  │             ② 前端硬编码口径会导致与接口数字漂移(#67 病灶之一)。                      │
  │ 取舍：❌ 不做前端规则引擎(如自定义阈值告警) —— 阈值报警系统已有独立规划，本页只做展示。│
  │       ❌ 不做"无异常"的空白态 —— 无结论时显式说明原因(如对比期覆盖不足)，              │
  │       因为**沉默会被误读为"一切正常"**。                                      │
  │ 工单：#67 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §二.1         │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="cb-wrap">
    <div v-if="items.length" class="cb-list">
      <div
        v-for="(c, i) in items"
        :key="i"
        class="cb-item"
        :class="[`cb-${c.level}`, { 'cb-clickable': !!c.drill }]"
        @click="onClick(c)"
      >
        <span class="cb-dot" />
        <span class="cb-text">{{ c.text }}</span>
        <el-icon v-if="c.drill" class="cb-arrow"><ArrowRight /></el-icon>
      </div>
    </div>
    <!-- ★ 无结论时显式说明，不留空白(空白会被误读为"一切正常") -->
    <div v-else class="cb-empty">
      <el-icon><InfoFilled /></el-icon>
      <span>本期未触发任何结论（无超期积压、环比波动均在阈值内，或对比期数据不足）</span>
    </div>

    <div class="cb-foot">
      <span class="cb-foot-hint">
        结论由后端按口径生成 · 环比波动 &lt;20% 或对比期基数 &lt;5 条不提示 · 超期阈值 &gt;7 天
      </span>
      <el-popover placement="top-end" :width="420" trigger="click">
        <template #reference>
          <el-icon class="cb-info"><QuestionFilled /></el-icon>
        </template>
        <div class="dr-body">
          <div class="dr-title">结论条说明</div>
          <div class="dr-sec">
            <b>这些结论怎么来的</b>
            <div>后端按固定规则逐条判定：环比涨跌 ≥20% 且对比期基数 ≥5 条；积压超期（&gt;7 天）；
              升级率（内部/商户号投诉后升级到外部）；涉诉退款金额。</div>
          </div>
          <div class="dr-sec">
            <b>为什么小基数不报百分比</b>
            <div>3 条变 6 条就是 +100%，但业务上毫无意义 —— 会掩盖真正的恶化信号。</div>
          </div>
          <div class="dr-sec">
            <b>为什么按周/月看而不是按天</b>
            <div>投诉登记存在工作日节律，日环比噪声大。阈值报警按周/月对齐。</div>
          </div>
          <div class="dr-sec">
            <b>点一下会去哪</b>
            <div>每条结论（除"升级率"外）都可点击跳到对应明细页，便于直接跟进。</div>
          </div>
        </div>
      </el-popover>
    </div>
  </div>
</template>

<script setup>
/**
 * 结论条（#67 P1）—— 把数字翻译成"该干什么"。
 *
 * 纯展示组件：不计算结果、不做判断，只渲染后端给的 `conclusions`。
 * 点击 `drill` 时把意图冒泡给父级（父级知道自己的 tab 结构，组件不知道）。
 */
import { ArrowRight, InfoFilled, QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['drill'])

function onClick(c) {
  if (c?.drill) emit('drill', c.drill)
}
</script>

<style scoped>
.cb-wrap {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fcfcfd;
}
.cb-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cb-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  line-height: 1.5;
  padding: 4px 6px;
  border-radius: 6px;
}
.cb-clickable { cursor: pointer; transition: background 0.15s; }
.cb-clickable:hover { background: #f3f4f6; }
.cb-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.cb-danger .cb-dot { background: #dc2626; }
.cb-warn .cb-dot { background: #ea580c; }
.cb-info .cb-dot { background: #2563eb; }
.cb-ok .cb-dot { background: #16a34a; }
.cb-danger .cb-text { color: #b91c1c; font-weight: 600; }
.cb-warn .cb-text { color: #c2410c; font-weight: 600; }
.cb-info .cb-text { color: #1d4ed8; }
.cb-ok .cb-text { color: #15803d; }
.cb-arrow { color: #a0a4ab; font-size: 12px; }
.cb-empty {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #909399;
  padding: 4px 6px;
}
.cb-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #f1f3f5;
}
.cb-foot-hint { font-size: 11px; color: #b0b4bb; }
.cb-info { color: #909399; cursor: help; font-size: 14px; }
.dr-body { font-size: 12px; line-height: 1.6; color: #4b5563; }
.dr-title { font-weight: 700; font-size: 13px; color: #1f2937; margin-bottom: 8px; }
.dr-sec { margin-bottom: 8px; }
.dr-sec b { color: #374151; }

@media (max-width: 768px) {
  .cb-item { font-size: 12px; }
  .cb-foot-hint { font-size: 10px; }
}
</style>
