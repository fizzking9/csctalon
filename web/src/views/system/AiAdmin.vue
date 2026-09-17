<!--
  ┌──────────────── 设计说明（Design Rationale） ────────────────────────────────┐
  │ 工单：#137（AI 管理页面 5 Tab 整合入口）+ #141（重组: 报告列表/事务通知拆出）   │
  │ 性质：REQ（具体功能/集成）— 非元需求，不进 AGENTS.md 引用链                    │
  │ 范围：本页 3 个 Tab 整合入口(模板/模型/提示词)，各 Tab 内嵌存量独立组件           │
  │ 目的（替谁/答何决策）：admin 一站式管理 AI 报告系统的「配置侧」——              │
  │       模板配置如何？模型节点是否健康？提示词是否更新？                          │
  │ 逻辑（为何这样组织）：按「日常使用频度」排序——模板（每周改）→                │
  │       模型（每月调）→ 提示词（季度更新）。全是配置类，是 AI 系统的"管控面"。    │
  │ 口径：各 Tab 内嵌存量独立组件，数据口径同原页面（不重复定义）。                  │
  │ 取舍（不做什么及原因）：
  │   ❌ 不删除旧路由 /system/ai-templates 等——保留书签直达，不破坏既有使用习惯    │
  │   ❌ 不做权限再分级——沿用 admin: true，与各子页面一致                          │
  │   ❌ #141 不再含「报告列表」Tab——报告是「查看侧」而非「配置侧」，              │
  │      拆出独立 AiReports.vue (/system/ai-reports)，归入「AI 与分析」分组，      │
  │      避免配置与查看混在一页（admin 看 AI 报告历史时要切到一个独立入口而非       │
  │      挤进配置 Tab 里翻）；旧路由 /admin/ai/reports API 复用，不新增列表接口     │
  │   ❌ #141 不再含「事务通知」Tab——事务通知是审计域功能，已在「审计与配置」        │
  │      分组下有独立菜单 /system/system-events，混在 AI 管理 Tab 里语义突兀         │
  │      （admin 查通知异常应该走审计侧，而非 AI 配置侧）                           │
  │ 设计文档：本文件头部（代码层）+ ⓘ 气泡（页面层）+                              │
  │           docs/standards/review-checklist.md（审查层）                         │
  └──────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="ai-admin">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">AI 管理</div>
        <div class="page-sub">模板 / 模型 / 提示词 — AI 系统配置侧一站入口</div>
      </div>
      <el-popover placement="bottom-end" :width="460" trigger="click">
        <template #reference>
          <el-button size="small" :icon="InfoFilled" circle />
        </template>
        <div class="rationale">
          <p><b>替谁：</b>admin（管理员）。AI 报告系统的「配置侧」（模板/模型节点/提示词）此前分散在多个独立路由，配置时需多页跳转。</p>
          <p><b>答何决策：</b>① 模板配置如何？② 模型节点是否健康？③ 提示词是否更新？</p>
          <p><b>怎么用：</b>Tab 按日常使用频度排序——① 模板（每周改）→ ② 模型（每月调）→ ③ 提示词（季度更新）。</p>
          <p><b>不做什么：</b></p>
          <ul>
            <li>❌ 不删除旧路由（保留书签直达）；</li>
            <li>❌ 不做权限再分级（沿用 admin: true）；</li>
            <li>❌ #141 不再含「报告列表」Tab（拆出 /system/ai-reports 独立菜单，归入「AI 与分析」分组）；</li>
            <li>❌ #141 不再含「事务通知」Tab（事务通知是审计域功能，走「审计与配置」分组的 /system/system-events）。</li>
          </ul>
          <p><b>设计文档：</b>本文件头部注释块（代码层）+ 本 ⓘ 气泡（页面层）+ docs/standards/review-checklist.md（审查层）。</p>
          <p><b>工单：</b>#137 + #141</p>
        </div>
      </el-popover>
    </div>

    <el-tabs v-model="tab" type="border-card" class="ai-admin-tabs">
      <!-- ═══ Tab1 模板管理（内嵌存量组件，单一真身） ═══ -->
      <el-tab-pane label="① 模板管理" name="templates" lazy>
        <AiTemplates />
      </el-tab-pane>

      <!-- ═══ Tab2 模型/算力节点（内嵌存量组件） ═══ -->
      <el-tab-pane label="② 模型节点" name="llm" lazy>
        <LlmProviders />
      </el-tab-pane>

      <!-- ═══ Tab3 提示词管理（内嵌存量组件） ═══ -->
      <el-tab-pane label="③ 提示词管理" name="focus" lazy>
        <AiFocusConfigs />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { InfoFilled } from '@element-plus/icons-vue'
import AiTemplates from './AiTemplates.vue'
import LlmProviders from './LlmProviders.vue'
import AiFocusConfigs from './AiFocusConfigs.vue'

// #141: 移除 Tab1 报告列表 + Tab5 事务通知(各自拆出独立路由)
//   - 报告列表 → /system/ai-reports (AiReports.vue)
//   - 事务通知 → /system/system-events (已有 SystemEvents.vue + 审计与配置菜单)
const tab = ref('templates')
</script>

<style scoped>
.ai-admin {
  padding: 16px;
}
.ai-admin-tabs {
  min-height: 400px;
}
.rationale p { margin: 6px 0; line-height: 1.6; }
.rationale ul { margin: 6px 0 6px 18px; padding: 0; }
.rationale li { margin: 2px 0; line-height: 1.6; }
</style>
