<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">报表中心</div>
        <div class="page-sub">将分析视图导出为 Excel / 只读链接 / PDF 报告,供领导查看</div>
      </div>
      <el-tag type="info" effect="plain" size="large">规划中</el-tag>
    </div>

    <el-alert type="info" :closable="false" class="mb-16" show-icon>
      <template #title>该模块为规划占位,暂未实现</template>
      <div class="alert-body">
        导出质量取决于上游数据视图的成熟度。当前分析视图体系、组合条件与报告内容编排尚未完善,
        待沉淀面向领导的标准预设视图后,将按设计文档依次落地三种导出能力。
        详见 <el-link type="primary" :underline="false" @click="showDesign = true">导出功能设计文档</el-link>。
      </div>
    </el-alert>

    <el-row :gutter="16">
      <el-col :xs="24" :md="8" v-for="cap in capabilities" :key="cap.key">
        <el-card class="cap-card" shadow="hover">
          <div class="cap-header">
            <el-icon :size="28" :color="cap.color"><component :is="cap.icon" /></el-icon>
            <el-tag size="small" type="info" effect="plain">{{ cap.phase }}</el-tag>
          </div>
          <div class="cap-title">{{ cap.title }}</div>
          <div class="cap-desc">{{ cap.desc }}</div>
          <div class="cap-meta">
            <div class="meta-row"><span class="meta-label">适用场景</span>{{ cap.scene }}</div>
            <div class="meta-row"><span class="meta-label">技术方案</span>{{ cap.tech }}</div>
            <div class="meta-row"><span class="meta-label">权限</span>{{ cap.perm }}</div>
          </div>
          <el-button class="cap-btn" disabled plain>即将上线</el-button>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="mt-16" header="实施路线图">
      <el-timeline>
        <el-timeline-item v-for="(s, i) in roadmap" :key="i" :type="s.type" :timestamp="s.phase" placement="top">
          <strong>{{ s.title }}</strong>
          <div class="roadmap-desc">{{ s.desc }}</div>
        </el-timeline-item>
      </el-timeline>
    </el-card>

    <!-- 设计文档预览 -->
    <el-dialog v-model="showDesign" title="导出功能设计文档(摘要)" width="720px">
      <div class="design-body">
        <p><strong>定位:</strong>三种形式并存 — Excel(明细数据)、只读链接(免登录远程查看)、PDF(正式归档)。</p>
        <p><strong>架构:</strong>导出能力依附于分析视图(AnalysisView),不独立建数据源。</p>
        <ul>
          <li><strong>Excel</strong>:视图执行结果 rows → openpyxl 工作簿;多 segment 多 sheet</li>
          <li><strong>只读链接</strong>:生成 token 链接,公开页复用 ViewRender 渲染,可设有效期/吊销</li>
          <li><strong>PDF</strong>:基于报告模板(章节编排:view/text/chart),weasyprint + 图表服务端渲染</li>
        </ul>
        <p><strong>前置依赖:</strong>① 标准预设视图沉淀 ② 报告模板元数据 ③ 图表快照能力</p>
        <p class="muted">完整设计见 <code>.trae/documents/export-feature-design.md</code></p>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Document, Share, Files } from '@element-plus/icons-vue'

const showDesign = ref(false)

const capabilities = [
  {
    key: 'excel',
    title: 'Excel 数据导出',
    desc: '把分析视图的全量执行结果导出为 .xlsx,含表头冻结、自动列宽、筛选器,供二次透视。',
    scene: '明细数据下沉、财务核对',
    tech: 'openpyxl',
    perm: 'admin / supervisor',
    icon: Files,
    color: '#67c23a',
    phase: 'P3-1',
  },
  {
    key: 'share',
    title: '只读链接分享',
    desc: '生成带 token 的免登录只读链接,领导点开即可查看实时视图,可设有效期、随时吊销。',
    scene: '远程查看、微信/邮件分享',
    tech: 'ShareLink + 公开路由',
    perm: 'admin / supervisor 生成',
    icon: Share,
    color: '#409eff',
    phase: 'P3-2',
  },
  {
    key: 'pdf',
    title: 'PDF 报告',
    desc: '基于报告模板编排章节(视图+图表+文字说明),生成排版固定的 PDF,适合正式归档与会议汇报。',
    scene: '正式归档、打印、汇报',
    tech: 'weasyprint + 图表渲染',
    perm: 'admin / supervisor',
    icon: Document,
    color: '#e6a23c',
    phase: 'P3-3',
  },
]

const roadmap = [
  { phase: '前置 P-1', title: '沉淀面向领导的标准预设视图', desc: '月度运营概览、现金流汇总、退款挽回率等,在 ViewBuilder 中固化。', type: 'primary' },
  { phase: '前置 P-2', title: '报告内容编排元数据落地', desc: 'ReportTemplate 模型(章节结构:view/text/chart)。', type: 'primary' },
  { phase: 'P3-1', title: 'Excel 数据导出', desc: '最简,单视图 → xlsx,openpyxl 实现。', type: 'success' },
  { phase: 'P3-2', title: '只读链接分享', desc: 'ShareLink + 公开渲染页,独立免登录路由。', type: 'success' },
  { phase: 'P3-3', title: 'PDF 报告', desc: '模板编排 + 图表服务端渲染 + weasyprint,成本最高,最后做。', type: 'success' },
]
</script>

<style scoped>
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.alert-body {
  font-size: 13px;
  line-height: 1.6;
  margin-top: 4px;
}
.cap-card {
  height: 100%;
}
.cap-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.cap-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}
.cap-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  min-height: 62px;
}
.cap-meta {
  margin: 12px 0;
  padding: 10px 12px;
  background: #f5f7fa;
  border-radius: 4px;
}
.meta-row {
  font-size: 12px;
  color: #606266;
  line-height: 1.8;
}
.meta-label {
  display: inline-block;
  width: 64px;
  color: #909399;
}
.cap-btn {
  width: 100%;
}
.roadmap-desc {
  font-size: 13px;
  color: #606266;
  margin-top: 4px;
}
.design-body {
  font-size: 13px;
  line-height: 1.8;
  color: #303133;
}
.design-body p,
.design-body ul {
  margin: 6px 0;
}
.design-body .muted {
  color: #909399;
  font-size: 12px;
}
.design-body code {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 12px;
}
.mt-16 {
  margin-top: 16px;
}
</style>
