<!--
  ┌──────────────────────── 设计说明（Design Rationale） ─────────────────────────┐
  │ 目的：退款挽回主管定位「反复出问题的设备」，决定催回优先级/设备回收/客户回访，  │
  │       并借 Top20 个案判断是设备问题还是服务问题。                              │
  │ 逻辑：4 张风险计数卡（异常信号前置）→ Top20 个案明细 → 2 张分布图（时差/次数）， │
  │       由异常计数到个案再到分布规律，顶部筛选条统一控制口径。                    │
  │ 口径：全部基于「挽留失败 SN」= 周期内 cs_records problem_category='退款' 去重 SN │
  │       中，同期出现 refunds 线上退款单(状态非驳回/无需退款) 或 service_tickets   │
  │       service_category=退款 工单(非终止) 者；复退款=接待≥2次且失败；            │
  │       跨人员=失败且≥2名客服接待；高金额=失败且退款>1000元。                     │
  │       详见 backend/app/services/analytics/refund.py _refund_recovery_core /     │
  │       refund_device_analysis。                                                  │
  │ 取舍：① 不做「跨人员」精确筛选下钻——接口无该筛选参数，仅可按「接待人员数」列     │
  │          排序近似查看，强给会误导；                                             │
  │       ② 两张分布图不做图内点击下钻——分布为统计切面，无单桶明细接口；            │
  │       ③ 不新建设备明细页——Top20 行内「详情」抽屉+「时序」审计页已承载个案下钻。  │
  │ 工单：#205（看板卡片双强制补齐：ⓘ 数据说明 + 可下钻）                           │
  └───────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="device-analysis">
    <!-- 1. 筛选栏 -->
    <section class="section-card">
      <div class="block-title">设备风险分析 · 筛选<el-tooltip effect="dark" placement="bottom-start" raw-content
        content="<b>这个页面为什么这样设计</b><br/><b>给谁</b>：退款挽回主管，定位高风险设备、安排催回/回收时使用。<br/><b>回答什么</b>：哪些设备反复出现退款失败？金额集中在哪里？接待到流失隔多久？<br/><b>组织逻辑</b>：4 张风险计数卡（异常信号）→ Top20 个案 → 2 张分布图（时差/次数），由计数到个案再到规律。<br/><b>口径</b>：全部基于「挽留失败 SN」判据（接待退款类 + 最终产生退款行为），详见 backend/app/services/analytics/refund.py _refund_recovery_core。<br/><br/><b>⚠️ 我们不做什么（及原因）</b><br/>① <b>不做</b>「跨人员」精确筛选下钻 —— 接口无该筛选参数，仅可按 Top20「接待人员数」列排序近似查看；<br/>② 两张分布图<b>不做</b>图内点击下钻 —— 分布为统计切面，无单桶明细接口；<br/>③ <b>不新建</b>设备明细页 —— Top20「详情」抽屉 +「时序」审计页已承载个案下钻。<br/><b>关联</b>：工单 #205">
        <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
      </el-tooltip></div>
      <div class="filter-row">
        <el-select v-model="filters.reason" placeholder="失败原因" size="small" style="width: 220px" @change="onFilterChange">
          <el-option label="全部原因" value="" />
          <el-option v-for="r in REASON_OPTIONS" :key="r" :label="r" :value="r" />
        </el-select>
        <el-select v-model="filters.amountRange" placeholder="金额区间" size="small" style="width: 160px" @change="onFilterChange">
          <el-option label="全部金额" value="" />
          <el-option label="<100元" value="lt100" />
          <el-option label="100-500元" value="100-500" />
          <el-option label="500-1000元" value="500-1000" />
          <el-option label="1000+元" value="gt1000" />
        </el-select>
        <el-select v-model="filters.receptionMin" placeholder="接待次数" size="small" style="width: 140px" @change="onFilterChange">
          <el-option label="全部次数" value="" />
          <el-option label="≥2次" :value="2" />
          <el-option label="≥3次" :value="3" />
        </el-select>
        <el-button :icon="Refresh" :loading="loading" size="small" type="primary" @click="loadData">刷新</el-button>
      </div>
    </section>

    <!-- 2. 设备风险分布 KPI
         #205: 4 张卡均有ⓘ说明; 复退款/高金额/总失败 3 张可点下钻到 Top20 表(带筛选), 跨人员卡不下钻(见ⓘ)。 -->
    <section v-loading="loading" class="section-card">
      <div class="block-title">设备风险分布<el-tooltip effect="dark" placement="top" raw-content
        content="<b>来源</b>：/analytics/refund/device-analysis → risk_summary<br/><b>口径</b>：4 张卡均基于「挽留失败 SN」= 周期内退款类接待(cs_records problem_category='退款')去重 SN 中，最终产生退款行为(refunds 线上退款单非驳回/无需退款，或 service_tickets 退款类工单非终止)者；复退款=接待≥2次且失败 · 跨人员=失败且≥2名客服接待 · 高金额=失败且退款&gt;1000元 · 总失败=失败 SN 总数<br/><b>时点</b>：跟随父页时间条与筛选条，查询后端缓存 120s<br/><b>「空」的含义</b>：0 = 当前窗口/筛选条件下无满足判据的设备（先确认窗口内是否有退款类接待）<br/><b>下钻</b>：复退款/高金额/总失败 3 张卡可点 → Top20 表（带对应筛选）；跨人员卡不下钻（接口无该参数，见其ⓘ）">
        <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
      </el-tooltip></div>
      <el-row :gutter="12">
        <el-col :xs="12" :sm="12" :md="6">
          <!-- #205 卡片说明+下钻: 判据=失败SN且退款类接待≥2次; 用「接待次数≥2」筛选近似承载(Top20 仅金额前20, 代码注释已声明降级) -->
          <div class="risk-card risk-red drillable" title="点击查看接待≥2次的失败设备" @click="drillRepeat">
            <div class="risk-label">复退款设备数<el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/analytics/refund/device-analysis → risk_summary.repeat_refund_devices<br/><b>口径</b>：周期内该 SN 退款类接待(cs_records problem_category='退款')≥2 次 <b>且</b> 属于「挽留失败 SN」（最终产生退款行为）<br/><b>时点</b>：跟随父页时间条与筛选条；后端缓存 120s<br/><b>「空」的含义</b>：0 = 窗口内无接待≥2次且失败的设备<br/><b>下钻</b>：点卡片 → Top20 表（自动选中「接待次数≥2」，按金额倒序；仅显示前 20 台）">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip></div>
            <div class="risk-sub">周期内接待≥2次</div>
            <div class="risk-value">{{ riskSummary.repeat_refund_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <!-- #205 ⚠️ 不做下钻(规范 §3.4): 接口无「跨人员」筛选参数; 「接待次数≥2」是总接待次数、
               ≠去重接待人数≥2, 判据不同, 强行复用会误导。替代路径= Top20 表按「接待人员数」列倒序近似查看。 -->
          <div class="risk-card risk-orange">
            <div class="risk-label">跨人员接待设备数<el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/analytics/refund/device-analysis → risk_summary.cross_person_devices<br/><b>口径</b>：挽留失败 SN 中，周期内接待过该 SN 的<b>去重客服人数≥2</b>（同一人多条接待只算 1 人）<br/><b>时点</b>：跟随父页时间条与筛选条；后端缓存 120s<br/><b>「空」的含义</b>：0 = 窗口内失败设备均为单人接待<br/><b>⚠️ 不下钻</b>：接口无「跨人员」筛选参数；「接待次数≥2」是接待记录数、≠去重人数，判据不同不能复用。近似查看：Top20 表按「接待人员数」列倒序">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip></div>
            <div class="risk-sub">≥2名客服接待</div>
            <div class="risk-value">{{ riskSummary.cross_person_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <!-- #205 卡片说明+下钻: 判据=失败SN且退款金额>1000元; 用「金额区间>1000」筛选精确承载 -->
          <div class="risk-card risk-purple drillable" title="点击查看退款>1000元的失败设备" @click="drillHighAmount">
            <div class="risk-label">高金额设备数<el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/analytics/refund/device-analysis → risk_summary.high_amount_devices<br/><b>口径</b>：挽留失败 SN 中，失败退款金额 &gt; 1000 元者<br/><b>时点</b>：跟随父页时间条与筛选条；后端缓存 120s<br/><b>「空」的含义</b>：0 = 窗口内失败设备均不超过 1000 元<br/><b>下钻</b>：点卡片 → Top20 表（自动选中「金额区间&gt;1000元」）">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip></div>
            <div class="risk-sub">退款 &gt; 1000元</div>
            <div class="risk-value">{{ riskSummary.high_amount_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="12" :md="6">
          <!-- #205 卡片说明+下钻: 判据=挽留失败SN全集; Top20 表即其金额倒序列表(前20), 点击时重置筛选 -->
          <div class="risk-card risk-gray drillable" title="点击查看失败设备列表" @click="drillAllFailed">
            <div class="risk-label">总失败设备数<el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/analytics/refund/device-analysis → risk_summary.total_failed_devices<br/><b>口径</b>：挽留失败 SN 总数 = 周期内退款类接待去重 SN 中，同期出现 refunds 线上退款单(状态非驳回/无需退款)或 service_tickets 退款类工单(非终止)者<br/><b>时点</b>：跟随父页时间条与筛选条；后端缓存 120s<br/><b>「空」的含义</b>：0 = 窗口内所有退款类接待均挽留成功（或无接待数据）<br/><b>下钻</b>：点卡片 → Top20 表（重置筛选；表为金额倒序前 20，全量可用「时序」逐台查看）">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip></div>
            <div class="risk-sub">T+7 窗口内失败</div>
            <div class="risk-value">{{ riskSummary.total_failed_devices || 0 }}<span class="unit">个</span></div>
          </div>
        </el-col>
      </el-row>
    </section>

    <!-- 3. Top20 高风险设备表 (#205: 本表即风险 KPI 卡的下钻承载; 行内「详情」抽屉/「时序」审计页已可逐台下钻) -->
    <section id="sec-top20" v-loading="loading" class="section-card">
      <div class="flex-between mb-12">
        <div class="block-title">Top20 高风险设备<el-tooltip effect="dark" placement="top" raw-content
          content="<b>来源</b>：/analytics/refund/device-analysis → top_devices<br/><b>口径</b>：挽留失败 SN 按退款金额倒序前 20（受顶部 失败原因/金额区间/接待次数 筛选影响）；接待次数=周期内退款类接待记录数，接待人员数=去重客服人数；退款时间取该 SN 最近一笔线上退款（状态非驳回/无需退款）<br/><b>时点</b>：跟随父页时间条与筛选条；后端缓存 120s<br/><b>「空」的含义</b>：为空 = 当前窗口/筛选条件下无挽留失败设备<br/><b>下钻</b>：行内「详情」→ 设备画像抽屉（接待时间线/退款/工单/钱包）；「时序」→ 设备审计页（新窗口，带当前窗口）">
          <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
        </el-tooltip></div>
        <el-tag size="small" type="info" effect="plain">默认按退款金额倒序</el-tag>
      </div>
      <el-table
        :data="topDevices"
        border
        stripe
        size="small"
        max-height="520"
        :default-sort="{ prop: 'refund_amount', order: 'descending' }"
      >
        <el-table-column prop="device_sn" label="设备SN" min-width="140" fixed>
          <template #default="{ row }">
            <span style="font-weight: 600">{{ row.device_sn }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="reception_count" label="接待次数" width="90" align="right" sortable />
        <el-table-column prop="receptionist_count" label="接待人员数" width="100" align="right" sortable />
        <el-table-column label="接待人员" min-width="160">
          <template #default="{ row }">
            <span v-if="row.receptionists && row.receptionists.length">{{ row.receptionists.join(', ') }}</span>
            <span v-else style="color: #9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="fail_reason" label="失败原因" width="160">
          <template #default="{ row }">
            <el-tag v-if="row.fail_reason" size="small" :type="reasonTagType(row.fail_reason)" effect="light">{{ row.fail_reason }}</el-tag>
            <span v-else style="color: #9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="refund_amount" label="退款金额" width="120" align="right" sortable>
          <template #default="{ row }">
            <span :style="{ color: Number(row.refund_amount) > 1000 ? '#dc2626' : '#374151', fontWeight: 600 }">¥{{ formatMoney(row.refund_amount) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="refund_time" label="退款时间" width="150">
          <template #default="{ row }">{{ formatTime(row.refund_time) }}</template>
        </el-table-column>
        <el-table-column prop="final_status" label="最终状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.final_status" size="small" :type="statusTagType(row.final_status)" effect="plain">{{ row.final_status }}</el-tag>
            <span v-else style="color: #9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row.device_sn)">详情</el-button>
            <el-button link type="primary" size="small" @click="openAudit(row.device_sn)">时序</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !topDevices.length" description="暂无高风险设备数据" />
    </section>

    <!-- 4. 接待→流失时差分布
         #205 ⚠️ 不做图内点击下钻（规范 §3.4）: 分布为统计切面，后端无单桶明细接口；
              单桶定位可用顶部「失败原因/金额区间」筛选近似，个案构成看 Top20「详情」抽屉。 -->
    <section class="section-card">
      <div class="block-title">接待 → 流失时差分布<el-tooltip effect="dark" placement="top" raw-content
        content="<b>来源</b>：/analytics/refund/device-analysis → loss_time_dist<br/><b>口径</b>：挽留失败设备按「首次退款类接待 → 退款时间(最近一笔线上退款)」差值分桶（当天/1-3天/3-7天/7-15天/15天+）；柱=桶内失败 SN 数，悬停可见涉及金额<br/><b>时点</b>：跟随父页时间条与筛选条；后端缓存 120s<br/><b>「空」的含义</b>：为空 = 窗口内无「接待与退款时间都齐全」的失败设备（缺退款时间的 SN 不入桶）<br/><b>⚠️ 不下钻</b>：分布为统计切面，无单桶明细接口；定位可用顶部筛选近似，个案看 Top20「详情」">
        <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
      </el-tooltip></div>
      <BaseChart
        v-if="lossTimeOption && lossTimeOption.series && lossTimeOption.series.length"
        :option="lossTimeOption"
        height="320px"
      />
      <el-empty v-else description="暂无时差分布数据" />
    </section>

    <!-- 5. 设备接待次数分布
         #205 ⚠️ 不做图内点击下钻（规范 §3.4）: 分布为统计切面，后端无单桶明细接口；
              桶内计数与「复退款」判据(接待记录条数)可能存在去重口径差异，数值以接口返回为准。 -->
    <section class="section-card">
      <div class="block-title">设备接待次数分布<el-tooltip effect="dark" placement="top" raw-content
        content="<b>来源</b>：/analytics/refund/device-analysis → reception_count_dist<br/><b>口径</b>：全部退款类接待 SN 按「接待次数」分桶（1/2/3/4/5+次），线=桶内挽留失败率%；分桶计数与「复退款」判据（接待记录条数≥2）可能存在去重口径差异，<b>数值以接口返回为准</b><br/><b>时点</b>：跟随父页时间条与筛选条；后端缓存 120s<br/><b>「空」的含义</b>：为空 = 窗口内无退款类接待 SN<br/><b>⚠️ 不下钻</b>：分布为统计切面，无单桶明细接口；单设备构成看 Top20「详情」抽屉">
        <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
      </el-tooltip></div>
      <BaseChart
        v-if="receptionCountOption && receptionCountOption.series && receptionCountOption.series.length"
        :option="receptionCountOption"
        height="320px"
      />
      <el-empty v-else description="暂无接待次数分布数据" />
    </section>

    <!-- 6. 设备详情抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      size="60%"
      :title="`设备详情 · ${currentDeviceSn}`"
      direction="rtl"
      destroy-on-close
    >
      <div v-loading="drawerLoading" class="drawer-content">
        <template v-if="detail">
          <!-- 设备画像 -->
          <section class="section-card">
            <div class="block-title">设备画像</div>
            <el-row :gutter="12">
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">SN</span><span class="v">{{ detail.device_sn || '—' }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">型号</span><span class="v">{{ detail.device_type || '—' }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv">
                  <span class="k">最终结果</span>
                  <el-tag v-if="detail.result" size="small" :type="detail.result === 'failed' ? 'danger' : 'success'" effect="dark">
                    {{ detail.result === 'failed' ? '失败' : '成功' }}
                  </el-tag>
                  <span v-else style="color: #9ca3af">—</span>
                </div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">首次接待</span><span class="v">{{ formatTime(detail.first_reception) }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">末次接待</span><span class="v">{{ formatTime(detail.last_reception) }}</span></div>
              </el-col>
              <el-col :xs="12" :md="8">
                <div class="kv"><span class="k">接待次数</span><span class="v">{{ detail.reception_count || 0 }} 次</span></div>
              </el-col>
              <el-col :xs="24" :md="12">
                <div class="kv">
                  <span class="k">接待人员</span>
                  <span class="v">{{ (detail.receptionists || []).join(', ') || '—' }}</span>
                </div>
              </el-col>
              <el-col :xs="12" :md="6">
                <div class="kv">
                  <span class="k">失败原因</span>
                  <el-tag v-if="detail.fail_reason" size="small" :type="reasonTagType(detail.fail_reason)" effect="light">{{ detail.fail_reason }}</el-tag>
                  <span v-else style="color: #9ca3af">—</span>
                </div>
              </el-col>
              <el-col :xs="12" :md="6">
                <div class="kv">
                  <span class="k">退款金额</span>
                  <span class="v" style="color: #dc2626; font-weight: 600">¥{{ formatMoney(detail.refund_amount) }}</span>
                </div>
              </el-col>
              <el-col :xs="24" :md="12">
                <div class="kv"><span class="k">退款单号</span><span class="v">{{ detail.refund_no || '—' }}</span></div>
              </el-col>
            </el-row>
          </section>

          <!-- 接待记录时间线 -->
          <section class="section-card">
            <div class="block-title">接待记录时间线</div>
            <el-table :data="sortedTimeline" border stripe size="small" max-height="280">
              <el-table-column prop="time" label="时间" width="160">
                <template #default="{ row }">{{ formatTime(row.time) }}</template>
              </el-table-column>
              <el-table-column prop="operator_name" label="接待人" width="100" />
              <el-table-column prop="problem_type" label="问题类型" width="120" />
              <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip />
              <el-table-column prop="execution_result" label="处理结果" min-width="160" show-overflow-tooltip />
              <el-table-column prop="consult_time" label="接待时长" width="100" align="right">
                <template #default="{ row }">{{ row.consult_time != null ? row.consult_time + 's' : '—' }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!sortedTimeline.length" description="暂无接待记录" />
          </section>

          <!-- 退款记录 -->
          <section class="section-card">
            <div class="block-title">退款记录</div>
            <el-table :data="detail.refund_records || []" border stripe size="small" max-height="280">
              <el-table-column prop="refund_no" label="退款单号" min-width="160" />
              <el-table-column prop="refund_amount" label="金额" width="110" align="right">
                <template #default="{ row }">¥{{ formatMoney(row.refund_amount) }}</template>
              </el-table-column>
              <el-table-column prop="refund_method" label="方式" width="100" />
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag v-if="row.status" size="small" :type="statusTagType(row.status)" effect="plain">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="operator" label="操作人" width="100" />
              <el-table-column prop="auditor" label="审核人" width="100">
                <template #default="{ row }">{{ row.auditor || '—' }}</template>
              </el-table-column>
              <el-table-column prop="created_at_raw" label="时间" width="160">
                <template #default="{ row }">{{ formatTime(row.created_at_raw) }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!detail.refund_records || !detail.refund_records.length" description="暂无退款记录" />
          </section>

          <!-- 关联工单 -->
          <section v-if="detail.tickets && detail.tickets.length" class="section-card">
            <div class="block-title">关联工单</div>
            <el-table :data="detail.tickets" border stripe size="small" max-height="280">
              <el-table-column prop="ticket_no" label="工单号" min-width="140" />
              <el-table-column prop="service_item" label="服务项" min-width="120" />
              <el-table-column prop="service_category" label="类别" width="100" />
              <el-table-column prop="register_time" label="登记时间" width="160">
                <template #default="{ row }">{{ formatTime(row.register_time) }}</template>
              </el-table-column>
              <el-table-column prop="finish_time" label="完成时间" width="160">
                <template #default="{ row }">{{ formatTime(row.finish_time) }}</template>
              </el-table-column>
              <el-table-column prop="handler" label="处理人" width="100" />
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag v-if="row.status" size="small" :type="statusTagType(row.status)" effect="plain">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </section>

          <!-- 钱包提现 -->
          <section v-if="detail.wallet_records && detail.wallet_records.length" class="section-card">
            <div class="block-title">钱包提现</div>
            <el-table :data="detail.wallet_records" border stripe size="small" max-height="280">
              <el-table-column prop="op_time" label="时间" width="160">
                <template #default="{ row }">{{ formatTime(row.op_time) }}</template>
              </el-table-column>
              <el-table-column prop="op_amount" label="金额" width="120" align="right">
                <template #default="{ row }">¥{{ formatMoney(row.op_amount) }}</template>
              </el-table-column>
              <el-table-column prop="op_type" label="操作类型" min-width="120" />
              <el-table-column prop="wallet_id" label="钱包ID" min-width="120" />
            </el-table>
          </section>
        </template>
        <el-empty v-else-if="!drawerLoading" description="暂无详情数据" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { Refresh, QuestionFilled } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { refundDeep } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

// 父组件传入的时间范围
const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
})

// 失败原因 7 大类 (任务说明: 不想用了/充值错误/设备故障/网卡/卡无网络/套餐问题/卡商问题/其他)
const REASON_OPTIONS = [
  '不想用了',
  '充值错误',
  '设备故障',
  '网卡',
  '卡无网络',
  '套餐问题(流量虚、涨价、升级)',
  '卡商问题',
  '其他(填写备注)',
]

// 筛选条件
const filters = reactive({
  reason: '',
  amountRange: '',
  receptionMin: '',
})

// 加载状态
const loading = ref(false)

// 风险摘要 KPI
const riskSummary = ref({
  repeat_refund_devices: 0,
  cross_person_devices: 0,
  high_amount_devices: 0,
  total_failed_devices: 0,
})

// Top20 设备列表
const topDevices = ref([])
// 接待→流失时差分布
const lossTimeDist = ref([])
// 接待次数分布
const receptionCountDist = ref([])

// 金额区间 → { min, max } 映射
function resolveAmountRange(range) {
  switch (range) {
    case 'lt100': return { min: null, max: 100 }
    case '100-500': return { min: 100, max: 500 }
    case '500-1000': return { min: 500, max: 1000 }
    case 'gt1000': return { min: 1000, max: null }
    default: return { min: null, max: null }
  }
}

// 组装请求参数
function buildParams() {
  const { min, max } = resolveAmountRange(filters.amountRange)
  const params = {}
  if (props.start) params.start = props.start
  if (props.end) params.end = props.end
  if (filters.reason) params.reason = filters.reason
  if (min !== null) params.amount_min = min
  if (max !== null) params.amount_max = max
  if (filters.receptionMin !== '' && filters.receptionMin !== null) {
    params.reception_count_min = filters.receptionMin
  }
  return params
}

// 加载设备分析数据
async function loadData() {
  loading.value = true
  try {
    const params = buildParams()
    const res = await refundDeep.deviceAnalysis(params)
    const data = res.data || {}
    riskSummary.value = Object.assign({
      repeat_refund_devices: 0,
      cross_person_devices: 0,
      high_amount_devices: 0,
      total_failed_devices: 0,
    }, data.risk_summary || {})
    topDevices.value = data.top_devices || []
    lossTimeDist.value = data.loss_time_dist || []
    receptionCountDist.value = data.reception_count_dist || []
  } catch (e) {
    riskSummary.value = { repeat_refund_devices: 0, cross_person_devices: 0, high_amount_devices: 0, total_failed_devices: 0 }
    topDevices.value = []
    lossTimeDist.value = []
    receptionCountDist.value = []
  } finally {
    loading.value = false
  }
}

// 筛选条件变化时触发
function onFilterChange() {
  loadData()
}

// ---- #205 KPI 卡下钻（规范 §3.2 优先级③: 同页区块；时间窗口随父页 props 共用，无需透传）----
function scrollToTop20() {
  document.getElementById('sec-top20')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
// 复退款设备数 → 接待次数≥2 筛选。
// ⚠️ 降级说明: 后端 repeat_refund_devices = 失败SN 且 退款类接待记录条数≥2；
//    Top20 的 reception_count_min 筛选同为退款类接待记录条数 → 判据一致，但 Top20 仅展示金额前 20 台，
//    超出部分不展示（接口 limit=20），故为「近似承载」，已在卡片ⓘ中声明。
function drillRepeat() {
  filters.receptionMin = 2
  loadData()
  scrollToTop20()
}
// 高金额设备数 → 金额区间>1000 筛选（与后端 high_amount 判据一致，精确承载）
function drillHighAmount() {
  filters.amountRange = 'gt1000'
  loadData()
  scrollToTop20()
}
// 总失败设备数 → 重置筛选后展示全部失败设备列表（Top20 金额倒序前 20）
function drillAllFailed() {
  filters.reason = ''
  filters.amountRange = ''
  filters.receptionMin = ''
  loadData()
  scrollToTop20()
}
// ⚠️ 「跨人员接待设备数」卡不做下钻: 接口无该筛选参数（见该卡模板注释与ⓘ）。
// ⚠️ 两张分布图不做图内点击下钻: 分布为统计切面，无单桶明细接口（见各图模板注释与ⓘ）。

// 监听 props.start / props.end 变化
watch(() => [props.start, props.end], () => {
  loadData()
})

onMounted(loadData)

// === 失败原因 tag 颜色映射 ===
const REASON_TAG_MAP = {
  '不想用了': 'info',
  '充值错误': 'warning',
  '设备故障': 'danger',
  '网卡': 'primary',
  '卡无网络': 'primary',
  '套餐问题(流量虚、涨价、升级)': 'success',
  '卡商问题': 'warning',
  '其他(填写备注)': 'info',
}
function reasonTagType(reason) {
  return REASON_TAG_MAP[reason] || 'info'
}

// === 退款/工单状态 tag 颜色 ===
function statusTagType(status) {
  if (!status) return 'info'
  if (/已退款|完成|成功|通过/.test(status)) return 'success'
  if (/驳回|失败|拒绝|终止/.test(status)) return 'danger'
  if (/审核|处理中|待|进行/.test(status)) return 'warning'
  return 'info'
}

// === 时间格式化 (ISO → YYYY-MM-DD HH:mm) ===
function formatTime(s) {
  if (!s) return '—'
  const str = String(s).replace('T', ' ').replace('Z', '')
  return str.slice(0, 16)
}

// === 设备详情抽屉 ===
const drawerVisible = ref(false)
const drawerLoading = ref(false)
const currentDeviceSn = ref('')
const detail = ref(null)

// 接待时间线按时间正序排列
const sortedTimeline = computed(() => {
  const list = (detail.value && detail.value.reception_timeline) ? [...detail.value.reception_timeline] : []
  list.sort((a, b) => {
    const ta = a.time ? new Date(a.time).getTime() : 0
    const tb = b.time ? new Date(b.time).getTime() : 0
    return ta - tb
  })
  return list
})

// 打开设备详情抽屉
async function openDetail(sn) {
  currentDeviceSn.value = sn
  drawerVisible.value = true
  drawerLoading.value = true
  detail.value = null
  try {
    const params = { device_sn: sn }
    if (props.start) params.start = props.start
    if (props.end) params.end = props.end
    const res = await refundDeep.deviceDetail(params)
    detail.value = res.data || null
  } catch (e) {
    detail.value = null
  } finally {
    drawerLoading.value = false
  }
}

// 新窗口打开时序审计页
function openAudit(sn) {
  const url = `/audit/device?sn=${encodeURIComponent(sn)}&start=${encodeURIComponent(props.start || '')}&end=${encodeURIComponent(props.end || '')}`
  window.open(url, '_blank')
}

// === 图表: 接待→流失时差分布 (柱状图) ===
const lossTimeOption = computed(() => {
  const list = lossTimeDist.value || []
  if (!list.length) return {}
  const x = list.map(r => r.bucket)
  const snCnt = list.map(r => Number(r.sn_cnt) || 0)
  const pal = ['#60a5fa', '#34d399', '#fbbf24', '#fb923c', '#f87171']
  const barData = snCnt.map((v, i) => ({
    value: v,
    itemStyle: { color: pal[i % pal.length], borderRadius: [4, 4, 0, 0] },
  }))
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = list[i] || {}
        return `<b>${it.bucket}</b><br/>失败SN数: <b>${Number(it.sn_cnt) || 0}</b> 个<br/>涉及金额: ¥${formatMoney(Number(it.amount) || 0)}`
      },
    },
    grid: { left: 56, right: 32, top: 24, bottom: 36 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 12 } },
    yAxis: { type: 'value', name: '失败SN数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
    series: [
      {
        name: '失败SN数',
        type: 'bar',
        data: barData,
        barMaxWidth: 48,
        label: { show: true, position: 'top', fontSize: 11, formatter: '{c}' },
      },
    ],
  }
})

// === 图表: 设备接待次数分布 (双Y轴 柱+线) ===
const receptionCountOption = computed(() => {
  const list = receptionCountDist.value || []
  if (!list.length) return {}
  const x = list.map(r => r.bucket)
  const deviceCnt = list.map(r => Number(r.device_cnt) || 0)
  const failRates = list.map(r => Number(r.fail_rate) || 0)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = list[i] || {}
        return `<b>${it.bucket}</b><br/>设备数: <b>${Number(it.device_cnt) || 0}</b> 个<br/>失败率: <b>${Number(it.fail_rate) || 0}%</b>`
      },
    },
    legend: { data: ['设备数', '失败率%'], top: 0, type: 'scroll' },
    grid: { left: 56, right: 64, top: 36, bottom: 36 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 12 } },
    yAxis: [
      { type: 'value', name: '设备数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '失败率%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '设备数',
        type: 'bar',
        data: deviceCnt,
        barMaxWidth: 40,
        itemStyle: { color: '#60a5fa', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', fontSize: 11, formatter: '{c}' },
      },
      {
        name: '失败率%',
        type: 'line',
        yAxisIndex: 1,
        data: failRates,
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        itemStyle: { color: '#7c3aed' },
        lineStyle: { width: 3, color: '#7c3aed' },
      },
    ],
  }
})
</script>

<style scoped>
.section-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.block-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #111827;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb-12 {
  margin-bottom: 12px;
}

/* 风险 KPI 卡片 */
.risk-card {
  border-radius: 10px;
  padding: 14px 16px 12px;
  border: 1px solid rgba(148, 163, 184, 0.15);
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 96px;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.risk-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px -8px rgba(15, 23, 42, 0.18);
}
/* #205 可下钻卡片: cursor:pointer(规范 §3.5); hover 反馈复用 .risk-card:hover */
.drillable { cursor: pointer; }
.risk-label {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.2px;
}
.risk-sub {
  font-size: 11px;
  color: #6b7280;
  opacity: 0.9;
}
.risk-value {
  margin-top: 4px;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.3px;
}
.risk-value .unit {
  font-size: 13px;
  font-weight: 500;
  margin-left: 2px;
  opacity: 0.85;
}
.risk-red {
  background: #fef2f2;
}
.risk-red .risk-label,
.risk-red .risk-value {
  color: #dc2626;
}
.risk-orange {
  background: #fff7ed;
}
.risk-orange .risk-label,
.risk-orange .risk-value {
  color: #ea580c;
}
.risk-purple {
  background: #faf5ff;
}
.risk-purple .risk-label,
.risk-purple .risk-value {
  color: #7c3aed;
}
.risk-gray {
  background: #f3f4f6;
}
.risk-gray .risk-label,
.risk-gray .risk-value {
  color: #6b7280;
}

/* 抽屉内画像 */
.drawer-content {
  padding: 0 4px;
}
.kv {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 13px;
}
.kv .k {
  color: #6b7280;
  min-width: 64px;
  flex-shrink: 0;
}
.kv .v {
  color: #111827;
  font-weight: 500;
  word-break: break-all;
}

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .filter-row, .flex-between { flex-wrap: wrap; gap: 6px; }
  .block-title { font-size: 14px; }
  .section-card { padding: 12px; }
  :deep(.el-table) { min-width: 620px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
  :deep(.el-card) { overflow: hidden; }
  .risk-card { padding: 10px; }
}
</style>
