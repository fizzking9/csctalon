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
      <FilterBar
        v-model:modelValue="quickDate"
        :quick-options="todayQuickOptions"
        :show-custom="false"
        show-grain="false"
        :loading="loading"
        @refresh="loadAll"
      >
        <template #left>
          <el-date-picker
            v-model="customDate"
            type="date"
            size="small"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 160px; margin-right: 8px"
            @change="onCustomDate"
          />
        </template>
      </FilterBar>
    </div>

    <!-- 模块1: KPI卡 -->
    <section class="section-card mb-24">
      <SectionHeader title="1. 核心指标" sub="对比昨日同时段 · 悬停标签查看口径" />
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
            :device-count="c.key === 'reception' ? c.device_count : null"
            :tip="c.tip"
          />
        </el-col>
      </el-row>

      <!-- 今日现金流出拆分：3合计卡 + 四路堆叠比例条（复用 RouteSplit compact 模式） -->
      <div class="mt-12" v-if="kpiData.cash_routes_today">
        <RouteSplit :routes="kpiData.cash_routes_today" compact />
      </div>
    </section>

    <!-- 模块2: 小时节奏 -->
    <section class="section-card mb-24">
      <SectionHeader title="2. 今日实时节奏" sub="小时级双柱：会话数(次) + 接待数(个) + 退款笔数 + 投诉量 + 退款金额线" />
      <ChartCard body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="320px" />
      </ChartCard>
    </section>

    <!-- 模块3: 接待分项 + 模块4: 退款分项 → 并排两栏 -->
    <el-row :gutter="16" class="mb-24">
      <el-col :xs="24" :lg="12">
        <section class="section-card h-full">
          <SectionHeader title="3. 今日接待分项" />
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
      </el-col>
      <el-col :xs="24" :lg="12">
        <section class="section-card h-full">
          <SectionHeader title="4. 今日退款分项" />
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
      </el-col>
    </el-row>

    <!-- 模块5: 今日投诉分项 -->
    <section class="section-card mb-24">
      <SectionHeader title="5. 今日投诉分项" :sub="complaintSub" />
      <el-row :gutter="12">
        <el-col :xs="24" :md="12">
          <ChartCard title="投诉子类分布（商品/服务/微信/12315/工信部/公共服务平台）">
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
      <SectionHeader title="6. 今日处理队列" />
      <QueuePanel
        :todos="queueData.todos"
        :tasks="queueData.tasks"
        :incidents="queueData.incidents"
      />
    </section>

    <!-- 模块7: 人均效率表 -->
    <section class="section-card mb-24">
      <SectionHeader title="7. 今日人均效率" />
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
// Today.vue 图标通过字符串名传给 KpiCard (KpiCard 内部 component :is 动态解析)
// 本地脚本不直接使用图标组件

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

// KPI 环比 tag 颜色逻辑已在 KpiCard 组件内(BAD_UP_KEYS),此处不再重复定义

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
// 处理队列三栏的优先级/状态/格式化已迁移到 QueuePanel 组件内部,此处无需重复定义

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
/* .section-title/.section-sub 已迁移到 SectionHeader.vue
   .kpi-* 已迁移到 KpiCard.vue
   .queue-* 已迁移到 QueuePanel.vue
   .chart-title 已迁移到 ChartCard.vue
   .trv2-* / .today-routes-v2 已迁移到 RouteSplit.vue */
</style>
