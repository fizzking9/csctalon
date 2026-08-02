<template>
  <div class="page-container today-dashboard">
    <!-- 日期+看板名水印：绝对定位铺满 指针事件穿透不阻塞tooltip；局部截图任何区域都能看到日期 -->
    <div class="today-watermark" :style="watermarkBg" aria-hidden="true"></div>
    <!-- 顶部标题 + 时间筛选 -->
    <div class="flex-between mb-16 td-header" style="position: relative; z-index: 2">
      <div class="page-title" style="margin: 0; white-space: nowrap">
        客服日度运营看板
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag size="small" type="success" effect="plain" style="margin-left: 8px">单日切面</el-tag>
        <el-tag v-if="kpiData.merchant_tip" size="small" type="warning" effect="plain" style="margin-left: 6px">
          {{ kpiData.merchant_tip }}
        </el-tag>
      </div>
      <div class="filter-bar">
        <el-radio-group v-model="quickDate" size="small" @change="onQuickDate">
          <el-radio-button label="today">{{ dayjs().format('MM-DD') }} 今日</el-radio-button>
          <el-radio-button label="yesterday">{{ dayjs().subtract(1, 'day').format('MM-DD') }} 昨日</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-model="customDate"
          type="date"
          size="small"
          value-format="YYYY-MM-DD"
          placeholder="选择日期"
          style="width: 160px; margin-left: 8px"
          @change="onCustomDate"
        />
        <el-button :icon="Refresh" :loading="loading" @click="loadAll" style="margin-left: 8px">刷新</el-button>
      </div>
    </div>

    <!-- 模块1: KPI卡 -->
    <section class="section-card mb-24">
      <h2 class="section-title">1. 核心指标 <span class="section-sub">（对比昨日同时段 · 悬停标签查看口径）</span></h2>
      <el-row :gutter="16">
        <el-col :xs="12" :sm="8" :md="4" v-for="c in kpiCards" :key="c.key">
          <el-card shadow="hover" class="kpi-card" body-style="padding: 14px 18px">
            <div class="kpi-icon" :style="{ background: c.bg, color: c.color }">
              <el-icon :size="22"><component :is="c.icon" /></el-icon>
            </div>
            <div class="kpi-label" :style="{ cursor: c.tip ? 'help' : 'default' }">
              {{ c.label }}
              <el-tooltip v-if="c.tip" effect="dark" placement="top" :content="c.tip">
                <el-icon style="margin-left:4px; color:#909399; vertical-align: -1px"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
            <div class="kpi-value">
              <template v-if="c.money">{{ formatMoney(c.value) }}</template>
              <template v-else>{{ formatNum(c.value) }}<span class="kpi-unit">{{ c.unit || '' }}</span></template>
            </div>
            <!-- 接待量卡追加一行: 今日接待数 (distinct device_sn ≈ 独立客户人数, 一人多设备近似当人数无影响) -->
            <div v-if="c.key === 'reception' && c.device_count != null" class="kpi-device-sub">
              <span class="kpi-device-sub-label">接待数</span>
              <span class="kpi-device-sub-val">{{ formatNum(c.device_count) }}<span class="kpi-device-sub-unit"> 个</span></span>
            </div>
            <div class="kpi-yoy">
              <el-tag v-if="c.yoy_pct != null" size="small" effect="plain" :type="yoyTagType(c.key, c.yoy_pct)">
                {{ c.yoy_pct > 0 ? '↑' : '↓' }} {{ Math.abs(c.yoy_pct) }}%
              </el-tag>
              <el-tag v-else size="small" type="info" effect="plain">昨日 0</el-tag>
              <span class="kpi-yoy-val">
                <template v-if="c.money">{{ formatMoney(c.yoy_value) }}</template>
                <template v-else>{{ formatNum(c.yoy_value) }}{{ c.unit || '' }}</template>
                 · 昨日
              </span>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 今日现金流出拆分（紧凑版：3列合计卡 + 横向堆叠比例条，一目了然 经营/实际/偏差 + A/B/C/D占比） -->
      <div class="today-routes today-routes-v2" v-if="kpiData.cash_routes_today">
        <div class="today-routes-title">
          今日现金流出拆分
          <el-tag size="small" type="info" effect="plain" style="margin-left:8px">A/B/C/D 四路 → 经营估算 vs 商户号实际现金流</el-tag>
        </div>
        <!-- 一行 3 个核心合计（经营 / 实际 / 偏差）-->
        <el-row :gutter="10" class="mt-6">
          <el-col :span="8"><div class="trv2-sum trv2-sum-op">
            <div class="trv2-sum-head">经营估算</div>
            <div class="trv2-sum-body">
              <span class="trv2-sum-val">{{ formatMoney(kpiData.cash_routes_today.operational.amount) }}</span>
              <span class="trv2-sum-sub">{{ formatNum(kpiData.cash_routes_today.operational.count) }}笔 · A+B+C+D</span>
            </div>
          </div></el-col>
          <el-col :span="8"><div class="trv2-sum trv2-sum-fc">
            <div class="trv2-sum-head">实际现金流 <span class="trv2-sum-tip">（对账基准）</span></div>
            <div class="trv2-sum-body">
              <span class="trv2-sum-val">{{ formatMoney(kpiData.cash_routes_today.actual_cashflow.amount ?? kpiData.cash_routes_today.financial.amount) }}</span>
              <span class="trv2-sum-sub">{{ formatNum(kpiData.cash_routes_today.actual_cashflow.count ?? kpiData.cash_routes_today.financial.count) }}笔 · 商户号 SUCCESS</span>
            </div>
          </div></el-col>
          <el-col :span="8"><div class="trv2-sum" :class="(kpiData.cash_routes_today.diff.amount ?? 0) >= 0 ? 'trv2-sum-diff-pos' : 'trv2-sum-diff-neg'">
            <div class="trv2-sum-head">对账偏差 <span class="trv2-sum-tip">（经营 − 实际）</span></div>
            <div class="trv2-sum-body">
              <span class="trv2-sum-val">{{ (kpiData.cash_routes_today.diff.amount ?? 0) >= 0 ? '+' : '' }}{{ formatMoney(kpiData.cash_routes_today.diff.amount) }}</span>
              <span class="trv2-sum-sub">{{ (kpiData.cash_routes_today.diff.count ?? 0) >= 0 ? '+' : '' }}{{ formatNum(kpiData.cash_routes_today.diff.count) }}笔 · 差值</span>
            </div>
          </div></el-col>
        </el-row>
        <!-- A/B/C/D 四路横向堆叠条：长度 = 占经营估算的比例，一眼看出每一路贡献 -->
        <div class="trv2-stack mt-10">
          <div class="trv2-stack-bar">
            <template v-for="(seg, k) in stackSegs" :key="k">
              <div class="trv2-seg"
                   :class="seg.cls"
                   :style="{ width: seg.pct + '%', minWidth: seg.amount > 0 ? (seg.pct < 1.2 ? '48px' : 'auto') : '0' }"
                   :title="seg.title">
                <span v-if="seg.pct >= 8 || seg.amount > 0 && seg.pct >= 3" class="trv2-seg-in">
                  <b>{{ seg.label }}</b> {{ formatMoney(seg.amount) }}
                </span>
                <span v-else-if="seg.amount > 0" class="trv2-seg-in trv2-seg-mini">
                  {{ seg.label }}
                </span>
              </div>
            </template>
            <div v-if="stackSegs.every(s => s.amount === 0 || s.amount == null)"
                 class="trv2-seg trv2-seg-empty" style="width:100%">
              <span class="trv2-seg-in">今日暂无数据</span>
            </div>
          </div>
          <!-- 四路图例 & 笔数 -->
          <div class="trv2-stack-legend mt-8">
            <div v-for="(seg, k) in stackSegs" :key="'lg-'+k" class="trv2-lg">
              <i class="trv2-lg-dot" :class="seg.dotCls"></i>
              <span class="trv2-lg-name">{{ seg.name }}</span>
              <span class="trv2-lg-val">{{ formatMoney(seg.amount) }}</span>
              <span class="trv2-lg-sub">{{ formatNum(seg.count) }}笔</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 模块2: 小时节奏 -->
    <section class="section-card mb-24">
      <h2 class="section-title">2. 今日实时节奏 <span class="section-sub">（小时级双柱：会话数(次) + 接待数(个) + 退款笔数 + 投诉量 + 退款金额线）</span></h2>
      <el-card shadow="never" body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="320px" />
      </el-card>
    </section>

    <!-- 模块3: 接待分项 + 模块4: 退款分项 → 并排两栏 -->
    <el-row :gutter="16" class="mb-24">
      <el-col :xs="24" :lg="12">
        <section class="section-card h-full">
          <h2 class="section-title">3. 今日接待分项</h2>
          <el-row :gutter="12">
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">接待分类分布</div>
                <BaseChart :option="receptionPieOption" height="280px" />
              </el-card>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">接待量 Top 10 客服 <span style="color:#909399; font-size: 11px; font-weight: 400">（会话数(次) + 接待数(个)）</span></div>
                <BaseChart :option="receptionStaffOption" height="280px" />
              </el-card>
            </el-col>
          </el-row>
        </section>
      </el-col>
      <el-col :xs="24" :lg="12">
        <section class="section-card h-full">
          <h2 class="section-title">4. 今日退款分项</h2>
          <el-row :gutter="12">
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">退款细分原因 Top 10（订单数 + 金额 双柱）<span style="font-weight:400;color:#909399;font-size:11px;margin-left:8px">按工单分类 + 商品名兜底（替换原退款单内refund_reason='用户退款'无效字段）</span></div>
                <BaseChart :option="refundReasonOption" height="280px" />
              </el-card>
            </el-col>
            <el-col :xs="24" :md="12">
              <el-card shadow="never" body-style="padding: 4px 6px">
                <div class="chart-title">退款接待人 Top 10（退款笔数 + 金额 双柱）<span style="font-weight:400;color:#909399;font-size:11px;margin-left:8px">按 PersonAlias → Person.display_name 姓名归一；来源: 工单handler(关联工单45天) → auditor/operator(自动补建兜底)；[未分配]=未匹配&无人名信息；用户自助=wechat/客户非客服操作；系统自动=sys/system类自动操作</span></div>
                <BaseChart :option="refundStaffOption" height="280px" />
              </el-card>
            </el-col>
          </el-row>
        </section>
      </el-col>
    </el-row>

    <!-- 模块5: 今日投诉分项 -->
    <section class="section-card mb-24">
      <h2 class="section-title">
        5. 今日投诉分项
        <span class="section-sub">（共 {{ complaintData.complaint_total || 0 }} 条投诉
          <template v-if="complaintData.complaint_unmatched > 0">
            · {{ complaintData.complaint_unmatched }} 条未关联接待记录
          </template>
          ）
        </span>
      </h2>
      <el-row :gutter="12">
        <el-col :xs="24" :md="12">
          <el-card shadow="never" body-style="padding: 4px 6px">
            <div class="chart-title">投诉子类分布（商品/服务/微信/12315/工信部）</div>
            <BaseChart :option="complaintSubPieOption" height="300px" />
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12">
          <el-card shadow="never" body-style="padding: 4px 6px">
            <div class="chart-title">投诉根因分布（关联网速/套餐/退款等接待记录）</div>
            <BaseChart :option="complaintRootBarOption" height="300px" />
          </el-card>
        </el-col>
      </el-row>
    </section>

    <!-- 模块6: 处理队列 三栏 -->
    <section class="section-card mb-24">
      <h2 class="section-title">6. 今日处理队列</h2>
      <el-row :gutter="16">
        <el-col :xs="24" :md="8">
          <el-card shadow="hover" class="queue-card">
            <template #header>
              <div class="flex-between">
                <span class="queue-title">
                  <el-icon color="#e6a23c"><Bell /></el-icon>
                  今日截止待办
                </span>
                <el-badge :value="queueData.todos?.length || 0" :max="99" class="queue-badge" />
              </div>
            </template>
            <div class="queue-list">
              <div v-for="t in queueData.todos || []" :key="t.id" class="queue-item"
                   @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="priorityTagType(t.priority)" effect="dark" class="mr-8">
                  {{ priorityLabel(t.priority) }}
                </el-tag>
                <span class="queue-item-title">{{ t.title }}</span>
                <el-tag size="small" effect="plain" class="queue-status">{{ todoStatusLabel(t.status) }}</el-tag>
              </div>
              <el-empty v-if="!queueData.todos?.length" description="今日无待办截止" :image-size="60" />
            </div>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-card shadow="hover" class="queue-card">
            <template #header>
              <div class="flex-between">
                <span class="queue-title">
                  <el-icon color="#409eff"><List /></el-icon>
                  进行中专项
                </span>
                <el-badge :value="queueData.tasks?.length || 0" :max="99" class="queue-badge" />
              </div>
            </template>
            <div class="queue-list">
              <div v-for="t in queueData.tasks || []" :key="t.id" class="queue-item"
                   @click="$router.push(`/tasks/${t.id}`)">
                <el-tag size="small" :type="taskStatusTagType(t.status)" effect="dark" class="mr-8">
                  {{ taskStatusLabel(t.status) }}
                </el-tag>
                <span class="queue-item-title">{{ t.title }}</span>
                <span v-if="t.deadline" class="queue-deadline">{{ formatDeadline(t.deadline) }}</span>
              </div>
              <el-empty v-if="!queueData.tasks?.length" description="暂无进行中专项" :image-size="60" />
            </div>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="8">
          <el-card shadow="hover" class="queue-card">
            <template #header>
              <div class="flex-between">
                <span class="queue-title">
                  <el-icon color="#f56c6c"><Warning /></el-icon>
                  今日异常事件
                </span>
                <el-badge :value="queueData.incidents?.length || 0" :max="99" class="queue-badge" />
              </div>
            </template>
            <div class="queue-list">
              <div v-for="inc in queueData.incidents || []" :key="inc.id" class="queue-item"
                   @click="$router.push('/management/incidents')">
                <el-tag size="small" :type="incidentStatusTagType(inc.status)" effect="dark" class="mr-8">
                  {{ incidentStatusLabel(inc.status) }}
                </el-tag>
                <span class="queue-item-title">{{ inc.title }}</span>
                <span class="queue-deadline">{{ formatDeadline(inc.occurred_at, 'time') }}</span>
              </div>
              <el-empty v-if="!queueData.incidents?.length" description="今日无异常事件 ✅" :image-size="60" />
            </div>
          </el-card>
        </el-col>
      </el-row>
    </section>

    <!-- 模块7: 人均效率表 -->
    <section class="section-card mb-24">
      <h2 class="section-title">7. 今日人均效率</h2>
      <el-card shadow="never">
        <el-table :data="staffRows" border stripe size="small" max-height="520">
          <el-table-column label="排名" width="64" align="center" fixed>
            <template #default="{ $index }">
              <el-tag v-if="$index < 3" size="small" :type="['danger','warning','success'][$index]" effect="dark">
                Top{{ $index + 1 }}
              </el-tag>
              <span v-else class="text-muted">{{ $index + 1 }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="人员" width="110" />
          <el-table-column prop="department" label="部门" width="110">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.department === '未设置' ? 'info' : 'success'">{{ row.department }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="岗位・分组・专项" width="208">
            <template #default="{ row }">
              <div style="display:flex; flex-direction:column; gap:3px">
                <div style="display:flex; gap:4px; flex-wrap:wrap">
                  <el-tag size="small" effect="plain" :type="posTagType(row.position)">
                    {{ row.position || (row.name === '用户自助' ? '系统位' : '—') }}
                  </el-tag>
                  <el-tag size="small" effect="dark" :type="grpTagType(row.group)" style="align-self:flex-start">
                    {{ row.group === '未分组' ? (row.name === '用户自助' ? '系统位' : '未分组') : (row.group || '部门统管') }}
                  </el-tag>
                </div>
                <el-tag size="small" effect="plain" type="primary" style="align-self:flex-start">
                  <span style="font-weight:400;color:#909399;font-size:10px; margin-right:4px">专项</span>
                  {{ (row.current_specialty && row.current_specialty !== '组长') ? row.current_specialty : inferSpecialty(row) }}
                </el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="reception_count" label="会话数(次)" width="116" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
          </el-table-column>
          <el-table-column prop="reception_device_count" label="接待数(个)" width="108" align="right" sortable>
            <template #default="{ row }">
              <span style="color:#409eff">
                {{ formatNum(row.reception_device_count ?? 0) }}
                <span style="color:#909399; font-size: 11px">个</span>
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="refund_op_count" label="退款挽回" width="110" align="right" sortable>
            <template #default="{ row }">
              <span style="color:#909399;font-size:10px">挽回</span>
              {{ formatNum(row.sp_tuikuan ?? row.refund_op_count) }}
            </template>
          </el-table-column>
          <el-table-column label="四专项明细" width="298" align="left">
            <template #default="{ row }">
              <div class="sp-legend">
                <span class="sp-chip zx" v-if="row.sp_zixun" title="咨询接待 ×难度1.0">咨询{{ row.sp_zixun }}×1.0</span>
                <span class="sp-chip tc" v-if="row.sp_taocan" title="套餐专项 ×难度1.8">套餐{{ row.sp_taocan }}×1.8</span>
                <span class="sp-chip tk" v-if="row.sp_tuikuan" title="退款挽回 ×难度2.2">退款{{ row.sp_tuikuan }}×2.2</span>
                <span class="sp-chip ts" v-if="row.sp_tousu" title="投诉专员 ×难度3.0">投诉{{ row.sp_tousu }}×3.0</span>
                <el-tooltip
                  v-if="row.rank === null || row.is_virtual === true"
                  effect="dark" placement="top"
                  content="系统/用户自助/第三方设备账号等 不计入真人员工排名"
                >
                  <el-tag size="small" effect="dark" type="info" style="margin-left:4px">非真人</el-tag>
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="工作量分" width="138" align="right" sortable :sort-method="(a,b)=>Number(a.work_score||0)-Number(b.work_score||0)">
            <template #default="{ row }">
              <el-tooltip effect="dark" placement="top" :width="440" :content="scoreTooltip(row,'work')">
                <el-tag size="small" effect="plain" :type="scoreTagType(row.work_score,500,300,100)">
                  <span style="font-weight:600">{{ formatNum(row.work_score) }}</span>
                  <span style="color:#909399;font-size:10px;margin-left:4px">分(次)</span>
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="效率分" width="138" align="right" sortable :sort-method="(a,b)=>Number(a.eff_score||0)-Number(b.eff_score||0)">
            <template #default="{ row }">
              <el-tooltip effect="dark" placement="top" :width="440" :content="scoreTooltip(row,'eff')">
                <el-tag size="small" effect="plain" :type="scoreTagType(row.eff_score,350,200,80)">
                  <span style="font-weight:600;color:#2563eb">{{ formatNum(row.eff_score) }}</span>
                  <span style="color:#909399;font-size:10px;margin-left:4px">分(个)</span>
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!staffRows.length" description="今日暂无人员工作数据" />
      </el-card>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { today } from '@/api/analytics'
import BaseChart from '@/components/BaseChart.vue'
import { formatMoney } from '@/utils/format'
import dayjs from 'dayjs'
import {
  Refresh, ChatDotRound, Document, Money, Wallet, Checked, UserFilled,
  Bell, List, Warning, WarningFilled, QuestionFilled
} from '@element-plus/icons-vue'

const loading = ref(false)
const quickDate = ref('today')  // today / yesterday
const customDate = ref('')      // YYYY-MM-DD, 空表示用quickDate

// 当前实际传参date: 空=今天, 'YYYY-MM-DD'=指定日期
const currentDate = computed(() => {
  if (customDate.value) return customDate.value
  if (quickDate.value === 'yesterday') return dayjs().subtract(1, 'day').format('YYYY-MM-DD')
  return ''  // 今天不传参数(实时窗口)
})
// 大标题显示用日期 (YYYY-MM-DD): 今天不传参数也显示今天的日期, 避免截图时不知道是哪天
const displayDate = computed(() => {
  if (customDate.value) return customDate.value
  if (quickDate.value === 'yesterday') return dayjs().subtract(1, 'day').format('YYYY-MM-DD')
  return dayjs().format('YYYY-MM-DD')
})

// ---------- 日期水印 (SVG旋转平铺, 绝对定位穿透 pointer-events, 卡片内容区/卡片间都能看到; 局部截图也清晰) ----------
const WATERMARK_BOARD_NAME = '客服日度运营看板'
const WATERMARK_PRIVACY = '内部数据 请勿外传'
function buildWatermark(dateStr) {
  const line1 = dateStr
  const line2 = WATERMARK_BOARD_NAME
  const line3 = WATERMARK_PRIVACY
  const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="560" height="360" viewBox="0 0 560 360">
  <g transform="rotate(-17 280 180)">
    <text x="30" y="140" fill="#1f2937" font-family="PingFang SC,Microsoft YaHei,sans-serif" font-size="30" font-weight="600" opacity="0.062">${line1}</text>
    <text x="30" y="185" fill="#1f2937" font-family="PingFang SC,Microsoft YaHei,sans-serif" font-size="26" font-weight="500" opacity="0.052">${line2}</text>
    <text x="30" y="222" fill="#6b7280" font-family="PingFang SC,Microsoft YaHei,sans-serif" font-size="18" font-weight="400" opacity="0.050">${line3}</text>
  </g>
</svg>`
  const encoded = encodeURIComponent(svg)
    .replace(/'/g, '%27').replace(/"/g, '%22')
  return `url("data:image/svg+xml;charset=utf-8,${encoded}") 0 0 / 560px 360px repeat`
}
const watermarkBg = computed(() => ({
  background: buildWatermark(displayDate.value),
}))

// ---------- 模块1 KPI ----------
const kpiData = ref({})
const kpiCards = computed(() => {
  const k = kpiData.value || {}
  const def = [
    { key: 'reception',      label: '今日会话数',   icon: 'ChatDotRound', bg: '#ecf5ff', color: '#409eff', unit: '次',
      money: false,
      tip: `今日会话数 = 客服助手 沟通事项次数 (1条接待记录=1次会话, 只记当日已发生)
今日接待数 = 去重 device_sn (按活跃设备近似为独立客户人数, 单位:个)
—— 会话数(次) > 接待数(个) 说明老客户多次进线沟通; ≈相等说明当日基本都是新客户`,
      ...(k.reception || { value: 0, yoy_value: 0, yoy_pct: null, device_count: 0, device_count_yoy: 0 }) },
    { key: 'refund_count',   label: '今日退款笔数', icon: 'Document',    bg: '#fdf6ec', color: '#e6a23c', unit: '笔',
      money: false,
      tip: '路线A: 退费单管理中 refund_method=\'线上\' 的记录 (统一全域口径: 只退款方式为线上才算一条退款路线)',
      ...(k.refund_count || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'refund_amount',  label: '今日退款金额', icon: 'Money',       bg: '#fef0f0', color: '#f56c6c', unit: '',
      money: true,
      tip: '路线A: 退费单 refund_method=\'线上\' 的金额合计 (不包含退回钱包/线下退款等方式)',
      ...(k.refund_amount || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'cash_out',       label: '真实现金流出', icon: 'Wallet',      bg: '#fdf6ec', color: '#e6a23c', unit: '',
      money: true,
      tip: '经营侧登记估算 = A(退费单线上直退) + B(钱包-用户取现) 两路合计',
      ...(k.cash_out || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'complaint',      label: '今日投诉量',   icon: 'WarningFilled', bg: '#fef0f0', color: '#f56c6c', unit: '单',
      money: false, tip: null, ...(k.complaint || { value: 0, yoy_value: 0, yoy_pct: null }) },
    /* ticket_done (今日已完工单): 暂时不关注, 从KPI/排行榜/工作量分中全部移除; 后端接口仍保留字段但不计入权重 */
    { key: 'staff_on_duty',  label: '今日在岗人数', icon: 'UserFilled',  bg: '#f4f4f5', color: '#909399', unit: '人',
      money: false, tip: null, ...(k.staff_on_duty || { value: 0, yoy_value: 0, yoy_pct: null }) },
  ]
  return def
})

// KPI 环比 tag 颜色: 接待量/在岗/退款笔数(越大越好↑绿↓红); 退款金额/现金流出/投诉量(越小越好↑红↓绿)
const BAD_UP_KEYS = new Set(['refund_amount', 'cash_out', 'complaint'])
function yoyTagType(key, pct) {
  if (pct == null) return 'info'
  const good = BAD_UP_KEYS.has(key) ? pct < 0 : pct > 0
  if (pct === 0) return 'info'
  return good ? 'success' : 'danger'
}

// ---------- 模块1 现金流出拆分 堆叠条数据 ----------
const stackSegs = computed(() => {
  const r = kpiData.value?.cash_routes_today || {}
  const defs = [
    { key: 'route_a', label: 'A', name: 'A. 线上直退',    cls: 'trv2-seg-a', dotCls: 'd-a' },
    { key: 'route_b', label: 'B', name: 'B. 用户取现',    cls: 'trv2-seg-b', dotCls: 'd-b' },
    { key: 'route_c', label: 'C', name: 'C. 闪猫退款',    cls: 'trv2-seg-c', dotCls: 'd-c' },
    { key: 'route_d', label: 'D', name: 'D. 其他退款',    cls: 'trv2-seg-d', dotCls: 'd-d' },
  ]
  const totalAmt = Math.max(0, Number(r.operational?.amount ?? 0))
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

// ---------- 模块2 小时节奏 ----------
const hourlyData = ref({ hour_labels: [], reception: [], reception_devices: [], refund_count: [], refund_amount: [], complaint: [] })
const hourlyOption = computed(() => ({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
    formatter: (params) => {
      if (!params || !params.length) return ''
      const p0 = params[0]
      const h = hourlyData.value.hour_labels?.[p0.dataIndex] ?? ''
      const val = (name) => {
        const m = params.find(x => x.seriesName === name)
        return m == null ? '-' : m.value == null ? '-' : (name === '退款金额' ? '¥'+Number(m.value).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : Number(m.value).toLocaleString('zh-CN'))
      }
      return `<b>${h}</b><br/>` +
        `会话数(次): ${val('会话数(次)')}<br/>` +
        `接待数(个): ${val('接待数(个)')}<br/>` +
        `退款笔数: ${val('退款笔数')}<br/>` +
        `投诉量: ${val('投诉量')}<br/>` +
        `退款金额: ${val('退款金额')}`
    } },
  legend: { data: ['会话数(次)', '接待数(个)', '退款笔数', '投诉量', '退款金额'], top: 0 },
  grid: { left: 56, right: 72, bottom: 36, top: 40 },
  xAxis: { type: 'category', data: hourlyData.value.hour_labels || [], axisLabel: { fontSize: 10, interval: 1 } },
  yAxis: [
    { type: 'value', name: '数量(次/个/笔/单)', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: 'dashed' } } },
    { type: 'value', name: '退款金额(元)', axisLabel: { formatter: (v) => (v >= 10000 ? (v/10000).toFixed(0)+'万' : v), fontSize: 10 } },
  ],
  series: [
    {
      name: '会话数(次)', type: 'bar', stack: null, data: hourlyData.value.reception || [],
      itemStyle: { color: '#409eff', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 14,
    },
    {
      name: '接待数(个)', type: 'bar', data: hourlyData.value.reception_devices || [],
      itemStyle: { color: '#91cc75', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 14,
    },
    {
      name: '退款笔数', type: 'bar', data: hourlyData.value.refund_count || [],
      itemStyle: { color: '#e6a23c', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 14,
    },
    {
      name: '投诉量', type: 'bar', data: hourlyData.value.complaint || [],
      itemStyle: { color: '#f56c6c', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 14,
    },
    {
      name: '退款金额', type: 'line', yAxisIndex: 1, smooth: true,
      data: hourlyData.value.refund_amount || [],
      itemStyle: { color: '#9b59b6' },
      areaStyle: { color: 'rgba(155,89,182,0.10)' },
      symbol: 'circle', symbolSize: 5,
      lineStyle: { width: 2 },
    },
  ],
}))

// ---------- 模块3 接待分项 ----------
const receptionData = ref({ categories: [], top_staff: [] })
const receptionPieOption = computed(() => {
  const data = (receptionData.value.categories || []).filter(d => d.value > 0)
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    color: ['#f56c6c', '#e6a23c', '#409eff', '#67c23a', '#909399', '#9b59b6'],
    series: [{
      name: '接待分类', type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: '{b}\n{d}%', fontSize: 10 },
      data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '暂无数据' } }],
    }],
  }
})
const receptionStaffOption = computed(() => {
  const list = (receptionData.value.top_staff || []).slice(0, 10)
  const dept = (d) => d.department && d.department !== '未设置' ? `(${d.department})` : ''
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const i = list.length - 1 - p[0].dataIndex
        const d = list[i]
        if (!d) return ''
        const grp = d.group && d.group !== '未分组' ? `组别: ${d.group}` : ''
        const dev = d.device_count != null ? `<br/>接待数: ${formatNum(d.device_count)} 个 (独立客户≈)` : ''
        return `<b>${d.name}</b><br/>部门: ${d.department || '未设置'}${grp ? '<br/>' + grp : ''}<br/>会话数: ${formatNum(d.count)} 次${dev}`
      },
    },
    grid: { left: 150, right: 140, bottom: 20, top: 10 },
    xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: list.map(d => `${d.name}${dept(d)}`).reverse(),
      axisLabel: { fontSize: 10, width: 140, overflow: 'truncate' },
    },
    legend: { show: false, data: ['会话数(次)', '接待数(个)'] },
    series: [
      {
        name: '会话数(次)', type: 'bar',
        data: list.map(d => d.count).reverse(),
        itemStyle: { color: '#67c23a', borderRadius: [0, 3, 3, 0] },
        barMaxWidth: 12,
        label: { show: true, position: 'right', fontSize: 10, formatter: (p) => `${formatNum(p.value)} 次` },
      },
      {
        name: '接待数(个)', type: 'bar',
        data: list.map(d => (d.device_count ?? 0)).reverse(),
        itemStyle: { color: '#409eff', borderRadius: [0, 3, 3, 0] },
        barMaxWidth: 12,
        label: { show: true, position: 'right', fontSize: 10, formatter: (p) => `${formatNum(p.value)} 个` },
      },
    ],
  }
})

// ---------- 模块5 今日投诉分项 (新增) ----------
// complaintData 字段来自 reception 接口响应 (complaint_total/complaint_subs/complaint_roots/complaint_unmatched)
const complaintData = ref({ complaint_total: 0, complaint_subs: [], complaint_roots: [], complaint_unmatched: 0 })
// 饼图: 投诉子类分布 (商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台)
const complaintSubPieOption = computed(() => {
  const data = (complaintData.value.complaint_subs || []).filter(d => d.value > 0)
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    color: ['#f56c6c', '#e6a23c', '#909399', '#9b59b6', '#409eff', '#67c23a', '#8e44ad'],
    series: [{
      name: '投诉子类', type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: '{b}\n{d}%', fontSize: 10 },
      data: data.length ? data : [{ name: '今日暂无投诉', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '今日暂无投诉 ✅' } }],
    }],
  }
})
// 柱图: 投诉根因分布 (网络质量类/套餐争议类/退款协商失败类/降成争议类/其它/待标注)
const complaintRootBarOption = computed(() => {
  const list = (complaintData.value.complaint_roots || []).filter(d => d.count > 0).slice(0, 10)
  const rootColorMap = {
    '网络质量类': '#f56c6c',
    '套餐争议类': '#e6a23c',
    '退款协商失败类': '#909399',
    '降成争议类': '#9b59b6',
    '其它(注销/发票/退换)': '#409eff',
    '待标注': '#c0c4cc',
  }
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const item = p[0]
        return `${item.name}<br/>投诉数: ${formatNum(item.data)} 条`
      },
    },
    grid: { left: 150, right: 48, bottom: 24, top: 10 },
    xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: list.map(d => d.name).reverse(),
      axisLabel: { fontSize: 10, width: 140, overflow: 'truncate' },
    },
    series: [{
      type: 'bar',
      data: list.map(d => ({
        value: d.count,
        itemStyle: { color: rootColorMap[d.name] || '#409eff', borderRadius: [0, 3, 3, 0] },
      })).reverse(),
      barMaxWidth: 18,
      label: { show: true, position: 'right', fontSize: 10, formatter: (p) => formatNum(p.value) + '条' },
    }],
  }
})

// ---------- 模块4 退款分项 ----------
const refundData = ref({ reasons: [], methods: [] })
const refundReasonOption = computed(() => {
  const list = (refundData.value.reasons || []).slice(0, 10).reverse()
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        // list 已经 reverse() 过，让 Top1 显示在图表底部；series.data 的 dataIndex 与 list 下标一一对应
        const idx = params[0].dataIndex
        if (idx == null || idx < 0 || idx >= list.length) return ''
        const d = list[idx]
        if (!d) return ''
        return `${d.name}<br/>退款笔数: ${formatNum(d.count)} 笔<br/>退款金额: ${formatMoney(d.amount)}`
      },
    },
    legend: { top: 0, right: 10, data: ['退款笔数', '退款金额'], textStyle: { fontSize: 11 } },
    grid: { left: 160, right: 88, bottom: 20, top: 36 },
    xAxis: {
      type: 'value',
      axisLabel: { fontSize: 10, formatter: (v) => (v >= 10000 ? (v / 10000).toFixed(0) + '万' : v) },
    },
    yAxis: {
      type: 'category',
      data: list.map(d => d.name.length > 18 ? d.name.slice(0,18)+'…' : d.name),
      axisLabel: { fontSize: 10, width: 150, overflow: 'truncate' },
    },
    series: [
      {
        name: '退款笔数', type: 'bar',
        data: list.map(d => d.count),
        itemStyle: { color: '#e6a23c', borderRadius: [0, 3, 3, 0] },
        barMaxWidth: 10,
        label: { show: true, position: 'right', fontSize: 10, formatter: (p) => formatNum(p.value) + '笔' },
      },
      {
        name: '退款金额', type: 'bar',
        data: list.map(d => d.amount),
        itemStyle: { color: '#f56c6c', borderRadius: [0, 3, 3, 0] },
        barMaxWidth: 10,
        label: { show: true, position: 'right', fontSize: 10, formatter: (p) => formatMoney(p.value) },
      },
    ],
  }
})
const refundStaffOption = computed(() => {
  const list = (refundData.value.by_staff || []).slice(0, 10).reverse()
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const idx = params[0].dataIndex
        if (idx == null || idx < 0 || idx >= list.length) return ''
        const d = list[idx]
        if (!d) return ''
        return `${d.name}<br/>退款笔数: ${formatNum(d.count)} 笔<br/>退款金额: ${formatMoney(d.amount)}`
      },
    },
    legend: { top: 0, right: 10, data: ['退款笔数', '退款金额'], textStyle: { fontSize: 11 } },
    grid: { left: 140, right: 88, bottom: 20, top: 36 },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: list.map(d => d.name.length > 16 ? d.name.slice(0,16)+'…' : d.name),
      axisLabel: { fontSize: 11, color: '#606266' },
    },
    series: [
      { name: '退款笔数', type: 'bar', data: list.map(d => d.count),
        itemStyle: { color: '#e6a23c' }, barMaxWidth: 14, label: { show: true, position: 'right', fontSize: 10, formatter: '{c}' } },
      { name: '退款金额', type: 'bar', data: list.map(d => d.amount),
        itemStyle: { color: '#f56c6c' }, barMaxWidth: 14,
        label: { show: true, position: 'right', fontSize: 10,
          formatter: (p) => '¥' + (Number(p.value) || 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 }) } },
    ],
  }
})

// ---------- 模块5 处理队列 ----------
const queueData = ref({ todos: [], tasks: [], incidents: [] })
function priorityTagType(p) {
  return { urgent: 'danger', high: 'warning', medium: 'primary', low: 'info' }[p] || 'info'
}
function priorityLabel(p) {
  return { urgent: '紧急', high: '高', medium: '中', low: '低' }[p] || '中'
}
function todoStatusLabel(s) {
  return { pending: '待办', processing: '处理中', confirming: '待确认', done: '已完成' }[s] || s
}
function taskStatusTagType(s) {
  return { pending_start: 'info', executing: 'warning', following: 'primary', done: 'success', archived: 'info' }[s] || 'info'
}
function taskStatusLabel(s) {
  return { pending_start: '待启动', executing: '执行中', following: '跟进中', done: '已完成', archived: '已归档' }[s] || s
}
function incidentStatusTagType(s) {
  return { registered: 'danger', processing: 'warning', calmed: 'primary', reviewed: 'success' }[s] || 'info'
}
function incidentStatusLabel(s) {
  return { registered: '登记', processing: '处理中', calmed: '已平息', reviewed: '已复盘' }[s] || s
}
function formatDeadline(d, type='date') {
  if (!d) return ''
  if (type === 'time') return dayjs(d).format('MM-DD HH:mm')
  return dayjs(d).format('MM-DD')
}

// ---------- 模块6~7 人员效率表: 工作分 / 效率分 双列 + 本职/转接 breakdown ----------
const staffRows = ref([])
function scoreTagType(score, tHi, tMid, tLo) {
  const n = Number(score) || 0
  if (n >= tHi) return 'danger'
  if (n >= tMid) return 'warning'
  if (n >= tLo) return 'success'
  return 'info'
}
function posTagType(p) {
  if (p === '主管') return 'danger'
  if (p === '组长') return 'warning'
  if (p === '客服') return 'success'
  return 'info'
}
// 5 个组颜色区分一眼就能识别
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
// 组长 4人 current_specialty 原本=「组长」(非专项名) → 按实际分组属性 反推其默认专项显示
function inferSpecialty(r) {
  // 先看实际做了什么(Top 2 专项) 自动生成
  const items = [
    { name: '咨询接待', n: Number(r.sp_zixun) || 0 },
    { name: '套餐专项', n: Number(r.sp_taocan) || 0 },
    { name: '退款挽回', n: Number(r.sp_tuikuan) || 0 },
    { name: '投诉专员', n: Number(r.sp_tousu) || 0 },
  ].filter(x => x.n > 0).sort((a, b) => b.n - a.n)
  if (items.length) {
    if (items.length === 1) return items[0].name
    return items.slice(0, 2).map(i => i.name).join('+')
  }
  // 没数据 按组默认属性兜底(已知截图分组)
  const GRP_DEFAULT_SP = {
    '董梦玲组': '咨询接待',
    '吴晓龙组': '投诉接待+咨询',
    '叶珍凤组': '咨询接待+退款挽回',
    '朱海洋组': '套餐专项+退款挽回',
    '投诉专项组': '投诉接待',
  }
  return GRP_DEFAULT_SP[r.group] || (r.group === '未分组' ? '—' : '综合管理')
}
// 工作分 / 效率分 通用 tooltip (mode: 'work' 会话次数维度 / 'eff' 接待个数维度)
function scoreTooltip(r, mode) {
  const fmt = (v) => formatNum(v)
  const w = r.weights || { '咨询接待': 1.0, '套餐专项': 1.8, '退款挽回': 2.2, '投诉专员': 3.0 }
  const xfer = Number(r.transfer_penalty || 0.2)
  const bd = r.breakdown || {}
  const useBreakdown = bd && bd['咨询接待'] && (typeof bd['咨询接待'].coef === 'number')
  const isWork = mode === 'work'
  const unitChar = isWork ? '次' : '个'
  const contribKey = isWork ? 'work_contrib' : 'eff_contrib'
  const countKey = isWork ? 'sessions' : 'people'
  const sp = (r.current_specialty && r.current_specialty !== '组长') ? r.current_specialty : inferSpecialty(r)
  const mgmtMark = r.is_management ? '  [管理层:跨类全系数豁免]' : ''
  const hrMark = r.hr_category ? `  (HR大类=${r.hr_category})` : ''

  const CATS = [
    { k: '咨询接待', short: '咨询', fallback_s: r.sp_zixun, fallback_p: r.pp_zixun ?? 0 },
    { k: '套餐专项', short: '套餐', fallback_s: r.sp_taocan, fallback_p: r.pp_taocan ?? 0 },
    { k: '退款挽回', short: '退款', fallback_s: r.sp_tuikuan, fallback_p: r.pp_tuikuan ?? 0 },
    { k: '投诉专员', short: '投诉', fallback_s: r.sp_tousu, fallback_p: r.pp_tousu ?? 0 },
  ]
  const head = [
    `【${r.name || ''}】${r.position || '—'} · ${r.group || '部门统管'} · 专项：${sp}${hrMark}${mgmtMark}`,
    `模式: ${isWork ? '工作量分 (会话次数维度, 反映忙闲强度)' : '效率分 (接待个数维度, SN去重, 反映覆盖面)'}`,
    `系数: 本职=难度系数  非本职(转接)=难度系数×${xfer}`,
    '————————————————————',
  ]
  const lines = []
  let total = 0
  for (const c of CATS) {
    if (useBreakdown) {
      const b = bd[c.k] || { weight: w[c.k], coef: w[c.k], [countKey]: 0, [contribKey]: 0, is_own: true }
      const n = Number(b[countKey]) || 0
      const coef = Number(b.coef) || 0
      const contrib = Number(b[contribKey]) || 0
      const mark = b.is_own ? '本职' : `转接×${xfer}`
      lines.push(`${c.short} ${fmt(n)}${unitChar} × ${coef} [${mark}] = ${contrib.toFixed(1)}`)
      total += contrib
    } else {
      const n = isWork ? (Number(c.fallback_s) || 0) : (Number(c.fallback_p) || 0)
      const coef = w[c.k]
      const contrib = n * coef
      lines.push(`${c.short} ${fmt(n)}${unitChar} × ${coef} = ${contrib.toFixed(1)}`)
      total += contrib
    }
  }
  const reported = isWork ? (Number(r.work_score) || 0) : (Number(r.eff_score) || 0)
  lines.push('————————————————————')
  lines.push(`合成分 = ${total.toFixed(2)} 分${unitChar}   (接口返回: ${reported})`)
  return head.concat(lines).join('\n')
}


// ---------- 通用格式化 ----------
function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

// ---------- 日期切换 ----------
function onQuickDate() {
  customDate.value = ''
  loadAll()
}
function onCustomDate(val) {
  if (!val) {
    quickDate.value = 'today'
  } else {
    const y = dayjs().format('YYYY-MM-DD')
    const yst = dayjs().subtract(1, 'day').format('YYYY-MM-DD')
    if (val === y) quickDate.value = 'today'
    else if (val === yst) quickDate.value = 'yesterday'
  }
  loadAll()
}

// ---------- 数据加载 ----------
async function loadAll() {
  loading.value = true
  const params = currentDate.value ? { date: currentDate.value } : {}
  try {
    const [k, h, r, rf, q, s] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend(params),
      today.reception(params),
      today.refund(params),
      today.queue(params),
      today.staff(params),
    ])
    kpiData.value = k.data || {}
    hourlyData.value = h.data || { hour_labels: [], reception: [], refund_amount: [] }
    // reception 响应同时包含 接待分类(top_staff/categories) + 新增投诉统计(complaint_*)
    const rdata = r.data || {}
    receptionData.value = {
      categories: rdata.categories || [],
      top_staff: rdata.top_staff || [],
    }
    complaintData.value = {
      complaint_total: rdata.complaint_total || 0,
      complaint_subs: rdata.complaint_subs || [],
      complaint_roots: rdata.complaint_roots || [],
      complaint_unmatched: rdata.complaint_unmatched || 0,
    }
    refundData.value = rf.data || { reasons: [], methods: [] }
    queueData.value = q.data || { todos: [], tasks: [], incidents: [] }
    staffRows.value = s.data || []
  } catch (e) {
    console.error('[Today] load error', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.today-dashboard { padding-bottom: 24px; position: relative; overflow: visible; }
/* 全屏水印层: 绝对定位铺满父容器, z-index=9999 始终位于所有卡片/图表上层, 但 pointer-events:none 永远不挡tooltip/按钮操作
   局部截图任何一个 chart 子区域, 水印 (日期+看板名+内部数据) 都清晰可见 (旋转-17度, 重复平铺) */
.today-watermark {
  position: absolute;
  inset: 0 0 -40px 0; /* 下方向外40px: 覆盖最底表格尾部 */
  pointer-events: none !important;
  user-select: none;
  z-index: 9999;
  background-repeat: repeat !important;
}
.title-date-sep { color: #c0c4cc; margin: 0 10px; font-weight: 400; }
.title-date { color: #409eff; font-size: 20px; font-weight: 600; letter-spacing: 0.5px; }
.filter-bar { display: flex; align-items: center; }
.section-card { }
.section-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  margin: 0 0 14px;
  padding-left: 10px;
  border-left: 3px solid #409eff;
}
.section-sub { font-size: 12px; color: #909399; font-weight: 400; margin-left: 6px; }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.h-full { height: 100%; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.text-muted { color: #909399; }
.mr-8 { margin-right: 8px; }

/* KPI卡 */
.kpi-card { position: relative; transition: transform 0.15s; }
.kpi-card:hover { transform: translateY(-2px); }
.kpi-icon {
  width: 42px; height: 42px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 10px;
}
.kpi-label { color: #909399; font-size: 13px; }
.kpi-value { font-size: 24px; font-weight: 600; color: #303133; margin-top: 4px; line-height: 1.2; }
.kpi-unit { font-size: 13px; font-weight: 400; color: #909399; margin-left: 4px; }
.kpi-yoy { margin-top: 8px; display: flex; align-items: center; gap: 6px; font-size: 12px; color: #909399; }
.kpi-yoy-val { color: #c0c4cc; font-size: 12px; }
/* 接待量KPI卡: 主金额下方追加一行"覆盖设备 XX 台" (蓝色强调, 与设备台数主题呼应) */
.kpi-device-sub { margin-top: 5px; display: flex; align-items: baseline; gap: 6px; }
.kpi-device-sub-label { font-size: 11px; color: #909399; }
.kpi-device-sub-val { font-size: 15px; font-weight: 600; color: #409eff; letter-spacing: -0.3px; }
.kpi-device-sub-unit { font-size: 11px; font-weight: 400; color: #909399; margin-left: 2px; }

.chart-title { font-size: 13px; color: #606266; font-weight: 500; padding: 4px 8px; }

/* 处理队列 */
.queue-card { height: 100%; }
.queue-title { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 500; color: #303133; }
.queue-badge { }
.queue-list { max-height: 320px; overflow-y: auto; }
.queue-item {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 8px; border-bottom: 1px solid #f5f7fa;
  cursor: pointer; transition: background 0.15s;
}
.queue-item:hover { background: #f5f7fa; }
.queue-item:last-child { border-bottom: none; }
.queue-item-title {
  flex: 1;
  font-size: 13px; color: #303133;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.queue-status { margin-left: auto; font-size: 11px; }
.queue-deadline { font-size: 12px; color: #909399; }

/* 今日现金流出拆分 v2：紧凑一体化（3合计卡 + 1堆叠比例条 + 1图例行） */
.today-routes-v2 { padding: 10px 12px 10px; background: linear-gradient(180deg,#fbfcfe,#f6f8fb); border: 1px solid #ebeef5; border-radius: 10px; }
.today-routes-v2 .today-routes-title { margin-bottom: 6px; }

.mt-6 { margin-top: 6px; }
.mt-8 { margin-top: 8px; }
.mt-10 { margin-top: 10px; }

/* 3 张合计卡：左色条 + 主金额 + 小计文字，紧凑一行 */
.trv2-sum {
  position: relative;
  padding: 6px 10px 6px 12px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid #ebeef5;
}
.trv2-sum::before {
  content: '';
  position: absolute;
  left: 0; top: 6px; bottom: 6px;
  width: 3px;
  border-radius: 0 3px 3px 0;
}
.trv2-sum-head { font-size: 12px; color: #606266; font-weight: 500; line-height: 1.4; }
.trv2-sum-tip { color: #909399; font-weight: 400; font-size: 11px; }
.trv2-sum-body { margin-top: 2px; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.trv2-sum-val { font-size: 18px; font-weight: 600; color: #303133; line-height: 1.25; letter-spacing: -0.3px; }
.trv2-sum-sub { font-size: 11px; color: #909399; }
.trv2-sum-op::before { background: #409eff; }
.trv2-sum-op { border-color: #d9ecff; }
.trv2-sum-fc::before { background: #e6a23c; }
.trv2-sum-fc { border-color: #faecd8; }
.trv2-sum-diff-pos::before { background: #f56c6c; }
.trv2-sum-diff-pos { border-color: #fbc4c4; }
.trv2-sum-diff-pos .trv2-sum-val,
.trv2-sum-diff-pos .trv2-sum-sub { color: #f56c6c; }
.trv2-sum-diff-neg::before { background: #67c23a; }
.trv2-sum-diff-neg { border-color: #c2e7b0; }
.trv2-sum-diff-neg .trv2-sum-val,
.trv2-sum-diff-neg .trv2-sum-sub { color: #67c23a; }

/* 四路堆叠条：100% 宽一行分 4 段 = 经营估算总长度（A+B+C+D 加总即 100%） */
.trv2-stack { }
.trv2-stack-bar {
  display: flex;
  width: 100%;
  height: 28px;
  line-height: 28px;
  border-radius: 6px;
  overflow: hidden;
  background: #f0f2f5;
  border: 1px solid #e4e7ed;
}
.trv2-seg {
  height: 100%;
  position: relative;
  transition: width 0.3s ease;
  white-space: nowrap;
}
.trv2-seg-in {
  display: block;
  padding: 0 8px;
  font-size: 12px;
  color: #fff;
  text-shadow: 0 1px 1px rgba(0,0,0,0.12);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  line-height: 28px;
}
.trv2-seg-mini .trv2-seg-in { padding: 0 4px; font-size: 11px; }
.trv2-seg-a { background: linear-gradient(90deg,#409eff,#66b1ff); }
.trv2-seg-b { background: linear-gradient(90deg,#67c23a,#85ce61); }
.trv2-seg-c { background: linear-gradient(90deg,#e6a23c,#ebb563); }
.trv2-seg-d { background: linear-gradient(90deg,#909399,#a6a9ad); }
.trv2-seg-empty { background: #f4f4f5; color: #909399; text-align: center; }
.trv2-seg-empty .trv2-seg-in { color: #909399; text-shadow: none; text-align: center; }

/* 四路图例：一行 4 列对齐，色点 + 名称 + 金额 + 笔数 */
.trv2-stack-legend {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 4px 8px;
}
.trv2-lg { display: flex; align-items: center; gap: 6px; font-size: 12px; line-height: 1.4; }
.trv2-lg-dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
.d-a { background: #409eff; }
.d-b { background: #67c23a; }
.d-c { background: #e6a23c; }
.d-d { background: #909399; }
.trv2-lg-name { color: #606266; }
.trv2-lg-val { color: #303133; font-weight: 500; margin-left: auto; }
.trv2-lg-sub { color: #909399; font-size: 11px; }

/* 模块7 四专项明细胶囊 (zx咨询/tc套餐/tk退款/ts投诉) */
.sp-legend { display: flex; flex-wrap: wrap; gap: 4px 6px; line-height: 1.35; }
.sp-chip {
  display: inline-flex; align-items: center;
  padding: 1px 6px; border-radius: 999px;
  font-size: 10.5px; letter-spacing: -0.2px;
  border: 1px solid transparent;
}
.sp-chip.zx { background: #ecf5ff; color: #409eff; border-color: #d9ecff; }
.sp-chip.tc { background: #f0f9eb; color: #67c23a; border-color: #e1f3d8; }
.sp-chip.tk { background: #fdf6ec; color: #e6a23c; border-color: #faecd8; }
.sp-chip.ts { background: #fef0f0; color: #f56c6c; border-color: #fde2e2; }
</style>
