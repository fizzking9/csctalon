<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="flex-between" style="gap: 12px">
        <div class="page-title" style="margin: 0">{{ dashboard?.name || '加载中...' }}</div>
        <el-tag v-if="dashboard" size="small">{{ dashboard.category }}</el-tag>
        <el-tag v-if="dashboard?.is_preset" type="warning" size="small">预设</el-tag>
      </div>
      <div v-if="dashboard" class="filter-bar">
        <el-radio-group v-model="rangeMode" size="small" @change="onRangeChange">
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
          <el-radio-button label="default">默认</el-radio-button>
          <el-radio-button label="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="rangeMode === 'custom'"
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width: 240px"
          @change="onRangeChange"
        />
        <el-radio-group v-model="grainMode" size="small" @change="onGrainChange">
          <el-radio-button label="day">按日</el-radio-button>
          <el-radio-button label="week">按周</el-radio-button>
          <el-radio-button label="month">按月</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" :loading="batchLoading" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="dashboard?.description"
      :title="dashboard.description"
      type="info"
      :closable="false"
      class="mb-16"
    />

    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <!-- ============ 区块1: 催单风险 (预测性退款参考) ============ -->
    <section v-if="urgeItems.length" class="section-card mb-24">
      <div class="block-title">一、退款催单风险分析（预测性退款参考因素）</div>
      <el-row :gutter="16">
        <el-col
          v-for="item in urgeItems"
          :key="item.view_id"
          :span="item.span"
          class="mb-16"
        >
          <ViewItemCard
            :view-id="item.view_id"
            :title="item.title || ''"
            :start="effectiveStart"
            :end="effectiveEnd"
            :prefetched-result="itemResult(item.view_id).result"
            :prefetched-error="itemResult(item.view_id).error"
            :prefetched-view="itemView(item.view_id)"
            :batch-loading="batchLoading"
          />
        </el-col>
      </el-row>
    </section>

    <!-- ============ 区块2: 退款挽回专项 ============ -->
    <section v-if="hasRecovery" class="section-card mb-24">
      <div class="flex-between mb-12">
        <div>
          <div class="block-title">二、退款挽回专项</div>
          <div class="block-sub">
            分母=退款组人员本期接待的退款类SN去重 · 挽留失败=分母SN∩最终退款(退款单∪退款类工单) · 成功率=成功/分母 · 独立分析 不冲抵接待效率
          </div>
        </div>
        <div>
          <el-tag size="small" effect="dark" type="danger" style="margin-right:6px">{{ recoveryData.date }}~{{ recoveryData.date_end || recoveryData.date }}</el-tag>
          <el-tooltip effect="light" placement="top" :show-arrow="false" popper-class="score-pop" teleported>
            <template #content>
              <div style="max-width:440px; line-height:1.9; font-size:12px; color:#4b5563;">
                <div style="font-weight:600; color:#1f2937; margin-bottom:6px">【退款挽回 核心口径】</div>
                <div>① 总接待SN(分母): 退款专项组 (叶珍凤组 / 朱海洋组 或 HR专项=退款挽回) 人员在周期内, 智能助手(cs_records)接待的 problem_category 含「退款」的 去重 device_sn 总数</div>
                <div>② 挽留失败(分子): 分母SN中, 同期出现在 refunds线上退款单 或 service_tickets(service_category=退款)中的 SN → 最终进入了退款流程</div>
                <div>③ 挽留成功: 分母SN - 失败SN → 接待过但最终没走到退款流程 (即被客服沟通拦截下来)</div>
                <div>④ 成功率 = 挽留成功 / 总接待SN</div>
                <div style="margin-top:6px;padding-top:6px;border-top:1px dashed #e5e7eb">
                  <b>金额口径说明:</b><br/>
                  · 成功金额暂不核算 (挽留成功的设备未进入退款流程, 无实际退款工单金额可对应)<br/>
                  · 设备SN数量 / 订单单数 依然保留作为核心参考指标<br/>
                  · 费占比改为单量口径 = 成功单数 / (成功单数 + 实际退款单数(失败+退充)) · 参考目标 37%+
                </div>
              </div>
            </template>
            <el-tag size="small" effect="plain" type="primary">查看口径</el-tag>
          </el-tooltip>
        </div>
      </div>

      <!-- 2.1 顶部 KPI 条 -->
      <el-row :gutter="12" class="mb-12">
        <!-- SN核心 3卡 + 成功率 -->
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" style="background:#eff6ff">
            <div class="kpi-mini-label" style="color:#2563eb">总接待SN(分母)</div>
            <div class="kpi-mini-sub">退款组接待退款类的去重SN</div>
            <div class="kpi-mini-value" style="color:#2563eb">
              {{ formatNum(recoveryData.kpi.total_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" style="background:#f0fdf4">
            <div class="kpi-mini-label" style="color:#16a34a">挽留成功</div>
            <div class="kpi-mini-sub">没进入退款流程</div>
            <div class="kpi-mini-value" style="color:#16a34a">
              {{ formatNum(recoveryData.kpi.success_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" style="background:#fef2f2">
            <div class="kpi-mini-label" style="color:#dc2626">挽留失败</div>
            <div class="kpi-mini-sub">最终退了的SN</div>
            <div class="kpi-mini-value" style="color:#dc2626">
              {{ formatNum(recoveryData.kpi.failed_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" :style="{background: Number(recoveryData.kpi.success_rate||0)>=38?'#f0fdf4':(Number(recoveryData.kpi.success_rate||0)>=30?'#fefce8':'#fef2f2'}">
            <div class="kpi-mini-label" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626'}">成功率</div>
            <div class="kpi-mini-sub">成功 / 总接待SN</div>
            <div class="kpi-mini-value" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626'}">
              {{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
            </div>
          </div>
        </el-col>
        <!-- 4 分类单数 -->
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" style="background:#f3f4f6">
            <div class="kpi-mini-label" style="color:#6b7280">放弃单数</div>
            <div class="kpi-mini-sub">系统清退·待处理/无需退款</div>
            <div class="kpi-mini-value" style="color:#6b7280">
              {{ formatNum(recoveryData.kpi.abandon_cnt) }}<span class="kpi-mini-unit">单</span>
            </div>
            <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.abandon_amount||0)) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" style="background:#fef2f2">
            <div class="kpi-mini-label" style="color:#dc2626">失败单数</div>
            <div class="kpi-mini-sub">拦截失败·实际退款成功</div>
            <div class="kpi-mini-value" style="color:#dc2626">
              {{ formatNum(recoveryData.kpi.fail_cnt) }}<span class="kpi-mini-unit">单</span>
            </div>
            <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.fail_amount||0)) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" style="background:#f0fdf4">
            <div class="kpi-mini-label" style="color:#16a34a">成功单数</div>
            <div class="kpi-mini-sub">拦截成功·沟通后没退款 · 金额暂不核算</div>
            <div class="kpi-mini-value" style="color:#16a34a">
              {{ formatNum(recoveryData.kpi.success_cnt) }}<span class="kpi-mini-unit">单</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="3">
          <div class="kpi-mini-card" style="background:#eff6ff">
            <div class="kpi-mini-label" style="color:#2563eb">退充单</div>
            <div class="kpi-mini-sub">正常退充·金额互抵</div>
            <div class="kpi-mini-value" style="color:#2563eb">
              {{ formatNum(recoveryData.kpi.recharge_cnt) }}<span class="kpi-mini-unit">单</span>
            </div>
            <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.recharge_amount||0)) }}</div>
          </div>
        </el-col>
        <!-- 目标/实际 总览 + 费占比 -->
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-mini-card" style="background:#f0fdfa">
            <div class="kpi-mini-label" style="color:#0f766e">目标总单量</div>
            <div class="kpi-mini-value" style="color:#0f766e">
              {{ formatNum(recoveryData.kpi.target_total_cnt) }}<span class="kpi-mini-unit">单</span>
            </div>
            <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.target_total_amount||0)) }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <div class="kpi-mini-card" style="background:#fff7ed">
            <div class="kpi-mini-label" style="color:#7c2d12">实际退款金额</div>
            <div class="kpi-mini-sub">失败金额 + 退充金额</div>
            <div class="kpi-mini-value" style="color:#7c2d12">
              <span class="kpi-mini-unit"></span>
            </div>
            <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.actual_refund_amount||0)) }}</div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="8" :md="4">
          <div class="kpi-mini-card" :style="{background: Number(recoveryData.kpi.fee_ratio||0)>=37?'#f0fdf4':'#fef2f2'}">
            <div class="kpi-mini-label" :style="{color: Number(recoveryData.kpi.fee_ratio||0)>=37?'#16a34a':'#dc2626'}">拦截费占比</div>
            <div class="kpi-mini-sub">成功单数/(成功+实际退款单数) · 单量口径 · 目标37%+</div>
            <div class="kpi-mini-value" :style="{color: Number(recoveryData.kpi.fee_ratio||0)>=37?'#16a34a':'#dc2626'}">
              {{ Number(recoveryData.kpi.fee_ratio||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 2.2 第一行: 退款原因分布(柱) + 单分布/金额分布 双饼 -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="24" :md="12" class="mb-12 md:mb-0">
          <el-card shadow="never">
            <template #header>
              <div style="display:flex; align-items:center; justify-content:space-between">
                <span style="font-weight:600">退款原因分布 · 挽留失败SN归因</span>
                <el-tag size="small" effect="plain" type="info">归一化 7 大类 · 悬停看金额</el-tag>
              </div>
            </template>
            <BaseChart v-if="recoveryData.reason_dist?.length" :option="reasonBarOption" height="320px" />
            <el-empty v-else description="暂无原因分布数据" />
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12">
          <el-row :gutter="12">
            <el-col :xs="24" :sm="12" class="mb-12 sm:mb-0">
              <el-card shadow="never">
                <template #header><span style="font-weight:600">退款接待单分布</span></template>
                <BaseChart v-if="recoveryOrderPieOption && recoveryOrderPieOption.series" :option="recoveryOrderPieOption" height="300px" />
                <el-empty v-else description="暂无分布数据" />
              </el-card>
            </el-col>
            <el-col :xs="24" :sm="12">
              <el-card shadow="never">
                <template #header><span style="font-weight:600">退款金额分布</span></template>
                <BaseChart v-if="recoveryAmountPieOption && recoveryAmountPieOption.series" :option="recoveryAmountPieOption" height="300px" />
                <el-empty v-else description="暂无金额分布" />
              </el-card>
            </el-col>
          </el-row>
        </el-col>
      </el-row>

      <!-- 2.3 第二行: 周趋势 双图 -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="24" :md="12" class="mb-12 md:mb-0">
          <el-card shadow="never">
            <template #header>
              <div style="display:flex; align-items:center; justify-content:space-between">
                <span style="font-weight:600">退款拦截订单汇总(周)趋势</span>
                <el-tag size="small" effect="plain" type="warning">最近 8 周</el-tag>
              </div>
            </template>
            <BaseChart v-if="recoveryWeeklyOption && recoveryWeeklyOption.series" :option="recoveryWeeklyOption" height="340px" />
            <el-empty v-else description="暂无周趋势数据" />
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12">
          <el-card shadow="never">
            <template #header>
              <div style="display:flex; align-items:center; justify-content:space-between">
                <span style="font-weight:600">实际退款汇总(周)趋势</span>
                <el-tag size="small" effect="plain" type="info">成功金额暂不核算 · 按全局单均粗估</el-tag>
              </div>
            </template>
            <BaseChart v-if="recoveryAmountWeeklyOption && recoveryAmountWeeklyOption.series" :option="recoveryAmountWeeklyOption" height="340px" />
            <el-empty v-else description="暂无金额趋势" />
          </el-card>
        </el-col>
      </el-row>

      <!-- 2.4 第三行: 按人员明细 + 日×原因 交叉表 -->
      <el-row :gutter="12">
        <el-col :xs="24" :md="10" class="mb-12 md:mb-0">
          <el-card shadow="never">
            <template #header>
              <div style="display:flex; align-items:center; justify-content:space-between">
                <span style="font-weight:600">退款挽回 · 按人员明细</span>
                <el-tag size="small" effect="plain" type="primary">按总接待SN倒序</el-tag>
              </div>
            </template>
            <el-table :data="recoveryData.staff_detail || []" border stripe size="small" max-height="420">
              <el-table-column label="姓名" width="108" fixed>
                <template #default="{ row }">
                  <span style="font-weight:600">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column label="身份" width="84">
                <template #default="{ row }">
                  <el-tag v-if="row._is_refund_staff" size="small" effect="light" type="success" style="margin-right:4px">挽回</el-tag>
                  <el-tag v-else-if="row._is_leader" size="small" effect="light" type="warning">主管</el-tag>
                  <el-tag v-else size="small" effect="light" type="primary">执行</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="组" width="100">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" :type="grpTagType(row.group)">{{ row.group || '未分组' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="total_sn" label="接待SN" width="82" align="right" sortable>
                <template #default="{ row }">{{ formatNum(row.total_sn) }}个</template>
              </el-table-column>
              <el-table-column prop="success_sn" label="成功" width="64" align="right">
                <template #default="{ row }"><span style="color:#16a34a">{{ formatNum(row.success_sn) }}</span></template>
              </el-table-column>
              <el-table-column prop="failed_sn" label="失败" width="64" align="right">
                <template #default="{ row }"><span style="color:#dc2626">{{ formatNum(row.failed_sn) }}</span></template>
              </el-table-column>
              <el-table-column prop="success_rate" label="成功率" width="82" align="right" sortable :sort-method="(a,b)=>Number(a.success_rate??-1)-Number(b.success_rate??-1)">
                <template #default="{ row }">
                  <template v-if="row.success_rate == null">
                    <span style="color:#9ca3af">—</span>
                  </template>
                  <template v-else>
                    <el-tag size="small" :effect="Number(row.success_rate||0)>=38?'dark':'plain'"
                      :type="Number(row.success_rate||0)>=38?'success':(Number(row.success_rate||0)>=30?'warning':'danger')">
                      {{ Number(row.success_rate||0).toFixed(1) }}%
                    </el-tag>
                  </template>
                </template>
              </el-table-column>
              <el-table-column prop="refund_op_cnt" label="退款操作SN" width="96" align="right" sortable>
                <template #default="{ row }">
                  <span :style="{color: Number(row.refund_op_cnt||0)>0 ? '#1d4ed8' : '#9ca3af', fontWeight: Number(row.refund_op_cnt||0)>0 ? 600 : 400}">
                    {{ formatNum(row.refund_op_cnt) }}个
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="refund_op_amount" label="退款操作金额" width="112" align="right" sortable>
                <template #default="{ row }">
                  <span :style="{color: Number(row.refund_op_amount||0)>0 ? '#b91c1c' : '#9ca3af', fontWeight: Number(row.refund_op_amount||0)>0 ? 600 : 400}">
                    ¥{{ formatMoney(row.refund_op_amount) }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!recoveryData.staff_detail?.length" description="暂无人员明细" />
          </el-card>
        </el-col>

        <el-col :xs="24" :md="14">
          <el-card shadow="never">
            <template #header>
              <div style="display:flex; align-items:center; justify-content:space-between">
                <span style="font-weight:600">退款原因 × 日期 明细</span>
                <el-tag size="small" effect="plain" type="info">失败SN按首次退款日归因 · 单元格=单数(占比%)</el-tag>
              </div>
            </template>
            <el-table
              v-if="recoveryData.daily_reason?.length"
              :data="recoveryData.daily_reason"
              border stripe size="small" max-height="420"
              style="width:100%"
            >
              <el-table-column prop="date" label="日期" width="108" fixed align="center" />
              <el-table-column
                v-for="col in dailyReasonCols" :key="col.k"
                :label="col.label" :min-width="112" align="right"
              >
                <template #default="{ row }">
                  <div style="display:flex; flex-direction:column; align-items:flex-end; gap:2px">
                    <span style="font-weight:500">{{ formatNum(row[col.k+'_cnt']) }}</span>
                    <span :style="{ color: ratioColor(Number(row[col.k+'_ratio']||0)), fontSize: '10px' }">
                      {{ Number(row[col.k+'_ratio']||0).toFixed(1) }}%
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="当日合计" width="88" align="right" fixed="right">
                <template #default="{ row }">
                  <span style="font-weight:600; color:#2563eb">{{ formatNum(row._total) }}</span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="暂无日×原因明细 · 切换到周/月粒度可看到更多天" />
          </el-card>
        </el-col>
      </el-row>
    </section>

    <!-- ============ 区块3: 现金流 / 退款基础 ============ -->
    <section v-if="cashflowItems.length" class="section-card">
      <div class="block-title">三、现金流与退款基础分析</div>
      <el-row :gutter="16">
        <el-col
          v-for="item in cashflowItems"
          :key="item.view_id"
          :span="item.span"
          class="mb-16"
        >
          <ViewItemCard
            :view-id="item.view_id"
            :title="item.title || ''"
            :start="effectiveStart"
            :end="effectiveEnd"
            :prefetched-result="itemResult(item.view_id).result"
            :prefetched-error="itemResult(item.view_id).error"
            :prefetched-view="itemView(item.view_id)"
            :batch-loading="batchLoading"
          />
        </el-col>
      </el-row>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import ViewItemCard from '@/components/ViewItemCard.vue'
import BaseChart from '@/components/BaseChart.vue'
import { getDashboard, listDashboards, executeDashboard } from '@/api/dashboard'
import { today } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

const route = useRoute()

const dashboard = ref(null)
const rangeMode = ref('30')
const customRange = ref([])
const grainMode = ref('day')
const batchLoading = ref(false)
const batchResults = ref({})
const errorMsg = ref('')

// ---- 退款挽回数据 & 图表配置 ----
function emptyKpi() {
  return {
    total_sn: 0, success_sn: 0, failed_sn: 0, success_rate: 0,
    abandon_cnt: 0, fail_cnt: 0, success_cnt: 0, recharge_cnt: 0, target_total_cnt: 0,
    abandon_amount: 0, fail_amount: 0, success_amount: 0, recharge_amount: 0,
    target_total_amount: 0, actual_refund_amount: 0, fee_ratio: 0,
    _calc_note: {},
  }
}
const recoveryData = ref({
  kpi: emptyKpi(), reason_dist: [], staff_detail: [], weekly_trend: [], daily_reason: [],
})
const hasRecovery = computed(() => {
  const k = recoveryData.value?.kpi || {}
  return (Number(k.total_sn) || 0) > 0 || (recoveryData.value?.staff_detail || []).length > 0
})
function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

// 催单风险 = 前5个视图
const urgeItems = computed(() => (dashboard.value?.items || []).filter((_, idx) => idx < 5 && (_.visible !== false)))
// 现金流模块 = 从第6个视图开始(索引5往后)
const cashflowItems = computed(() => (dashboard.value?.items || []).filter((it, idx) => idx >= 5 && it.visible !== false))

const effectiveStart = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().format('YYYY-MM-DD')
})

function itemResult(viewId) {
  const r = batchResults.value[viewId]
  return r || { result: null, error: null }
}
function itemView(viewId) {
  const r = batchResults.value[viewId]
  if (!r || r.view_name === null || r.view_name === undefined) return undefined
  return { name: r.view_name, ratios: r.ratios, is_preset: r.is_preset }
}

function onRangeChange() {}
function onGrainChange() { loadAll() }

// --------- 退款挽回图表 options ---------
const reasonBarOption = computed(() => {
  const list = recoveryData.value?.reason_dist || []
  if (!list.length) return {}
  const labels = list.map(x => x.reason)
  const cnts = list.map(x => Number(x.order_cnt) || 0)
  const ratios = list.map(x => Number(x.ratio) || 0)
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = list[i] || {}
        return `<b>${it.reason}</b><br/>工单数: <b>${Number(it.order_cnt)||0}</b> (占比 ${Number(it.ratio)||0}%)<br/>涉及金额: ¥${formatMoney(Number(it.amount)||0)}`
      }
    },
    grid: { left: 60, right: 70, top: 28, bottom: 60 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 10, rotate: 18, interval: 0 } },
    yAxis: [
      { type: 'value', name: '工单数', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '占比(%)', max: 100, axisLabel: { fontSize: 10, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '工单数', type: 'bar', data: cnts, barMaxWidth: 32,
        itemStyle: {
          color: (p) => {
            const pal = ['#e74c3c','#f39c12','#3498db','#9b59b6','#1abc9c','#34495e','#95a5a6','#e67e22']
            return pal[p.dataIndex % pal.length]
          },
          borderRadius: [4,4,0,0],
        },
        label: { show: true, position: 'top', fontSize: 10,
          formatter: (p) => `${p.value} (${ratios[p.dataIndex]}%)` },
      },
    ],
  }
})

const recoveryOrderPieOption = computed(() => {
  const k = recoveryData.value?.kpi || {}
  const pie_data = [
    { name: '放弃',   value: Number(k.abandon_cnt)  || 0, color: '#909399' },
    { name: '失败',   value: Number(k.fail_cnt)     || 0, color: '#e74c3c' },
    { name: '成功',   value: Number(k.success_cnt)  || 0, color: '#67c23a' },
    { name: '退充单', value: Number(k.recharge_cnt) || 0, color: '#409eff' },
  ].filter(x => x.value > 0)
  if (!pie_data.length) return {}
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 单 ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    color: pie_data.map(x => x.color),
    series: [{
      name: '退款接待单分布', type: 'pie', radius: ['42%','68%'], center: ['50%','45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: '{b}\n{c}单 ({d}%)', fontSize: 11 },
      data: pie_data,
    }],
  }
})

const recoveryAmountPieOption = computed(() => {
  const k = recoveryData.value?.kpi || {}
  const items = [
    { name: '放弃金额',   value: Number(k.abandon_amount)  || 0, color: '#909399' },
    { name: '失败金额',   value: Number(k.fail_amount)     || 0, color: '#e74c3c' },
    { name: '退充金额',   value: Number(k.recharge_amount) || 0, color: '#409eff' },
  ].filter(x => x.value > 0)
  if (!items.length) return {}
  return {
    tooltip: { trigger: 'item', formatter: (p) => `${p.name}: ¥${formatMoney(p.value)} (${p.percent}%)` },
    legend: { bottom: 0, type: 'scroll' },
    color: items.map(x => x.color),
    series: [{
      name: '退款金额分布', type: 'pie', radius: ['42%','68%'], center: ['50%','45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: (p) => `${p.name}\n¥${(p.value/10000).toFixed(1)}万 (${p.percent}%)`, fontSize: 11 },
      data: items,
    }],
  }
})

const recoveryWeeklyOption = computed(() => {
  const wl = recoveryData.value?.weekly_trend || []
  if (!wl.length) return {}
  const labels = wl.map(x => (x.date_range && x.date_range[0]) ? `${x.date_range[0]}~${x.date_range[1]}` : (x.week_label || ''))
  const ab = wl.map(x => Number(x.abandon_cnt) || 0)
  const fl = wl.map(x => Number(x.fail_cnt)    || 0)
  const sc = wl.map(x => Number(x.success_cnt) || 0)
  const rc = wl.map(x => Number(x.recharge_cnt)|| 0)
  const tt = wl.map(x => Number(x.total_cnt)   || 0)
  const rt = wl.map(x => Number(x.success_rate)|| 0)
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const w = wl[i] || {}
        return `<b>${labels[i]}</b><br/>放弃: ${w.abandon_cnt||0} · 失败: ${w.fail_cnt||0} · 成功: ${w.success_cnt||0} · 退充: ${w.recharge_cnt||0}<br/>总单量: <b>${w.total_cnt||0}</b><br/>成功率: <b style="color:#16a34a">${Number(w.success_rate||0).toFixed(1)}%</b>`
      }
    },
    legend: { data: ['放弃单数','失败单数','成功单数','退充单','总单量','成功率'], top: 0, type: 'scroll' },
    grid: { left: 56, right: 68, bottom: 52, top: 44 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 10, rotate: 10 } },
    yAxis: [
      { type: 'value', name: '单数', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '成功率(%)', max: 100, min: 0, axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { show: false } },
    ],
    series: [
      { name: '放弃单数', type: 'bar', stack: 'od', data: ab, itemStyle: { color: '#909399' }, barMaxWidth: 18 },
      { name: '失败单数', type: 'bar', stack: 'od', data: fl, itemStyle: { color: '#e74c3c' } },
      { name: '成功单数', type: 'bar', stack: 'od', data: sc, itemStyle: { color: '#67c23a' } },
      { name: '退充单',   type: 'bar', stack: 'od', data: rc, itemStyle: { color: '#409eff' } },
      { name: '总单量',   type: 'bar', data: tt, barMaxWidth: 8, itemStyle: { color: 'rgba(37,99,235,0.30)', borderRadius: [4,4,0,0] } },
      { name: '成功率',   type: 'line', yAxisIndex: 1, data: rt, smooth: true, symbol: 'circle', symbolSize: 7, itemStyle: { color: '#15803d' }, lineStyle: { width: 3, color: '#15803d' } },
    ],
  }
})

const recoveryAmountWeeklyOption = computed(() => {
  const wl = recoveryData.value?.weekly_trend || []
  if (!wl.length) return {}
  const k = recoveryData.value?.kpi || {}
  const avg_ab = k.abandon_cnt  ? (Number(k.abandon_amount)  / k.abandon_cnt)  : 0
  const avg_fl = k.fail_cnt     ? (Number(k.fail_amount)     / k.fail_cnt)     : 0
  const avg_rc = k.recharge_cnt ? (Number(k.recharge_amount) / k.recharge_cnt) : 0
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const x = wl[i] || {}
        const ab = Math.round((Number(x.abandon_cnt)  || 0) * avg_ab)
        const fl = Math.round((Number(x.fail_cnt)     || 0) * avg_fl)
        const rc = Math.round((Number(x.recharge_cnt) || 0) * avg_rc)
        const total = ab + fl + rc
        const rate = total ? Number(((fl+rc) / total * 100).toFixed(1)) : 0
        return `<b>${(x.date_range && x.date_range[0]) ? x.date_range[0]+'~'+x.date_range[1] : (x.week_label||'')}</b><br/>放弃金额估算: ¥${formatMoney(ab)} (${x.abandon_cnt||0}单)<br/>失败金额估算: ¥${formatMoney(fl)} (${x.fail_cnt||0}单)<br/>退充金额估算: ¥${formatMoney(rc)} (${x.recharge_cnt||0}单)<br/>退款金额合计: <b>¥${formatMoney(total)}</b><br/>实际退款占比(失败+退充): <b>${rate}%</b>`
      }
    },
    grid: { left: 56, right: 68, bottom: 52, top: 44 },
    xAxis: { type: 'category', data: wl.map(x => (x.date_range && x.date_range[0]) ? `${x.date_range[0]}~${x.date_range[1]}` : (x.week_label||'')), axisLabel: { fontSize: 10, rotate: 10 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 10, formatter: (v) => (v>=10000 ? (v/10000).toFixed(0)+'万' : v) }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '合计(元)', axisLabel: { fontSize: 10, formatter: (v) => (v>=10000 ? (v/10000).toFixed(0)+'万' : v) }, splitLine: { show: false } },
    ],
    legend: { data: ['放弃金额(粗估)','失败金额(粗估)','退充金额(粗估)','退款金额合计'], top: 0, type: 'scroll' },
    series: [
      { name: '放弃金额(粗估)', type: 'bar', stack: 'amt', data: wl.map(x => Math.round((Number(x.abandon_cnt)||0)*avg_ab)), barMaxWidth: 22, itemStyle: { color: '#909399' } },
      { name: '失败金额(粗估)', type: 'bar', stack: 'amt', data: wl.map(x => Math.round((Number(x.fail_cnt)||0)*avg_fl)), barMaxWidth: 22, itemStyle: { color: '#e74c3c' } },
      { name: '退充金额(粗估)', type: 'bar', stack: 'amt', data: wl.map(x => Math.round((Number(x.recharge_cnt)||0)*avg_rc)), barMaxWidth: 22, itemStyle: { color: '#409eff', borderRadius: [4,4,0,0] } },
      { name: '退款金额合计', type: 'line', yAxisIndex: 1, smooth: true, data: wl.map(x => {
          const ab = Math.round((Number(x.abandon_cnt)||0)*avg_ab)
          const fl = Math.round((Number(x.fail_cnt)||0)*avg_fl)
          const rc = Math.round((Number(x.recharge_cnt)||0)*avg_rc)
          return ab + fl + rc
        }), symbol: 'circle', symbolSize: 7, itemStyle: { color: '#7c2d12' }, lineStyle: { width: 3, color: '#7c2d12' } },
    ],
  }
})

const dailyReasonCols = computed(() => {
  const reasons = recoveryData.value?.kpi?._calc_note?.reason_order || []
  return reasons.map(r => ({ k: r, label: r }))
})
function ratioColor(pct) {
  if (pct >= 40) return '#dc2626'
  if (pct >= 20) return '#ca8a04'
  return '#6b7280'
}
const GRP_COLOR_MAP = {
  '朱海洋组': 'success',
  '董梦玲组': 'warning',
  '吴晓龙组': 'primary',
  '叶珍凤组': 'danger',
  '投诉专项组': 'info',
}
function grpTagType(g) {
  if (!g || g === '未分组') return 'info'
  return GRP_COLOR_MAP[g] || 'info'
}

async function resolveDashboardId() {
  const code = 'preset_dashboard_refund'
  const res = await listDashboards({ page: 1, page_size: 100 })
  const d = res.data.items.find((x) => x.code === code)
  return d?.id || null
}

async function loadDashboard() {
  errorMsg.value = ''
  dashboard.value = null
  batchResults.value = {}
  try {
    const id = await resolveDashboardId()
    if (!id) {
      errorMsg.value = '退款看板不存在或无权访问'
      return
    }
    const res = await getDashboard(id)
    dashboard.value = res.data
    if (dashboard.value.time_range_default?.days) {
      rangeMode.value = String(dashboard.value.time_range_default.days)
    } else {
      rangeMode.value = 'default'
    }
    await loadAll()
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载看板失败'
  }
}

async function loadAll() {
  if (!dashboard.value) return
  batchLoading.value = true
  try {
    const params = {}
    if (effectiveStart.value) params.start = effectiveStart.value
    if (effectiveEnd.value) params.end = effectiveEnd.value
    if (grainMode.value !== 'day') params.time_grain = grainMode.value
    // 并行: 批量视图 + 退款挽回专项接口
    const [dashRes, recoverRes] = await Promise.all([
      executeDashboard(dashboard.value.id, params),
      today.refundRecovery(params),
    ])
    const map = {}
    for (const it of dashRes.data.items || []) {
      map[it.view_id] = it
    }
    batchResults.value = map
    recoveryData.value = recoverRes.data || { kpi: emptyKpi(), reason_dist: [], staff_detail: [], weekly_trend: [], daily_reason: [] }
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载失败'
  } finally {
    batchLoading.value = false
  }
}

watch([effectiveStart, effectiveEnd], () => {
  if (dashboard.value) loadAll()
})
watch(
  () => [route.name, route.params.id, route.params.code],
  () => { loadDashboard() }
)

onMounted(loadDashboard)
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.error-msg {
  color: #f56c6c;
  padding: 12px;
  background: #fef0f0;
  border-radius: 4px;
}
.block-title {
  font-size: 17px;
  font-weight: 700;
  color: #111827;
  letter-spacing: 0.3px;
  margin-bottom: 4px;
}
.block-sub {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 16px;
}
.section-card { }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }

/* 退款挽回 KPI 卡样式 (和 Today.vue 保持一致) */
.kpi-mini-card {
  border-radius: 10px;
  padding: 12px 14px 10px;
  border: 1px solid rgba(148,163,184,0.15);
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-height: 88px;
  transition: transform .15s ease, box-shadow .15s ease;
}
.kpi-mini-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px -8px rgba(15,23,42,0.18);
}
.kpi-mini-label { font-size: 12px; font-weight: 600; letter-spacing: 0.2px; line-height: 1.3; }
.kpi-mini-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; opacity: 0.9; }
.kpi-mini-value { margin-top: 2px; font-size: 22px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-mini-unit { font-size: 13px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
.kpi-mini-money { margin-top: 1px; font-size: 11.5px; font-weight: 500; color: #374151; font-variant-numeric: tabular-nums; opacity: 0.92; }
</style>
