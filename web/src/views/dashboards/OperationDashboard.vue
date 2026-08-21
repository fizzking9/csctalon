<template>
  <div class="page-container operation-dashboard">
    <!-- 顶部标题 + 时间粒度切换 -->
    <div class="flex-between mb-16 td-header" style="position: relative; z-index: 2">
      <div class="page-title" style="margin: 0; white-space: nowrap">
        运营看板
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag size="small" :type="grainTagType" effect="plain" style="margin-left: 8px">{{ grainLabel }}</el-tag>
      </div>
      <FilterBar
        v-model:modelValue="quickGrainOpt"
        v-model:range="customDateRange"
        :quick-options="grainQuickOptions"
        :show-custom="true"
        show-grain="false"
        :loading="loading"
        @refresh="loadAll"
      >
        <template #left>
          <el-radio-group v-model="grain" size="small" style="margin-right: 12px">
            <el-radio-button value="week">周</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
            <el-radio-button value="quarter">季度</el-radio-button>
            <el-radio-button value="year">年</el-radio-button>
          </el-radio-group>
          <el-select v-if="grain === 'week'" v-model="weekOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="w in weekOptions" :key="w.value" :label="w.label" :value="w.value" />
          </el-select>
          <el-select v-if="grain === 'month'" v-model="monthOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="m in monthOptions" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
          <el-select v-if="grain === 'quarter'" v-model="quarterOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="q in quarterOptions" :key="q.value" :label="q.label" :value="q.value" />
          </el-select>
          <el-select v-if="grain === 'year'" v-model="yearOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="y in yearOptions" :key="y.value" :label="y.label" :value="y.value" />
          </el-select>
          <el-select v-if="grain === 'month' || grain === 'quarter' || grain === 'year'" v-model="trendGrain" size="small" style="width: 120px; margin-right: 8px">
            <el-option label="按日聚合" value="day" />
            <el-option label="按周聚合" value="week" />
            <el-option label="按月聚合" value="month" />
          </el-select>
        </template>
      </FilterBar>
    </div>

    <!-- 模块1: 核心指标KPI -->
    <section class="section-card mb-20">
      <SectionHeader :title="'核心指标'" :sub="'对比' + compareName + '同时段 · ' + periodName" />
      <el-row :gutter="10">
        <el-col :xs="12" :sm="8" :md="4" v-for="c in kpiCards" :key="c.key">
          <KpiCard
            :label="c.label"
            :value="c.value"
            :unit="c.unit"
            :money="c.money"
            :icon="c.icon"
            :bg="c.bg"
            :color="c.color"
            :yoy-key="c.yoy_key || c.key"
            :yoy-pct="c.yoy_pct"
            :yoy-value="c.yoy_value"
            :yoy-label="compareName"
            :device-count="c.key === 'reception' ? c.device_count : null"
            :tip="c.tip"
            compact
          />
        </el-col>
      </el-row>
    </section>

    <!-- 模块2: 趋势分析 -->
    <section class="section-card mb-24">
      <SectionHeader title="趋势分析" :sub="`${periodName} · ${rhythmSub}`">
        <template #extra>
          <el-checkbox-group v-model="trendSelectedGroups" size="small" class="trend-group-check">
            <el-checkbox-button value="reception">接待</el-checkbox-button>
            <el-checkbox-button value="complaint">投诉</el-checkbox-button>
            <el-checkbox-button value="refund">退款</el-checkbox-button>
          </el-checkbox-group>
        </template>
      </SectionHeader>
      <ChartCard body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="360px" />
      </ChartCard>
    </section>

    <!-- 模块3: 接待与退款汇总 -->
    <el-row :gutter="12" class="mb-24">
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <SectionHeader title="接待分项汇总" :sub="periodName" />
          <ChartCard title="接待分类分布（累计）">
            <template #extra>
              <el-radio-group v-model="receptionPerspective" size="small">
                <el-radio-button value="count">接待量(次)</el-radio-button>
                <el-radio-button value="device">设备量(台)</el-radio-button>
              </el-radio-group>
            </template>
            <BaseChart :option="receptionPieOption" height="260px" />
          </ChartCard>
        </section>
      </el-col>
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <SectionHeader title="退款分项汇总" :sub="periodName" />
          <ChartCard title="退款原因Top10（累计）">
            <BaseChart :option="refundReasonOption" height="260px" />
          </ChartCard>
        </section>
      </el-col>
    </el-row>

    <!-- 模块4: 工作效率值排名 -->
    <section class="section-card mb-24">
      <SectionHeader title="工作效率值排名" sub="按工作量得分排序 · 支持部门/分组筛选">
        <template #extra>
          <el-space wrap size="small">
            <el-select v-model="filterDepartment" placeholder="全部部门" clearable size="small" style="width: 120px">
              <el-option v-for="d in departmentOptions" :key="d" :label="d" :value="d" />
            </el-select>
            <el-select v-model="filterGroupId" placeholder="全部分组" clearable size="small" style="width: 140px">
              <el-option v-for="g in groupsList" :key="g.id" :label="g.name" :value="g.name" />
            </el-select>
            <el-radio-group v-model="staffFilter.aggregateBy" size="small">
              <el-radio-button label="person">按人</el-radio-button>
              <el-radio-button label="group">按分组</el-radio-button>
              <el-radio-button label="all">全员</el-radio-button>
            </el-radio-group>
            <el-checkbox
              v-model="showAllStaff"
              size="small"
              :disabled="staffFilter.aggregateBy !== 'person'"
            >
              显示所有人(含非企微)
            </el-checkbox>
          </el-space>
        </template>
      </SectionHeader>
      <el-card shadow="never">
        <el-table :data="staffRows" border stripe size="small" max-height="500"
          :default-sort="{ prop: 'eff_score', order: 'descending' }"
        >
          <el-table-column label="排名" width="64" align="center">
            <template #default="{ $index }">
              <el-tag v-if="$index < 3 && staffFilter.aggregateBy !== 'all'" size="small" :type="['danger','warning','success'][$index]" effect="dark">
                Top{{ $index + 1 }}
              </el-tag>
              <span v-else class="text-muted">{{ $index + 1 }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="人员" width="140">
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openPersonAudit(row.name, row.person_id)">{{ row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column v-else-if="staffFilter.aggregateBy === 'group'" label="分组" width="140">
            <template #default="{ row }">
              <el-tag size="default" effect="dark" type="primary">{{ row.name || '未分组' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="专项" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.current_specialty" size="small" effect="plain" type="warning">{{ row.current_specialty }}</el-tag>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column v-if="showDeptColumn" label="部门" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.department === '未设置' || !row.department ? 'info' : 'success'">
                {{ row.department || '—' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reception_count" label="会话数(次)" width="90" align="right" sortable :sort-method="(a,b)=>(Number(a.reception_count)||0)-(Number(b.reception_count)||0)">
            <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
          </el-table-column>
          <!-- 各专项接待数列 (按类型分开SN) -->
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="各专项接待数(个)" align="center">
            <el-table-column
              v-for="col in SPECIALTY_COLS"
              :key="col.key"
              :label="col.short"
              width="50"
              align="right"
            >
              <template #header>
                <span :style="{ color: col.color, fontWeight: 600, fontSize: '11px' }" :title="col.key">{{ col.short }}</span>
              </template>
              <template #default="{ row }">
                <span v-if="getSpecialtyPeople(row, col.key) > 0" :style="{ color: col.color, fontWeight: 500 }">
                  {{ formatNum(getSpecialtyPeople(row, col.key)) }}
                </span>
                <span v-else class="text-muted" style="font-size:11px">—</span>
              </template>
            </el-table-column>
          </el-table-column>
          <el-table-column prop="reception_device_count" label="接待合计" width="80" align="right" sortable :sort-method="(a,b)=>(Number(a.reception_device_count)||0)-(Number(b.reception_device_count)||0)">
            <template #default="{ row }">
              <span style="color:#409eff;font-weight:600">{{ formatNum(row.reception_device_count ?? 0) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="work_score" label="会话效率" width="90" align="right" sortable :sort-method="(a,b)=>(Number(a.work_score)||0)-(Number(b.work_score)||0)">
            <template #default="{ row }">
              <el-popover placement="top" :width="260" trigger="hover">
                <template #reference>
                  <el-tag size="small" effect="plain" :type="scoreTagType(row.work_score,500,300,100)" class="score-tag-hover">
                    <span style="font-weight:600">{{ formatNum(row.work_score) }}</span>
                  </el-tag>
                </template>
                <div class="score-tip">
                  <div class="score-tip-title">会话效率构成</div>
                  <div v-for="(v, k) in row.breakdown || {}" :key="k" class="score-tip-row">
                    <span class="score-tip-cat">{{ k }}</span>
                    <span class="score-tip-val">
                      {{ v.sessions }}次 × {{ v.coef }}
                      <el-tag v-if="!v.is_own" size="small" type="warning" effect="plain" style="margin-left:4px">转接</el-tag>
                    </span>
                    <span class="score-tip-sub">=</span>
                    <span class="score-tip-contrib">{{ v.work_contrib }}</span>
                  </div>
                  <div class="score-tip-total">合计 = {{ row.work_score }}</div>
                  <div v-if="row._calc_note?.weight_rule" class="score-tip-note">{{ row._calc_note.weight_rule }}</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
          <el-table-column prop="eff_score" label="接待效率" width="90" align="right" sortable :sort-method="(a,b)=>(Number(a.eff_score)||0)-(Number(b.eff_score)||0)">
            <template #default="{ row }">
              <el-popover placement="top" :width="260" trigger="hover">
                <template #reference>
                  <el-tag size="small" effect="plain" :type="scoreTagType(row.eff_score,350,200,80)" class="score-tag-hover">
                    <span style="font-weight:600;color:#2563eb">{{ formatNum(row.eff_score) }}</span>
                  </el-tag>
                </template>
                <div class="score-tip">
                  <div class="score-tip-title">接待效率构成</div>
                  <div v-for="(v, k) in row.breakdown || {}" :key="k" class="score-tip-row">
                    <span class="score-tip-cat">{{ k }}</span>
                    <span class="score-tip-val">
                      {{ v.people }}个 × {{ v.coef }}
                      <el-tag v-if="!v.is_own" size="small" type="warning" effect="plain" style="margin-left:4px">转接</el-tag>
                    </span>
                    <span class="score-tip-sub">=</span>
                    <span class="score-tip-contrib">{{ v.eff_contrib }}</span>
                  </div>
                  <div class="score-tip-total">合计 = {{ row.eff_score }}</div>
                  <div v-if="row._calc_note?.sn_merge_rule" class="score-tip-note">{{ row._calc_note.sn_merge_rule }}</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
          <el-table-column label="核算" width="48" align="center">
            <template #default="{ row }">
              <el-popover placement="left" :width="300" trigger="click">
                <template #reference>
                  <el-button link type="primary" size="small" class="calc-help">?</el-button>
                </template>
                <div class="score-tip">
                  <div class="score-tip-title">核算标准</div>
                  <div v-if="row.current_specialty" class="score-tip-row">
                    <span class="score-tip-cat">主专项</span>
                    <span>{{ row.current_specialty }}</span>
                  </div>
                  <div v-if="row.hr_category" class="score-tip-row">
                    <span class="score-tip-cat">归类</span>
                    <span>{{ row.hr_category }} ({{ row.is_management ? '管理岗·跨类全豁免' : '本职·按专项系数' }})</span>
                  </div>
                  <div class="score-tip-note">专项难度系数: 咨询×1.0 套餐×1.8 退款处理×2.0 退款挽回×2.2 内诉×3.0 外诉×5.0 · 转接×0.2</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </section>

    <!-- 模块5: 退款挽回专项分析 -->
    <section class="section-card mb-24" v-if="hasRecovery">
      <SectionHeader title="退款挽回专项分析" :sub="`${periodName} · 成功率统计`">
        <template #extra>
          <el-tag size="small" effect="dark" type="danger" style="margin-right:6px">{{ recoveryData.date }}~{{ recoveryData.date_end || recoveryData.date }}</el-tag>
          <el-button size="small" type="primary" plain @click="$router.push('/dashboards/refund')">
            查看退款分析详情 →
          </el-button>
        </template>
      </SectionHeader>

      <!-- KPI卡片行 -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="8">
          <div class="kpi-mini-card" style="background:#eff6ff">
            <div class="kpi-mini-label" style="color:#2563eb">总接待SN(分母)</div>
            <div class="kpi-mini-value" style="color:#2563eb">
              {{ formatNum(recoveryData.kpi.total_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="8">
          <div class="kpi-mini-card" :style="{background: Number(recoveryData.kpi.success_rate||0)>=38?'#f0fdf4':(Number(recoveryData.kpi.success_rate||0)>=30?'#fefce8':'#fef2f2')}">
            <div class="kpi-mini-label" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626')}">成功率</div>
            <div class="kpi-mini-value" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626')}">
              {{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="8">
          <div class="kpi-mini-card" style="background:#fef2f2">
            <div class="kpi-mini-label" style="color:#dc2626">挽留失败</div>
            <div class="kpi-mini-value" style="color:#dc2626">
              {{ formatNum(recoveryData.kpi.failed_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 人员挽回详情列表 -->
      <el-card shadow="never">
        <template #header>
          <div class="flex-between">
            <span style="font-weight:600">各人员挽回详情</span>
          </div>
        </template>
        <el-table :data="recoveryStaffList" size="small" border stripe>
          <el-table-column label="人员" width="140">
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openPersonAudit(row.name)">{{ row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="group" label="分组" width="120">
            <template #default="{ row }">{{ row.group || '—' }}</template>
          </el-table-column>
          <el-table-column prop="total_count" label="接待数" width="90" align="center" sortable />
          <el-table-column prop="success_count" label="成功数" width="90" align="center">
            <template #default="{ row }">
              <span style="color:#16a34a;font-weight:600">{{ row.success_count }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="failed_count" label="失败数" width="90" align="center">
            <template #default="{ row }">
              <span style="color:#dc2626;font-weight:600">{{ row.failed_count }}</span>
            </template>
          </el-table-column>
          <el-table-column label="成功率" width="100" align="center" sortable>
            <template #default="{ row }">
              <el-progress
                :percentage="Number((row.success_rate || 0) * 100).toFixed(1)"
                :color="row.success_rate >= 0.5 ? '#16a34a' : row.success_rate >= 0.3 ? '#ca8a04' : '#dc2626'"
                :stroke-width="8"
                style="width: 80px; display:inline-block"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </section>

    <!-- 模块6: 投诉专项分析 -->
    <section class="section-card mb-24">
      <SectionHeader title="投诉专项分析" :sub="`${periodName} · 三源统计`">
        <template #extra>
          <el-button size="small" type="primary" plain @click="$router.push('/dashboards/complaint-analysis')">
            投诉分析 →
          </el-button>
        </template>
      </SectionHeader>

      <!-- 三源 KPI (点击即作为下方明细表的来源筛选) -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="8">
          <div
            class="kpi-mini-card kpi-clickable"
            :class="{ 'kpi-active': complaintSourceFilter === '内部投诉' }"
            style="background:#eff6ff"
            @click="toggleComplaintSourceFilter('内部投诉')"
          >
            <div class="kpi-mini-label" style="color:#2563eb">内部投诉</div>
            <div class="kpi-mini-value" style="color:#2563eb">{{ formatNum(complaintSummaryData.internal?.total) }}</div>
            <div class="kpi-mini-hint">点击筛选明细</div>
          </div>
        </el-col>
        <el-col :xs="8">
          <div
            class="kpi-mini-card kpi-clickable"
            :class="{ 'kpi-active': complaintSourceFilter === '商户号投诉' }"
            style="background:#f5f3ff"
            @click="toggleComplaintSourceFilter('商户号投诉')"
          >
            <div class="kpi-mini-label" style="color:#7c3aed">商户号投诉</div>
            <div class="kpi-mini-value" style="color:#7c3aed">{{ formatNum(complaintSummaryData.merchant?.total) }}</div>
            <div class="kpi-mini-hint">点击筛选明细</div>
          </div>
        </el-col>
        <el-col :xs="8">
          <div
            class="kpi-mini-card kpi-clickable"
            :class="{ 'kpi-active': complaintSourceFilter === '外部投诉' }"
            style="background:#fef2f2"
            @click="toggleComplaintSourceFilter('外部投诉')"
          >
            <div class="kpi-mini-label" style="color:#dc2626">外部投诉</div>
            <div class="kpi-mini-value" style="color:#dc2626">{{ formatNum(complaintSummaryData.external?.total) }}</div>
            <div class="kpi-mini-hint">点击筛选明细</div>
          </div>
        </el-col>
      </el-row>

      <!-- 来源分布 + 处理人Top -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="24" :md="12">
          <ChartCard title="投诉来源分布">
            <BaseChart :option="complaintSourcePie" height="240px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="12">
          <ChartCard title="处理人 Top10">
            <BaseChart :option="complaintHandlerOption" height="240px" />
          </ChartCard>
        </el-col>
      </el-row>

      <!-- 明细表 -->
      <el-card shadow="never">
        <template #header>
          <div class="flex-between">
            <span style="font-weight:600">
              投诉明细（{{ filteredComplaintList.length }} 条<span v-if="complaintList.length !== filteredComplaintList.length"> / 共 {{ complaintList.length }} 条</span>）
            </span>
            <div class="flex gap-8" style="flex-wrap:wrap">
              <el-tag v-if="complaintSourceFilter" closable size="small" effect="dark" @close="complaintSourceFilter = ''">来源: {{ complaintSourceFilter }}</el-tag>
              <el-tag v-if="complaintCategoryFilter" closable size="small" effect="plain" @close="complaintCategoryFilter = ''">分类: {{ complaintCategoryFilter }}</el-tag>
              <el-tag v-if="complaintStatusFilter" closable size="small" effect="plain" type="warning" @close="complaintStatusFilter = ''">状态: {{ complaintStatusFilter }}</el-tag>
              <el-button v-if="complaintSourceFilter || complaintCategoryFilter || complaintStatusFilter" text size="small" @click="clearComplaintFilters">清除筛选</el-button>
            </div>
          </div>
        </template>
        <el-table :data="filteredComplaintList" size="small" border stripe max-height="400">
          <el-table-column label="来源" width="100" :filters="complaintSourceFilters" :filter-method="(v,row)=>row.source===v" filter-placement="bottom-end">
            <template #default="{ row }">
              <el-tag :type="complaintSourceType(row.source)" size="small" effect="plain">{{ row.source }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="130" sortable :sort-method="(a,b)=>sortTimeStr(a.time,b.time)">
            <template #default="{ row }">{{ formatShortTime(row.time) }}</template>
          </el-table-column>
          <el-table-column label="设备SN" width="140" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn || '—' }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="分类" width="130" show-overflow-tooltip :filters="complaintCategoryFilters" :filter-method="(v,row)=>row.category===v" filter-placement="bottom-end" />
          <el-table-column prop="problem" label="投诉内容" min-width="180" show-overflow-tooltip />
          <el-table-column label="处理/跟进" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler || '—' }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="结果/状态" width="110" show-overflow-tooltip :filters="complaintStatusFilters" :filter-method="(v,row)=>row.status===v" filter-placement="bottom-end" />
        </el-table>
      </el-card>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { today } from '@/api/analytics'
import { listGroups } from '@/api/group'
import { complaintSummary, complaintDetail } from '@/api/complaint'
import BaseChart from '@/components/BaseChart.vue'
import { useAudit } from '@/composables/useAudit'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import weekOfYear from 'dayjs/plugin/weekOfYear'
dayjs.extend(isoWeek)
dayjs.extend(quarterOfYear)
dayjs.extend(weekOfYear)

const loading = ref(false)
const isMobile = ref(false)
let _mq = null

// ========== 时间选择 ==========
const grain = ref('week')
const weekOffset = ref(0)
const monthOffset = ref(0)
const quarterOffset = ref(0)
const yearOffset = ref(0)
const customDateRange = ref(null)
const trendGrain = ref('day')
const quickGrainOpt = ref('all')
const grainQuickOptions = [{ label: '全部', value: 'all' }]

// ========== 人员筛选 ==========
const filterDepartment = ref('')
const filterGroupId = ref(null)
const groupsList = ref([])

const staffFilter = reactive({
  aggregateBy: 'person',
})

// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
const showAllStaff = ref(false)

// 审计弹窗(点击人员/设备唤起)
const { openPersonAudit, openDeviceAudit } = useAudit()

// ========== 图表配置 ==========
const receptionPerspective = ref('count')

// 趋势图: 默认只显示接待,可勾选投诉/退款
const trendSelectedGroups = ref(['reception'])

// 工作效率表: 各专项接待明细列 (按类型分开SN/接待数)
const SPECIALTY_COLS = [
  { key: '咨询接待', short: '咨询', color: '#3b82f6' },
  { key: '套餐专项', short: '套餐', color: '#f97316' },
  { key: '退款处理', short: '处理', color: '#ef4444' },
  { key: '退款挽回', short: '挽回', color: '#06b6d4' },
  { key: '内部投诉', short: '内诉', color: '#7c3aed' },
  { key: '外部投诉', short: '外诉', color: '#dc2626' },
]
function getSpecialtyPeople(row, catKey) {
  const bd = row.breakdown || {}
  const info = bd[catKey]
  return info ? Number(info.people || 0) : 0
}

// ========== 数据存储 ==========
const kpiData = ref({})
const hourlyData = ref({})
const receptionData = ref({})
const refundData = ref({})
const staffData = ref({})
const recoveryData = ref({})
const hasRecovery = computed(() => recoveryData.value && recoveryData.value.kpi)

// 投诉专项数据
const complaintSummaryData = ref({})
const complaintList = ref([])

// 投诉明细表 筛选/排序状态 (KPI 卡片点击 = 来源筛选)
const complaintSourceFilter = ref('')
const complaintCategoryFilter = ref('')
const complaintStatusFilter = ref('')

function toggleComplaintSourceFilter(src) {
  complaintSourceFilter.value = complaintSourceFilter.value === src ? '' : src
}
function clearComplaintFilters() {
  complaintSourceFilter.value = ''
  complaintCategoryFilter.value = ''
  complaintStatusFilter.value = ''
}
// 时间字符串排序(ISO), 支持空值排在后
function sortTimeStr(a, b) {
  const va = a || ''
  const vb = b || ''
  if (va === vb) return 0
  return va < vb ? -1 : 1
}

// 来源/分类/状态 可选项(el-table :filters 需要 {text,value} 形式)
const complaintSourceFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.source).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})
const complaintCategoryFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.category).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})
const complaintStatusFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.status).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})

// 明细表最终数据 = 来源(KPI点击/列内筛选二者合并) + 分类 + 状态 三级筛选
const filteredComplaintList = computed(() => {
  let list = complaintList.value || []
  if (complaintSourceFilter.value) {
    list = list.filter(r => r.source === complaintSourceFilter.value)
  }
  if (complaintCategoryFilter.value) {
    list = list.filter(r => r.category === complaintCategoryFilter.value)
  }
  if (complaintStatusFilter.value) {
    list = list.filter(r => r.status === complaintStatusFilter.value)
  }
  return list
})

// ========== 选项 ==========
const weekOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const d = dayjs().subtract(i, 'week')
    const ws = d.startOf('week').add(1, 'day')
    const we = ws.add(6, 'day')
    const label = i === 0 ? '本周' : i === 1 ? '上周' : `${ws.format('MM-DD')}~${we.format('MM-DD')}`
    opts.push({ label, value: i })
  }
  return opts
})
const monthOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const d = dayjs().subtract(i, 'month')
    const label = i === 0 ? '本月' : i === 1 ? '上月' : d.format('YYYY年MM月')
    opts.push({ label, value: i })
  }
  return opts
})
const quarterOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 8; i++) {
    const d = dayjs().subtract(i, 'quarter')
    const label = i === 0 ? '本季度' : i === 1 ? '上季度' : `${d.year()}年Q${d.quarter()}`
    opts.push({ label, value: i })
  }
  return opts
})
const yearOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 3; i++) {
    const d = dayjs().subtract(i, 'year')
    const label = i === 0 ? '今年' : i === 1 ? '去年' : `${d.year()}年`
    opts.push({ label, value: i })
  }
  return opts
})

// ========== 标签 ==========
const grainLabel = computed(() => {
  const gMap = { week: '周视图', month: '月视图', quarter: '季度视图', year: '年视图' }
  const tMap = { day: '·按日', week: '·按周', month: '·按月' }
  return (gMap[grain.value] || '周视图') + (tMap[trendGrain.value] || '')
})
const grainTagType = computed(() => ({ week: 'primary', month: 'warning', quarter: 'danger', year: 'info' }[grain.value] || 'info'))

const periodName = computed(() => {
  if (customDateRange.value) return '自定义范围'
  switch (grain.value) {
    case 'week': {
      const i = weekOffset.value
      return i === 0 ? '本周' : i === 1 ? '上周' : `第${dayjs().subtract(i, 'week').isoWeek()}周`
    }
    case 'month': {
      const i = monthOffset.value
      return i === 0 ? '本月' : i === 1 ? '上月' : dayjs().subtract(i, 'month').format('YYYY年MM月')
    }
    case 'quarter': {
      const i = quarterOffset.value
      return i === 0 ? '本季度' : i === 1 ? '上季度' : `${dayjs().subtract(i, 'quarter').year()}年Q${dayjs().subtract(i, 'quarter').quarter()}`
    }
    case 'year': {
      const i = yearOffset.value
      return i === 0 ? '今年' : i === 1 ? '去年' : `${dayjs().subtract(i, 'year').year()}年`
    }
  }
  return ''
})
const compareName = computed(() => {
  if (customDateRange.value) return '对比期'
  switch (grain.value) {
    case 'week': {
      const i = weekOffset.value + 1
      return i === 1 ? '上周' : `前${i}周`
    }
    case 'month': {
      const i = monthOffset.value + 1
      return i === 1 ? '上月' : `前${i}月`
    }
    case 'quarter': {
      const i = quarterOffset.value + 1
      return i === 1 ? '上季度' : `前${i}季`
    }
    case 'year': {
      const i = yearOffset.value + 1
      return i === 1 ? '去年' : `前${i}年`
    }
  }
  return ''
})

const displayDate = computed(() => periodName.value)

const rhythmSub = computed(() => {
  if (grain.value === 'week') return '按日聚合'
  if (grain.value === 'month') return '按日聚合'
  return `按${trendGrain.value === 'day' ? '日' : trendGrain.value === 'week' ? '周' : '月'}聚合`
})

// ========== 日期范围计算 ==========
function getDateRange() {
  if (customDateRange.value) {
    return { start: customDateRange.value[0], end: customDateRange.value[1] }
  }
  const now = dayjs()
  let start, end
  switch (grain.value) {
    case 'week': {
      const offset = weekOffset.value
      const weekStart = now.subtract(offset, 'week').startOf('week').add(1, 'day')
      start = weekStart.format('YYYY-MM-DD')
      end = weekStart.add(6, 'day').format('YYYY-MM-DD')
      break
    }
    case 'month': {
      const offset = monthOffset.value
      const monthDate = now.subtract(offset, 'month')
      start = monthDate.startOf('month').format('YYYY-MM-DD')
      end = monthDate.endOf('month').format('YYYY-MM-DD')
      break
    }
    case 'quarter': {
      const offset = quarterOffset.value
      const quarterDate = now.subtract(offset, 'quarter')
      start = quarterDate.startOf('quarter').format('YYYY-MM-DD')
      end = quarterDate.endOf('quarter').format('YYYY-MM-DD')
      break
    }
    case 'year': {
      const offset = yearOffset.value
      const yearDate = now.subtract(offset, 'year')
      start = yearDate.startOf('year').format('YYYY-MM-DD')
      end = yearDate.endOf('year').format('YYYY-MM-DD')
      break
    }
  }
  return { start, end }
}

// ========== KPI卡 ==========
function _pctDelta(cur, prev) {
  if (prev == null || Number(prev) === 0) return null
  return Math.round(((Number(cur) - Number(prev)) / Number(prev)) * 1000) / 10
}
const kpiCards = computed(() => {
  const d = kpiData.value || {}
  const reception = d.reception || {}
  const refundCnt = d.refund_count || {}
  const refundAmt = d.refund_amount || {}
  const complaint = d.complaint || {}
  const recoveryRate = recoveryData.value?.kpi?.success_rate
  return [
    { key: 'reception', label: '接待数', value: reception.device_count, unit: '个', icon: 'Service', bg: '#eff6ff', color: '#2563eb', yoy_key: 'reception', yoy_pct: _pctDelta(reception.device_count, reception.device_count_yoy), yoy_value: reception.device_count_yoy, device_count: null, tip: '接待设备去重数' },
    { key: 'session', label: '会话数', value: reception.value, unit: '次', icon: 'ChatDotRound', bg: '#f0fdf4', color: '#16a34a', yoy_key: 'session', yoy_pct: reception.yoy_pct, yoy_value: reception.yoy_value, tip: '含客服接待+系统处理' },
    { key: 'refund_count', label: '退款笔数', value: refundCnt.value, unit: '笔', icon: 'Warning', bg: '#fef2f2', color: '#dc2626', yoy_key: 'refund_count', yoy_pct: refundCnt.yoy_pct, yoy_value: refundCnt.yoy_value, tip: '四路经营合计 A+B+C+D' },
    { key: 'refund_amount', label: '退款金额', value: refundAmt.value, money: true, icon: 'Money', bg: '#fff7ed', color: '#ea580c', yoy_key: 'refund_amount', yoy_pct: refundAmt.yoy_pct, yoy_value: refundAmt.yoy_value, tip: '四路经营合计退款金额' },
    { key: 'complaint', label: '投诉数', value: complaint.value, unit: '条', icon: 'Bell', bg: '#faf5ff', color: '#7c3aed', yoy_key: 'complaint', yoy_pct: complaint.yoy_pct, yoy_value: complaint.yoy_value, tip: '内部+外部投诉合计' },
    { key: 'recovery', label: '挽回率', value: Number(recoveryRate || 0), unit: '%', icon: 'SuccessFilled', bg: '#ecfdf5', color: '#059669', yoy_key: 'recovery', yoy_pct: null, yoy_value: null, tip: '退款挽回成功率' },
  ]
})

// ========== 图表配置 ==========
// 四路经营退款路线配色 (A 线上直退 / B 钱包取现 / C 闪猫退款 / D 其他退款)
const REFUND_ROUTES = [
  { key: 'a', label: 'A线上直退', short: 'A直退', color: '#ef4444' },
  { key: 'b', label: 'B钱包取现', short: 'B取现', color: '#f97316' },
  { key: 'c', label: 'C闪猫退款', short: 'C闪猫', color: '#a855f7' },
  { key: 'd', label: 'D其他退款', short: 'D其他', color: '#94a3b8' },
]
function _rrLabel(r) { return isMobile.value ? (r.short || r.label) : r.label }

function buildTrendSeries(data) {
  const groups = trendSelectedGroups.value
  const series = []
  if (groups.includes('reception')) {
    series.push({ name: isMobile.value ? '会话' : '会话数', type: 'bar', data: data.reception || [], itemStyle: { color: '#3b82f6' }, barMaxWidth: isMobile.value ? 14 : 20 })
    series.push({ name: isMobile.value ? '接待' : '接待数', type: 'bar', data: data.reception_devices || [], itemStyle: { color: '#10b981' }, barMaxWidth: isMobile.value ? 14 : 20 })
  }
  if (groups.includes('complaint')) {
    series.push({ name: isMobile.value ? '投诉' : '投诉量', type: 'bar', data: data.complaint || [], itemStyle: { color: '#f59e0b' }, barMaxWidth: isMobile.value ? 14 : 20 })
  }
  if (groups.includes('refund')) {
    REFUND_ROUTES.forEach(r => {
      series.push({ name: _rrLabel(r), type: 'bar', stack: 'refund_cnt', data: data[`refund_count_${r.key}`] || [], itemStyle: { color: r.color }, barMaxWidth: isMobile.value ? 14 : 20 })
    })
    REFUND_ROUTES.forEach(r => {
      series.push({ name: `${_rrLabel(r)}·金额`, type: 'line', stack: 'refund_amt', yAxisIndex: 1, data: data[`refund_amount_${r.key}`] || [], smooth: true, itemStyle: { color: r.color }, lineStyle: { width: 1 }, areaStyle: { opacity: 0.12 } })
    })
  }
  return series
}

function buildTrendLegend() {
  const groups = trendSelectedGroups.value
  const items = []
  if (groups.includes('reception')) {
    items.push(isMobile.value ? '会话' : '会话数')
    items.push(isMobile.value ? '接待' : '接待数')
  }
  if (groups.includes('complaint')) {
    items.push(isMobile.value ? '投诉' : '投诉量')
  }
  if (groups.includes('refund')) {
    REFUND_ROUTES.forEach(r => items.push(_rrLabel(r)))
  }
  return items
}

const hourlyOption = computed(() => {
  const data = hourlyData.value || {}
  const labels = data?.slot_labels || data?.hour_labels || data?.hours || []
  const mob = isMobile.value
  const showRefund = trendSelectedGroups.value.includes('refund')
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: {
      data: buildTrendLegend(),
      top: 0,
      type: mob ? 'scroll' : 'plain',
      itemWidth: 10,
      itemHeight: 8,
      itemGap: mob ? 6 : 12,
      textStyle: { fontSize: mob ? 10 : 12 },
      pageIconColor: '#94a3b8',
      pageTextStyle: { color: '#6b7280', fontSize: 10 },
    },
    grid: mob
      ? { left: 28, right: showRefund ? 40 : 16, bottom: 16, top: 24, containLabel: true }
      : { left: 50, right: showRefund ? 60 : 20, bottom: 30, top: 40 },
    xAxis: [{
      type: 'category',
      data: labels,
      axisPointer: { type: 'shadow' },
      axisLabel: { fontSize: mob ? 9 : 11, interval: 0, rotate: mob ? 30 : 0 },
    }],
    yAxis: [
      {
        type: 'value',
        name: mob ? '' : '数量',
        position: 'left',
        axisLabel: { fontSize: mob ? 9 : 11 },
        splitLine: { lineStyle: { type: 'dashed', opacity: 0.4 } },
      },
      {
        type: 'value',
        name: mob ? '' : '金额(元)',
        position: 'right',
        show: showRefund,
        axisLine: { show: !mob, lineStyle: { color: '#ea580c' } },
        axisLabel: { fontSize: mob ? 9 : 11 },
        splitLine: { show: false },
      },
    ],
    series: buildTrendSeries(data),
  }
})

const receptionPieOption = computed(() => {
  const data = receptionData.value
  const items = receptionPerspective.value === 'count'
    ? (data?.categories || [])
    : (data?.categories_device || [])
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 10, top: 'center' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      label: { show: false },
      data: items.map(d => ({ name: d.name, value: d.value || 0 })),
    }],
  }
})

const refundReasonOption = computed(() => {
  const data = refundData.value?.reasons || []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 120, right: 60, bottom: 30, top: 10 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: data.map(d => d.name).reverse() },
    series: [
      { name: '订单数', type: 'bar', data: data.map(d => d.count).reverse(), itemStyle: { color: '#3b82f6' }, barMaxWidth: 16 },
      { name: '金额(元)', type: 'bar', data: data.map(d => d.amount).reverse(), itemStyle: { color: '#ea580c' }, barMaxWidth: 16 },
    ],
  }
})

// ========== 人均效率表 ==========
const departmentOptions = computed(() => {
  const depts = new Set()
  ;(staffData.value?.data || []).forEach(r => {
    if (r.department && r.department !== '未分配') depts.add(r.department)
  })
  return Array.from(depts).sort()
})

// 判定"企微人员":部门=企微 / 姓名前缀"企微-" / qw_user_id 已绑定
function isQwStaff(s) {
  if (!s) return false
  if ((s.qw_user_id || '').trim()) return true
  if ((s.department || s.dept || '') === '企微') return true
  const nm = (s.name || s.op || s.display_name || '').trim()
  return /^企微[-_·\s]?/.test(nm)
}

const staffRows = computed(() => {
  let rows = staffData.value?.data || []
  // 按人视图下默认仅显示企微人员(宽松判定), 勾选后显示所有人
  if (staffFilter.aggregateBy === 'person' && !showAllStaff.value) {
    rows = rows.filter(isQwStaff)
  }
  if (filterDepartment.value) {
    rows = rows.filter(r => r.department === filterDepartment.value)
  }
  if (filterGroupId.value) {
    rows = rows.filter(r => r.group === filterGroupId.value)
  }
  return rows
})

// 部门列显示策略:移动端 + 按人视图 + 仅企微时隐藏(表头已筛选); 其余保留
const showDeptColumn = computed(
  () => !(isMobile.value && staffFilter.aggregateBy === 'person' && !showAllStaff.value)
)

// ========== 退款挽回 ==========
const recoveryStaffList = computed(() => {
  const details = recoveryData.value?.staff_details || recoveryData.value?.staff_detail || []
  return details.map(d => ({
    name: d.name,
    group: d.group,
    total_count: d.total_sn,
    success_count: d.success_sn,
    failed_count: d.failed_sn,
    success_rate: d.total_sn > 0 ? d.success_sn / d.total_sn : 0,
  }))
})

// ========== 投诉专项 ==========
const complaintSourcePie = computed(() => {
  const s = complaintSummaryData.value || {}
  const data = [
    { name: '内部投诉', value: Number(s.internal?.total || 0), itemStyle: { color: '#2563eb' } },
    { name: '商户号投诉', value: Number(s.merchant?.total || 0), itemStyle: { color: '#7c3aed' } },
    { name: '外部投诉', value: Number(s.external?.total || 0), itemStyle: { color: '#dc2626' } },
  ]
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['40%', '65%'],
      center: ['50%', '45%'],
      label: { show: false },
      labelLine: { show: false },
      data,
    }],
  }
})

const complaintHandlerOption = computed(() => {
  const s = complaintSummaryData.value || {}
  const map = {}
  ;[
    ...(s.internal?.by_handler || []),
    ...(s.merchant?.by_handler || []),
    ...(s.external?.by_handler || []),
  ].forEach(h => {
    const key = h.name || '未标注'
    map[key] = (map[key] || 0) + Number(h.count || 0)
  })
  const rows = Object.entries(map)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10)
    .reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 100, right: 40, bottom: 30, top: 10 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: rows.map(d => d.name) },
    series: [{ name: '投诉数', type: 'bar', data: rows.map(d => d.count), itemStyle: { color: '#7c3aed' }, barMaxWidth: 16 }],
  }
})

function complaintSourceType(source) {
  if (source === '内部投诉') return 'warning'
  if (source === '外部投诉') return 'danger'
  if (source === '商户号投诉') return 'primary'
  return 'info'
}

function formatShortTime(t) {
  if (t === null || t === undefined || t === '' || t === '-') return '-'
  const d = dayjs(t)
  if (!d.isValid()) return '-'
  return d.format('MM-DD HH:mm')
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

function scoreTagType(score, a, b, c) {
  if (score >= a) return 'success'
  if (score >= b) return 'warning'
  if (score >= c) return 'danger'
  return 'info'
}

// ========== 数据加载 ==========
async function loadAll() {
  loading.value = true
  const { start, end } = getDateRange()
  const params = { start, end, trend_grain: trendGrain.value }

  try {
    const [kpiRes, hourlyRes, receptionRes, refundRes, staffRes, recoveryRes, complaintSummaryRes, complaintDetailRes] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend(params),
      today.reception(params),
      today.refund(params),
      today.staff({ ...params, aggregate_by: staffFilter.aggregateBy, topn: 200 }),
      today.refundRecovery(params),
      complaintSummary({ start, end }),
      complaintDetail({ start, end, limit: 500 }),
    ])

    if (kpiRes.data) kpiData.value = kpiRes.data
    if (hourlyRes.data) hourlyData.value = hourlyRes.data
    if (receptionRes.data) receptionData.value = receptionRes.data
    if (refundRes.data) refundData.value = refundRes.data
    if (staffRes.data) staffData.value = staffRes.data
    if (recoveryRes.data) recoveryData.value = recoveryRes.data
    if (complaintSummaryRes.data) complaintSummaryData.value = complaintSummaryRes.data
    if (complaintDetailRes.data) complaintList.value = complaintDetailRes.data?.items || []
  } catch (e) {
    console.error('加载数据失败', e)
  } finally {
    loading.value = false
  }
}

async function loadGroups() {
  try {
    const res = await listGroups()
    groupsList.value = res.data || []
  } catch {}
}

function _onMediaChange(e) {
  isMobile.value = e.matches
}

onMounted(async () => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
  await Promise.all([loadAll(), loadGroups()])
})

onBeforeUnmount(() => {
  if (_mq) {
    _mq.removeEventListener('change', _onMediaChange)
    _mq = null
  }
})

watch([grain, weekOffset, monthOffset, quarterOffset, yearOffset, customDateRange, trendGrain, filterDepartment, filterGroupId, staffFilter.aggregateBy], () => {
  loadAll()
})
</script>

<style scoped>
.operation-dashboard {
  padding-bottom: 24px;
}
.section-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb-16 { margin-bottom: 16px; }
.mb-20 { margin-bottom: 20px; }
.mb-24 { margin-bottom: 24px; }
.kpi-mini-card {
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}
/* 投诉KPI卡: 可点击筛选明细表 */
.kpi-clickable {
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}
.kpi-clickable:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
  border-color: #c7c7c7;
}
.kpi-clickable.kpi-active {
  outline: 2px solid #2563eb;
  outline-offset: -2px;
  box-shadow: 0 0 0 2px rgba(37,99,235,0.18);
}
.kpi-mini-hint {
  font-size: 10px;
  color: #9ca3af;
  margin-top: 4px;
  letter-spacing: 0.5px;
}
.kpi-clickable.kpi-active .kpi-mini-hint { color: #2563eb; }
.kpi-mini-label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.kpi-mini-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}
.kpi-mini-unit {
  font-size: 14px;
  font-weight: 400;
  color: #9ca3af;
  margin-left: 4px;
}
.text-muted { color: #9ca3af; }

/* 窄屏: 精简排版, 避免文字/数字被挤成两行 */
@media (max-width: 768px) {
  .td-header {
    flex-wrap: wrap;
    gap: 8px;
  }
  .td-header :deep(.filter-bar-wrap) {
    width: 100%;
  }
  .section-card {
    padding: 12px;
  }
  .kpi-mini-card {
    padding: 10px 12px;
  }
  .kpi-mini-value {
    font-size: 18px;
  }
  .kpi-mini-unit {
    font-size: 12px;
  }
  :deep(.kpi-card.is-compact .kpi-value) {
    font-size: 16px;
  }
  :deep(.kpi-card.is-compact .kpi-yoy) {
    flex-wrap: nowrap;
  }
  :deep(.kpi-card.is-compact .kpi-yoy-val) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
  }
}

/* 效率分数 tooltip */
.score-tag-hover { cursor: help; }
.score-tag-hover:hover { opacity: 0.85; }
.score-tip { font-size: 12px; line-height: 1.7; }
.score-tip-title { font-weight: 600; font-size: 13px; margin-bottom: 6px; color: #303133; }
.score-tip-row { display: flex; align-items: center; gap: 6px; margin: 2px 0; }
.score-tip-cat { min-width: 64px; font-weight: 500; color: #606266; }
.score-tip-val { flex: 1; color: #606266; }
.score-tip-sub { color: #c0c4cc; }
.score-tip-contrib { font-weight: 600; color: #409eff; font-family: ui-monospace, monospace; }
.score-tip-total { margin-top: 6px; padding-top: 4px; border-top: 1px dashed #ebeef5; font-weight: 600; color: #303133; }
.score-tip-note { margin-top: 6px; padding-top: 4px; border-top: 1px dashed #ebeef5; color: #909399; font-size: 11px; line-height: 1.6; }
.calc-help { font-weight: 600; }

/* 趋势类型勾选 */
.trend-group-check :deep(.el-checkbox-button__inner) {
  padding: 4px 10px;
  font-size: 12px;
}
</style>
