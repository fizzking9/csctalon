<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">投诉分析</div>
        <div class="page-sub">三源统一统计 · 管理摘要 · 外部投诉重点 · 商户号投诉 · 受理分析</div>
      </div>
      <div class="filter-bar">
        <el-radio-group v-model="rangeMode" size="small">
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
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
        />
        <el-button type="primary" :icon="Search" :loading="loading" @click="loadData">分析</el-button>
      </div>
    </div>

    <!-- 未加载时的提示 -->
    <section v-if="!loaded" class="empty-hint">
      <el-empty :image-size="80" description="">
        <template #description>
          <div class="empty-tip">
            <div class="empty-main">选择时间范围后点击 <el-button size="small" type="primary" :icon="Search">分析</el-button> 加载数据</div>
            <div class="empty-sub">按需加载 · 避免进入页面即产生大量请求</div>
          </div>
        </template>
      </el-empty>
    </section>

    <template v-if="loaded && data">
      <!-- ============ TAB: 决策概览 ============ -->
      <section v-if="activeTab === 'overview'" class="block-card">
        <div class="block-head">
          <span class="block-title">管理摘要</span>
          <span class="block-hint">自动结论 · 随筛选区间更新</span>
        </div>
        <el-row :gutter="12" class="mb-12">
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#eff6ff">
              <div class="kpi-label" style="color:#2563eb">投诉总量</div>
              <div class="kpi-value">{{ formatNum(s.summary.total) }}</div>
              <div class="kpi-sub">内{{ s.summary.internal_total }} · 商{{ s.summary.merchant_total }} · 外{{ s.summary.external_total }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card kpi-clickable" style="background:#fef2f2" @click="activeTab = 'backlog'">
              <div class="kpi-label" style="color:#dc2626">积压未处理</div>
              <div class="kpi-value">{{ formatNum(b.total) }}</div>
              <div class="kpi-sub">内{{ bySourceCount('内部投诉') }} · 商{{ bySourceCount('商户号投诉') }} · 外{{ bySourceCount('外部投诉') }} · 点击下钻</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f0fdf4">
              <div class="kpi-label" style="color:#16a34a">完成率</div>
              <div class="kpi-value">{{ (s.summary.completion_rate * 100).toFixed(1) }}<span class="unit">%</span></div>
              <div class="kpi-sub">已完成 {{ formatNum(s.summary.done_total) }} 条</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fff7ed">
              <div class="kpi-label" style="color:#ea580c">平均处理时长</div>
              <div class="kpi-value">{{ s.summary.avg_handle_hours > 24 ? (s.summary.avg_handle_hours / 24).toFixed(1) + '天' : s.summary.avg_handle_hours + 'h' }}</div>
              <div class="kpi-sub">有真实完成时间的工单</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f5f3ff">
              <div class="kpi-label" style="color:#7c3aed">外部投诉</div>
              <div class="kpi-value">{{ formatNum(s.external_total) }}</div>
              <div class="kpi-sub">315平台 · 关联{{ formatNum(s.external_matched) }} · 未关联{{ formatNum(s.external_unmatched) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fefce8">
              <div class="kpi-label" style="color:#ca8a04">商户号投诉</div>
              <div class="kpi-value">{{ formatNum(s.summary.merchant_total) }}</div>
              <div class="kpi-sub">{{ merchantEntitySummary }}</div>
            </div>
          </el-col>
        </el-row>

        <!-- 积压快照(可下钻: 三源 + 龄期 + 明细) -->
        <div class="block-head" style="margin-top:4px">
          <span class="block-title" style="font-size:14px">积压快照</span>
          <span class="block-hint">窗口内登记的未完成(默认近30天, 跟随时间筛选) · 三源拆分 · 各来源积压龄期 · 最近积压明细</span>
          <div style="flex:1" />
          <el-button size="small" type="primary" plain @click="activeTab = 'backlog'">查看全部积压 →</el-button>
        </div>
        <el-row :gutter="12" class="mb-16">
          <el-col :xs="24" :md="7">
            <div class="chart-card">
              <div class="chart-title">积压三源 + 关键指标</div>
              <div class="snapshot-kpi-row">
                <div class="snapshot-kpi" style="color:#dc2626">
                  <div class="snapshot-kpi-v">{{ formatNum(b.total) }}</div>
                  <div class="snapshot-kpi-l">积压总量</div>
                </div>
                <div class="snapshot-kpi" style="color:#ea580c">
                  <div class="snapshot-kpi-v">{{ formatNum(b.overdue) }}</div>
                  <div class="snapshot-kpi-l">超期(&gt;7天)</div>
                </div>
                <div class="snapshot-kpi" style="color:#7c3aed">
                  <div class="snapshot-kpi-v">{{ formatNum(b.unassigned) }}</div>
                  <div class="snapshot-kpi-l">待分派</div>
                </div>
                <div class="snapshot-kpi" style="color:#2563eb">
                  <div class="snapshot-kpi-v">{{ b.max_aging != null ? b.max_aging : '—' }}</div>
                  <div class="snapshot-kpi-l">最旧(天)</div>
                </div>
              </div>
              <div class="snapshot-source-bars">
                <div v-for="src in ['内部投诉', '商户号投诉', '外部投诉']" :key="src" class="snapshot-source-row">
                  <span class="snapshot-source-name">{{ src }}</span>
                  <div class="snapshot-source-track">
                    <div class="snapshot-source-fill" :style="{ width: sourceRatioPct(src), background: sourceBarColor(src) }" />
                  </div>
                  <span class="snapshot-source-cnt">{{ formatNum(bySourceCount(src)) }}</span>
                </div>
              </div>
            </div>
          </el-col>
          <el-col :xs="24" :md="9">
            <div class="chart-card">
              <div class="chart-title">来源×龄期（各来源积压多久）</div>
              <BaseChart v-if="backlogSourceAgingOption" :option="backlogSourceAgingOption" height="190px" />
              <el-empty v-else description="暂无数据" :image-size="36" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">最近积压 Top5（最旧优先）</div>
              <div v-if="backlogSnapshotItems.length" class="snapshot-list">
                <div v-for="(it, i) in backlogSnapshotItems" :key="i" class="snapshot-row">
                  <span class="snapshot-rank">{{ i + 1 }}</span>
                  <el-tag :type="sourceTagType(it.source)" size="small" effect="plain">{{ it.source }}</el-tag>
                  <span class="snapshot-no">{{ it.ticket_no }}</span>
                  <el-link v-if="it.device_sn" type="primary" :underline="false" class="snapshot-sn" @click="openDeviceAudit(it.device_sn)">{{ it.device_sn }}</el-link>
                  <el-tag v-if="it.aging_days != null" size="small" :type="it.aging_days >= 8 ? 'danger' : it.aging_days >= 4 ? 'warning' : 'info'">{{ it.aging_days }}天</el-tag>
                </div>
              </div>
              <el-empty v-else description="无积压" :image-size="36" />
            </div>
          </el-col>
        </el-row>

        <!-- 投诉水位(新增投诉/处理投诉/净水位 = 积压增减的流量视角) -->
        <div class="block-head" style="margin-top:4px">
          <span class="block-title" style="font-size:14px">投诉水位</span>
          <span class="block-hint">新增投诉=窗口内按日新增登记 · 处理投诉=窗口内登记且已完成(与新增对称) · 净水位=新增-处理 · 累计净水位最终值=当前积压量(应与上方积压快照一致)</span>
        </div>
        <div class="chart-card mb-16">
          <BaseChart v-if="complaintWaterOption" :option="complaintWaterOption" height="260px" />
          <el-empty v-else description="暂无水位数据" :image-size="40" />
        </div>

        <!-- 来源分布饼图 + 趋势堆叠图 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">投诉来源分布</div>
              <BaseChart v-if="sourcePieOption" :option="sourcePieOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="16">
            <div class="chart-card">
              <div class="chart-title">三源趋势(按日)</div>
              <BaseChart v-if="trendStackOption" :option="trendStackOption" height="260px" />
              <el-empty v-else description="暂无趋势数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 处理人 Top10 + 时效分布 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">处理人 Top10</div>
              <BaseChart v-if="handlerBarOption" :option="handlerBarOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">处理时效分布</div>
              <BaseChart v-if="timelinessOption" :option="timelinessOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>
      </section>

      <!-- ============ TAB: 外部投诉重点 ============ -->
      <section v-if="activeTab === 'external'" class="block-card">
        <div class="block-head">
          <span class="block-title">外部投诉专项分析</span>
          <span class="block-hint">按投诉日期统计 · 今日登记见卡片 · 明细表可看登记时间</span>
        </div>

        <!-- 外部投诉 KPI -->
        <el-row :gutter="12" class="mb-16">
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#fef2f2">
              <div class="kpi-label" style="color:#dc2626">外部投诉总量</div>
              <div class="kpi-value">{{ formatNum(s.external_total) }}</div>
              <div class="kpi-sub">按投诉日期 · 覆盖全部平台</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#fefce8">
              <div class="kpi-label" style="color:#ca8a04">今日登记</div>
              <div class="kpi-value">{{ formatNum(s.external_today_registered) }}</div>
              <div class="kpi-sub">今天同步/登记进来的</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#f0fdf4">
              <div class="kpi-label" style="color:#16a34a">已关联工单</div>
              <div class="kpi-value">{{ formatNum(s.external_matched) }}</div>
              <div class="kpi-sub">已落工单跟踪</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6">
            <div class="kpi-card" style="background:#fffbeb">
              <div class="kpi-label" style="color:#b45309">未关联工单</div>
              <div class="kpi-value">{{ formatNum(s.external_unmatched) }}</div>
              <div class="kpi-sub">尚未落工单 · 需跟进 · {{ platformSummary }}</div>
            </div>
          </el-col>
        </el-row>

        <!-- 平台分布 + 问题分类 + 平台趋势 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">外部投诉平台分布</div>
              <BaseChart v-if="platformPieOption" :option="platformPieOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">投诉问题分类</div>
              <BaseChart v-if="externalCategoryOption" :option="externalCategoryOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">外部投诉趋势(按日)</div>
              <BaseChart v-if="externalTrendOption" :option="externalTrendOption" height="260px" />
              <el-empty v-else description="暂无趋势数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 外部投诉明细表(315表, 完整内容) -->
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span style="font-weight:600">外部投诉明细（{{ externalDetail.length }} 条）</span>
              <el-button size="small" @click="loadExternalDetail">刷新</el-button>
            </div>
          </template>
          <el-table :data="externalDetail" v-loading="extDetailLoading" border stripe size="small" max-height="400">
            <el-table-column label="投诉日期" width="100" sortable>
              <template #default="{ row }">{{ formatTime(row.complaint_date) }}</template>
            </el-table-column>
            <el-table-column label="登记时间" width="100">
              <template #default="{ row }">
                <span v-if="row.created_at" class="dim-text">{{ formatTime(row.created_at) }}</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="platform" label="平台" width="120" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag size="small" effect="plain" type="danger">{{ row.platform || '未标注' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="complaint_no" label="投诉编号" width="150" show-overflow-tooltip />
            <el-table-column label="投诉内容" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="problem-text">{{ row.problem || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="complaint_category" label="问题分类" width="110" show-overflow-tooltip />
            <el-table-column prop="entity" label="被投诉主体" width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.entity || row.company_entity || '—' }}</template>
            </el-table-column>
            <el-table-column prop="device_sn" label="设备SN" width="130">
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="处理/跟进" width="100" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link v-if="row.handler" type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="结果" width="100" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag v-if="row.result" size="small" :type="row.result.includes('结案') ? 'success' : 'info'" effect="plain">{{ row.result }}</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="关联工单" width="130">
              <template #default="{ row }">
                <el-link v-if="row.matched_ticket_no" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.matched_ticket_no }}</el-link>
                <el-tag v-else size="small" type="warning" effect="plain">未关联</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </section>

      <!-- ============ TAB: 商户号投诉 ============ -->
      <section v-if="activeTab === 'merchant'" class="block-card">
        <div class="block-head">
          <span class="block-title">商户号投诉分析</span>
          <span class="block-hint">微信投诉 · 被投诉主体(商户号)与处理人(客服)分开统计</span>
        </div>

        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">被投诉主体分布（商户号）</div>
              <BaseChart v-if="merchantEntityOption" :option="merchantEntityOption" height="300px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">处理人 Top（客服）</div>
              <BaseChart v-if="merchantHandlerOption" :option="merchantHandlerOption" height="300px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 被投诉主体趋势(堆叠面积, 商户号内细分到具体商户号; 支持按日/周/月) -->
        <div class="chart-card mb-16">
          <div class="flex-between">
            <div class="chart-title">被投诉主体趋势（堆叠面积）<span class="dim-text"> · 商户号内细分, 云客猫/大秦WiFi管家等按商户号分开</span></div>
            <el-radio-group v-model="merchantEntityTrendGrain" size="small">
              <el-radio-button label="day">按日</el-radio-button>
              <el-radio-button label="week">按周</el-radio-button>
              <el-radio-button label="month">按月</el-radio-button>
            </el-radio-group>
          </div>
          <BaseChart v-if="merchantEntityTrendOption" :option="merchantEntityTrendOption" height="280px" />
          <el-empty v-else description="暂无趋势数据" :image-size="40" />
        </div>

        <!-- 商户号投诉明细表 -->
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span style="font-weight:600">商户号投诉明细（{{ merchantDetail.length }} 条）</span>
              <el-button size="small" @click="loadMerchantDetail">刷新</el-button>
            </div>
          </template>
          <el-table :data="merchantDetail" v-loading="merDetailLoading" border stripe size="small" max-height="400">
            <el-table-column label="时间" width="120" sortable>
              <template #default="{ row }">{{ formatTime(row.time) }}</template>
            </el-table-column>
            <el-table-column label="被投诉主体" width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag v-if="row.entity" size="small" effect="plain" type="warning">{{ row.entity }}</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="分类" width="110" show-overflow-tooltip />
            <el-table-column prop="problem" label="投诉内容" min-width="220" show-overflow-tooltip />
            <el-table-column prop="device_sn" label="设备SN" width="130">
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="处理人" width="100">
              <template #default="{ row }">
                <el-link v-if="row.handler && row.handler !== '未分配'" type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.status === '已完成' ? 'success' : row.status === '处理中' ? 'warning' : 'danger'" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </section>

      <!-- ============ TAB: 受理分析 ============ -->
      <section v-if="activeTab === 'handling'" class="block-card">
        <div class="block-head">
          <span class="block-title">受理分析</span>
          <span class="block-hint">内/商/外 三源为不同维度任务 · 处理人效率分组展示</span>
        </div>

        <!-- 三源切换 -->
        <el-radio-group v-model="effSource" size="small" class="mb-12">
          <el-radio-button label="internal">内部投诉</el-radio-button>
          <el-radio-button label="merchant">商户号投诉</el-radio-button>
          <el-radio-button label="external">外部投诉</el-radio-button>
        </el-radio-group>

        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="16">
            <div class="chart-card">
              <div class="chart-title">处理人效率排行（{{ effSourceLabel }}）</div>
              <BaseChart v-if="handlerEffOption" :option="handlerEffOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">处理时效分布</div>
              <BaseChart v-if="timelinessOption" :option="timelinessOption" height="260px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 处理人效率明细表(按三源) -->
        <el-card shadow="never" class="mb-16">
          <template #header><span style="font-weight:600">处理人效率明细（{{ effSourceLabel }}）</span></template>
          <el-table :data="currentEff" border stripe size="small" max-height="300">
            <el-table-column label="排名" width="60" type="index" />
            <el-table-column prop="name" label="处理人" width="120" />
            <el-table-column label="受理总数" width="110" align="right">
              <template #default="{ row }">{{ formatNum(row.total) }}</template>
            </el-table-column>
            <el-table-column label="已完成" width="110" align="right">
              <template #default="{ row }">{{ formatNum(row.done) }}</template>
            </el-table-column>
            <el-table-column label="完成率" width="110" align="right">
              <template #default="{ row }">{{ row.total > 0 ? (row.done / row.total * 100).toFixed(1) + '%' : '—' }}</template>
            </el-table-column>
            <el-table-column label="平均处理时长" width="140" align="right">
              <template #default="{ row }">{{ row.avg_hours > 0 ? (row.avg_hours > 24 ? (row.avg_hours / 24).toFixed(1) + '天' : row.avg_hours.toFixed(1) + 'h') : '—' }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </section>

      <!-- ============ TAB: 积压审核 ============ -->
      <section v-if="activeTab === 'backlog'" class="block-card">
        <div class="block-head">
          <span class="block-title">积压未处理投诉审核</span>
          <span class="block-hint">统计窗口内登记的未完成(跟随页面时间范围, 默认近30天) · 归一化状态非已完成 · 默认最旧积压优先 · 支持核查留痕(归因/评判/结论)</span>
        </div>

        <!-- 筛选 -->
        <div class="filter-bar mb-12">
          <el-select v-model="backlogFilters.source_type" size="small" placeholder="来源" clearable style="width:130px" @change="backlogPage = 1; loadBacklog()">
            <el-option label="内部投诉" value="内部投诉" />
            <el-option label="商户号投诉" value="商户号投诉" />
            <el-option label="外部投诉" value="外部投诉" />
          </el-select>
          <el-select v-model="backlogFilters.status" size="small" placeholder="状态" clearable style="width:110px" @change="backlogPage = 1; loadBacklog()">
            <el-option label="处理中" value="处理中" />
            <el-option label="待处理" value="待处理" />
            <el-option label="待跟进" value="待跟进" />
          </el-select>
          <el-select v-model="backlogFilters.has_handler" size="small" placeholder="分派" clearable style="width:110px" @change="backlogPage = 1; loadBacklog()">
            <el-option label="待分派" :value="false" />
            <el-option label="已分派" :value="true" />
          </el-select>
          <el-button size="small" :icon="Refresh" @click="loadBacklog">刷新</el-button>
        </div>

        <!-- KPI -->
        <el-row :gutter="12" class="mb-16">
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fef2f2">
              <div class="kpi-label" style="color:#dc2626">积压总量</div>
              <div class="kpi-value">{{ formatNum(b.total) }}</div>
              <div class="kpi-sub">内{{ b.by_source?.find(x => x.name === '内部投诉')?.count ?? 0 }} · 商{{ b.by_source?.find(x => x.name === '商户号投诉')?.count ?? 0 }} · 外{{ b.by_source?.find(x => x.name === '外部投诉')?.count ?? 0 }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fff7ed">
              <div class="kpi-label" style="color:#ea580c">超期积压(&gt;7天)</div>
              <div class="kpi-value">{{ formatNum(b.overdue) }}</div>
              <div class="kpi-sub">登记满7天仍未完成 · 优先审核</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fffbeb">
              <div class="kpi-label" style="color:#b45309">待分派</div>
              <div class="kpi-value">{{ formatNum(b.unassigned) }}</div>
              <div class="kpi-sub">无真实处理人 · 无人认领</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f0fdf4">
              <div class="kpi-label" style="color:#16a34a">已核查</div>
              <div class="kpi-value">{{ formatNum(b.audited) }}</div>
              <div class="kpi-sub">已落核查记录 · 避免重复</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#eff6ff">
              <div class="kpi-label" style="color:#2563eb">最旧积压</div>
              <div class="kpi-value">{{ oldestAging }}<span class="unit">天</span></div>
              <div class="kpi-sub">当前未处理工单最大龄期</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f5f3ff">
              <div class="kpi-label" style="color:#7c3aed">处理中占比</div>
              <div class="kpi-value">{{ doingPct }}<span class="unit">%</span></div>
              <div class="kpi-sub">已有处理人的积压占比</div>
            </div>
          </el-col>
        </el-row>

        <!-- 图表 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">积压来源 × 龄期（各来源积压多久）</div>
              <BaseChart v-if="backlogSourceAgingOption" :option="backlogSourceAgingOption" height="240px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">积压龄期分布</div>
              <BaseChart v-if="backlogAgingOption" :option="backlogAgingOption" height="240px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">积压投诉原因分布<span class="dim-text"> · 按内容关键词归类</span></div>
              <BaseChart v-if="backlogReasonOption" :option="backlogReasonOption" height="240px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 积压明细表 -->
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span style="font-weight:600">积压明细（{{ backlogTotal }} 条）</span>
              <div class="flex gap-8">
                <el-tag size="small" type="warning">超期 {{ b.overdue }}</el-tag>
                <el-tag size="small" type="info">待分派 {{ b.unassigned }}</el-tag>
                <el-tag size="small" type="success">已核查 {{ b.audited }}</el-tag>
                <el-button size="small" type="primary" plain :icon="Download" :loading="backlogExporting" @click="handleBacklogExport">导出Excel</el-button>
              </div>
            </div>
          </template>
          <el-table :data="backlogItems" v-loading="backlogLoading" border stripe size="small" max-height="420">
            <el-table-column prop="ticket_no" label="工单号" width="150" show-overflow-tooltip />
            <el-table-column label="来源" width="90">
              <template #default="{ row }">
                <el-tag :type="sourceTagType(row.source)" size="small" effect="plain">{{ row.source }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="登记时间" width="110" sortable>
              <template #default="{ row }">{{ formatTime(row.register_time) }}</template>
            </el-table-column>
            <el-table-column label="积压天数" width="90" sortable>
              <template #default="{ row }">
                <el-tag v-if="row.aging_days != null" size="small" :type="row.aging_days >= 8 ? 'danger' : row.aging_days >= 4 ? 'warning' : 'info'">{{ row.aging_days }}天</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="category" label="分类" width="130" show-overflow-tooltip />
            <el-table-column label="投诉原因" width="100" show-overflow-tooltip>
              <template #default="{ row }">
                <el-tag v-if="row.reason && row.reason !== '未填写'" size="small" effect="plain" :type="reasonTagType(row.reason)">{{ row.reason }}</el-tag>
                <span v-else class="dim">{{ row.reason === '未填写' ? '未填写' : '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="设备SN" width="130">
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" :underline="false" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="problem" label="投诉内容" min-width="180" show-overflow-tooltip />
            <el-table-column label="处理人" width="100">
              <template #default="{ row }">
                <el-link v-if="row.handler !== '未分配'" type="primary" :underline="false" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="dim">未分配</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.status === '处理中' ? 'warning' : row.status === '待跟进' ? 'info' : 'danger'" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="核查" width="70">
              <template #default="{ row }">
                <el-tag v-if="row.audited" size="small" type="success" effect="plain">已核查</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.device_sn" size="small" text type="primary" @click="openDeviceAudit(row.device_sn)">时序</el-button>
                <el-button size="small" text type="warning" @click="openAudit(row)">核查</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pag-wrap">
            <el-pagination
              v-model:current-page="backlogPage"
              v-model:page-size="backlogPageSize"
              :total="backlogTotal"
              :page-sizes="[20, 50, 100]"
              layout="total, sizes, prev, pager, next, jumper"
              background
              small
              @current-change="loadBacklog"
              @size-change="backlogPage = 1; loadBacklog()"
            />
          </div>
        </el-card>

        <!-- 核查弹窗 -->
        <el-dialog v-model="auditVisible" title="积压投诉核查" width="480px" append-to-body>
          <el-form :model="auditForm" label-width="80px">
            <el-form-item label="来源">
              <el-select v-model="auditForm.source_type" style="width:100%">
                <el-option label="内部投诉" value="internal" />
                <el-option label="商户号投诉" value="merchant" />
                <el-option label="外部投诉" value="external" />
              </el-select>
            </el-form-item>
            <el-form-item label="工单号"><el-input v-model="auditForm.ref_id" disabled /></el-form-item>
            <el-form-item label="设备SN"><el-input v-model="auditForm.device_sn" disabled /></el-form-item>
            <el-form-item label="归因">
              <el-select v-model="auditForm.attribution" style="width:100%">
                <el-option v-for="a in attributions" :key="a" :label="a" :value="a" />
              </el-select>
            </el-form-item>
            <el-form-item label="评判">
              <el-radio-group v-model="auditForm.judgment">
                <el-radio-button label="通过" />
                <el-radio-button label="有问题" />
                <el-radio-button label="需改进" />
              </el-radio-group>
            </el-form-item>
            <el-form-item label="核查结论">
              <el-input v-model="auditForm.conclusion" type="textarea" :rows="4" placeholder="核查结论" />
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="auditVisible = false">取消</el-button>
            <el-button type="primary" :loading="auditSubmitting" @click="submitAudit">保存核查</el-button>
          </template>
        </el-dialog>
      </section>

      <!-- ============ TAB: 被投诉主体 ============ -->
      <section v-if="activeTab === 'subject'" class="block-card">
        <div class="block-head">
          <span class="block-title">被投诉主体分析</span>
          <span class="block-hint">商户号投诉=工单被投诉商户主体 · 外部投诉=315公司主体/投诉店铺 · 内部投诉无主体字段不纳入 · 升级链反映「内部处理不好→商户号→外部」</span>
        </div>

        <!-- KPI -->
        <el-row :gutter="12" class="mb-16">
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#eff6ff">
              <div class="kpi-label" style="color:#2563eb">识别主体数</div>
              <div class="kpi-value">{{ formatNum(subj.summary.subject_total) }}</div>
              <div class="kpi-sub">商户号+外部投诉去重</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#f5f3ff">
              <div class="kpi-label" style="color:#7c3aed">商户号投诉</div>
              <div class="kpi-value">{{ formatNum(subj.summary.merchant_total) }}</div>
              <div class="kpi-sub">有主体(工单registrant)</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fef2f2">
              <div class="kpi-label" style="color:#dc2626">外部投诉</div>
              <div class="kpi-value">{{ formatNum(subj.summary.external_total) }}</div>
              <div class="kpi-sub">识别主体{{ formatNum(subj.summary.external_attributed) }} · 无主体{{ formatNum(subj.summary.external_unattributed) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fefce8">
              <div class="kpi-label" style="color:#ca8a04">涉诉退款</div>
              <div class="kpi-value">{{ formatNum(subj.summary.refund_count) }}<span class="unit">笔</span></div>
              <div class="kpi-sub">主体投诉SN关联线上退款</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-card" style="background:#fff7ed">
              <div class="kpi-label" style="color:#ea580c">涉诉退款金额</div>
              <div class="kpi-value" style="font-size:18px">¥{{ formatNum(subj.summary.refund_amount) }}</div>
              <div class="kpi-sub">投诉处理不好最终导致退款</div>
            </div>
          </el-col>
        </el-row>

        <!-- 严重性数量分布 + 升级链漏斗 -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">被投诉主体来源分布<span class="dim-text"> · 商户号 vs 外部投诉占比</span></div>
              <BaseChart v-if="subjectSeverityOption" :option="subjectSeverityOption" height="240px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="16">
            <div class="chart-card">
              <div class="chart-title">投诉升级链（按设备SN）<span class="dim-text"> · 传递性: 内部处理不好→商户号→外部</span></div>
              <BaseChart v-if="escalationChainOption" :option="escalationChainOption" height="240px" />
              <el-empty v-else description="暂无数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 主体趋势 + Top5主体趋势 (支持按日/周/月汇聚) -->
        <el-row :gutter="16" class="mb-16">
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="flex-between">
                <div class="chart-title">主体投诉趋势<span class="dim-text"> · 商户号+外部投诉汇聚</span></div>
                <el-radio-group v-model="subjectTrendGrain" size="small">
                  <el-radio-button label="day">按日</el-radio-button>
                  <el-radio-button label="week">按周</el-radio-button>
                  <el-radio-button label="month">按月</el-radio-button>
                </el-radio-group>
              </div>
              <BaseChart v-if="subjectTrendOption" :option="subjectTrendOption" height="250px" />
              <el-empty v-else description="暂无趋势数据" :image-size="40" />
            </div>
          </el-col>
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="flex-between">
                <div class="chart-title">Top5 被投诉主体趋势<span class="dim-text"> · 汇聚后按主体总量排序</span></div>
                <el-radio-group v-model="topSubjectTrendGrain" size="small">
                  <el-radio-button label="day">按日</el-radio-button>
                  <el-radio-button label="week">按周</el-radio-button>
                  <el-radio-button label="month">按月</el-radio-button>
                </el-radio-group>
              </div>
              <BaseChart v-if="topSubjectTrendOption" :option="topSubjectTrendOption" height="250px" />
              <el-empty v-else description="暂无趋势数据" :image-size="40" />
            </div>
          </el-col>
        </el-row>

        <!-- 主体Top榜 -->
        <el-card shadow="never">
          <template #header>
            <div class="flex-between">
              <span style="font-weight:600">被投诉主体 Top（{{ subjectRanking.length }} 个）</span>
              <el-tag size="small" type="warning" effect="plain">升级标识 = 同一主体同时有商户号+外部投诉</el-tag>
            </div>
          </template>
          <el-table :data="subjectRanking" border stripe size="small" max-height="420">
            <el-table-column label="排名" width="60" type="index" />
            <el-table-column label="被投诉主体" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="subject-name">{{ row.name }}</span>
                <el-tag v-if="row.escalated" size="small" type="danger" effect="plain" style="margin-left:6px">升级</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="商户号投诉" width="100" align="right" sortable :sort-method="(a,b)=>a.merchant_count-b.merchant_count">
              <template #default="{ row }">
                <span v-if="row.merchant_count > 0" style="color:#7c3aed;font-weight:600">{{ formatNum(row.merchant_count) }}</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="外部投诉" width="100" align="right" sortable :sort-method="(a,b)=>a.external_count-b.external_count">
              <template #default="{ row }">
                <span v-if="row.external_count > 0" style="color:#dc2626;font-weight:600">{{ formatNum(row.external_count) }}</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="合计" width="90" align="right" sortable :sort-method="(a,b)=>a.total-b.total">
              <template #default="{ row }">{{ formatNum(row.total) }}</template>
            </el-table-column>
            <el-table-column label="外部投诉占比" width="110" align="right">
              <template #default="{ row }">
                <span :style="{ color: extRatioColor(row.external_count / row.total), fontWeight: 600 }">
                  {{ row.total > 0 ? (row.external_count / row.total * 100).toFixed(1) + '%' : '—' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="关联退款" width="100" align="right" sortable :sort-method="(a,b)=>a.refund_count-b.refund_count">
              <template #default="{ row }">
                <span v-if="row.refund_count > 0" style="color:#ca8a04;font-weight:600">{{ formatNum(row.refund_count) }}笔</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="关联退款金额" width="130" align="right">
              <template #default="{ row }">
                <span v-if="row.refund_amount > 0" style="color:#ea580c">¥{{ formatNum(row.refund_amount) }}</span>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </section>
    </template>
    <div v-if="loaded" class="block-tabs">
      <el-radio-group v-model="activeTab" size="small">
        <el-radio-button label="overview">决策概览</el-radio-button>
        <el-radio-button label="external">外部投诉重点</el-radio-button>
        <el-radio-button label="merchant">商户号投诉</el-radio-button>
        <el-radio-button label="handling">受理分析</el-radio-button>
        <el-radio-button label="backlog">积压审核</el-radio-button>
        <el-radio-button label="subject">被投诉主体</el-radio-button>
      </el-radio-group>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount } from 'vue'
import { Search, Refresh, Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
dayjs.extend(isoWeek)
import BaseChart from '@/components/BaseChart.vue'
import { complaintAnalysis, complaintDetail, complaintSupplementary, complaintBacklog, complaintBacklogExport, complaintSubjectAnalysis, createAudit } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'

const { openDeviceAudit, openPersonAudit } = useAudit()

const loaded = ref(false)
const loading = ref(false)
const extDetailLoading = ref(false)
const merDetailLoading = ref(false)
const activeTab = ref('overview')
const rangeMode = ref('30')
const customRange = ref([])
const data = ref(null)

// 移动端响应式: 简化描述文字/控件
const isMobile = ref(false)
let _mq = null
function _onMediaChange(e) { isMobile.value = e.matches }
onMounted(() => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
})
onBeforeUnmount(() => { _mq?.removeEventListener('change', _onMediaChange) })
// 趋势图粒度(按日/周/月): 周=ISO自然周, 月=自然月; 前端对后端按日数据做汇聚
const merchantEntityTrendGrain = ref('day')
const subjectTrendGrain = ref('day')
const topSubjectTrendGrain = ref('day')
const externalDetail = ref([])
const merchantDetail = ref([])
const effSource = ref('internal')

// ── 积压审核(backlog tab) ──
const backlogData = ref(null)
const backlogLoading = ref(false)
const backlogExporting = ref(false)
const backlogPage = ref(1)
const backlogPageSize = ref(50)
const backlogTotal = ref(0)
const backlogFilters = reactive({ source_type: '', status: '', has_handler: null })
const auditVisible = ref(false)
const auditSubmitting = ref(false)
const auditForm = reactive({ source_type: 'internal', ref_id: '', device_sn: '', attribution: '其他', judgment: '通过', conclusion: '' })
const attributions = ['网络质量', '套餐争议', '退款协商失败', '降成争议', '其他', '未关联SN']

// ── 被投诉主体分析(subject tab) ──
const subjectData = ref(null)
const subjectLoading = ref(false)

const b = computed(() => backlogData.value || {})
const backlogItems = computed(() => b.value.items || [])
const oldestAging = computed(() => (b.value.max_aging != null ? b.value.max_aging : '—'))

// ── 积压快照(决策概览) helpers ──
function bySourceCount(src) {
  return (b.value.by_source || []).find(x => x.name === src)?.count ?? 0
}
function sourceBarColor(src) {
  if (src === '内部投诉') return '#2563eb'
  if (src === '外部投诉') return '#dc2626'
  return '#7c3aed'
}
function sourceRatioPct(src) {
  const t = b.value.total || 0
  if (!t) return '0%'
  return Math.round(bySourceCount(src) / t * 100) + '%'
}
const backlogSnapshotItems = computed(() => backlogItems.value.slice(0, 5))
// 来源×龄期堆叠柱: x=来源, y=数量, 5个龄期桶堆叠
const backlogSourceAgingOption = computed(() => {
  const d = b.value.by_source_aging || []
  if (!d.length || d.every(x => (x.buckets || []).every(bk => !bk.count))) return null
  const buckets = d[0].buckets.map(bk => bk.name)
  const agingColors = ['#2563eb', '#3b82f6', '#f59e0b', '#ea580c', '#dc2626']
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, left: 'center', itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
    grid: { left: 44, right: 16, bottom: 30, top: 8 },
    xAxis: { type: 'category', data: d.map(x => x.source), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value' },
    series: buckets.map((bk, i) => ({
      name: bk, type: 'bar', stack: 'total',
      data: d.map(x => x.buckets.find(y => y.name === bk)?.count || 0),
      itemStyle: { color: agingColors[i] }, barMaxWidth: 22,
    })),
  }
})
const doingPct = computed(() => {
  const t = b.value.total || 0
  if (!t) return 0
  return ((t - (b.value.unassigned || 0)) / t * 100).toFixed(1)
})
const backlogAgingOption = computed(() => {
  const d = b.value.by_aging || []
  if (!d.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 48, right: 24, bottom: 32, top: 8 },
    xAxis: { type: 'category', data: d.map(x => x.name), axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '工单数' },
    series: [{
      type: 'bar', data: d.map(x => x.count), barMaxWidth: 28,
      itemStyle: { color: '#eab308', borderRadius: [4, 4, 0, 0] },
      label: { show: true, position: 'top', fontSize: 11 },
    }],
  }
})
// 投诉原因分布(按内容关键词归类, 替代区分度低的来源子类分类)
const reasonColors = {
  '退款/售后': '#dc2626',
  '网络/技术': '#2563eb',
  '套餐/计费': '#ea580c',
  '充值/规则': '#ca8a04',
  '其他': '#6b7280',
  '未填写': '#9ca3af',
}
function reasonTagType(reason) {
  if (reason === '退款/售后') return 'danger'
  if (reason === '网络/技术') return 'primary'
  if (reason === '套餐/计费') return 'warning'
  if (reason === '充值/规则') return 'warning'
  return 'info'
}
const backlogReasonOption = computed(() => {
  const d = b.value.by_reason || []
  if (!d.length || d.every(x => !x.count)) return null
  const items = [...d].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 90, right: 44, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar', data: items.map(x => ({
        value: x.count,
        itemStyle: { color: reasonColors[x.name] || '#6b7280', borderRadius: [0, 4, 4, 0] },
      })), barMaxWidth: 18,
      label: { show: true, position: 'right', fontSize: 11 },
    }],
  }
})

const subj = computed(() => subjectData.value || {
  summary: { subject_total: 0, merchant_total: 0, external_total: 0, external_attributed: 0, external_unattributed: 0, refund_count: 0, refund_amount: 0 },
  subject_ranking: [],
  severity_distribution: [],
  escalation_funnel: [],
  subject_trend: [],
  top_subject_trend: [],
})
const subjectRanking = computed(() => subj.value.subject_ranking || [])
function extRatioColor(ratio) {
  if (ratio == null) return '#909399'
  if (ratio >= 0.5) return '#dc2626'
  if (ratio >= 0.25) return '#ea580c'
  return '#16a34a'
}

// 严重性数量分布(堆叠柱状图, 显示商户号vs外部投诉占比)
const subjectSeverityOption = computed(() => {
  const d = subj.value.severity_distribution || []
  if (!d.length || d.every(x => !x.count)) return null
  const total = d.reduce((sum, x) => sum + x.count, 0)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const p = params[0]
        const item = d.find(x => x.name === p.seriesName)
        if (!item) return ''
        const pct = total > 0 ? (item.count / total * 100).toFixed(1) : 0
        return `${item.name}<br/>数量: ${item.count}<br/>占比: ${pct}%`
      }
    },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 24, bottom: 36, top: 8 },
    xAxis: { type: 'value', name: '投诉数' },
    yAxis: { type: 'category', data: [''], axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    series: [
      { name: '商户号投诉', type: 'bar', stack: 'total',
        data: d.find(x => x.name === '商户号投诉') ? [d.find(x => x.name === '商户号投诉').count] : [0],
        itemStyle: { color: '#7c3aed', borderRadius: [4, 0, 0, 4] },
        barMaxWidth: 28,
        label: { show: true, position: 'inside', fontSize: 12, fontWeight: 600,
          formatter: (p) => p.value > 0 ? `${p.value}` : '' },
      },
      { name: '外部投诉', type: 'bar', stack: 'total',
        data: d.find(x => x.name === '外部投诉') ? [d.find(x => x.name === '外部投诉').count] : [0],
        itemStyle: { color: '#dc2626', borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 28,
        label: { show: true, position: 'inside', fontSize: 12, fontWeight: 600,
          formatter: (p) => p.value > 0 ? `${p.value}` : '' },
      },
    ],
  }
})

// 投诉升级链(按SN, 内部→商户号→外部 传递性)
const escalationChainOption = computed(() => {
  const f = subj.value.escalation_funnel || []
  if (!f.length || f.every(x => !x.count)) return null
  // 按升级路径排序: 先展示"从内部起步"的链, 再是直发/组合
  const order = ['internal_only', 'internal_merchant', 'internal_external', 'full_chain', 'merchant_only', 'merchant_external', 'external_only']
  const items = order.map(k => f.find(x => x.key === k)).filter(Boolean)
  const colors = ['#2563eb', '#f59e0b', '#ea580c', '#dc2626', '#7c3aed', '#a855f7', '#dc2626']
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 110, right: 48, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar', data: items.map((x, i) => ({
        value: x.count,
        itemStyle: { color: colors[i], borderRadius: [0, 4, 4, 0] },
      })), barMaxWidth: 18,
      label: { show: true, position: 'right', fontSize: 11 },
    }],
  }
})

// 主体投诉趋势(商户号+外部 堆叠面积; 支持按日/周/月汇聚)
const subjectTrendOption = computed(() => {
  const t = subj.value.subject_trend || []
  if (!t.length) return null
  const srcs = ['商户号投诉', '外部投诉']
  const bySource = { '商户号投诉': {}, '外部投诉': {} }
  t.forEach(r => {
    srcs.forEach(s => { if (r[s]) bySource[s][r.day] = (bySource[s][r.day] || 0) + r[s] })
  })
  const agg = aggregateMultiSeries(bySource, subjectTrendGrain.value)
  return {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 24, bottom: 44, top: 16 },
    xAxis: { type: 'category', data: agg.buckets.map(b => b.label), axisLabel: { fontSize: 10, rotate: agg.buckets.length > 12 ? 30 : 0 } },
    yAxis: { type: 'value', name: '投诉数' },
    series: [
      { name: '商户号投诉', type: 'line', stack: 'total', smooth: true,
        areaStyle: { opacity: 0.6 },
        data: agg.byName['商户号投诉'].values, itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2 }, symbol: 'circle', symbolSize: 4 },
      { name: '外部投诉', type: 'line', stack: 'total', smooth: true,
        areaStyle: { opacity: 0.6 },
        data: agg.byName['外部投诉'].values, itemStyle: { color: '#dc2626' }, lineStyle: { width: 2 }, symbol: 'circle', symbolSize: 4 },
    ],
  }
})

// Top5 被投诉主体趋势(堆叠面积, 支持按日/周/月汇聚; Top5按汇聚总量重新排序)
const topSubjectColors = ['#dc2626', '#ea580c', '#ca8a04', '#0891b2', '#7c3aed']
const topSubjectTrendOption = computed(() => {
  const t = subj.value.top_subject_trend || []
  if (!t.length) return null
  // 各主体 {day: count}
  const bySubject = {}
  t.forEach(s => {
    const m = {}
    ;(s.trend || []).forEach(p => { m[p.day] = (m[p.day] || 0) + p.count })
    bySubject[s.name] = m
  })
  const agg = aggregateMultiSeries(bySubject, topSubjectTrendGrain.value)
  // Top5: 按当前粒度下的汇聚总量降序取前5
  const ranked = agg.names
    .map(n => ({ name: n, total: agg.byName[n].values.reduce((s, v) => s + v, 0) }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 5)
  return {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, left: 'center', type: 'scroll', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 10 } },
    grid: { left: 48, right: 24, bottom: 44, top: 16 },
    xAxis: { type: 'category', data: agg.buckets.map(b => b.label), axisLabel: { fontSize: 10, rotate: agg.buckets.length > 12 ? 30 : 0 } },
    yAxis: { type: 'value' },
    series: ranked.map((s, i) => ({
      name: s.name, type: 'line', stack: 'total', smooth: true,
      data: agg.byName[s.name].values,
      itemStyle: { color: topSubjectColors[i % topSubjectColors.length] },
      lineStyle: { color: topSubjectColors[i % topSubjectColors.length], width: 2 },
      areaStyle: { opacity: 0.55 },
      symbolSize: 4,
    })),
  }
})

const s = computed(() => data.value || {
  summary: { total: 0, internal_total: 0, merchant_total: 0, external_total: 0, backlog_total: 0, done_total: 0, completion_rate: 0, avg_handle_hours: 0 },
  source_distribution: [],
  trend: [],
  handler_top: [],
  platform_distribution: [],
  external_category_distribution: [],
  external_trend: [],
  external_total: 0,
  external_matched: 0,
  external_unmatched: 0,
  external_today_registered: 0,
  merchant_category: [],
  merchant_entity_distribution: [],
  merchant_entity_trend: [],
  merchant_handler_distribution: [],
  timeliness: [],
  handler_efficiency: { internal: [], merchant: [], external: [] },
  external_platform_trend: [],
  water: [],
})

// ── 投诉水位(进水/出水 并列柱 + 净水位/累计净水位 折线) ──
const complaintWaterOption = computed(() => {
  const w = s.value.water || []
  if (!w.length) return null
  const dates = w.map(x => (x.day || '').slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['新增投诉', '处理投诉', '净水位', '累计净水位'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 56, bottom: 44, top: 36 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, rotate: 45 } },
    yAxis: [
      { type: 'value', name: '条数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '净水位', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      // 新增投诉 vs 处理投诉 = 此消彼长的比较关系: 并列柱(不同颜色, 不堆叠)
      { name: '新增投诉', type: 'bar', data: w.map(x => x.in), itemStyle: { color: '#f87171' }, barMaxWidth: 24 },
      { name: '处理投诉', type: 'bar', data: w.map(x => x.out), itemStyle: { color: '#10b981' }, barMaxWidth: 24 },
      { name: '净水位', type: 'line', yAxisIndex: 1, data: w.map(x => x.net), smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
      { name: '累计净水位', type: 'line', yAxisIndex: 1, data: w.map(x => x.cum_net), smooth: true, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' } },
    ],
  }
})

const effSourceLabel = computed(() => ({
  internal: '内部投诉',
  merchant: '商户号投诉',
  external: '外部投诉',
}[effSource.value] || '内部投诉'))

const currentEff = computed(() => s.value.handler_efficiency?.[effSource.value] || [])

const platformSummary = computed(() => {
  const p = s.value.platform_distribution || []
  return p.slice(0, 3).map(x => x.name).join(' / ') || '暂无'
})

const merchantEntitySummary = computed(() => {
  const p = s.value.merchant_entity_distribution || []
  return p.slice(0, 2).map(x => x.name).join(' / ') || '暂无'
})

const effectiveStart = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  return dayjs().format('YYYY-MM-DD')
})

const params = computed(() => {
  const p = {}
  if (effectiveStart.value) p.start = effectiveStart.value
  if (effectiveEnd.value) p.end = effectiveEnd.value
  return p
})

// ── 来源分布饼图(含兜底: source_distribution 为空时用 summary 三源数) ──
const sourcePieColors = ['#2563eb', '#7c3aed', '#dc2626']
const sourcePieOption = computed(() => {
  let d = s.value.source_distribution || []
  if (!d.length) {
    const sm = s.value.summary || {}
    const internal = sm.internal_total || 0
    const merchant = sm.merchant_total || 0
    const external = sm.external_total || 0
    if (internal + merchant + external === 0) return null
    d = [
      { name: '内部投诉', count: internal },
      { name: '商户号投诉', count: merchant },
      { name: '外部投诉', count: external },
    ]
  }
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['40%', '65%'], center: ['50%', '45%'],
      label: { show: false }, labelLine: { show: false },
      data: d.map((x, i) => ({ name: x.name, value: x.count, itemStyle: { color: sourcePieColors[i] } })),
    }],
  }
})

// ── 三源趋势堆叠图 ──
const trendStackOption = computed(() => {
  const t = s.value.trend || []
  if (!t.length) return null
  const x = t.map(r => r.day)
  return {
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 24, bottom: 48, top: 16 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 10, rotate: 45 } },
    yAxis: { type: 'value' },
    series: [
      { name: '内部投诉', type: 'bar', stack: 'total', data: t.map(r => r['内部投诉'] || 0), itemStyle: { color: '#2563eb' } },
      { name: '商户号投诉', type: 'bar', stack: 'total', data: t.map(r => r['商户号投诉'] || 0), itemStyle: { color: '#7c3aed' } },
      { name: '外部投诉', type: 'bar', stack: 'total', data: t.map(r => r['外部投诉'] || 0), itemStyle: { color: '#dc2626' } },
    ],
  }
})

// ── 处理人 Top10 横向柱状图 ──
const handlerBarOption = computed(() => {
  const h = s.value.handler_top || []
  if (!h.length) return null
  const items = [...h].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name) },
    series: [{
      type: 'bar', data: items.map(x => x.count),
      itemStyle: { color: '#7c3aed', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 18,
    }],
  }
})

// ── 时效分布柱状图 ──
const timelinessOption = computed(() => {
  const t = s.value.timeliness || []
  if (!t.length || t.every(x => x.count === 0)) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 48, right: 24, bottom: 32, top: 8 },
    xAxis: { type: 'category', data: t.map(x => x.name), axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '工单数' },
    series: [{
      type: 'bar', data: t.map(x => x.count),
      itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] },
      barMaxWidth: 24,
    }],
  }
})

// ── 外部投诉平台分布饼图 ──
const platformColors = ['#dc2626', '#ea580c', '#ca8a04', '#65a30d', '#0891b2', '#7c3aed', '#db2777', '#4f46e5']
const platformPieOption = computed(() => {
  const p = s.value.platform_distribution || []
  if (!p.length) return null
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['40%', '65%'], center: ['50%', '45%'],
      label: { show: false }, labelLine: { show: false },
      data: p.map((x, i) => ({ name: x.name, value: x.count, itemStyle: { color: platformColors[i % platformColors.length] } })),
    }],
  }
})

// ── 外部投诉问题分类柱状图 ──
const externalCategoryOption = computed(() => {
  const c = s.value.external_category_distribution || []
  if (!c.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 90, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: c.map(x => x.name).reverse() },
    series: [{
      type: 'bar', data: c.map(x => x.count).reverse(),
      itemStyle: { color: '#dc2626', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 16,
    }],
  }
})

// ── 外部投诉趋势(按日) ──
const externalTrendOption = computed(() => {
  const t = s.value.external_trend || []
  if (!t.length) return null
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 24, bottom: 40, top: 16 },
    xAxis: { type: 'category', data: t.map(r => r.day), axisLabel: { fontSize: 10, rotate: 45 } },
    yAxis: { type: 'value', name: '投诉数' },
    series: [{
      type: 'line', data: t.map(r => r.count), smooth: true,
      areaStyle: { opacity: 0.15 }, itemStyle: { color: '#dc2626' }, lineStyle: { color: '#dc2626' },
    }],
  }
})

// ── 商户号被投诉主体分布 ──
const merchantEntityOption = computed(() => {
  const c = s.value.merchant_entity_distribution || []
  if (!c.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 150, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: c.map(x => x.name).reverse() },
    series: [{
      type: 'bar', data: c.map(x => x.count).reverse(),
      itemStyle: { color: '#ca8a04', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 16,
    }],
  }
})

// ── 商户号被投诉主体趋势(堆叠面积, 商户号内细分; 支持按日/周/月汇聚) ──
const merchantEntityColors = ['#7c3aed', '#ea580c', '#0891b2', '#ca8a04', '#dc2626', '#2563eb', '#16a34a', '#db2777', '#4f46e5']
const merchantEntityTrendOption = computed(() => {
  const t = s.value.merchant_entity_trend || []
  if (!t.length) return null
  const entities = Object.keys(t[0]).filter(k => k !== 'day')
  const byEntity = {}
  entities.forEach(e => { byEntity[e] = {} })
  t.forEach(r => {
    entities.forEach(e => { if (r[e]) byEntity[e][r.day] = (byEntity[e][r.day] || 0) + r[e] })
  })
  const agg = aggregateMultiSeries(byEntity, merchantEntityTrendGrain.value)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, left: 'center', type: 'scroll', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 10 } },
    grid: { left: 48, right: 24, bottom: 44, top: 16 },
    xAxis: { type: 'category', data: agg.buckets.map(b => b.label), axisLabel: { fontSize: 10, rotate: agg.buckets.length > 12 ? 30 : 0 } },
    yAxis: { type: 'value', name: '投诉数' },
    series: entities.map((e, i) => ({
      name: e, type: 'line', stack: 'total', smooth: true,
      data: agg.byName[e].values,
      itemStyle: { color: merchantEntityColors[i % merchantEntityColors.length] },
      lineStyle: { color: merchantEntityColors[i % merchantEntityColors.length], width: 2 },
      areaStyle: { opacity: 0.55 },
      symbolSize: 4,
    })),
  }
})

// ── 商户号处理人 ──
const merchantHandlerOption = computed(() => {
  const h = s.value.merchant_handler_distribution || []
  if (!h.length) return null
  const items = [...h].reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 80, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name) },
    series: [{
      type: 'bar', data: items.map(x => x.count),
      itemStyle: { color: '#7c3aed', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 16,
    }],
  }
})

// ── 处理人效率排行(当前三源) ──
const handlerEffOption = computed(() => {
  const e = currentEff.value
  if (!e.length) return null
  const items = [...e].sort((a, b) => b.total - a.total).slice(0, 10).reverse()
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const p = ps[0]
        const row = e.find(x => x.name === p?.name)
        if (!row) return ''
        return `${row.name}<br/>受理: ${row.total}<br/>完成: ${row.done}<br/>完成率: ${row.total > 0 ? (row.done / row.total * 100).toFixed(1) : 0}%<br/>平均时长: ${row.avg_hours > 24 ? (row.avg_hours / 24).toFixed(1) + '天' : row.avg_hours.toFixed(1) + 'h'}`
      }
    },
    grid: { left: 80, right: 40, bottom: 24, top: 8 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name) },
    series: [{
      type: 'bar', data: items.map(x => x.total),
      itemStyle: { color: '#409eff', borderRadius: [0, 4, 4, 0] },
      barMaxWidth: 18,
    }],
  }
})

function sourceTagType(source) {
  if (source === '内部投诉') return 'warning'
  if (source === '外部投诉') return 'danger'
  if (source === '商户号投诉') return 'primary'
  return 'info'
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

function formatTime(t) {
  if (!t) return '—'
  const d = dayjs(t)
  return d.isValid() ? d.format('MM-DD HH:mm') : '—'
}

// ── 趋势粒度汇聚工具: 把按日序列(day→count)按周/月聚合成带标签的序列 ──
function aggregateSeries(dayMap, grain) {
  if (grain === 'day') {
    const sorted = Object.keys(dayMap).sort()
    return {
      labels: sorted.map(d => d.slice(5)),
      keys: sorted,
      values: sorted.map(d => dayMap[d]),
    }
  }
  const groups = new Map()
  Object.entries(dayMap).forEach(([d, v]) => {
    const dt = dayjs(d)
    if (!dt.isValid()) return
    let key, label
    if (grain === 'week') {
      const ws = dt.startOf('isoWeek')
      const we = ws.add(6, 'day')
      key = ws.format('YYYY-MM-DD')
      label = `${ws.format('MM-DD')}~${we.format('MM-DD')}`
    } else {
      key = dt.format('YYYY-MM')
      label = dt.format('YYYY-MM')
    }
    const g = groups.get(key) || { label, value: 0 }
    g.value += v
    groups.set(key, g)
  })
  const sorted = [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))
  return {
    labels: sorted.map(([, g]) => g.label),
    keys: sorted.map(([k]) => k),
    values: sorted.map(([, g]) => g.value),
  }
}
// 多序列汇聚: series 形如 { name: {day: count} }, 返回 { names, buckets, byName(values 对齐 buckets) }
function aggregateMultiSeries(series, grain) {
  const names = Object.keys(series)
  if (!names.length) return { names: [], buckets: [], byName: {} }
  const byName = {}
  names.forEach(n => { byName[n] = aggregateSeries(series[n], grain) })
  // 统一 key 集合(并集, 升序)
  const keys = new Set()
  names.forEach(n => byName[n].keys.forEach(k => keys.add(k)))
  const sortedKeys = [...keys].sort()
  // key → label 映射(取任一存在的 label)
  const labelOf = {}
  names.forEach(n => {
    byName[n].keys.forEach((k, i) => { if (!labelOf[k]) labelOf[k] = byName[n].labels[i] })
  })
  const buckets = sortedKeys.map(k => ({ key: k, label: labelOf[k] || k }))
  // 每个序列对齐到统一 buckets(缺失补0)
  names.forEach(n => {
    const m = {}
    byName[n].keys.forEach((k, i) => { m[k] = byName[n].values[i] })
    byName[n].values = buckets.map(b => m[b.key] || 0)
  })
  return { names, buckets, byName }
}

// ── 加载数据 ──
async function loadData() {
  loading.value = true
  try {
    backlogPage.value = 1 // 时间范围变化 → 积压分页重置
    const res = await complaintAnalysis(params.value)
    data.value = res.data || {}
    loaded.value = true
    // 同时加载明细
    loadExternalDetail()
    loadMerchantDetail()
    loadBacklog()
    loadSubjectAnalysis()
  } finally {
    loading.value = false
  }
}

async function loadSubjectAnalysis() {
  subjectLoading.value = true
  try {
    const res = await complaintSubjectAnalysis(params.value)
    subjectData.value = res.data || {}
  } finally {
    subjectLoading.value = false
  }
}

async function loadExternalDetail() {
  extDetailLoading.value = true
  try {
    const p = { ...params.value, limit: 300, source_type: 'external' }
    const res = await complaintSupplementary(p)
    // 补充 entity 字段 (315表无 entity 字段, 用 company_entity 兼容)
    externalDetail.value = (res.data?.items || []).map(it => ({ ...it, entity: it.entity || it.company_entity }))
  } finally {
    extDetailLoading.value = false
  }
}

async function loadMerchantDetail() {
  merDetailLoading.value = true
  try {
    const p = { ...params.value, limit: 200, include_supplementary: false, source_type: '商户号投诉' }
    const res = await complaintDetail(p)
    merchantDetail.value = res.data?.items || []
  } finally {
    merDetailLoading.value = false
  }
}

async function loadBacklog() {
  backlogLoading.value = true
  try {
    const p = {
      page: backlogPage.value,
      page_size: backlogPageSize.value,
      sort: 'aging_asc',
      ...params.value, // 时间窗口: 默认近30天, 7/90/自定义跟随页面筛选(仅统计窗口内登记的未完成)
    }
    if (backlogFilters.source_type) p.source_type = backlogFilters.source_type
    if (backlogFilters.status) p.status = backlogFilters.status
    if (backlogFilters.has_handler != null) p.has_handler = backlogFilters.has_handler
    const res = await complaintBacklog(p)
    backlogData.value = res.data || {}
    backlogTotal.value = res.data?.total || 0
  } finally {
    backlogLoading.value = false
  }
}

// 导出积压明细 Excel(与 /backlog 同口径, 含当前筛选, 不受分页限制)
async function handleBacklogExport() {
  backlogExporting.value = true
  try {
    const p = { sort: 'aging_asc', ...params.value }
    if (backlogFilters.source_type) p.source_type = backlogFilters.source_type
    if (backlogFilters.status) p.status = backlogFilters.status
    if (backlogFilters.has_handler != null) p.has_handler = backlogFilters.has_handler
    const res = await complaintBacklogExport(p)
    const blobData = res instanceof Blob ? res : res?.data
    const blob = new Blob([blobData], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    let fname = `积压投诉明细_${effectiveStart.value || '全量'}_${effectiveEnd.value || '至今'}.xlsx`
    const cd = res.headers?.['content-disposition'] || ''
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd)
    if (m) fname = decodeURIComponent(m[1])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = fname
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    ElMessage.success('已导出 ' + (backlogTotal.value || 0) + ' 条')
  } catch (e) {
    ElMessage.error('导出失败: ' + (e?.message || e))
  } finally {
    backlogExporting.value = false
  }
}

const SOURCE_TO_AUDIT = { '内部投诉': 'internal', '商户号投诉': 'merchant', '外部投诉': 'external' }

function openAudit(row) {
  auditForm.source_type = SOURCE_TO_AUDIT[row.source] || 'internal'
  auditForm.ref_id = row.ticket_no || ''
  auditForm.device_sn = row.device_sn || ''
  auditForm.attribution = '其他'
  auditForm.judgment = '通过'
  auditForm.conclusion = ''
  auditVisible.value = true
}

async function submitAudit() {
  if (!auditForm.device_sn.trim() && !auditForm.ref_id.trim()) {
    ElMessage.warning('该工单无SN且无工单号，无法关联核查')
    return
  }
  auditSubmitting.value = true
  try {
    await createAudit({ ...auditForm })
    ElMessage.success('核查已保存')
    auditVisible.value = false
    loadBacklog()
  } finally {
    auditSubmitting.value = false
  }
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.page-sub { color: #909399; font-size: 13px; margin-top: 4px; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.flex { display: flex; align-items: center; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.gap-8 { gap: 8px; }
.unit { font-size: 14px; font-weight: 400; color: #909399; }
.dim { color: #909399; }
.dim-text { color: #a0a4ab; font-size: 12px; }

.filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.kpi-card { border-radius: 10px; padding: 12px 14px; min-height: 68px; display: flex; flex-direction: column; gap: 4px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 11px; color: #6b7280; }

.block-card { margin-bottom: 16px; }
.block-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.block-title { font-size: 16px; font-weight: 600; color: #1f2937; }
.block-hint { font-size: 12px; color: #9ca3af; }
.block-tabs { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); z-index: 100; background: #fff; padding: 8px 16px; border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.12); }

.chart-card { background: #fff; border-radius: 8px; padding: 12px; border: 1px solid #ebeef5; }
.chart-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 8px; }
.problem-text { font-size: 12px; color: #374151; }
.subject-name { font-weight: 600; color: #1f2937; }

/* 积压快照(决策概览) */
.kpi-clickable { cursor: pointer; transition: all 0.15s; }
.kpi-clickable:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.08); border-color: #c7c7c7; }
.snapshot-kpi-row { display: flex; gap: 8px; margin-bottom: 10px; }
.snapshot-kpi { flex: 1; background: #f9fafb; border: 1px solid #f1f3f5; border-radius: 8px; padding: 8px 6px; text-align: center; }
.snapshot-kpi-v { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
.snapshot-kpi-l { font-size: 10px; color: #9ca3af; margin-top: 2px; }
.snapshot-source-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
.snapshot-source-name { width: 62px; font-size: 11px; color: #6b7280; white-space: nowrap; }
.snapshot-source-track { flex: 1; height: 10px; background: #f1f3f5; border-radius: 5px; overflow: hidden; }
.snapshot-source-fill { height: 100%; border-radius: 5px; transition: width 0.3s; }
.snapshot-source-cnt { width: 36px; text-align: right; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.snapshot-list { max-height: 190px; overflow-y: auto; }
.snapshot-row { display: flex; align-items: center; gap: 6px; padding: 5px 2px; border-bottom: 1px dashed #f1f3f5; }
.snapshot-rank { width: 16px; font-size: 11px; color: #9ca3af; text-align: center; }
.snapshot-no { flex: 1; font-size: 12px; color: #374151; font-family: ui-monospace, monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.snapshot-sn { font-size: 11px; font-family: ui-monospace, monospace; max-width: 110px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.empty-hint { margin-top: 60px; }
.empty-tip { text-align: center; }
.empty-main { font-size: 15px; color: #6b7280; margin-bottom: 8px; }
.empty-sub { font-size: 13px; color: #9ca3af; }

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .page-sub { font-size: 11px; }
  .filter-bar { gap: 6px; }
  .block-title { font-size: 14px; }
  .block-hint { font-size: 11px; display: block; }
  .block-head { flex-wrap: wrap; gap: 6px; }
  .kpi-card { padding: 8px 10px; min-height: 60px; }
  .kpi-label { font-size: 11px; }
  .kpi-value { font-size: 19px; }
  .kpi-sub { font-size: 10px; }
  .chart-card { padding: 8px; }
  .chart-title { font-size: 12px; }
  /* 表格在手机上保持可横向滚动 */
  :deep(.el-table) { min-width: 560px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
  :deep(.el-card) { overflow: hidden; }
  .block-tabs { padding: 6px 10px; }
}
</style>