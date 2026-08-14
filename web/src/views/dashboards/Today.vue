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
          <!-- 1. 视图粒度单选: 日 / 周 / 月 / 季度 / 年 -->
          <el-radio-group v-model="grain" size="small" style="margin-right: 12px">
            <el-radio-button value="day">日</el-radio-button>
            <el-radio-button value="week">周</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
            <el-radio-button value="quarter">季度</el-radio-button>
            <el-radio-button value="year">年</el-radio-button>
          </el-radio-group>
          <!-- 2. 根据视图粒度显示对应快捷选择 -->
          <el-radio-group v-if="grain === 'day'" v-model="quickDate" size="small" style="margin-right: 8px">
            <el-radio-button value="today">今日</el-radio-button>
            <el-radio-button value="yesterday">昨日</el-radio-button>
            <el-radio-button value="custom">自选日</el-radio-button>
          </el-radio-group>
          <!-- 日粒度自定义单日选择 -->
          <el-date-picker
            v-if="grain === 'day' && quickDate === 'custom'"
            v-model="customDate"
            type="date"
            size="small"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            style="width: 160px; margin-right: 8px"
            @change="onCustomDate"
          />
          <el-select v-else-if="grain === 'week'" v-model="weekOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="w in weekOptions" :key="w.value" :label="w.label" :value="w.value" />
          </el-select>
          <el-select v-else-if="grain === 'month'" v-model="monthOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="m in monthOptions" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
          <el-select v-else-if="grain === 'quarter'" v-model="quarterOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="q in quarterOptions" :key="q.value" :label="q.label" :value="q.value" />
          </el-select>
          <el-select v-else-if="grain === 'year'" v-model="yearOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="y in yearOptions" :key="y.value" :label="y.label" :value="y.value" />
          </el-select>
          <!-- 3. 聚合粒度选择: 日固定小时, 周固定日, 月/季/年可选 -->
          <el-select v-if="grain === 'month' || grain === 'quarter' || grain === 'year'" v-model="trendGrain" size="small" style="width: 120px; margin-right: 8px">
            <el-option label="按日聚合" value="day" />
            <el-option label="按周聚合" value="week" />
            <el-option label="按月聚合" value="month" />
          </el-select>
          <!-- 4. 自定义日期范围 -->
          <el-date-picker
            v-if="customRangeVisible"
            v-model="customDateRange"
            type="daterange"
            size="small"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 260px; margin-right: 8px"
            @change="onCustomRangeChange"
          />
          <el-button v-if="grain !== 'day'" size="small" :type="customDateRange ? 'primary' : 'default'" @click="toggleCustomRange">
            {{ customDateRange ? '自定义范围' : '自定义范围' }}
          </el-button>
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
    <section class="section-card mb-20">
      <SectionHeader :title="'1. 核心指标'" :sub="'对比' + compareName + '同时段 · 悬停标签查看口径'" />
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

      <!-- 现金流出拆分：3合计卡 + 四路堆叠比例条（复用 RouteSplit compact 模式） -->
      <div class="mt-10" v-if="kpiData.cash_routes_today">
        <RouteSplit :routes="kpiData.cash_routes_today" compact />
      </div>
    </section>

    <!-- 模块2: 实时节奏 -->
    <section class="section-card mb-24">
      <SectionHeader title="2. 实时节奏" :sub="rhythmSub" />
      <!-- 分组标签条：工作量 / 款项 / 投诉 三组视觉分隔 -->
      <div class="rhythm-group-bar">
        <div class="rhythm-group-tag workload">
          <span class="dot workload-dot"></span>
          <span>工作量</span>
          <span class="group-desc">会话·接待</span>
        </div>
        <div class="rhythm-divider"></div>
        <div class="rhythm-group-tag payment">
          <span class="dot payment-dot"></span>
          <span>款项</span>
          <span class="group-desc">退款笔数·金额·真实现金流</span>
        </div>
        <div class="rhythm-divider"></div>
        <div class="rhythm-group-tag complaint">
          <span class="dot complaint-dot"></span>
          <span>投诉</span>
          <span class="group-desc">投诉量</span>
        </div>
      </div>
      <ChartCard body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="360px" />
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
          <ChartCard title="投诉子类分布（按三大类分组着色）">
            <template #extra>
              <span style="font-weight:400;color:#909399;font-size:11px">
                <span style="color:#e74c3c">■</span>外部投诉(12315/工信部/消协/12345等)　
                <span style="color:#9b59b6">■</span>商户号投诉(微信, 属外部)　
                <span style="color:#409eff">■</span>内部投诉(商品/服务)
              </span>
            </template>
            <BaseChart :option="complaintSubPieOption" height="300px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="12">
          <ChartCard title="投诉根因分布（所有投诉工单均纳入统计）">
            <template #extra>
              <span v-if="complaintData.complaint_unmatched != null"
                    style="font-weight:400;color:#909399;font-size:11px">
                精确关联网速/套餐/退款接待记录 {{ formatNum(complaintData.complaint_total - complaintData.complaint_unmatched) }} 条
                （其余用投诉标题关键词归类）
              </span>
            </template>
            <BaseChart :option="complaintRootBarOption" height="300px" />
          </ChartCard>
        </el-col>
      </el-row>
      <!-- 模块5第二行: 处理状态饼图 + 处理人排行表 -->
      <el-row :gutter="12" class="mt-12">
        <el-col :xs="24" :md="10">
          <ChartCard title="投诉处理状态分布（三表合并统一口径）">
            <template #extra>
              <span style="font-weight:400;color:#909399;font-size:11px">
                5 桶口径: 待处理 / 处理中 / 已完成 / 已终止 / 未标注
              </span>
            </template>
            <BaseChart :option="complaintStatusPieOption" height="320px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="14">
          <ChartCard title="投诉处理人排行（Top 15 · 三表合并）">
            <template #extra>
              <span style="font-weight:400;color:#909399;font-size:11px">
                来源: 内部工单handler + 外部投诉handler + 卡商群投诉handler
              </span>
            </template>
            <el-table
              :data="complaintData.complaint_handlers || []"
              size="small"
              stripe
              height="320px"
              style="width: 100%"
              v-loading="!complaintData.complaint_handlers?.length && complaintData.complaint_total > 0"
            >
              <el-table-column label="#" width="44" align="center" type="index" />
              <el-table-column prop="handler" label="处理人" min-width="100" align="left">
                <template #default="{ row }">
                  <span :class="row.handler === '未分配' ? 'tag-note' : ''" style="font-weight:500">
                    {{ row.handler }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="total" label="总数" width="68" align="center" sortable>
                <template #default="{ row }">
                  <b>{{ formatNum(row.total) }}</b>
                </template>
              </el-table-column>
              <el-table-column label="按状态(条)" min-width="220" align="center">
                <template #default="{ row }">
                  <el-space wrap size="4">
                    <el-tag v-if="row.by_status?.['待处理']" type="danger" size="small" effect="plain">
                      待处理 {{ formatNum(row.by_status['待处理']) }}
                    </el-tag>
                    <el-tag v-if="row.by_status?.['处理中']" type="warning" size="small" effect="plain">
                      处理中 {{ formatNum(row.by_status['处理中']) }}
                    </el-tag>
                    <el-tag v-if="row.by_status?.['已完成']" type="success" size="small" effect="plain">
                      已完成 {{ formatNum(row.by_status['已完成']) }}
                    </el-tag>
                    <el-tag v-if="row.by_status?.['已终止']" type="info" size="small" effect="plain">
                      已终止 {{ formatNum(row.by_status['已终止']) }}
                    </el-tag>
                    <el-tag v-if="row.by_status?.['未标注']" size="small" effect="plain" style="background:#f4f4f5;color:#909399;border-color:#e9e9eb">
                      未标注 {{ formatNum(row.by_status['未标注']) }}
                    </el-tag>
                    <span v-if="!Object.values(row.by_status || {}).some(v => v)" class="tag-note">—</span>
                  </el-space>
                </template>
              </el-table-column>
              <el-table-column label="按来源(条)" min-width="200" align="center">
                <template #default="{ row }">
                  <el-space wrap size="4">
                    <el-tag v-if="row.by_source?.['内部投诉']" type="primary" size="small" effect="plain">
                      内部 {{ formatNum(row.by_source['内部投诉']) }}
                    </el-tag>
                    <el-tag v-if="row.by_source?.['外部投诉']" type="danger" size="small" effect="dark">
                      外部 {{ formatNum(row.by_source['外部投诉']) }}
                    </el-tag>
                    <el-tag v-if="row.by_source?.['卡商群投诉']" type="success" size="small" effect="plain">
                      卡商 {{ formatNum(row.by_source['卡商群投诉']) }}
                    </el-tag>
                    <span v-if="!Object.values(row.by_source || {}).some(v => v)" class="tag-note">—</span>
                  </el-space>
                </template>
              </el-table-column>
            </el-table>
          </ChartCard>
        </el-col>
      </el-row>

      <!-- 模块5第三行: 近期未处理投诉列表 (创建窗口 = 当前视图终点向前倒查 7 天, 状态=未闭环; 长跨度>31天不展示) -->
      <el-row :gutter="12" class="mt-12">
        <el-col :xs="24">
          <ChartCard
            title="近7天未闭环投诉（最早积压排第一 · 倒查7天创建窗口）"
            v-loading="loadingReception"
          >
            <template #extra>
              <el-space wrap size="default" style="font-size:12px;color:#606266;font-weight:400">
                <el-tag
                  v-if="complaintData.complaint_pending_total != null"
                  type="danger" size="small" effect="dark"
                >未闭环 {{ formatNum(complaintData.complaint_pending_total) }} 条</el-tag>
                <el-tooltip
                  v-if="complaintData.complaint_pending_skipped"
                  :content="complaintData.complaint_pending_skip_reason || '当前跨度未展示列表'"
                  placement="top"
                >
                  <el-tag type="warning" size="small" effect="plain" style="cursor:help">
                    ⚠ 列表未展示
                  </el-tag>
                </el-tooltip>
                <el-tooltip
                  content="独立于当前日/周视图：只要投诉创建时间落在「视图终点向前7天」区间内，且至今仍未闭环（未完成/未终止）就会出现在这里，避免当日视图漏掉前几天的积压投诉。"
                  placement="top"
                >
                  <span style="color:#909399;cursor:help;border-bottom:1px dashed #c0c4cc;padding-bottom:1px">
                    口径说明：倒查7天 × 待处理/处理中/未标注（三表合并）
                  </span>
                </el-tooltip>
              </el-space>
            </template>

            <!-- 列表未展示提示 -->
            <div
              v-if="complaintData.complaint_pending_skipped && complaintData.complaint_pending_skip_reason"
              style="padding:28px 20px;text-align:center;color:#909399;font-size:13px;background:#fafafa;border-radius:8px"
            >
              <el-icon :size="22" style="color:#e6a23c;margin-right:6px;vertical-align:-4px"><WarningFilled /></el-icon>
              {{ complaintData.complaint_pending_skip_reason }}
            </div>

            <!-- 无未处理投诉 -->
            <div
              v-else-if="!complaintData.complaint_pending_list?.length && complaintData.complaint_total > 0"
              style="padding:28px 20px;text-align:center;color:#67c23a;font-size:13px;background:#f5faf3;border-radius:8px"
            >
              <el-icon :size="22" style="color:#67c23a;margin-right:6px;vertical-align:-4px"><CircleCheckFilled /></el-icon>
              所有投诉均已闭环处理，暂无积压 👍
            </div>
            <div
              v-else-if="!complaintData.complaint_pending_list?.length && !complaintData.complaint_total"
              style="padding:28px 20px;text-align:center;color:#909399;font-size:13px"
            >
              {{ periodName }}暂无投诉记录
            </div>

            <!-- 待处理投诉明细列表 -->
            <el-table
              v-else
              :data="complaintData.complaint_pending_list || []"
              size="small"
              stripe
              height="420px"
              style="width:100%"
              row-key="no"
            >
              <el-table-column label="#" width="44" align="center" type="index" />
              <el-table-column label="来源" width="100" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.source_type === '外部投诉'" type="danger" size="small" effect="dark">
                    外部投诉
                  </el-tag>
                  <el-tag v-else-if="row.source_type === '卡商群投诉'" type="success" size="small" effect="plain">
                    卡商群
                  </el-tag>
                  <el-tag v-else type="primary" size="small" effect="plain">
                    内部投诉
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="当前状态" width="96" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.status_bucket === '待处理'" type="danger" size="small" effect="dark">
                    待处理
                  </el-tag>
                  <el-tag v-else-if="row.status_bucket === '处理中'" type="warning" size="small" effect="plain">
                    处理中
                  </el-tag>
                  <el-tag v-else size="small" effect="plain" style="background:#f4f4f5;color:#909399;border-color:#e9e9eb">
                    {{ row.status_bucket || '未标注' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="投诉时间" width="168" align="center" prop="occur_time">
                <template #default="{ row }">
                  <span style="font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size:12px">
                    {{ formatShortTime(row.occur_time) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="投诉内容（含来源标识/分类/问题描述）" min-width="340" align="left" show-overflow-tooltip>
                <template #default="{ row }">
                  <div style="font-weight:500;color:#303133;line-height:1.55">
                    {{ row.title || '—' }}
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="投诉人 / 联系" width="148" align="left" show-overflow-tooltip>
                <template #default="{ row }">
                  <div v-if="row.complainant || row.complainant_phone" style="line-height:1.55">
                    <div v-if="row.complainant">
                      <el-icon :size="12" style="color:#909399"><User /></el-icon>
                      <span style="margin-left:4px">{{ row.complainant }}</span>
                    </div>
                    <div v-if="row.complainant_phone" style="color:#606266;font-size:12px">
                      📞 {{ row.complainant_phone }}
                    </div>
                  </div>
                  <span v-else class="tag-note">—</span>
                </template>
              </el-table-column>
              <el-table-column label="设备 SN" width="156" align="left" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.device_sn" class="mono-small" style="color:#409eff">{{ row.device_sn }}</span>
                  <span v-else class="tag-note">—</span>
                </template>
              </el-table-column>
              <el-table-column label="处理人" width="112" align="left">
                <template #default="{ row }">
                  <span :class="!row.handler || row.handler === '未分配' ? 'tag-note' : ''" style="font-weight:500">
                    {{ row.handler || '未分配' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="编号" width="176" align="left" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row.no" class="mono-small" style="color:#606266">{{ row.no }}</span>
                  <span v-else class="tag-note">—</span>
                </template>
              </el-table-column>
            </el-table>
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
      <SectionHeader title="7. 人均效率" sub="会话数(次)=cs_record+工单完工条数 · 接待数(个)=智能助手SN ∪ 工单SN 跨表去重 · 六专项独立列:咨询接待×1.0|套餐专项×1.8|退款挽回×2.2|退款处理×2.0|内部投诉×3.0|外部投诉×5.0 · 排除sys/system/系统自动记录">
        <template #extra>
          <el-space wrap size="small">
            <el-alert
              v-if="dutyLeaders.length"
              :title="'今日值班组长: ' + dutyLeaders.map(l => l.name).join('、')"
              type="warning"
              effect="light"
              :closable="false"
              size="small"
              style="max-width: 360px"
            />
            <!-- 企微筛选 -->
            <el-tooltip effect="light" placement="top" content="仅显示 Person 维表中已绑定企微账号(qw_user_id)的员工；自动过滤 客户自助/系统位/非企微临时名">
              <el-switch
                v-model="staffFilter.qwOnly"
                @change="loadAll"
                size="small"
                active-text="仅企微"
                inactive-text="全部人员"
                inline-prompt
              />
            </el-tooltip>
            <!-- 聚合方式 Tab -->
            <el-radio-group v-model="staffFilter.aggregateBy" size="small" @change="onAggregateChange">
              <el-radio-button label="person">按人</el-radio-button>
              <el-radio-button label="specialty">按专项</el-radio-button>
              <el-radio-button label="group">按分组</el-radio-button>
              <el-radio-button label="all">全员</el-radio-button>
            </el-radio-group>
          </el-space>
        </template>
      </SectionHeader>
      <el-card shadow="never">
        <!-- 人员筛选工具栏：仅在按人模式显示 -->
        <div v-if="staffFilter.aggregateBy === 'person'" class="mb-12" style="display:flex; align-items:center; gap:8px; flex-wrap:wrap">
          <span style="font-size:13px; color:#909399">筛选：</span>
          <el-select v-model="filterDepartment" placeholder="全部部门" clearable size="small" style="width: 120px">
            <el-option v-for="d in departmentOptions" :key="d" :label="d" :value="d" />
          </el-select>
          <el-select v-model="filterGroupId" placeholder="全部分组" clearable size="small" style="width: 140px">
            <el-option v-for="g in groupsList" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
          <el-select v-model="filterSpecialtyId" placeholder="全部专项" clearable size="small" style="width: 140px">
            <el-option v-for="s in specialtiesList" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-button
            v-if="filterDepartment || filterGroupId || filterSpecialtyId"
            size="small"
            text
            type="primary"
            @click="filterDepartment=''; filterGroupId=null; filterSpecialtyId=null"
          >重置筛选</el-button>
          <span v-if="staffRows.length !== rawStaffRows.length" class="text-muted" style="font-size:12px; margin-left:8px">
            共 {{ staffRows.length }} / {{ rawStaffRows.length }} 人
          </span>
        </div>
        <el-table :data="staffRows" border stripe size="small" max-height="520">
          <!-- 排名列：所有模式都显示 -->
          <el-table-column label="排名" width="64" align="center" fixed>
            <template #default="{ $index }">
              <el-tag v-if="$index < 3 && staffFilter.aggregateBy !== 'all'" size="small" :type="['danger','warning','success'][$index]" effect="dark">
                Top{{ $index + 1 }}
              </el-tag>
              <span v-else-if="staffFilter.aggregateBy !== 'all'" class="text-muted">{{ $index + 1 }}</span>
              <span v-else class="text-muted">合计</span>
            </template>
          </el-table-column>

          <!-- 按人模式：人员列 -->
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="人员" width="170">
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

          <!-- 按专项模式：专项名称列 -->
          <el-table-column v-else-if="staffFilter.aggregateBy === 'specialty'" label="专项" width="170">
            <template #default="{ row }">
              <el-tag size="default" effect="dark" :type="specialtyTagType(row.name)" style="font-weight:600">
                {{ row.name || '未知专项' }}
              </el-tag>
            </template>
          </el-table-column>

          <!-- 按分组模式：分组名称列 -->
          <el-table-column v-else-if="staffFilter.aggregateBy === 'group'" label="分组" width="170">
            <template #default="{ row }">
              <el-tag size="default" effect="dark" :type="grpTagType(row.name)" style="font-weight:600">
                {{ row.name || '未分组' }}
              </el-tag>
            </template>
          </el-table-column>

          <!-- 全员模式：名称列 -->
          <el-table-column v-else-if="staffFilter.aggregateBy === 'all'" label="统计范围" width="170">
            <template #default>
              <el-tag size="default" type="primary" effect="dark" style="font-weight:600">
                {{ staffFilter.qwOnly ? '企微全员合计' : '全员合计' }}
              </el-tag>
            </template>
          </el-table-column>

          <!-- 部门列：按人/按分组模式显示 -->
          <el-table-column v-if="staffFilter.aggregateBy === 'person' || staffFilter.aggregateBy === 'group'" prop="department" label="部门" width="110">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.department === '未设置' || !row.department ? 'info' : 'success'">
                {{ row.department || '—' }}
              </el-tag>
            </template>
          </el-table-column>

          <!-- 人员数：按专项/按分组/全员模式显示 -->
          <el-table-column v-if="staffFilter.aggregateBy !== 'person'" label="人数" width="80" align="center">
            <template #default="{ row }">
              <span style="font-weight:600;color:#606266">{{ formatNum(row.person_count || row.staff_count || 0) }}</span>
              <span style="color:#909399;font-size:11px">人</span>
            </template>
          </el-table-column>

          <!-- 岗位・分组・专项列：仅按人模式显示 -->
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="岗位・分组・专项" width="208">
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
          <!-- 六专项独立列: 每列一行 n次(m个)，简洁不啰嗦 (2026-08-11 拆分退款为退款处理+退款挽回，拆分投诉为内部+外部) -->
          <el-table-column label="咨询接待" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt zx">{{ formatNum(row.breakdown?.['咨询接待']?.sessions ?? row.sp_zixun ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['咨询接待']?.people ?? row.pp_zixun ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="套餐专项" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt tc">{{ formatNum(row.breakdown?.['套餐专项']?.sessions ?? row.sp_taocan ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['套餐专项']?.people ?? row.pp_taocan ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="退款挽回" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt wh">{{ formatNum(row.breakdown?.['退款挽回']?.sessions ?? row.sp_wanhui ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['退款挽回']?.people ?? row.pp_wanhui ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="退款处理" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt tk">{{ formatNum(row.breakdown?.['退款处理']?.sessions ?? row.sp_tuikuan ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['退款处理']?.people ?? row.pp_tuikuan ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="内部投诉" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt ni">{{ formatNum(row.breakdown?.['内部投诉']?.sessions ?? row.sp_neibu ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['内部投诉']?.people ?? row.pp_neibu ?? 0) }}</span>个)</span>
            </template>
          </el-table-column>
          <el-table-column label="外部投诉" width="108" align="center">
            <template #default="{ row }">
              <span class="sp-txt wa">{{ formatNum(row.breakdown?.['外部投诉']?.sessions ?? row.sp_waibu ?? 0) }}次(<span class="sp-txt-p">{{ formatNum(row.breakdown?.['外部投诉']?.people ?? row.pp_waibu ?? 0) }}</span>个)</span>
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
                  v-if="staffFilter.aggregateBy === 'person' && (row.rank === null || row.is_virtual === true)"
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

    <!-- 模块8: 退款挽回专项 (概要, 详情见退款看板) -->
    <section v-if="hasRecovery" class="section-card mb-24">
      <SectionHeader
        title="8. 退款挽回专项"
        sub="概要指标 · 详情请进入「退款看板」查看完整分析"
      >
        <template #extra>
          <el-tag size="small" effect="dark" type="danger" style="margin-right:6px">{{ recoveryData.date }}~{{ recoveryData.date_end || recoveryData.date }}</el-tag>
          <el-button size="small" type="primary" plain @click="$router.push('/dashboards/refund')">
            查看退款挽回详情 →
          </el-button>
        </template>
      </SectionHeader>

      <!-- 概要: 3 个核心 KPI -->
      <el-row :gutter="12">
        <el-col :xs="12" :sm="8">
          <div class="kpi-mini-card" style="background:#eff6ff">
            <div class="kpi-mini-label" style="color:#2563eb">总接待SN(分母)</div>
            <div class="kpi-mini-sub">退款组接待退款类的去重SN</div>
            <div class="kpi-mini-value" style="color:#2563eb">
              {{ formatNum(recoveryData.kpi.total_sn) }}<span class="kpi-mini-unit">个</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8">
          <div class="kpi-mini-card" :style="{background: Number(recoveryData.kpi.success_rate||0)>=38?'#f0fdf4':(Number(recoveryData.kpi.success_rate||0)>=30?'#fefce8':'#fef2f2')}">
            <div class="kpi-mini-label" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626')}">成功率</div>
            <div class="kpi-mini-sub">挽留成功 / 总接待SN</div>
            <div class="kpi-mini-value" :style="{color: Number(recoveryData.kpi.success_rate||0)>=38?'#16a34a':(Number(recoveryData.kpi.success_rate||0)>=30?'#ca8a04':'#dc2626')}">
              {{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
            </div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="8">
          <div class="kpi-mini-card" style="background:#fef2f2">
            <div class="kpi-mini-label" style="color:#dc2626">挽留失败</div>
            <div class="kpi-mini-sub">最终退款的SN</div>
            <div class="kpi-mini-value" style="color:#dc2626">
              {{ formatNum(recoveryData.kpi.failed_sn) }}<span class="kpi-mini-unit">个</span>
              <span style="font-size:12px; font-weight:500; color:#9ca3af; margin-left:6px">
                失败单数 {{ formatNum(recoveryData.kpi.fail_cnt) }} · ¥{{ formatMoney(Number(recoveryData.kpi.actual_refund_amount||0)) }}
              </span>
            </div>
          </div>
        </el-col>
      </el-row>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { Timer } from '@element-plus/icons-vue'
import { today } from '@/api/analytics'
import { listGroups } from '@/api/group'
import { listSpecialties } from '@/api/specialty'
import BaseChart from '@/components/BaseChart.vue'
import { formatMoney } from '@/utils/format'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import weekOfYear from 'dayjs/plugin/weekOfYear'
// 扩展dayjs插件
dayjs.extend(isoWeek)
dayjs.extend(quarterOfYear)
dayjs.extend(weekOfYear)
// Today.vue 图标通过字符串名传给 KpiCard (KpiCard 内部 component :is 动态解析)
// 本地脚本不直接使用图标组件

const loading = ref(false)

// ========== 人员筛选: 部门/分组/专项 ==========
const filterDepartment = ref('')
const filterGroupId = ref(null)
const filterSpecialtyId = ref(null)
const groupsList = ref([])
const specialtiesList = ref([])

// 动态部门列表：从实际人员数据提取（不硬编码）
const departmentOptions = computed(() => {
  const depts = new Set()
  ;(rawStaffRows.value || []).forEach(r => {
    if (r.department && r.department !== '未分配') depts.add(r.department)
  })
  return Array.from(depts).sort()
})

// ========== 时间选择与聚合逻辑 ==========
const grain = ref('day')                    // 视图粒度: day/week/month/quarter/year
const quickDate = ref('today')              // day: today/yesterday/custom
const weekOffset = ref(0)                   // week: 0=本周, 1=上周, 2=上上周... (近12周)
const monthOffset = ref(0)                  // month: 0=本月, 1=上月... (近12月)
const quarterOffset = ref(0)                // quarter: 0=本季度... (近8季度)
const yearOffset = ref(0)                   // year: 0=今年... (近3年)
const customDate = ref('')                  // 日粒度自定义单日
const customDateRange = ref(null)           // 自定义日期范围 [start, end]
const customRangeVisible = ref(false)       // 是否显示自定义日期范围选择器
const trendGrain = ref('hour')              // 聚合粒度: hour/day/week/month
const quickGrainOpt = ref('all')
const grainQuickOptions = [{ label: '全部', value: 'all' }]

// 周选项: 近12周
const weekOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const d = dayjs().subtract(i, 'week')
    const ws = d.startOf('week').add(1, 'day') // 周一
    const we = ws.add(6, 'day')               // 周日
    const label = i === 0 ? '本周' : i === 1 ? '上周' : `${ws.format('MM-DD')}~${we.format('MM-DD')}`
    opts.push({ label, value: i })
  }
  return opts
})
// 月选项: 近12月
const monthOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const d = dayjs().subtract(i, 'month')
    const label = i === 0 ? '本月' : i === 1 ? '上月' : d.format('YYYY年MM月')
    opts.push({ label, value: i })
  }
  return opts
})
// 季度选项: 近8季度
const quarterOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 8; i++) {
    const d = dayjs().subtract(i, 'quarter')
    const q = d.quarter()
    const label = i === 0 ? '本季度' : i === 1 ? '上季度' : `${d.year()}年Q${q}`
    opts.push({ label, value: i })
  }
  return opts
})
// 年选项: 近3年
const yearOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 3; i++) {
    const d = dayjs().subtract(i, 'year')
    const label = i === 0 ? '今年' : i === 1 ? '去年' : `${d.year()}年`
    opts.push({ label, value: i })
  }
  return opts
})

// 标签显示
const grainLabel = computed(() => {
  const gMap = { day: '日视图', week: '周视图', month: '月视图', quarter: '季度视图', year: '年视图' }
  const tMap = { hour: '·按小时', day: '·按日', week: '·按周', month: '·按月' }
  return (gMap[grain.value] || '日视图') + (tMap[trendGrain.value] || '')
})
const grainTagType = computed(() => ({ day: 'success', week: 'primary', month: 'warning', quarter: 'danger', year: 'info' }[grain.value] || 'info'))

// 本期名 & 对比期名
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
    case 'day':
    default:
      if (quickDate.value === 'yesterday') return '昨日'
      if (quickDate.value === 'custom' && customDate.value) return '该日'
      return '今日'
  }
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
    case 'day':
    default:
      if (quickDate.value === 'yesterday') return '前日'
      if (quickDate.value === 'custom' && customDate.value) return '前一日'
      return '昨日'
  }
})

// 时间粒度自动映射规则
function getAutoTrendGrain() {
  if (grain.value === 'day') return 'hour'
  if (grain.value === 'week') return 'day'
  return trendGrain.value || 'day'
}

// 节奏副标题
const rhythmSub = computed(() => {
  const tg = trendGrain.value
  const grainDesc = { hour: '按北京时间小时级', day: '按日历日', week: '按周(周一~周日)', month: '按月' }
  return `【工作量】会话/接待 · 【款项】退款笔数四路堆叠+金额四路堆叠面积+真实现金流E虚线 · 【投诉】投诉量 · ${grainDesc[tg] || '自动'}`
})

// ---- 计算当前实际时间范围 [start, end] YYYY-MM-DD ----
function _startOfWeek(d) { return d.day(1) }
function _endOfWeek(d) { return d.day(7) }

const timeRange = computed(() => {
  // 优先使用自定义日期范围
  if (customDateRange.value && customDateRange.value.length === 2) {
    return [customDateRange.value[0], customDateRange.value[1]]
  }
  const now = dayjs()
  switch (grain.value) {
    case 'week': {
      const base = now.subtract(weekOffset.value, 'week')
      const s = _startOfWeek(base.startOf('day'))
      const e = (weekOffset.value === 0) ? now : _endOfWeek(base.startOf('day'))
      return [s.format('YYYY-MM-DD'), e.format('YYYY-MM-DD')]
    }
    case 'month': {
      const base = now.subtract(monthOffset.value, 'month')
      const s = base.startOf('month')
      const e = (monthOffset.value === 0) ? now : base.endOf('month')
      return [s.format('YYYY-MM-DD'), e.format('YYYY-MM-DD')]
    }
    case 'quarter': {
      const base = now.subtract(quarterOffset.value, 'quarter')
      const s = base.startOf('quarter')
      const e = (quarterOffset.value === 0) ? now : base.endOf('quarter')
      return [s.format('YYYY-MM-DD'), e.format('YYYY-MM-DD')]
    }
    case 'year': {
      const base = now.subtract(yearOffset.value, 'year')
      const s = base.startOf('year')
      const e = (yearOffset.value === 0) ? now : base.endOf('year')
      return [s.format('YYYY-MM-DD'), e.format('YYYY-MM-DD')]
    }
    case 'day':
    default: {
      let d
      if (customDate.value) d = dayjs(customDate.value)
      else if (quickDate.value === 'yesterday') d = now.subtract(1, 'day')
      else d = now
      const iso = d.format('YYYY-MM-DD')
      return [iso, iso]
    }
  }
})

// 展示给大标题的日期
const displayDate = computed(() => {
  const [s, e] = timeRange.value
  if (!e || s === e) return s
  return `${s} ~ ${e}`
})

// 当前请求参数
const currentParams = computed(() => {
  const [s, e] = timeRange.value
  const rangeDays = dayjs(e).diff(dayjs(s), 'day') + 1
  let params
  if (rangeDays <= 1) {
    const todayIso = dayjs().format('YYYY-MM-DD')
    if (s === todayIso && !customDateRange.value) {
      params = {}
    } else {
      params = { date: s }
    }
  } else {
    params = { start: s, end: e }
  }
  // 添加聚合粒度参数
  params.trend_grain = getAutoTrendGrain()
  return params
})

// 切换视图粒度时重置默认值
watch(grain, (newGrain) => {
  customDateRange.value = null
  customRangeVisible.value = false
  if (newGrain === 'month' || newGrain === 'quarter' || newGrain === 'year') {
    trendGrain.value = 'day'
  } else if (newGrain === 'week') {
    trendGrain.value = 'day'
  } else {
    trendGrain.value = 'hour'
  }
})

// 自定义日期范围
function toggleCustomRange() {
  customRangeVisible.value = !customRangeVisible.value
  if (!customRangeVisible.value) {
    customDateRange.value = null
    scheduleLoadAll()
  }
}
function onCustomRangeChange(val) {
  if (val && val.length === 2) {
    scheduleLoadAll()
  }
}

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
      tip: '退款笔数 = 四路经营合计: A线上直退 + B钱包取现 + C闪猫退款 + D其他退款 (C/D独立表建表后自动并入)',
      ...(k.refund_count || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'refund_amount',  label: '退款金额', icon: 'Money',       bg: '#fef0f0', color: '#f56c6c', unit: '',
      money: true,
      tip: '退款金额 = 四路经营合计: A线上直退 + B钱包取现 + C闪猫退款 + D其他退款 (经营侧登记的所有退款出款)',
      ...(k.refund_amount || { value: 0, yoy_value: 0, yoy_pct: null }) },
    { key: 'cash_out',       label: '真实现金流', icon: 'Wallet',      bg: '#f0f9eb', color: '#67c23a', unit: '',
      money: true,
      tip: '真实现金流(对账基准) = 商户号流水 merchant_refunds WHERE refund_status=\'SUCCESS\' 合计, 即微信侧真实扣款金额',
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
  // === 工作量组 ===
  reception: [],
  reception_devices: [],
  // === 款项组 - 退款笔数(四路堆叠) ===
  refund_count: [],            // 兼容旧 = 四路合计
  refund_count_a: [],          // A 线上直退
  refund_count_b: [],          // B 钱包提现
  refund_count_c: [],          // C 闪猫退款
  refund_count_d: [],          // D 其他退款
  refund_count_total: [],      // 四路合计 A+B+C+D
  // === 款项组 - 退款金额(四路堆叠面积 + 实际现金流E折线) ===
  refund_amount: [],           // 兼容旧 = 四路合计
  refund_amount_a: [],         // A 金额
  refund_amount_b: [],         // B 金额
  refund_amount_c: [],         // C 金额
  refund_amount_d: [],         // D 金额
  refund_amount_e: [],         // E 实际现金流金额(商户号SUCCESS)
  refund_amount_total: [],     // 四路经营合计 A+B+C+D
  cashflow_e_count: [],        // 实际现金流E 笔数
  cashflow_e_amount: [],       // 实际现金流E 金额
  // === 投诉组 ===
  complaint: [],
})

// 四路颜色映射
const ROUTE_COLORS = {
  a: { bar: '#e6a23c', area: 'rgba(230,162,60,0.20)', line: '#d4881a' },   // A 橙
  b: { bar: '#f0948e', area: 'rgba(240,148,142,0.18)', line: '#e0726a' },  // B 浅红
  c: { bar: '#9b59b6', area: 'rgba(155,89,182,0.16)', line: '#804399' },  // C 紫
  d: { bar: '#95a5a6', area: 'rgba(149,165,166,0.14)', line: '#7b8a8b' }, // D 灰
  e: { line: '#27ae60', area: 'rgba(39,174,96,0.10)' },                    // E 绿(真实现金流)
}
// 工作量组颜色
const WORK_COLORS = {
  session: '#409eff',     // 会话 蓝
  reception: '#91cc75',   // 接待 绿
}
// 投诉颜色
const COMPLAINT_COLOR = '#f56c6c'  // 投诉 红

const hourlyOption = computed(() => {
  const hd = hourlyData.value
  const labels = hd.hour_labels || []

  // 三路分组的legend数据（用legend.data里带分隔符标题的方式实现视觉分组）
  const legendData = [
    '会话数(次)', '接待数(个)',
    '',  // 占位分隔
    '退款-A(直退)', '退款-B(提现)', '退款-C(闪猫)', '退款-D(其他)',
    '',
    '退金-A', '退金-B', '退金-C', '退金-D', '退款合计', '真实现金流E',
    '',
    '投诉量',
  ]

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const p0 = params[0]
        const h = labels[p0.dataIndex] ?? ''
        const getVal = (name, opts = {}) => {
          const m = params.find(x => x.seriesName === name)
          if (m == null || m.value == null) return '-'
          const v = Number(m.value)
          if (opts.currency) return '¥' + v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          return v.toLocaleString('zh-CN')
        }
        const cntA = getVal('退款-A(直退)')
        const cntB = getVal('退款-B(提现)')
        const cntC = getVal('退款-C(闪猫)')
        const cntD = getVal('退款-D(其他)')
        const cntTotal = Number(hd.refund_count_total?.[p0.dataIndex] ?? 0)
        const amtA = getVal('退金-A', { currency: true })
        const amtB = getVal('退金-B', { currency: true })
        const amtC = getVal('退金-C', { currency: true })
        const amtD = getVal('退金-D', { currency: true })
        const amtTotal = getVal('退款合计', { currency: true })
        const amtE = getVal('真实现金流E', { currency: true })

        return `<div style="font-weight:600;margin-bottom:4px">${h}</div>` +
          // 工作量组
          `<div style="border-top:1px dashed #e4e7ed;padding-top:4px;margin-top:2px;color:#909399;font-size:11px">【工作量】</div>` +
          `<div>会话数(次): <b>${getVal('会话数(次)')}</b></div>` +
          `<div>接待数(个): <b>${getVal('接待数(个)')}</b></div>` +
          // 款项组
          `<div style="border-top:1px dashed #e4e7ed;padding-top:4px;margin-top:2px;color:#909399;font-size:11px">【款项·笔数】</div>` +
          `<div>退款笔数合计: <b>${cntTotal.toLocaleString('zh-CN')}</b> (直退${cntA} + 提现${cntB} + 闪猫${cntC} + 其他${cntD})</div>` +
          `<div style="color:#909399;font-size:11px">【款项·金额】</div>` +
          `<div>退款金额合计: <b>${amtTotal}</b> (A${amtA} + B${amtB} + C${amtC} + D${amtD})</div>` +
          `<div>真实现金流E: <b style="color:#27ae60">${amtE}</b></div>` +
          // 投诉组
          `<div style="border-top:1px dashed #e4e7ed;padding-top:4px;margin-top:2px;color:#909399;font-size:11px">【投诉】</div>` +
          `<div>投诉量: <b style="color:${COMPLAINT_COLOR}">${getVal('投诉量')}</b></div>`
      },
    },
    legend: {
      data: legendData,
      top: 0,
      type: 'scroll',
      formatter: (name) => {
        if (name === '') return ''
        return name
      },
      // 用不同颜色文字区分三组
      textStyle: { fontSize: 11 },
      selected: {
        '退款-C(闪猫)': false,  // C/D默认隐藏，因为当前没有数据
        '退款-D(其他)': false,
        '退金-C': false,
        '退金-D': false,
      },
    },
    grid: [
      // 主网格（左Y：笔数/工作量/投诉；右Y：金额）
      { left: 56, right: 72, bottom: 36, top: 58, containLabel: false },
    ],
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: 10, interval: 0 },
      axisPointer: { type: 'shadow' },
    },
    yAxis: [
      {
        type: 'value',
        name: '数量(次/个/笔)',
        nameTextStyle: { fontSize: 10, color: '#909399' },
        axisLabel: { fontSize: 10 },
        splitLine: { lineStyle: { type: 'dashed', color: '#f0f0f0' } },
      },
      {
        type: 'value',
        name: '金额(元)',
        nameTextStyle: { fontSize: 10, color: '#909399' },
        axisLabel: {
          fontSize: 10,
          formatter: (v) => (v >= 10000 ? (v / 10000).toFixed(0) + '万' : v),
        },
        splitLine: { show: false },
      },
    ],
    series: [
      // ========== 【工作量组】独立柱 ==========
      {
        name: '会话数(次)', type: 'bar', data: hd.reception || [],
        itemStyle: { color: WORK_COLORS.session, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 10,
        barGap: '10%',
      },
      {
        name: '接待数(个)', type: 'bar', data: hd.reception_devices || [],
        itemStyle: { color: WORK_COLORS.reception, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 10,
      },

      // ========== 【款项组·笔数】四路堆叠柱 stack=refund_cnt ==========
      {
        name: '退款-A(直退)', type: 'bar', stack: 'refund_cnt', data: hd.refund_count_a || [],
        itemStyle: { color: ROUTE_COLORS.a.bar },
        barMaxWidth: 16,
        barGap: '20%',
      },
      {
        name: '退款-B(提现)', type: 'bar', stack: 'refund_cnt', data: hd.refund_count_b || [],
        itemStyle: { color: ROUTE_COLORS.b.bar },
        barMaxWidth: 16,
      },
      {
        name: '退款-C(闪猫)', type: 'bar', stack: 'refund_cnt', data: hd.refund_count_c || [],
        itemStyle: { color: ROUTE_COLORS.c.bar },
        barMaxWidth: 16,
      },
      {
        name: '退款-D(其他)', type: 'bar', stack: 'refund_cnt', data: hd.refund_count_d || [],
        itemStyle: { color: ROUTE_COLORS.d.bar },
        barMaxWidth: 16,
      },

      // ========== 【投诉组】独立柱 ==========
      {
        name: '投诉量', type: 'bar', data: hd.complaint || [],
        itemStyle: { color: COMPLAINT_COLOR, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 10,
      },

      // ========== 【款项组·金额】四路堆叠面积 stack=refund_amt (右Y) ==========
      {
        name: '退金-A', type: 'line', yAxisIndex: 1, stack: 'refund_amt', smooth: true,
        data: hd.refund_amount_a || [],
        itemStyle: { color: ROUTE_COLORS.a.line },
        areaStyle: { color: ROUTE_COLORS.a.area },
        showSymbol: false, lineStyle: { width: 1 },
      },
      {
        name: '退金-B', type: 'line', yAxisIndex: 1, stack: 'refund_amt', smooth: true,
        data: hd.refund_amount_b || [],
        itemStyle: { color: ROUTE_COLORS.b.line },
        areaStyle: { color: ROUTE_COLORS.b.area },
        showSymbol: false, lineStyle: { width: 1 },
      },
      {
        name: '退金-C', type: 'line', yAxisIndex: 1, stack: 'refund_amt', smooth: true,
        data: hd.refund_amount_c || [],
        itemStyle: { color: ROUTE_COLORS.c.line },
        areaStyle: { color: ROUTE_COLORS.c.area },
        showSymbol: false, lineStyle: { width: 1 },
      },
      {
        name: '退金-D', type: 'line', yAxisIndex: 1, stack: 'refund_amt', smooth: true,
        data: hd.refund_amount_d || [],
        itemStyle: { color: ROUTE_COLORS.d.line },
        areaStyle: { color: ROUTE_COLORS.d.area },
        showSymbol: false, lineStyle: { width: 1 },
      },
      // 退款金额合计虚线
      {
        name: '退款合计', type: 'line', yAxisIndex: 1, smooth: true,
        data: hd.refund_amount_total || [],
        itemStyle: { color: '#7d3c98' },
        symbol: 'circle', symbolSize: 4,
        lineStyle: { width: 2, type: 'dashed' },
        z: 5,
      },
      // 真实现金流E (独立线,不堆叠)
      {
        name: '真实现金流E', type: 'line', yAxisIndex: 1, smooth: true,
        data: hd.refund_amount_e || hd.cashflow_e_amount || [],
        itemStyle: { color: ROUTE_COLORS.e.line },
        symbol: 'diamond', symbolSize: 5,
        lineStyle: { width: 2.5, type: 'solid' },
        areaStyle: { color: ROUTE_COLORS.e.area },
        z: 6,
      },
    ],
  }
})

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
// complaintData 字段来自 reception 接口响应 (complaint_total/complaint_subs/complaint_roots/complaint_unmatched/complaint_by_source/complaint_handlers/complaint_status_all/complaint_pending_*)
const complaintData = ref({
  complaint_total: 0,
  complaint_subs: [],
  complaint_roots: [],
  complaint_unmatched: 0,
  complaint_by_source: [],
  complaint_handlers: [],
  complaint_status_all: [],
  // 近期未处理投诉列表 (日/周视图下有效,长跨度会被后端跳过)
  complaint_pending_total: 0,
  complaint_pending_skipped: false,
  complaint_pending_skip_reason: '',
  complaint_pending_list: [],
})
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
// 按 source_type 三大类分组着色:
//   外部投诉 → 红色暖色系 (红/橙/深红/砖红)
//   商户号投诉(微信, 属外部) → 紫色独立色组 (紫/深紫/洋红)
//   内部投诉 → 蓝绿冷色系 (蓝/青绿/湖蓝)
const complaintSubPieOption = computed(() => {
  const rows = (complaintData.value.complaint_subs || []).filter(d => d.value > 0)
  const EXTERNAL_WARM = ['#f56c6c', '#e74c3c', '#e6a23c', '#d35400', '#c0392b', '#e67e22']
  const MERCHANT_PURPLE = ['#9b59b6', '#8e44ad', '#e91e63', '#b83280']  // 商户号投诉=微信: 紫色系(一眼区分)
  const INTERNAL_COOL = ['#409eff', '#3498db', '#67c23a', '#2ecc71', '#1abc9c', '#909399', '#34495e']
  let extIdx = 0, merIdx = 0, intIdx = 0
  const data = rows.map(r => {
    const st = r.source_type || '内部投诉'
    let color, tagCh, tagHtml, suffix
    if (st === '外部投诉') {
      color = EXTERNAL_WARM[extIdx % EXTERNAL_WARM.length]; extIdx++
      tagCh = '★'; tagHtml = '<span style="color:#e74c3c;font-weight:600">[外部]</span>'; suffix = ' · 外'
    } else if (st === '商户号投诉') {
      color = MERCHANT_PURPLE[merIdx % MERCHANT_PURPLE.length]; merIdx++
      tagCh = '◆'; tagHtml = '<span style="color:#9b59b6;font-weight:600">[商户号·外]</span>'; suffix = ' · 商户'
    } else {
      color = INTERNAL_COOL[intIdx % INTERNAL_COOL.length]; intIdx++
      tagCh = '·'; tagHtml = '<span style="color:#3498db;font-weight:600">[内部]</span>'; suffix = ' · 内'
    }
    return {
      name: r.name + suffix,
      value: r.value,
      source_type: r.source_type,
      itemStyle: { color },
      _tagCh: tagCh,
      _tagHtml: tagHtml,
    }
  })
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        const d = p.data || {}
        const nm = (p.name || '').replace(/ · (外|内|商户)$/, '')
        return `${d._tagHtml || ''} ${nm}<br/>数量: ${p.value} 条<br/>占比: ${p.percent}%`
      },
    },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 10 } },
    series: [{
      name: '投诉子类', type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'],
      avoidLabelOverlap: true,
      label: { show: true, formatter: (p) => {
        const nm = (p.name || '').replace(/ · (外|内|商户)$/, '')
        const tag = (p.data && p.data._tagCh) || '·'
        return `${tag}${nm}\n${p.percent}%`
      }, fontSize: 10 },
      data: data.length ? data : [{ name: '暂无投诉', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '暂无投诉 ✅' } }],
    }],
  }
})
// 柱图: 投诉根因分布 (6标准 + 2扩展: 外部平台/卡商群)
// SUM(count) 必须严格等于 complaint_total, 颜色映射覆盖所有扩展桶
const complaintRootBarOption = computed(() => {
  const list = (complaintData.value.complaint_roots || []).filter(d => d.count > 0).slice(0, 12)
  const rootColorMap = {
    '网络质量类': '#f56c6c',
    '套餐争议类': '#e6a23c',
    '退款协商失败类': '#909399',
    '降成争议类': '#9b59b6',
    '其它(注销/发票/退换)': '#409eff',
    '外部平台投诉受理类': '#c0392b',   // 扩展桶1: 外部投诉但无法识别具体根因
    '卡商群待标注类': '#27ae60',       // 扩展桶2: 卡商群投诉无法识别具体根因
    '待标注': '#c0c4cc',
  }
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (p) => {
        const item = p[0]
        const total = (complaintData.value.complaint_total || 0)
        const pct = total ? ((item.data / total) * 100).toFixed(1) : '0.0'
        return `${item.name}<br/>投诉数: ${formatNum(item.data)} 条 · 占比 ${pct}%`
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
      label: {
        show: true, position: 'right', fontSize: 10,
        formatter: (p) => {
          const total = (complaintData.value.complaint_total || 0)
          const pct = total ? `(${((p.value / total) * 100).toFixed(0)}%)` : ''
          return formatNum(p.value) + '条' + pct
        }
      },
    }],
  }
})

// ---------- 模块5-2: 投诉处理状态分布饼图 (三表合并 5 桶) ----------
const complaintStatusPieOption = computed(() => {
  const rows = (complaintData.value.complaint_status_all || []).filter(d => d.value > 0)
  const statusColorMap = {
    '待处理': '#f56c6c',   // 红=待办
    '处理中': '#e6a23c',   // 橙=进行中
    '已完成': '#67c23a',   // 绿=完结
    '已终止': '#909399',   // 灰=终止/取消
    '未标注': '#c0c4cc',   // 浅灰=未知
  }
  const data = rows.map(r => ({
    name: r.name,
    value: r.value,
    itemStyle: { color: statusColorMap[r.name] || '#409eff' },
  }))
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        const total = (complaintData.value.complaint_total || 0)
        const pct = total ? ((p.value / total) * 100).toFixed(1) : '0.0'
        return `${p.name}<br/>投诉数: ${formatNum(p.value)} 条 · 占比 ${pct}%`
      },
    },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [{
      name: '投诉处理状态', type: 'pie', radius: ['42%', '68%'], center: ['50%', '42%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 1 },
      label: { fontSize: 11, formatter: '{b}: {c}条({d}%)' },
      labelLine: { length: 8, length2: 6 },
      data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#f0f0f0' }, label: { formatter: '暂无数据 ✅' } }],
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
const rawStaffRows = ref([])  // 全量原始数据（用于前端筛选）
const staffFilter = reactive({
  qwOnly: false,
  aggregateBy: 'person', // person/specialty/group/all
})

// 前端筛选后的人均效率数据（仅作用于表格，不影响值班组长等其他模块）
// 注意：只有在"按人"明细模式下筛选才生效，聚合模式（按专项/分组/全员）显示全量汇总
const staffRows = computed(() => {
  let rows = [...(rawStaffRows.value || [])]
  
  // 聚合模式下不筛选，返回全量汇总数据
  if (staffFilter.aggregateBy !== 'person') {
    return rows
  }
  
  // 部门筛选（精确匹配）
  if (filterDepartment.value) {
    rows = rows.filter(r => r.department === filterDepartment.value)
  }
  
  // 分组筛选（支持包含匹配，兼容组名前缀/后缀差异）
  if (filterGroupId.value) {
    const group = groupsList.value.find(g => g.id === filterGroupId.value)
    if (group) {
      const targetName = group.name
      rows = rows.filter(r => {
        const g = r.group || ''
        return g === targetName || g.includes(targetName) || targetName.includes(g)
      })
    }
  }
  
  // 专项筛选：本职专项匹配 OR 有对应专项工作量的人员也显示
  // (2026-08-11修正: 本职专项匹配 + 有该专项工作量的人员都显示)
  // 原则: 专项筛选 = "谁做了这个专项的工作"; 本职人员 + 跨专项处理人员都应显示;
  //        譬如投诉专员处理了套餐问题,也应出现在套餐专项筛选中,通过工作量列区分主次
  if (filterSpecialtyId.value) {
    const specialty = specialtiesList.value.find(s => s.id === filterSpecialtyId.value)
    if (specialty) {
      const targetName = specialty.name
      // 专项名称 → 对应的sp工作量字段映射
      const SP_FIELD_MAP = {
        '咨询接待': 'sp_zixun',
        '套餐专项': 'sp_taocan',
        '退款挽回': 'sp_wanhui',
        '退款处理': 'sp_tuikuan',
        '内部投诉': 'sp_neibu',
        '外部投诉': 'sp_waibu',
      }
      const spField = SP_FIELD_MAP[targetName]
      rows = rows.filter(r => {
        const cs = (r.current_specialty || '').trim()
        const hc = (r.hr_category || '').trim()
        // 条件1: 本职专项匹配 (Person.specialty 或 hr_category 匹配目标专项; 空值不匹配)
        if (cs && (cs === targetName || cs.includes(targetName) || targetName.includes(cs))) return true
        if (hc && hc !== '管理层' && (hc === targetName || hc.includes(targetName) || targetName.includes(hc))) return true
        // 条件2: 有对应专项的实际工作量 → 跨专项处理也计入 (做了就显示)
        if (spField && Number(r[spField]) > 0) return true
        return false
      })
    }
  }
  
  return rows
})

// ---------- 模块8: 退款挽回专项分析 (独立视图, 不冲抵接待效率) ----------
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
// 是否有退款组数据 (没数据时整个模块隐藏 避免空版面)
const hasRecovery = computed(() => {
  const k = recoveryData.value?.kpi || {}
  return (Number(k.total_sn) || 0) > 0 || (Number(k.fail_cnt) || 0) > 0
})

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
  // 值班组长始终使用全量数据，不受人员筛选影响
  const list = (rawStaffRows.value || []).filter(isDutyLeader)
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
// 专项标签颜色
const SPECIALTY_COLOR_MAP = {
  '咨询接待': 'primary',
  '套餐专项': 'success',
  '退款处理': 'warning',
  '退款挽回': 'warning',
  '内部投诉': 'danger',
  '外部投诉': 'danger',
  '投诉接待': 'danger',
  '管理层': 'info',
}
function specialtyTagType(s) {
  return SPECIALTY_COLOR_MAP[s] || 'info'
}
// 推断人员主专项显示 (2026-08-11修正: 优先用current_specialty, 不按组名硬编码, 分组≠专项)
// 原则: 分组(group_name)是行政/管理维度, 专项(specialty)是业务内容维度, 同一组内可有多专项(如朱海洋组既有套餐又有退款挽回)
function inferSpecialty(r) {
  // 1. 优先使用后端返回的current_specialty (已在维表正确登记的专项)
  const STANDARD_SPECIALTIES = ['咨询接待', '套餐专项', '退款挽回', '退款处理', '内部投诉', '外部投诉']
  if (r.current_specialty && STANDARD_SPECIALTIES.includes(r.current_specialty)) {
    return r.current_specialty
  }
  
  // 2. 看实际做了什么(Top 2 专项), 从会话数自动推断
  // 先看实际做了什么(Top 2 专项) 自动生成 (2026-08-11: 更新为6个独立专项)
  const items = [
    { name: '咨询接待', n: Number(r.sp_zixun) || 0 },
    { name: '套餐专项', n: Number(r.sp_taocan) || 0 },
    { name: '退款挽回', n: Number(r.sp_wanhui) || 0 },
    { name: '退款处理', n: Number(r.sp_tuikuan) || 0 },
    { name: '内部投诉', n: Number(r.sp_neibu) || 0 },
    { name: '外部投诉', n: Number(r.sp_waibu) || 0 },
  ].filter(x => x.n > 0).sort((a, b) => b.n - a.n)
  if (items.length) {
    if (items.length === 1) return items[0].name
    return items.slice(0, 2).map(i => i.name).join('+')
  }
  // 3. 管理岗(组长/主管/副主管)且无工作数据, 显示"管理岗"
  if (r.position && ['组长', '主管', '副主管'].includes(r.position)) {
    return '管理岗'
  }
  if (r.current_specialty === '组长') {
    return '管理岗'
  }
  
  // 4. 兜底: 未分组显示"—", 其他显示"综合"
  return (r.group === '未分组') ? '—' : '综合'
}
// 工作量 / 效率 通用 tooltip (纯文本, 兼容旧逻辑; 新渲染统一走 scoreTooltipHtml 返回结构化HTML)
function scoreTooltip(r, mode) {
  const fmt = (v) => formatNum(v)
  const w = r.weights || { '咨询接待': 1.0, '套餐专项': 1.8, '退款处理': 2.0, '退款挽回': 2.2, '内部投诉': 3.0, '外部投诉': 5.0 }
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
    { k: '退款挽回', short: '挽回', fallback_s: r.sp_wanhui, fallback_p: r.pp_wanhui ?? 0 },
    { k: '退款处理', short: '退款', fallback_s: r.sp_tuikuan, fallback_p: r.pp_tuikuan ?? 0 },
    { k: '内部投诉', short: '内诉', fallback_s: r.sp_neibu, fallback_p: r.pp_neibu ?? 0 },
    { k: '外部投诉', short: '外诉', fallback_s: r.sp_waibu, fallback_p: r.pp_waibu ?? 0 },
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
  const w = r.weights || { '咨询接待': 1.0, '套餐专项': 1.8, '退款处理': 2.0, '退款挽回': 2.2, '内部投诉': 3.0, '外部投诉': 5.0 }
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
    { k: '退款挽回', short: '挽回', color: '#e67e22', fallback_s: r.sp_wanhui, fallback_p: r.pp_wanhui ?? 0 },
    { k: '退款处理', short: '退款', color: '#e6a23c', fallback_s: r.sp_tuikuan, fallback_p: r.pp_tuikuan ?? 0 },
    { k: '内部投诉', short: '内诉', color: '#e74c3c', fallback_s: r.sp_neibu, fallback_p: r.pp_neibu ?? 0 },
    { k: '外部投诉', short: '外诉', color: '#c0392b', fallback_s: r.sp_waibu, fallback_p: r.pp_waibu ?? 0 },
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
  const coefHtml = `<div class="stp-coef">难度系数: 咨询×1.0 · 套餐×1.8 · 挽回×2.2 · 退款×2.0 · 内诉×3.0 · 外诉×5.0 · 转接=固定×${xfer}</div>`
  // 六专项明细行
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
// 将 ISO 字符串 (例 2026-08-13T09:25:10+08:00) 转为 "MM-DD HH:mm" 看板友好时间
function formatShortTime(s) {
  if (!s) return '—'
  try {
    const d = dayjs(s)
    if (!d.isValid()) return String(s).slice(0, 16).replace('T', ' ')
    return d.format('MM-DD HH:mm')
  } catch (e) {
    return String(s).slice(0, 16).replace('T', ' ')
  }
}

// ---------- 日期切换 ----------
function onQuickDate() {
  customDate.value = ''
  scheduleLoadAll()
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
  scheduleLoadAll()
}

// ---------- 数据加载 ----------
// 性能优化(2026-08-11):
//   1. 防抖 300ms: 切换聚合粒度时 watch(grain) 会重置 trendGrain, 进而触发 watch([...]) 两次,
//      防抖可合并同 tick 内的多次触发, 避免 loadAll 被连续调用 (原: 1次切换=14个请求)
//   2. AbortController: 连续切换时取消未完成的旧请求, 避免后端 LATERAL JOIN 堆积打满连接池
let _loadAllAbort = null    // AbortController, 取消上一批未完成请求
let _loadAllTimer = null    // 防抖定时器
function scheduleLoadAll(delay = 300) {
  // 取消上一批未完成请求 (含 100s 的 staff 接口)
  if (_loadAllAbort) {
    _loadAllAbort.abort()
    _loadAllAbort = null
  }
  // 合并同 tick 内的多次触发 (grain 变化 + trendGrain 重置 = 2 次触发)
  if (_loadAllTimer) clearTimeout(_loadAllTimer)
  _loadAllTimer = setTimeout(() => {
    _loadAllTimer = null
    loadAll()
  }, delay)
}

async function loadAll() {
  // 创建本批请求的 AbortController, 保存到外层以便 scheduleLoadAll 能取消
  const ac = new AbortController()
  _loadAllAbort = ac
  const signal = ac.signal
  loading.value = true
  const params = currentParams.value
  try {
    // 人员筛选参数仅在前端对人均效率表格进行过滤，后端不传筛选参数获取全量数据
    const staffParams = {
      ...params,
      qw_only: staffFilter.qwOnly ? 1 : 0,
      aggregate_by: staffFilter.aggregateBy || 'person',
    }
    const cfg = { signal }
    const [k, h, r, rf, q, s, rr] = await Promise.all([
      today.kpi(params, cfg),
      today.hourlyTrend(params, cfg),
      today.reception(params, cfg),
      today.refund(params, cfg),
      today.queue(params, cfg),
      today.staff(staffParams, cfg),
      today.refundRecovery(params, cfg),
    ])
    // 如果已经被下一批请求取消, 丢弃本批结果
    if (signal.aborted) return
    kpiData.value = k.data || {}
    // 向后兼容：后端v4接口返回 A/B/C/D/E 全部字段；旧接口只返回 refund_count/amount(=合计) 或 direct/withdraw(A/B)
    const hd = h.data || {}
    const emptyArr = (len) => Array(len || 0).fill(0)
    const dataLen = (hd.hour_labels || hd.slot_labels || []).length
    // 新接口字段: refund_count_a/b/c/d, refund_amount_a/b/c/d/e, cashflow_e_count/amount
    // 旧接口兼容: refund_count_direct→a, refund_count_withdraw→b, refund_count_total→total
    hourlyData.value = {
      hour_labels: hd.hour_labels || hd.slot_labels || [],
      // === 工作量组 ===
      reception: hd.reception || [],
      reception_devices: hd.reception_devices || [],
      // === 款项组·笔数: 四路 A/B/C/D ===
      refund_count: hd.refund_count ?? hd.refund_count_total ?? [],  // 合计(旧兼容)
      refund_count_a: hd.refund_count_a ?? hd.refund_count_direct  ?? emptyArr(dataLen),
      refund_count_b: hd.refund_count_b ?? hd.refund_count_withdraw ?? emptyArr(dataLen),
      refund_count_c: hd.refund_count_c ?? emptyArr(dataLen),
      refund_count_d: hd.refund_count_d ?? emptyArr(dataLen),
      refund_count_total: hd.refund_count_total ?? hd.refund_count ?? emptyArr(dataLen),
      // === 款项组·金额: 四路 A/B/C/D + E(实际现金流) ===
      refund_amount: hd.refund_amount ?? hd.refund_amount_total ?? [],  // 合计(旧兼容)
      refund_amount_a: hd.refund_amount_a ?? hd.refund_amount_direct  ?? emptyArr(dataLen),
      refund_amount_b: hd.refund_amount_b ?? hd.refund_amount_withdraw ?? emptyArr(dataLen),
      refund_amount_c: hd.refund_amount_c ?? emptyArr(dataLen),
      refund_amount_d: hd.refund_amount_d ?? emptyArr(dataLen),
      refund_amount_e: hd.refund_amount_e ?? hd.cashflow_e_amount ?? emptyArr(dataLen),
      refund_amount_total: hd.refund_amount_total ?? hd.refund_amount ?? emptyArr(dataLen),
      cashflow_e_count: hd.cashflow_e_count ?? emptyArr(dataLen),
      cashflow_e_amount: hd.cashflow_e_amount ?? hd.refund_amount_e ?? emptyArr(dataLen),
      // === 投诉组 ===
      complaint: hd.complaint || [],
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
      complaint_handlers: rdata.complaint_handlers || [],
      complaint_status_all: rdata.complaint_status_all || [],
      complaint_pending_total: rdata.complaint_pending_total || 0,
      complaint_pending_skipped: !!rdata.complaint_pending_skipped,
      complaint_pending_skip_reason: rdata.complaint_pending_skip_reason || '',
      complaint_pending_list: rdata.complaint_pending_list || [],
    }
    refundData.value = rf.data || { reasons: [], methods: [] }
    queueData.value = q.data || { todos: [], tasks: [], incidents: [] }
    // 人均效率: 后端 range 模式 → {date, date_end, data: [...]}; 单日旧模式 → [...]
    // 保存全量原始数据到 rawStaffRows，前端通过 computed staffRows 进行筛选
    const sr = s.data
    if (sr && Array.isArray(sr)) {
      rawStaffRows.value = sr
    } else if (sr && Array.isArray((sr).data)) {
      rawStaffRows.value = (sr).data
    } else {
      rawStaffRows.value = []
    }
    // 退款挽回专项分析
    recoveryData.value = rr.data || { kpi: emptyKpi(), reason_dist: [], staff_detail: [], weekly_trend: [], daily_reason: [] }
  } catch (e) {
    // 被取消的请求不算错误, 不打日志不弹提示
    if (signal.aborted || e?.code === 'ERR_CANCELED' || e?.name === 'CanceledError') return
    console.error('[Today] load error', e)
  } finally {
    // 只有当前批是最新的 (未被后续取消) 才清 loading
    if (_loadAllAbort === ac) {
      loading.value = false
      _loadAllAbort = null
    }
  }
}

// 时间粒度/快捷范围/聚合粒度变化都需要重新请求后端
// 通过 scheduleLoadAll 防抖合并 + 取消旧请求, 避免 watch(grain) 重置 trendGrain 导致的重复触发
watch([grain, quickDate, weekOffset, monthOffset, quarterOffset, yearOffset, customDate, trendGrain, customDateRange], () => {
  scheduleLoadAll()
})

// 切换聚合方式：切到非"按人"模式时清空人员筛选
function onAggregateChange() {
  if (staffFilter.aggregateBy !== 'person') {
    filterDepartment.value = ''
    filterGroupId.value = null
    filterSpecialtyId.value = null
  }
  scheduleLoadAll()
}

// 部门/分组/专项筛选：自动切换到"按人"明细视图（只有按人模式支持筛选）
watch([filterDepartment, filterGroupId, filterSpecialtyId], () => {
  const hasFilter = filterDepartment.value || filterGroupId.value || filterSpecialtyId.value
  if (hasFilter && staffFilter.aggregateBy !== 'person') {
    staffFilter.aggregateBy = 'person'
    scheduleLoadAll() // 切换聚合模式需要后端重新返回对应数据
  }
})

// 组件卸载时清理: 取消未完成请求 + 清防抖定时器, 避免泄漏和卸载后状态写入
onBeforeUnmount(() => {
  if (_loadAllAbort) { _loadAllAbort.abort(); _loadAllAbort = null }
  if (_loadAllTimer) { clearTimeout(_loadAllTimer); _loadAllTimer = null }
})

// 加载分组和专项列表
async function loadFilterOptions() {
  try {
    const [gRes, sRes] = await Promise.all([listGroups(), listSpecialties()])
    groupsList.value = gRes.data || []
    specialtiesList.value = sRes.data || []
  } catch (e) {
    console.error('[Today] load filter options error', e)
  }
}

onMounted(() => {
  loadFilterOptions()
  loadAll()
})
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
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.mt-12 { margin-top: 12px; }
.h-full { height: 100%; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.text-muted { color: #909399; }
.mr-8 { margin-right: 8px; }

/* ---------- 模块2 实时节奏 分组标签条 ---------- */
.rhythm-group-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 14px 10px;
  flex-wrap: wrap;
}
.rhythm-group-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  padding: 4px 12px;
  border-radius: 20px;
}
.rhythm-group-tag.workload {
  background: linear-gradient(135deg, rgba(102,126,234,0.1), rgba(118,75,162,0.08));
  color: #667eea;
}
.rhythm-group-tag.payment {
  background: linear-gradient(135deg, rgba(245,108,108,0.08), rgba(230,162,60,0.06));
  color: #f56c6c;
}
.rhythm-group-tag.complaint {
  background: linear-gradient(135deg, rgba(230,126,34,0.1), rgba(231,76,60,0.08));
  color: #e67e22;
}
.rhythm-group-tag .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.workload-dot { background: linear-gradient(135deg, #667eea, #764ba2); }
.payment-dot { background: linear-gradient(135deg, #f56c6c, #e6a23c); }
.complaint-dot { background: #e67e22; }
.group-desc {
  font-size: 11px;
  font-weight: 400;
  opacity: 0.75;
  margin-left: 4px;
}
.rhythm-divider {
  width: 1px;
  height: 16px;
  background: #e5e7eb;
}

/* ---------- 模块8 退款挽回 KpiCardMini 内联样式 ---------- */
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
.kpi-mini-label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.2px;
  line-height: 1.3;
}
.kpi-mini-sub {
  font-size: 10.5px;
  color: #6b7280;
  line-height: 1.3;
  opacity: 0.9;
}
.kpi-mini-value {
  margin-top: 2px;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.3px;
}
.kpi-mini-unit {
  font-size: 13px;
  font-weight: 500;
  margin-left: 2px;
  opacity: 0.85;
}
.kpi-mini-money {
  margin-top: 1px;
  font-size: 11.5px;
  font-weight: 500;
  color: #374151;
  font-variant-numeric: tabular-nums;
  opacity: 0.92;
}

/* 模块7 六专项明细胶囊 (zx咨询/tc套餐/wh挽回/tk退款/ni内诉/wa外诉) */
.sp-legend { display: flex; flex-wrap: wrap; gap: 4px 6px; line-height: 1.35; }
.sp-chip {
  display: inline-flex; align-items: center;
  padding: 1px 6px; border-radius: 999px;
  font-size: 10.5px; letter-spacing: -0.2px;
  border: 1px solid transparent;
}
.sp-chip.zx { background: #ecf5ff; color: #409eff; border-color: #d9ecff; }
.sp-chip.tc { background: #f0f9eb; color: #67c23a; border-color: #e1f3d8; }
.sp-chip.wh { background: #fef3e2; color: #e67e22; border-color: #fde2c0; }
.sp-chip.tk { background: #fdf6ec; color: #e6a23c; border-color: #faecd8; }
.sp-chip.ni { background: #fef0f0; color: #e74c3c; border-color: #fde2e2; }
.sp-chip.wa { background: #fdecea; color: #c0392b; border-color: #f9cdc7; }

/* 模块7 六专项列 极简显示: n次(m个) */
.sp-txt { font-size: 12.5px; font-weight: 500; letter-spacing: -0.2px; line-height: 1.6; }
.sp-txt.zx { color: #409eff; }
.sp-txt.tc { color: #67c23a; }
.sp-txt.wh { color: #e67e22; }   /* 退款挽回 橙色 */
.sp-txt.tk { color: #e6a23c; }   /* 退款处理 浅橙 */
.sp-txt.ni { color: #e74c3c; }   /* 内部投诉 红色 */
.sp-txt.wa { color: #c0392b; }   /* 外部投诉 深红 */
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
