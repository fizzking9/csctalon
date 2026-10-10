<!--
  提示词版本管理（2026-09-23 全量版本化）

  统一管理除 AI 报告 gen/extract 之外的两类服务提示词：
  - 治理研判：AI 数据治理分析师的 judge_system（角色铁律+输出结构）/ judge_user（研判任务）
  - 接入助手：投诉接入起草的 draft_system / draft_user
  AI 报告生成/摘取侧（gen_system/extract_system）带金丝雀冒烟，在「提示词评估」区块⑤管理。

  三类提示词统一纪律：任何改动（开发分析 / 手动 / 系统迭代）都入版本账本，
  每版附「解决什么问题 / 影响什么内容」，可激活、可回滚。
-->
<template>
  <div class="prompt-versions">
    <el-card shadow="never">
      <template #header>
        <div class="header">
          <span class="title">提示词版本</span>
          <el-tag size="small" type="info" effect="plain">admin</el-tag>
        </div>
        <div class="sub">
          所有提示词改动（开发分析 / 手动 / 系统迭代）均版本化留痕：每版记录「解决什么问题、影响什么内容」，
          新版本为草稿不自动生效，人工激活后即时切换，任意历史版本可一键回滚。
          AI 报告生成/摘取侧（含金丝雀冒烟）在
          <router-link to="/system/ai-prompt-eval">提示词评估</router-link>
          管理。
        </div>
      </template>

      <el-tabs v-model="domain">
        <el-tab-pane name="governance">
          <template #label>
            <span><el-icon class="tab-icon"><CircleCheck /></el-icon>治理研判提示词</span>
          </template>
          <PromptVersionManager
            v-if="domain === 'governance'"
            kind="governance"
            :codes="GOVERNANCE_CODES"
          />
        </el-tab-pane>
        <el-tab-pane name="onboarding">
          <template #label>
            <span><el-icon class="tab-icon"><EditPen /></el-icon>接入助手起草提示词</span>
          </template>
          <PromptVersionManager
            v-if="domain === 'onboarding'"
            kind="onboarding"
            :codes="ONBOARDING_CODES"
          />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { CircleCheck, EditPen } from '@element-plus/icons-vue'
import PromptVersionManager from '@/components/PromptVersionManager.vue'

const domain = ref('governance')

const GOVERNANCE_CODES = [
  { code: 'judge_system', label: '研判·系统提示（角色铁律+输出结构）' },
  { code: 'judge_user', label: '研判·用户提示（待研判任务）' },
]
const ONBOARDING_CODES = [
  { code: 'draft_system', label: '起草·系统提示（角色铁律+输出结构）' },
  { code: 'draft_user', label: '起草·用户提示（起草任务）' },
]
</script>

<style scoped>
.prompt-versions { padding: 16px; }
.header { display: flex; align-items: center; gap: 10px; }
.title { font-weight: 600; font-size: 15px; }
.sub { font-size: 12px; color: #909399; line-height: 1.7; margin-top: 6px; }
.sub a { color: #409eff; text-decoration: none; }
.tab-icon { vertical-align: -2px; margin-right: 4px; }
</style>
