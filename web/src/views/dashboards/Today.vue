<template>
  <div class="page-container today-dashboard">
    <!-- 日期+看板名水印：绝对定位铺满 指针事件穿透不阻塞tooltip；局部截图任何区域都能看到日期 -->
    <div class="today-watermark" :style="watermarkBg" aria-hidden="true"></div>
    <!-- 顶部标题 + 时间粒度切换 + 时间筛选 -->
    <div class="flex-between mb-16 td-header" style="position: relative; z-index: 2">
      <div class="page-title" style="margin: 0; white-space: nowrap">
        运营看板
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag size="small" :type="grainTagType" effect="plain" style="margin-left: 8px">{{ grainLabel }}</el-tag>
        <el-tag v-if="kpiData.merchant_tip" size="small" type="warning" effect="plain" style="margin-left: 6px">
          {{ kpiData.merchant_tip }}
        </el-tag>
      </div>
      <FilterBar
        v-model:modelValue="quickGrainOpt"
        :quick-options="grainQuickOptions"
        :show-custom="false"
        show-grain="false"
        :loading="loading"
        @refresh="loadAll"
      >
        <template #left>
          <!-- 1. 粒度单选: 日 / 周 / 月 / 季度 -->
          <el-radio-group v-model="grain" size="small" style="margin-right: 12px">
            <el-radio-button value="day">日</el-radio-button>
            <el-radio-button value="week">周</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
            <el-radio-button value="quarter">季度</el-radio-button>
          </el-radio-group>
          <!-- 2. 根据粒度显示对应快捷范围 -->
          <el-radio-group v-if="grain === 'day'" v-model="quickDate" size="small" style="margin-right: 8px">
            <el-radio-button value="today">今日</el-radio-button>
            <el-radio-button value="yesterday">昨日</el-radio-button>
            <el-radio-button value="custom">自选日</el-radio-button>
          </el-radio-group>
          <el-radio-group v-else-if="grain === 'week'" v-model="quickWeek" size="small" style="margin-right: 8px">
            <el-radio-button value="this_week">本周</el-radio-button>
            <el-radio-button value="last_week">上周</el-radio-button>
          </el-radio-group>
          <el-radio-group v-else-if="grain === 'month'" v-model="quickMonth" size="small" style="margin-right: 8px">
            <el-radio-button value="this_month">本月</el-radio-button>
            <el-radio-button value="last_month">上月</el-radio-button>
          </el-radio-group>
          <el-radio-group v-else-if="grain === 'quarter'" v-model="quickQuarter" size="small" style="margin-right: 8px">
            <el-radio-button value="this_quarter">本季度</el-radio-button>
            <el-radio-button value="last_quarter">上季度</el-radio-button>
          </el-radio-group>
          <!-- 3. 自定义日期(日粒度: 单日; 其他粒度: 日期范围) -->
          <el-date-picker
            v-if="grain === 'day' && quickDate === 'custom'"
            v-model="customDate"
            type="date"
            size="small"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 160px; margin-right: 8px"
          />
        </template>
      </FilterBar>
    </div>
    <!-- 值班组长(今日有接待的组长)：只在日粒度展示（值班是日度概念），放在页头最显眼的位置 -->
    <div v-if="dutyLeaders.length && grain === 'day'" class="duty-leader-bar mb-16">
      <div class="duty-label">
        <el-icon><Timer /></el-icon>
        <span>值班组长</span>
        <span class="duty-count">{{ dutyLeaders.length }}人</span>
      </div>
      <el-space wrap size="small" class="duty-chips">
        <el-tag
          v-for="(l,i) in dutyLeaders"
          :key="i"
          type="warning"
          effect="dark"
          size="small"
          class="duty-chip"
        >
          <span class="chip-name">{{ l.name }}</span>
          <span class="chip-sep">·</span>
          <span class="chip-group">{{ l.group || l.department || '部门统管' }}</span>
          <span class="chip-sep">·</span>
          <span class="chip-stat">会话 {{ formatNum(l.reception_count) }} / 接待 {{ formatNum(l.reception_device_count ?? 0) }}</span>
        </el-tag>
      </el-space>
    </div>

    <!-- 模块1: KPI卡 -->
    <section class="section-card mb-24">
      <SectionHeader :title="'1. 核心指标'" :sub="'对比' + compareName + '同时段 · 悬停标签查看口径'" />
      <el-row :gutter="16">
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
          />
        </el-col>
      </el-row>

      <!-- 现金流出拆分：3合计卡 + 四路堆叠比例条（复用 RouteSplit compact 模式） -->
      <div class="mt-12" v-if="kpiData.cash_routes_today">
        <RouteSplit :routes="kpiData.cash_routes_today" compact />
      </div>
    </section>

    <!-- 模块2: 实时节奏 -->
    <section class="section-card mb-24">
      <SectionHeader title="2. 实时节奏" :sub="rhythmSub" />
      <ChartCard body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="320px" />
      </ChartCard>
    </section>

    <!-- 模块3: 接待分项 (独立一行, 为后续扩充预留空间) -->
    <section class="section-card mb-24">
      <SectionHeader title="3. 接待分项" />
      <el-row :gutter="12">
        <el-col :xs="24" :md="12">
          <ChartCard title="接待分类分布">
            <BaseChart :option="receptionPieOption" height="280px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="12">
          <ChartCard title="接待量 Top 10 客服 （会话数(次) + 接待数(个)）">
            <BaseChart :option="receptionStaffOption" height="280px" />
          </ChartCard>
        </el-col>
      </el-row>
    </section>

    <!-- 模块4: 退款分项 (独立一行, 为后续扩充预留空间) -->
    <section class="section-card mb-24">
      <SectionHeader title="4. 退款分项" />
      <el-row :gutter="12">
        <el-col :xs="24" :md="12">
          <ChartCard title="退款细分原因 Top 10（订单数 + 金额 双柱）">
            <template #extra>
              <span style="font-weight:400;color:#909399;font-size:11px">按工单分类 + 商品名兜底（替换原 refund_reason='用户退款'无效字段）</span>
            </template>
            <BaseChart :option="refundReasonOption" height="280px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="12">
          <ChartCard title="退款接待人 Top 10（退款笔数 + 金额 双柱）">
            <template #extra>
              <span style="font-weight:400;color:#909399;font-size:11px">按 PersonAlias → Person.display_name 姓名归一；[未分配]=未匹配；用户自助=wechat；系统自动=sys/system</span>
            </template>
            <BaseChart :option="refundStaffOption" height="280px" />
          </ChartCard>
        </el-col>
      </el-row>
    </section>

    <!-- 模块5: 投诉分项 -->
    <section class="section-card mb-24">
      <SectionHeader title="5. 投诉分项" :sub="complaintSub" />
      <!-- 内/外部投诉概览条 -->
      <div v-if="complaintData.complaint_by_source && complaintData.complaint_by_source.length" class="complaint-source-bar mb-12">
        <div class="csb-label">
          <el-icon><WarningFilled /></el-icon>
          <span>{{ periodName }}共 <b>{{ complaintData.complaint_total || 0 }}</b> 条投诉</span>
        </div>
        <el-space wrap size="default" class="csb-cards">
          <div v-for="(src, i) in complaintData.complaint_by_source" :key="i" class="csb-card" :class="src.name === '外部投诉' ? 'is-external' : 'is-internal'">
            <div class="csb-card-name">{{ src.name }}</div>
            <div class="csb-card-value">{{ formatNum(src.value) }}<span class="csb-unit">条</span></div>
            <div class="csb-card-ratio">{{ complaintData.complaint_total ? ((src.value / complaintData.complaint_total) * 100).toFixed(1) : '0.0' }}%</div>
          </div>
        </el-space>
      </div>
      <el-row :gutter="12">
        <el-col :xs="24" :md="12">
          <ChartCard title="投诉子类分布（商品/服务/微信/12315/工信部/公共服务平台）">
            <template #extra>
              <span style="font-weight:400;color:#909399;font-size:11px">外部投诉(工信部/12315/消协/12345等) 与内部投诉 已分别标识着色</span>
            </template>
            <BaseChart :option="complaintSubPieOption" height="300px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="12">
          <ChartCard title="投诉根因分布（关联网速/套餐/退款等接待记录）">
            <BaseChart :option="complaintRootBarOption" height="300px" />
          </ChartCard>
        </el-col>
      </el-row>
    </section>

    <!-- 模块6: 处理队列 三栏（直接复用 QueuePanel） -->
    <section class="section-card mb-24">
      <SectionHeader title="6. 处理队列" />
      <QueuePanel
        :todos="queueData.todos"
        :tasks="queueData.tasks"
        :incidents="queueData.incidents"
      />
    </section>

    <!-- 模块7: 人均效率表 -->
    <section class="section-card mb-24">
      <SectionHeader title="7. 人均效率" sub="会话数(次)=cs_record+工单完工条数 · 接待数(个)=智能助手SN ∪ 工单SN 跨表去重 · 排除sys/system/系统自动记录">
        <template #extra v-if="dutyLeaders.length">
          <el-tag type="warning" effect="plain" size="small" class="mr-4">值班组长</el-tag>
          <el-space wrap size="small">
            <el-tag
              v-for="(l,i) in dutyLeaders"
              :key="i"
              type="warning"
              effect="dark"
              size="small"
            >
              {{ l.name }} · {{ l.group || l.department || '部门统管' }}
            </el-tag>
          </el-space>
        </template>
      </SectionHeader>
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
          <el-table-column label="人员" width="170">
            <template #default="{ row }">
              <div style="display:flex; flex-direction:column; gap:3px">
                <div style="display:flex; align-items:center; gap:4px; flex-wrap:wrap">
                  <span style="font-weight:600">{{ row.name }}</span>
                  <el-tag
                    v-if="isDutyLeader(row)"
                    type="warning"
                    effect="dark"
                    size="small"
                    style="padding: 0 4px; font-size: 10px; height: 18px; line-height: 16px; border-radius: 3px"
                  >值班组长</el-tag>
                </div>
              </div>
            </template>
          </el-table-column>
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
          <!-- 四专项独立列: 每列一行 n次(m个)，简洁不啰嗦 -->
          <el-table-column label="咨询" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt zx">{{ formatNum(row.breakdown?.['咨询接待']?.sessions ?? row.sp_zixun ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['咨询接待']?.people ?? row.pp_zixun ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="套餐" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt tc">{{ formatNum(row.breakdown?.['套餐专项']?.sessions ?? row.sp_taocan ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['套餐专项']?.people ?? row.pp_taocan ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="退款" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt tk">{{ formatNum(row.breakdown?.['退款挽回']?.sessions ?? row.sp_tuikuan ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['退款挽回']?.people ?? row.pp_tuikuan ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="投诉" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt ts">{{ formatNum(row.breakdown?.['投诉专员']?.sessions ?? row.sp_tousu ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['投诉专员']?.people ?? row.pp_tousu ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="工作量" width="104" align="right" sortable :sort-method="(a,b)=>Number(a.work_score||0)-Number(b.work_score||0)">
            <template #default="{ row }">
              <div style="position:relative">
                <el-tooltip effect="light" placement="top" :show-arrow="false" popper-class="score-pop" teleported>
                  <template #content>
                    <div class="score-tooltip" v-html="scoreTooltipHtml(row,'work')" />
                  </template>
                  <el-tag size="small" effect="plain" :type="scoreTagType(row.work_score,500,300,100)">
                    <span style="font-weight:600">{{ formatNum(row.work_score) }}</span>
                  </el-tag>
                </el-tooltip>
                <el-tooltip
                  v-if="row.rank === null || row.is_virtual === true"
                  effect="dark" placement="top"
                  content="系统/用户自助/第三方设备账号等 不计入真人员工排名"
                >
                  <el-tag size="small" effect="dark" type="info" style="margin-left:4px;margin-top:2px">非真人</el-tag>
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="效率" width="104" align="right" sortable :sort-method="(a,b)=>Number(a.eff_score||0)-Number(b.eff_score||0)">
            <template #default="{ row }">
              <el-tooltip effect="light" placement="top" :show-arrow="false" popper-class="score-pop" teleported>
                <template #content>
                  <div class="score-tooltip" v-html="scoreTooltipHtml(row,'eff')" />
                </template>
                <el-tag size="small" effect="plain" :type="scoreTagType(row.eff_score,350,200,80)">
                  <span style="font-weight:600;color:#2563eb">{{ formatNum(row.eff_score) }}</span>
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!staffRows.length" :description="periodName + '暂无人员工作数据'" />
      </el-card>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { Timer } from '@element-plus/icons-vue'
import { today } from '@/api/analytics'
import BaseChart from '@/components/BaseChart.vue'
import { formatMoney } from '@/utils/format'
import dayjs from 'dayjs'
// Today.vue 图标通过字符串名传给 KpiCard (KpiCard 内部 component :is 动态解析)
// 本地脚本不直接使用图标组件

const loading = ref(false)

// ========== 时间粒度: day / week / month / quarter ==========
const grain = ref('day')
const quickDate = ref('today')  // day: today / yesterday / custom
const quickWeek = ref('this_week')   // week: this_week / last_week
const quickMonth = ref('this_month') // month: this_month / last_month
const quickQuarter = ref('this_quarter') // quarter: this_quarter / last_quarter
const customDate = ref('')      // YYYY-MM-DD (日粒度 自定义)
const quickGrainOpt = ref('all') // 供 FilterBar 占位 (无实际意义, 保持组件兼容)
const grainQuickOptions = [{ label: '全部', value: 'all' }] // 占位
const grainLabel = computed(() => ({ day: '日粒度', week: '周粒度', month: '月粒度', quarter: '季度粒度' }[grain.value] || '日粒度'))
const grainTagType = computed(() => ({ day: 'success', week: 'primary', month: 'warning', quarter: 'danger' }[grain.value] || 'info'))
// 本期名 & 对比期名 (用于: KpiCard 环比、核心指标副标题、投诉概览条等)
const periodName = computed(() => {
  switch (grain.value) {
    case 'week':    return quickWeek.value  === 'last_week'  ? '上周' : '本周'
    case 'month':   return quickMonth.value === 'last_month' ? '上月' : '本月'
    case 'quarter': return quickQuarter.value === 'last_quarter' ? '上季度' : '本季度'
    case 'day':
    default:
      if (quickDate.value === 'yesterday') return '昨日'
      if (quickDate.value === 'custom' && customDate.value) return '该日'
      return '今日'
  }
})
const compareName = computed(() => {
  switch (grain.value) {
    case 'week':    return quickWeek.value  === 'last_week'  ? '前周' : '上周'
    case 'month':   return quickMonth.value === 'last_month' ? '前月' : '上月'
    case 'quarter': return quickQuarter.value === 'last_quarter' ? '前季' : '上季度'
    case 'day':
    default:
      if (quickDate.value === 'yesterday') return '前日'
      if (quickDate.value === 'custom' && customDate.value) return '前一日'
      return '昨日'
  }
})
// 节奏粒度说明: 日粒度=小时级, 其他粒度=日历日级
const rhythmSub = computed(() => grain.value === 'day'
  ? '会话/接待/投诉独立柱 · 退款笔数双柱堆叠 · 退款金额堆叠双面积+合计虚线 · 按北京时间小时级'
  : '会话/接待/投诉独立柱 · 退款笔数双柱堆叠 · 退款金额堆叠双面积+合计虚线 · 按北京时间日历日'
)

// ---- 计算当前实际时间范围 [start, end] YYYY-MM-DD (start=end=单日 时 end 可为空传) ----
function _startOfWeek(d) { return d.day(1) } // 周一为起始 (国内习惯)
function _endOfWeek(d) { return d.day(7) }   // 周日为结束
function _startOfQuarter(d) { return d.startOf('quarter') }
function _endOfQuarter(d) { return d.endOf('quarter') }

const timeRange = computed(() => {
  const now = dayjs()
  switch (grain.value) {
    case 'week': {
      const base = quickWeek.value === 'last_week' ? now.subtract(1, 'week') : now
      const s = _startOfWeek(base.startOf('day'))
      const e = (quickWeek.value === 'this_week') ? now : _endOfWeek(base.startOf('day'))
      return [s.format('YYYY-MM-DD'), e.format('YYYY-MM-DD')]
    }
    case 'month': {
      const base = quickMonth.value === 'last_month' ? now.subtract(1, 'month') : now
      const s = base.startOf('month')
      const e = (quickMonth.value === 'this_month') ? now : base.endOf('month')
      return [s.format('YYYY-MM-DD'), e.format('YYYY-MM-DD')]
    }
    case 'quarter': {
      const base = quickQuarter.value === 'last_quarter' ? now.subtract(1, 'quarter') : now
      const s = _startOfQuarter(base)
      const e = (quickQuarter.value === 'this_quarter') ? now : _endOfQuarter(base)
      return [s.format('YYYY-MM-DD'), e.format('YYYY-MM-DD')]
    }
    case 'day':
    default: {
      let d
      if (customDate.value) d = dayjs(customDate.value)
      else if (quickDate.value === 'yesterday') d = now.subtract(1, 'day')
      else d = now
      const iso = d.format('YYYY-MM-DD')
      // 日实时(今日)/昨日整天 → 后端用 date 即可, 传 start=end 也等价
      return [iso, iso]
    }
  }
})
// 展示给大标题的日期(含范围)
const displayDate = computed(() => {
  const [s, e] = timeRange.value
  if (!e || s === e) return s
  return `${s} ~ ${e}`
})
// 当前请求参数: 单日 传 date / 范围 传 start + end
const currentParams = computed(() => {
  const [s, e] = timeRange.value
  const rangeDays = dayjs(e).diff(dayjs(s), 'day') + 1
  if (rangeDays <= 1) {
    // 单日: 判断是不是今天(实时) 还是昨天(整天)/历史(整天)
    const todayIso = dayjs().format('YYYY-MM-DD')
    if (s === todayIso) return {} // 实时今天: 不传任何 (向后兼容最稳)
    return { date: s }
  }
  return { start: s, end: e }
})

// ---------- 日期水印 (SVG旋转平铺, 绝对定位穿透 pointer-events, 卡片内容区/卡片间都能看到; 局部截图也清晰) ----------
const WATERMARK_BOARD_NAME = '运营看板'
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
  const pn = periodName.value
  const def = [
    { key: 'reception',      label: '会话数(次)',   icon: 'ChatDotRound', bg: '#ecf5ff', color: '#409eff', unit: '次',
      money: false,
      tip: `${pn}会话数 = 客服助手 沟通事项次数 (1条接待记录=1次会话, 只记本期已发生)
${pn}接待数 = 去重 device_sn (按活跃设备近似为独立客户人数, 单位:个)
—— 会话数(次) > 接待数(个) 说明老客户多次进线沟通; ≈相等说明本期基本都是新客户`,
      ...(k.reception || { value: 0, yoy_value: 0, yoy_pct: null, device_count: 0, device_count_yoy: 0 }) },
    { key: 'refund_count',   label: '退款笔数', icon: 'Document',    bg: '#fdf6ec', color: '#e6a23c', unit: '笔',
      money: false,
      tip: '路线A: 退费单管理中 refund_method=\'线上\' 的记录 (统一全域口径: 只退款方式为线上才算一条退款路线)',
      ...(k.refund_count || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'refund_amount',  label: '退款金额', icon: 'Money',       bg: '#fef0f0', color: '#f56c6c', unit: '',
      money: true,
      tip: '路线A: 退费单 refund_method=\'线上\' 的金额合计 (不包含退回钱包/线下退款等方式)',
      ...(k.refund_amount || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'cash_out',       label: '真实现金流出', icon: 'Wallet',      bg: '#fdf6ec', color: '#e6a23c', unit: '',
      money: true,
      tip: '经营侧登记估算 = A(退费单线上直退) + B(钱包-用户取现) 两路合计',
      ...(k.cash_out || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'complaint',      label: '投诉量',   icon: 'WarningFilled', bg: '#fef0f0', color: '#f56c6c', unit: '单',
      money: false, tip: null, ...(k.complaint || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'staff_on_duty',  label: '在岗人数', icon: 'UserFilled',  bg: '#f4f4f5', color: '#909399', unit: '人',
      money: false, tip: null, ...(k.staff_on_duty || { value: 0, yoy_value: 0, yoy_pct: null }) },
  ]
  return def
})

// KPI 环比 tag 颜色逻辑已在 KpiCard 组件内(BAD_UP_KEYS),此处不再重复定义

// ---------- 模块2 小时节奏 ----------
const hourlyData = ref({
  hour_labels: [],
  reception: [],
  reception_devices: [],
  complaint: [],
  // 退款笔数: 拆分 + 合计(兼容旧字段 = 合计)
  refund_count: [],            // 兼容旧 = A+B
  refund_count_direct: [],     // 路线A 直接退款
  refund_count_withdraw: [],   // 路线B 钱包提现
  refund_count_total: [],      // 明确合计
  // 退款金额: 拆分 + 合计(兼容旧字段 = 合计)
  refund_amount: [],           // 兼容旧 = A+B
  refund_amount_direct: [],    // 路线A 直接退款金额
  refund_amount_withdraw: [],  // 路线B 钱包提现金额
  refund_amount_total: [],     // 明确合计
})
const hourlyOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    axisPointer: { type: 'shadow' },
    formatter: (params) => {
      if (!params || !params.length) return ''
      const p0 = params[0]
      const h = hourlyData.value.hour_labels?.[p0.dataIndex] ?? ''
      const val = (name, opts = {}) => {
        const m = params.find(x => x.seriesName === name)
        if (m == null || m.value == null) return '-'
        const v = Number(m.value)
        if (opts.currency) return '¥' + v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        return v.toLocaleString('zh-CN')
      }
      const cntA = val('退款-直接退款')
      const cntB = val('退款-钱包提现')
      const amtA = val('退金-直接', { currency: true })
      const amtB = val('退金-提现', { currency: true })
      const cntTotal = val('退金-合计') != null ? (Number(hourlyData.value.refund_count_total?.[p0.dataIndex] ?? 0) || 0) : 0
      const amtTotal = val('退金-合计', { currency: true })
      return `<b>${h}</b><br/>` +
        `会话数(次): ${val('会话数(次)')}<br/>` +
        `接待数(个): ${val('接待数(个)')}<br/>` +
        `退款笔数: <b>${cntTotal.toLocaleString('zh-CN')}</b> （直退 ${cntA} + 提现 ${cntB}）<br/>` +
        `投诉量: ${val('投诉量')}<br/>` +
        `退款金额: <b>${amtTotal}</b> （直退 ${amtA} + 提现 ${amtB}）`
    },
  },
  legend: {
    data: ['会话数(次)', '接待数(个)', '退款-直接退款', '退款-钱包提现', '投诉量', '退金-直接', '退金-提现', '退金-合计'],
    top: 0, type: 'scroll',
  },
  grid: { left: 60, right: 80, bottom: 40, top: 52 },
  xAxis: { type: 'category', data: hourlyData.value.hour_labels || [], axisLabel: { fontSize: 10, interval: 1 } },
  yAxis: [
    { type: 'value', name: '数量(次/个/笔)', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: 'dashed' } } },
    { type: 'value', name: '退款金额(元)', axisLabel: { formatter: (v) => (v >= 10000 ? (v/10000).toFixed(0)+'万' : v), fontSize: 10 } },
  ],
  series: [
    // 左Y：独立柱
    {
      name: '会话数(次)', type: 'bar', data: hourlyData.value.reception || [],
      itemStyle: { color: '#409eff', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 12,
    },
    {
      name: '接待数(个)', type: 'bar', data: hourlyData.value.reception_devices || [],
      itemStyle: { color: '#91cc75', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 12,
    },
    // 左Y：退款双柱堆叠（stack=refund）
    {
      name: '退款-直接退款', type: 'bar', stack: 'refund', data: hourlyData.value.refund_count_direct || [],
      itemStyle: { color: '#e6a23c' },
      barMaxWidth: 12,
    },
    {
      name: '退款-钱包提现', type: 'bar', stack: 'refund', data: hourlyData.value.refund_count_withdraw || [],
      itemStyle: { color: '#f0948e' },
      barMaxWidth: 12,
    },
    // 左Y：独立柱-投诉
    {
      name: '投诉量', type: 'bar', data: hourlyData.value.complaint || [],
      itemStyle: { color: '#f56c6c', borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 12,
    },
    // 右Y：退款金额 - 堆叠双面积（stack=amt）
    {
      name: '退金-直接', type: 'line', yAxisIndex: 1, stack: 'amt', smooth: true,
      data: hourlyData.value.refund_amount_direct || [],
      itemStyle: { color: '#9b59b6' },
      areaStyle: { color: 'rgba(155,89,182,0.14)' },
      showSymbol: false, lineStyle: { width: 1 },
    },
    {
      name: '退金-提现', type: 'line', yAxisIndex: 1, stack: 'amt', smooth: true,
      data: hourlyData.value.refund_amount_withdraw || [],
      itemStyle: { color: '#c0392b' },
      areaStyle: { color: 'rgba(192,57,43,0.14)' },
      showSymbol: false, lineStyle: { width: 1 },
    },
    // 右Y：退款金额 - 合计虚线（不堆叠，指示堆叠总高度）
    {
      name: '退金-合计', type: 'line', yAxisIndex: 1, smooth: true,
      data: hourlyData.value.refund_amount_total || [],
      itemStyle: { color: '#7d3c98' },
      symbol: 'circle', symbolSize: 5,
      lineStyle: { width: 2, type: 'dashed' },
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
// complaintData 字段来自 reception 接口响应 (complaint_total/complaint_subs/complaint_roots/complaint_unmatched/complaint_by_source)
const complaintData = ref({ complaint_total: 0, complaint_subs: [], complaint_roots: [], complaint_unmatched: 0, complaint_by_source: [] })
// 模块5 SectionHeader 副标题: 展示今日投诉总数 & 外部/内部 两个桶
const complaintSub = computed(() => {
  const src = complaintData.value.complaint_by_source || []
  const ext = (src.find(x => x.name === '外部投诉') || {}).value || 0
  const int = (src.find(x => x.name === '内部投诉') || {}).value || 0
  const total = complaintData.value.complaint_total || 0
  if (!total) return '暂无投诉数据'
  return `${periodName}共 ${total} 条投诉 · 外部 ${ext} 条 · 内部 ${int} 条（按来源类型独立核算分析）`
})
// 饼图: 投诉子类分布 (商品投诉/服务投诉/微信投诉/12315/工信部/公共服务平台)
// 按 source_type(内部/外部) 区分着色: 外部投诉用红/橙暖色系, 内部投诉用蓝/绿冷色系
const complaintSubPieOption = computed(() => {
  const rows = (complaintData.value.complaint_subs || []).filter(d => d.value > 0)
  const EXTERNAL_WARM = ['#f56c6c', '#e74c3c', '#e6a23c', '#d35400', '#c0392b', '#e67e22']
  const INTERNAL_COOL = ['#409eff', '#3498db', '#67c23a', '#2ecc71', '#9b59b6', '#1abc9c', '#909399', '#34495e']
  let extIdx = 0, intIdx = 0
  const data = rows.map(r => {
    const isExt = r.source_type === '外部投诉'
    let color
    if (isExt) { color = EXTERNAL_WARM[extIdx % EXTERNAL_WARM.length]; extIdx++ }
    else { color = INTERNAL_COOL[intIdx % INTERNAL_COOL.length]; intIdx++ }
    return {
      name: r.name + (isExt ? ' · 外' : ''),   // 图例里加"·外"/"·内"后缀 一眼区分
      value: r.value,
      source_type: r.source_type,
      itemStyle: { color },
    }
  })
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        const d = p.data || {}
        const tag = d.source_type === '外部投诉' ? '<span style="color:#e74c3c;font-weight:600">[外部]</span>' : '<span style="color:#3498db;font-weight:600">[内部]</span>'
        return `${tag} ${p.name.replace(/ · [内外]$/, '')}<br/>数量: ${p.value} 条<br/>占比: ${p.percent}%`
      },
    },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 10 } },
    series: [{
      name: '投诉子类', type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: (p) => {
        const nm = p.name.replace(/ · [内外]$/, '')
        const tag = (p.data && p.data.source_type === '外部投诉') ? '★' : '·'
        return `${tag}${nm}\n${p.percent}%`
      }, fontSize: 10 },
      data: data.length ? data : [{ name: '暂无投诉', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '暂无投诉 ✅' } }],
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
// 处理队列三栏的优先级/状态/格式化已迁移到 QueuePanel 组件内部,此处无需重复定义

// ---------- 模块6~7 人员效率表: 工作分 / 效率分 双列 + 本职/转接 breakdown ----------
const staffRows = ref([])

// ---------- 值班组长判定:
//   1. 组长身份满足任一: position含"组长/主管" OR current_specialty==="组长/主管" OR 组名包含本人名(如"叶珍凤组"∋"叶珍凤")
//   2. 出现在今日 staffRows 即视为今天已上岗(有接待/退款/投诉任一动作) → 值班组长
//      (不再强制 reception_count>0: 组长当天可能只做管理/值班监督, 不亲自接客, 但仍算值班)
//   3. 排除虚拟账号
function isDutyLeader(r) {
  if (!r || r.is_virtual) return false
  const name = String(r.name || '').trim()
  const pos = String(r.position || '')
  const sp = String(r.current_specialty || '')
  const grp = String(r.group || '')
  const byPos = pos.includes('组长') || pos.includes('主管')
  const bySp = sp === '组长' || sp === '主管'
  const byGroup = !!(name && grp && (
    // 精确匹配: 组名 = 姓名+"组"(最常见)
    grp === `${name}组` ||
    // 兼容: 组名中包含该姓名(如"林栩烽投诉组" "叶珍凤专项组")
    grp.includes(name)
  ))
  return byPos || bySp || byGroup
}
const dutyLeaders = computed(() => {
  const list = (staffRows.value || []).filter(isDutyLeader)
  // 接待总量多的排前 → 一眼看到主力值班组长
  list.sort((a, b) => {
    const sa = (Number(a.reception_count) || 0) + (Number(a.reception_device_count) || 0)
    const sb = (Number(b.reception_count) || 0) + (Number(b.reception_device_count) || 0)
    // 同量按组名排 → 吴晓龙组/林栩烽组顺序稳定
    if (sb !== sa) return sb - sa
    return (a.group || '').localeCompare(b.group || '')
  })
  return list
})
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
// 工作量 / 效率 通用 tooltip (纯文本, 兼容旧逻辑; 新渲染统一走 scoreTooltipHtml 返回结构化HTML)
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
    `模式: ${isWork ? '工作量 (会话次数维度, 反映忙闲强度)' : '效率 (接待个数维度, SN去重, 反映覆盖面)'}`,
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
  lines.push(`合成值 = ${total.toFixed(2)}   (接口返回: ${reported})`)
  return head.concat(lines).join('\n')
}

// 工作量 / 效率 通用 tooltip (返回结构化HTML, 分段显示 不挤成横条)
function scoreTooltipHtml(r, mode) {
  const fmt = (v) => formatNum(v)
  const w = r.weights || { '咨询接待': 1.0, '套餐专项': 1.8, '退款挽回': 2.2, '投诉专员': 3.0 }
  const xfer = Number(r.transfer_penalty || 0.2)
  const bd = r.breakdown || {}
  const useBreakdown = bd && bd['咨询接待'] && (typeof bd['咨询接待'].coef === 'number')
  const isWork = mode === 'work'
  const unitChar = isWork ? '次' : '个'
  const modeColor = isWork ? '#d97706' : '#2563eb'  // 工作量橙 / 效率蓝
  const modeName = isWork ? '工作量' : '效率'
  const modeDesc = isWork ? '会话次数维度 · 反映忙闲强度' : '接待个数维度 · SN去重 · 反映覆盖面'
  const contribKey = isWork ? 'work_contrib' : 'eff_contrib'
  const countKey = isWork ? 'sessions' : 'people'
  const sp = (r.current_specialty && r.current_specialty !== '组长') ? r.current_specialty : inferSpecialty(r)
  const reported = isWork ? (Number(r.work_score) || 0) : (Number(r.eff_score) || 0)

  const CATS = [
    { k: '咨询接待', short: '咨询', color: '#409eff', fallback_s: r.sp_zixun, fallback_p: r.pp_zixun ?? 0 },
    { k: '套餐专项', short: '套餐', color: '#67c23a', fallback_s: r.sp_taocan, fallback_p: r.pp_taocan ?? 0 },
    { k: '退款挽回', short: '退款', color: '#e6a23c', fallback_s: r.sp_tuikuan, fallback_p: r.pp_tuikuan ?? 0 },
    { k: '投诉专员', short: '投诉', color: '#f56c6c', fallback_s: r.sp_tousu, fallback_p: r.pp_tousu ?? 0 },
  ]
  // 头部: 人员+岗位+分组+专项
  const headerHtml = `
    <div class="stp-head">
      <div class="stp-name">
        <span class="stp-avatar">${(r.name || '?').slice(0,1)}</span>
        <div>
          <div class="stp-nameline"><b>${r.name || ''}</b> <span class="stp-meta">${r.position || '—'}</span></div>
          <div class="stp-subline">${r.group || '部门统管'} · 专项 <b>${sp}</b>${r.is_management ? ' · <span class=\"stp-tag mgmt\">管理层·全系数</span>' : ''}${r.hr_category ? ` · <span class=\"stp-tag hr\">HR=${r.hr_category}</span>` : ''}</div>
        </div>
      </div>
      <div class="stp-mode" style="border-color:${modeColor}33;background:${modeColor}0d;color:${modeColor}">
        <div class="stp-mode-name">${modeName}</div>
        <div class="stp-mode-desc">${modeDesc}</div>
      </div>
    </div>`
  // 系数规则小提示
  const coefHtml = `<div class="stp-coef">难度系数: 咨询×1.0 · 套餐×1.8 · 退款×2.2 · 投诉×3.0 · 转接=系数×${xfer}</div>`
  // 四专项明细行
  let total = 0
  const rowsHtml = CATS.map(c => {
    let n, coef, contrib, isOwn = true
    if (useBreakdown) {
      const b = bd[c.k] || { weight: w[c.k], coef: w[c.k], [countKey]: 0, [contribKey]: 0, is_own: true }
      n = Number(b[countKey]) || 0
      coef = Number(b.coef) || 0
      contrib = Number(b[contribKey]) || 0
      isOwn = !!b.is_own
    } else {
      n = isWork ? (Number(c.fallback_s) || 0) : (Number(c.fallback_p) || 0)
      coef = w[c.k]
      contrib = n * coef
    }
    total += contrib
    const ownCls = isOwn ? 'own' : 'xfer'
    const ownLabel = isOwn ? '本职' : `转接`
    const isZero = n === 0
    return `
      <div class="stp-row ${isZero ? 'is-zero' : ''}" style="--cat-color:${c.color}">
        <div class="stp-cat"><span class="stp-dot" style="background:${c.color}"></span>${c.short}</div>
        <div class="stp-formula">
          <span class="stp-num">${fmt(n)}</span><span class="stp-unit">${unitChar}</span>
          <span class="stp-op">×</span><span class="stp-coef">${coef}</span>
          <span class="stp-op">=</span><span class="stp-contrib">${contrib.toFixed(1)}</span>
        </div>
        <div class="stp-flag ${ownCls}">${ownLabel}</div>
      </div>`
  }).join('')
  // 合计行
  const totalHtml = `
    <div class="stp-total">
      <div class="stp-total-lbl">合计 ${modeName}</div>
      <div class="stp-total-val" style="color:${modeColor}">
        ${total.toFixed(2)}
        <span class="stp-total-api">· 接口返回 ${reported}</span>
      </div>
    </div>`

  return `
    <div class="score-tooltip-inner">
      ${headerHtml}
      ${coefHtml}
      <div class="stp-divider"></div>
      <div class="stp-table">${rowsHtml}</div>
      <div class="stp-divider"></div>
      ${totalHtml}
    </div>`
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
  const params = currentParams.value
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
    // 向后兼容：后端新接口返回 A/B/TOTAL 全部字段；旧接口只返回 refund_count/amount(=合计)
    const hd = h.data || {}
    hourlyData.value = {
      hour_labels: hd.hour_labels || hd.slot_labels || [],
      reception: hd.reception || [],
      reception_devices: hd.reception_devices || [],
      complaint: hd.complaint || [],
      refund_count: hd.refund_count ?? [],  // 旧接口 = 合计；新接口 = 合计
      refund_count_direct:  hd.refund_count_direct  ?? hd.refund_count ?? [],  // 旧接口缺失则退化为合计（堆叠时只有一段）
      refund_count_withdraw: hd.refund_count_withdraw ?? Array(hd.refund_count?.length || 0).fill(0),
      refund_count_total:   hd.refund_count_total   ?? hd.refund_count ?? [],
      refund_amount: hd.refund_amount ?? [],
      refund_amount_direct:  hd.refund_amount_direct  ?? hd.refund_amount ?? [],
      refund_amount_withdraw: hd.refund_amount_withdraw ?? Array(hd.refund_amount?.length || 0).fill(0),
      refund_amount_total:   hd.refund_amount_total   ?? hd.refund_amount ?? [],
    }
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
      complaint_by_source: rdata.complaint_by_source || [],
    }
    refundData.value = rf.data || { reasons: [], methods: [] }
    queueData.value = q.data || { todos: [], tasks: [], incidents: [] }
    // 人均效率: 后端 range 模式 → {date, date_end, data: [...]}; 单日旧模式 → [...]
    const sr = s.data
    if (sr && Array.isArray(sr)) {
      staffRows.value = sr
    } else if (sr && Array.isArray((sr).data)) {
      staffRows.value = (sr).data
    } else {
      staffRows.value = []
    }
  } catch (e) {
    console.error('[Today] load error', e)
  } finally {
    loading.value = false
  }
}

// 粒度/快捷范围变化时自动 reload
watch([grain, quickDate, quickWeek, quickMonth, quickQuarter, customDate], () => {
  loadAll()
})

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
.section-card { }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.mt-12 { margin-top: 12px; }
.h-full { height: 100%; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.text-muted { color: #909399; }
.mr-8 { margin-right: 8px; }

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

/* 模块7 四专项列 极简显示: n次(m个) */
.sp-txt { font-size: 12.5px; font-weight: 500; letter-spacing: -0.2px; line-height: 1.6; }
.sp-txt.zx { color: #409eff; }
.sp-txt.tc { color: #67c23a; }
.sp-txt.tk { color: #e6a23c; }
.sp-txt.ts { color: #f56c6c; }
.sp-txt-p { color: #2563eb; font-weight: 600; }   /* 括号内SN个数 蓝色略粗一点 视觉一眼分清 */
/* 0次(0个) 弱化: 灰字 看起来不占视线 */
.sp-txt.sp-zero { color: #c0c4cc; font-weight: 400; }
.sp-txt.sp-zero .sp-txt-p { color: #909399; font-weight: 500; }
/* .section-title/.section-sub 已迁移到 SectionHeader.vue
   .kpi-* 已迁移到 KpiCard.vue
   .queue-* 已迁移到 QueuePanel.vue
   .chart-title 已迁移到 ChartCard.vue
   .trv2-* / .today-routes-v2 已迁移到 RouteSplit.vue */

/* ---------- 值班组长醒目栏 ---------- */
.duty-leader-bar {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  background: linear-gradient(90deg, #fff7e6 0%, #fffbeb 55%, #ffffff 100%);
  border: 1px solid #ffd591;
  border-radius: 8px;
  box-shadow: 0 4px 12px -6px rgba(217, 119, 6, 0.25);
}
.duty-label {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  font-weight: 600;
  color: #ad6800;
  font-size: 13px;
}
.duty-label .el-icon { color: #d97706; }
.duty-count {
  margin-left: 4px;
  padding: 1px 6px;
  font-weight: 500;
  font-size: 11px;
  background: #fed7aa;
  color: #7c2d12;
  border-radius: 10px;
}
.duty-chips { flex: 1; min-width: 0; }
.duty-chip {
  padding: 3px 8px 3px 10px !important;
  border-radius: 5px !important;
  --el-tag-bg-color: #f59e0b !important;
  --el-tag-border-color: #f59e0b !important;
  background: linear-gradient(135deg, #f59e0b, #d97706) !important;
  border: none !important;
}
.chip-name   { font-weight: 700; letter-spacing: 0.2px; }
.chip-sep    { opacity: 0.75; margin: 0 3px; }
.chip-group  { font-weight: 500; }
.chip-stat   { opacity: 0.92; font-size: 11px; font-variant-numeric: tabular-nums; }

/* ---------- 投诉内/外部概览条 ---------- */
.complaint-source-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  background: linear-gradient(90deg, #fff1f0 0%, #fff7ed 50%, #ffffff 100%);
  border: 1px solid #fecaca;
  border-radius: 8px;
}
.csb-label {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  font-weight: 600;
  color: #991b1b;
  font-size: 13px;
}
.csb-label .el-icon { color: #dc2626; }
.csb-label b { color: #7f1d1d; font-size: 15px; }
.csb-cards { flex: 1; min-width: 0; }
.csb-card {
  min-width: 140px;
  padding: 8px 14px;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.csb-card.is-external {
  background: linear-gradient(135deg, #fff1f0, #fee2e2);
  border: 1px solid #fca5a5;
}
.csb-card.is-internal {
  background: linear-gradient(135deg, #eff6ff, #dbeafe);
  border: 1px solid #93c5fd;
}
.csb-card-name {
  font-size: 11.5px;
  font-weight: 500;
  opacity: 0.85;
}
.csb-card.is-external .csb-card-name { color: #991b1b; }
.csb-card.is-internal .csb-card-name { color: #1e40af; }
.csb-card-value {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.csb-card.is-external .csb-card-value { color: #dc2626; }
.csb-card.is-internal .csb-card-value { color: #2563eb; }
.csb-unit {
  font-size: 12px;
  font-weight: 400;
  margin-left: 2px;
  opacity: 0.75;
}
.csb-card-ratio {
  font-size: 10.5px;
  opacity: 0.7;
  font-variant-numeric: tabular-nums;
}
.csb-card.is-external .csb-card-ratio { color: #991b1b; }
.csb-card.is-internal .csb-card-ratio { color: #1e40af; }
</style>

<!-- tooltip 是 teleported 到 body 下的, 必须写在非 scoped 块里, 否则 scoped 哈希匹配不上 -->
<style>
.score-pop.el-popper {
  background: #ffffff !important;
  border: 1px solid #eef2f7;
  box-shadow: 0 10px 30px -12px rgba(15,23,42,0.18), 0 4px 10px -6px rgba(15,23,42,0.1);
  border-radius: 10px !important;
  padding: 0 !important;
  max-width: 420px !important;
}
.score-pop .el-popper__arrow::before { background: #ffffff !important; border-color: #eef2f7 !important; }
/* tooltip 内部结构 */
.score-tooltip-inner { width: 380px; padding: 12px 14px; font-size: 12.5px; color: #1f2937; }
.stp-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
.stp-name { display: flex; align-items: center; gap: 10px; min-width: 0; }
.stp-avatar {
  width: 34px; height: 34px; border-radius: 9px; flex-shrink: 0;
  background: linear-gradient(135deg,#60a5fa,#3b82f6);
  color: #fff; font-weight: 700; font-size: 15px;
  display: inline-flex; align-items: center; justify-content: center;
  box-shadow: 0 2px 6px -2px rgba(59,130,246,.4);
}
.stp-nameline { line-height: 1.25; }
.stp-nameline b { font-size: 14px; color: #111827; margin-right: 6px; }
.stp-meta { color: #6b7280; font-size: 11.5px; font-weight: 500; padding: 1px 6px; background: #f3f4f6; border-radius: 4px; }
.stp-subline { margin-top: 3px; color: #4b5563; font-size: 11.5px; line-height: 1.35; }
.stp-subline b { color: #1f2937; font-weight: 600; }
.stp-tag { display: inline-block; padding: 0 6px; border-radius: 4px; font-size: 10.5px; font-weight: 500; }
.stp-tag.mgmt { background: #fef3c7; color: #92400e; margin-left: 2px; }
.stp-tag.hr   { background: #dbeafe; color: #1e40af; margin-left: 2px; }
.stp-mode {
  border: 1px solid; border-radius: 8px; padding: 5px 9px; text-align: right; flex-shrink: 0;
}
.stp-mode-name { font-weight: 700; font-size: 13.5px; line-height: 1.2; }
.stp-mode-desc { font-size: 10px; margin-top: 2px; opacity: .85; line-height: 1.15; }
.stp-coef { font-size: 11px; color: #6b7280; background: #f9fafb; padding: 4px 8px; border-radius: 6px; margin-bottom: 8px; line-height: 1.5; }
.stp-divider { height: 1px; background: #f0f2f5; margin: 8px 0; }
.stp-table { display: flex; flex-direction: column; gap: 4px; }
.stp-row {
  display: grid; grid-template-columns: 52px 1fr 50px; align-items: center; gap: 6px;
  padding: 5px 8px; border-radius: 6px;
  background: color-mix(in srgb, var(--cat-color,#999) 6%, transparent);
  border-left: 3px solid var(--cat-color, #999);
  transition: opacity .15s ease;
}
.stp-row.is-zero { opacity: .38; filter: grayscale(.4); }
.stp-cat { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; font-weight: 600; color: #374151; }
.stp-dot { width: 7px; height: 7px; border-radius: 50%; }
.stp-formula { font-variant-numeric: tabular-nums; text-align: center; color: #374151; line-height: 1.45; letter-spacing: -0.2px; }
.stp-formula .stp-num { font-weight: 700; font-size: 13px; color: #111827; }
.stp-formula .stp-unit { font-size: 10.5px; color: #6b7280; margin: 0 2px 0 1px; }
.stp-formula .stp-op { color: #9ca3af; margin: 0 2px; font-weight: 500; }
.stp-formula .stp-coef { font-weight: 600; color: #4b5563; }
.stp-formula .stp-contrib { font-weight: 800; color: var(--cat-color, #111); font-size: 13.5px; margin-left: 2px; }
.stp-flag { justify-self: end; text-align: center; font-size: 10.5px; font-weight: 600; padding: 2px 6px; border-radius: 5px; }
.stp-flag.own { background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
.stp-flag.xfer { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; letter-spacing: -0.3px; }
.stp-total {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  margin-top: 4px; padding: 8px 10px; border-radius: 8px;
  background: linear-gradient(90deg, #f9fafb 0%, #f3f4f6 100%);
  border: 1px solid #e5e7eb;
}
.stp-total-lbl { font-weight: 600; color: #374151; font-size: 12.5px; }
.stp-total-val { font-weight: 800; font-size: 18px; letter-spacing: -0.5px; font-variant-numeric: tabular-nums; }
.stp-total-api { font-size: 10.5px; font-weight: 400; color: #9ca3af; margin-left: 4px; letter-spacing: 0; }
</style>
