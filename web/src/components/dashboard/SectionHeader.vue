<template>
  <h2 class="section-title" :class="{ 'with-sub': !!sub }">
    {{ title }}
    <span v-if="sub" class="section-sub">（{{ sub }}）</span>
    <slot name="suffix" />
    <!-- ★ #80 修复存量 bug: 本组件原先**只支持 `suffix` 插槽**，但调用方
         （如 `OperationDashboard.vue`）有 6 处写的是 `#extra` → 全部**静默不渲染**。
         实测确认的后果（改之前）：
           · 核心指标的 ⓘ 数据说明不显示
           · 趋势分析的「接待/投诉/退款」勾选框不显示 ← 使用者无法切换分组，
             这正是「趋势图里看不到退款/投诉」的直接原因
           · 人效表的部门/分组/按人筛选控件不显示
           · 退款挽回、投诉专项的跳转按钮不显示
         修法: 新增 `extra` 插槽并右对齐（不改变 `suffix` 的既有语义与位置）。
         取舍: 不把 `extra` 直接当作 `suffix` 的别名 —— 两者语义不同：
           `suffix` = 紧跟副标题的行内补充；`extra` = 区块右上角的操作区。 -->
    <span v-if="$slots.extra" class="section-extra">
      <slot name="extra" />
    </span>
  </h2>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  sub: { type: String, default: '' },
})
</script>

<style scoped>
.section-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 14px;
  padding-left: 10px;
  border-left: 3px solid #409eff;
  line-height: 1.2;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.section-sub {
  font-size: 12px;
  color: #909399;
  font-weight: 400;
}
/* ★ #80: 右上角操作区（筛选控件 / 跳转按钮 / ⓘ 说明）
   用 margin-left:auto 推到最右，与左侧标题分开，避免控件与标题挤在一起。 */
.section-extra {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
