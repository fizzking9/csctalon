<!--
  ┌─────────────────────────── 设计说明（Design Rationale） ───────────────────────────┐
  │ 目的：替**退款挽回主管**在晨会快速回答「今天拦住了多少、漏了多少、钱漏在哪类设备/  │
  │       哪类原因、哪个人的环节出了问题」，支撑拦截话术调整与人员绩效沟通。           │
  │ 逻辑：KPI 区先给分母与成败结果（后果指标）→ 趋势区看时段/日分布（定位异常时段）→   │
  │       高风险设备与原因分布定位「哪台设备/哪类原因」（可控切入点）→ 人员明细支撑    │
  │       绩效沟通。按需加载（点「分析」才请求），避免进页即产生大量查询。             │
  │ 口径：全部来自 GET /analytics/today/refund-recovery → 后端                        │
  │       app/services/analytics/refund.py::refund_recovery_analysis：                │
  │       分母 = cs_records 退款类接待设备SN去重（不限原因，排除系统别名/自动流水）；  │
  │       失败 = T+7 窗口内出现退款单/退款工单/接待记录退款关键词；                    │
  │       时间换算 AT TIME ZONE 'Asia/Shanghai'（数据时间字段使用规范）。              │
  │       各卡口径详见卡片 ⓘ，本注释不重复展开，避免两处漂移。                        │
  │ 取舍：① 拦截费占比用单量口径、不用金额口径 —— 成功SN未进入退款流程，无退款工单    │
  │         金额可对应，金额无法准确核算（后端 success_amount 恒为0）；                │
  │       ② 放弃/退充单不做SN级下钻 —— 后端无按此分类的明细接口，强做只会给空明细页； │
  │       ③ 进页不自动加载 —— 点「分析」才请求（既有按需加载约定）；                  │
  │       ④ 不做历史环比 —— 最近7天接待的 T+7 窗口未关闭，环比只会放大口径噪声。      │
  │ 工单：#205                                                                        │
  └──────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="page-title" style="margin: 0">
        挽回分析
        <!-- ★ #205 页面层可见设计说明（界面设计说明规范 §二.2.2：含「取舍」段） -->
        <el-tooltip effect="dark" placement="bottom" raw-content
          content="<b>目的</b>：替退款挽回主管在晨会快速回答「今天拦住了多少、漏了多少、钱漏在哪类设备/哪类原因/哪个人」<br/><b>逻辑</b>：KPI 先给分母与成败结果 → 趋势区看时段/日分布 → 高风险设备与原因分布定位问题 → 人员明细支撑绩效沟通<br/><b>口径</b>：全部来自 GET /analytics/today/refund-recovery（分母=退款类接待SN去重，失败=T+7 判定）；各卡口径见各区块 ⓘ；缓存与失效见 docs/standards/数据源接入与缓存加速规范.md<br/><b>⚠️ 取舍（不做什么及原因）</b><br/>① 拦截费占比用单量不用金额 —— 成功SN无退款工单金额可核算<br/>② 成功单金额不核算（同上）<br/>③ 放弃/退充单无SN级下钻 —— 后端无对应明细接口<br/>④ 进页不自动加载，点「分析」才请求<br/><b>工单</b>：#205">
          <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:14px"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
      <div class="filter-bar" :class="{ 'is-mobile': isMobile }">
        <el-radio-group v-model="tabMode" size="small" class="tab-mode-group" @change="onTabChange">
          <el-radio-button value="today">今日</el-radio-button>
          <el-radio-button value="personnel">人员</el-radio-button>
          <el-radio-button value="device">设备</el-radio-button>
          <el-radio-button value="type">类型</el-radio-button>
        </el-radio-group>
        <el-radio-group v-if="tabMode !== 'today'" v-model="rangeMode" size="small" class="range-mode-group">
          <el-radio-button value="7">近7天</el-radio-button>
          <el-radio-button value="30">近30天</el-radio-button>
          <el-radio-button value="90">近90天</el-radio-button>
          <el-radio-button value="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="tabMode !== 'today' && rangeMode === 'custom'"
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width: 240px"
        />
        <el-button type="primary" :icon="Search" :loading="analyzing" @click="runAnalysis">分析</el-button>
      </div>
    </div>

    <!-- 未分析时的空态提示 -->
    <section v-if="!analyzed" class="empty-hint">
      <el-empty :image-size="96" description="">
        <template #description>
          <div class="empty-tip">
            <div class="empty-main">设置筛选区间后，点击右上角 <el-button size="small" type="primary" :icon="Search" @click="runAnalysis">分析</el-button> 加载数据</div>
            <div class="empty-sub">按需加载 · 避免进入页面即产生大量请求</div>
          </div>
        </template>
      </el-empty>
    </section>

    <template v-else>
    <!-- ============ TAB1: 今日挽回 (原退款看板-今日视图) ============ -->
    <template v-if="tabMode === 'today'">
      <section class="section-card mb-24">
        <div class="flex-between mb-12">
          <div>
            <div class="block-title">
              今日退款挽回 · KPI 核心
              <!-- ★ #205 数据说明（看板卡片双强制 §二）：11 张 KPI 卡共用本 ⓘ（卡片紧凑，逐卡加图标会撑破布局），
                   必含来源/口径/时点/空的含义/下钻 + 取舍段；各卡下钻去向逐卡列在 ⓘ 内。 -->
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：GET /analytics/today/refund-recovery（后端 app/services/analytics/refund.py::refund_recovery_analysis）<br/><b>口径</b>：<br/>· 总接待SN(分母) = 当日<b>所有人员</b>（含专职挽回/组长主管/其他岗位）cs_records 中 problem_category=退款 的接待设备SN<b>去重</b>；不限退款原因；已排除系统操作别名与「系统自动…」类流水<br/>· 挽留失败(SN) = 该SN在【最早接待时间，+7天】T+7窗口内出现任一：refunds 退款单(状态≠无需退款/驳回) / 退款类服务工单(状态≠终止) / 接待记录含退款流程关键词(退款说明/退款后重新充值/要求退回微信/非挽回用户)<br/>· 挽留成功(SN) = 分母SN − 失败SN；成功率 = 成功SN ÷ 分母<br/>· 单量四类：退充 = 原因含「退充」；放弃 = 工单待处理 / 无需退款 / 申请中 / 线下渠道；失败 = 其余失败SN；成功单数 = 成功SN数；目标总单量 = 四类合计<br/>· 实际退款金额 = 失败金额 + 退充金额（元）；拦截费占比(单量) = 成功单数 ÷ (成功单数+实际退款单数)，目标 37%+<br/><b>时点</b>：窗口=今日（Asia/Shanghai）；接口缓存 60s<br/><b>「空」的含义</b>：未点「分析」前整页为空态；某类单量为 0 = 当日无该类SN<br/><b>下钻</b>：总接待SN/挽留成功/挽留失败/挽回成功率/成功单数 → 点卡跳「归因·绩效」区（按人/按原因构成，同一次接口返回窗口必然一致）；失败单数 → 点卡跳「高风险设备」区（SN级明细）；<b>放弃/退充/目标总单量/实际退款金额/拦截费占比不下钻</b>（理由见取舍③）<br/><b>⚠️ 注意</b>：最近 7 天接待的 T+7 窗口未关闭，失败偏低、成功率偏高属正常<br/><br/><b>⚠️ 取舍（不做什么及原因）</b><br/>① 拦截费占比用<b>单量</b>口径、不用金额口径 —— 挽回成功的SN未进入退款流程，无实际退款工单金额可对应，金额无法准确核算<br/>② 成功单金额不核算（同上，success_amount 恒为 0，不计入目标总金额）<br/>③ 放弃/退充单<b>不下钻</b> —— 后端无按此分类的 SN 级明细接口，强做只能给出空明细页（规范 §3.4）<br/>④ 不做历史环比 —— T+7 未关闭的近期数据环比只会放大口径噪声">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
            <div class="block-sub">新口径:退款专项组接待真退款类问题 4类 · T+7 判定失败</div>
          </div>
          <el-tag size="small" effect="dark" type="danger">{{ todayDateStr }}</el-tag>
        </div>
        <!-- ★ #205 卡片下钻（规范 §3.2③ 同页区块）：可下钻卡 = 总接待SN/挽留成功/挽留失败/挽回成功率/成功单数 →
             「归因·绩效」区（rr-attribution，按人/按原因构成）；失败单数 → 「高风险设备」区（rr-risk，SN级明细）。
             放弃单数/退充单/目标总单量/实际退款金额/拦截费占比不下钻：后端无按类别的SN级明细接口，
             且后三者的分子分母即上方单量卡，再下钻无新增信息（规范 §3.4，理由同步在上方 ⓘ）。 -->
        <el-row :gutter="12" class="mb-12">
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card kpi-clickable" style="background:#eff6ff" title="点击查看按人构成明细" @click="drillToSection('rr-attribution')">
              <div class="kpi-mini-label" style="color:#2563eb">总接待SN(分母)</div>
              <div class="kpi-mini-sub">退款专员真退款类去重</div>
              <div class="kpi-mini-value" style="color:#2563eb">
                {{ formatNum(recoveryData.kpi.total_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card kpi-clickable" style="background:#f0fdf4" title="点击查看按人构成明细" @click="drillToSection('rr-attribution')">
              <div class="kpi-mini-label" style="color:#16a34a">挽留成功</div>
              <div class="kpi-mini-sub">未进入退款流程</div>
              <div class="kpi-mini-value" style="color:#16a34a">
                {{ formatNum(recoveryData.kpi.success_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card kpi-clickable" style="background:#fef2f2" title="点击查看失败原因构成" @click="drillToSection('rr-attribution')">
              <div class="kpi-mini-label" style="color:#dc2626">挽留失败</div>
              <div class="kpi-mini-sub">最终完成退款</div>
              <div class="kpi-mini-value" style="color:#dc2626">
                {{ formatNum(recoveryData.kpi.failed_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card kpi-clickable" :style="rateBg(recoveryData.kpi.success_rate)" title="点击查看按人成功率构成" @click="drillToSection('rr-attribution')">
              <div class="kpi-mini-label" :style="rateColor(recoveryData.kpi.success_rate)">挽回成功率</div>
              <div class="kpi-mini-sub">成功SN / 总接待SN</div>
              <div class="kpi-mini-value" :style="rateColor(recoveryData.kpi.success_rate)">
                {{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#f3f4f6">
              <div class="kpi-mini-label" style="color:#6b7280">放弃单数</div>
              <div class="kpi-mini-sub">系统清退·无需退款等</div>
              <div class="kpi-mini-value" style="color:#6b7280">{{ formatNum(recoveryData.kpi.abandon_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.abandon_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card kpi-clickable" style="background:#fef2f2" title="点击查看失败设备明细" @click="drillToSection('rr-risk')">
              <div class="kpi-mini-label" style="color:#dc2626">失败单数</div>
              <div class="kpi-mini-sub">拦截失败·真实退款成功</div>
              <div class="kpi-mini-value" style="color:#dc2626">{{ formatNum(recoveryData.kpi.fail_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.fail_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card kpi-clickable" style="background:#f0fdf4" title="点击查看按人构成明细" @click="drillToSection('rr-attribution')">
              <div class="kpi-mini-label" style="color:#16a34a">成功单数</div>
              <div class="kpi-mini-sub">拦截成功·金额暂不核算</div>
              <div class="kpi-mini-value" style="color:#16a34a">{{ formatNum(recoveryData.kpi.success_cnt) }}<span class="kpi-mini-unit">单</span></div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#eff6ff">
              <div class="kpi-mini-label" style="color:#2563eb">退充单</div>
              <div class="kpi-mini-sub">正常退充·金额互抵</div>
              <div class="kpi-mini-value" style="color:#2563eb">{{ formatNum(recoveryData.kpi.recharge_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.recharge_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-mini-card" style="background:#f0fdfa">
              <div class="kpi-mini-label" style="color:#0f766e">目标总单量</div>
              <div class="kpi-mini-value" style="color:#0f766e">{{ formatNum(recoveryData.kpi.target_total_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.target_total_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="kpi-mini-card" style="background:#fff7ed">
              <div class="kpi-mini-label" style="color:#7c2d12">实际退款金额</div>
              <div class="kpi-mini-sub">失败金额 + 退充金额</div>
              <div class="kpi-mini-money" style="font-size:22px;color:#7c2d12;font-weight:700;margin-top:4px">¥{{ formatMoney(Number(recoveryData.kpi.actual_refund_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="24" :sm="8" :md="4">
            <div class="kpi-mini-card" :style="feeRatioBg(recoveryData.kpi.fee_ratio)">
              <div class="kpi-mini-label" :style="feeRatioColor(recoveryData.kpi.fee_ratio)">拦截费占比 (单量)</div>
              <div class="kpi-mini-sub">成功单数/(成功+实际退款单数) · 目标37%+</div>
              <div class="kpi-mini-value" :style="feeRatioColor(recoveryData.kpi.fee_ratio)">
                {{ Number(recoveryData.kpi.fee_ratio||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
              </div>
            </div>
          </el-col>
        </el-row>
      </section>

      <section class="section-card mb-24">
        <div class="block-title mb-12">按时间粒度监控</div>
        <el-row :gutter="16">
          <el-col :xs="24" :lg="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    今日 按小时 · 挽回趋势 (08:00 ~ 当前)
                    <!-- ★ #205 数据说明（原 tooltip 为交互警示，此处补齐来源/时点/空的含义/下钻声明，保留警示） -->
                    <el-tooltip raw-content effect="dark" placement="top"
                      content="<b>来源</b>：/analytics/today/refund-recovery 的 hourly_today<br/><b>口径</b>：按接待时间(Asia/Shanghai)小时分桶；堆叠柱=成功/失败SN数（总高=该小时接待SN），线=成功率%；失败=T+7 判定 —— 今日接待的 T+7 窗口未关闭，失败偏低、成功率偏高，仅供参考；凌晨 0-7 时基本无接待，已前端过滤<br/><b>时点</b>：接口缓存 60s<br/><b>「空」的含义</b>：某小时无接待则无柱；全天无接待显示「今日暂无小时级趋势数据」<br/><b>下钻</b>：图内暂不支持点击下钻 —— 明细接口按日筛选、无小时级SN明细，强点只会落到整天明细，属误导（规范 §3.4）">
                      <el-icon style="vertical-align:-2px;color:#b45309;cursor:help"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="success">日视图 · 小时粒度</el-tag>
                </div>
              </template>
              <BaseChart v-if="todayHourlyOption.series?.length" :option="todayHourlyOption" :height="isMobile ? '250px' : '340px'" />
              <el-empty v-else description="今日暂无小时级趋势数据" />
            </el-card>
          </el-col>
          <el-col :xs="24" :lg="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    近 7 天 · 按日 挽回趋势
                    <!-- ★ #205 数据说明（原 tooltip 为交互警示，此处补齐来源/时点/空的含义/下钻声明，保留警示） -->
                    <el-tooltip raw-content effect="dark" placement="top"
                      content="<b>来源</b>：/analytics/today/refund-recovery 的 daily_trend（前端取末尾 7 天）<br/><b>口径</b>：按接待日期(Asia/Shanghai)分桶；堆叠柱=成功/失败SN数，线=成功率%；失败=T+7 判定 —— 退款单/工单可能未同步，漏判会使失败偏低、成功率偏高，仅供参考<br/><b>时点</b>：接口缓存 60s<br/><b>「空」的含义</b>：当日无接待则该日无柱；无数据显示「暂无日趋势数据」<br/><b>下钻</b>：图内不下钻 —— 失败SN明细见「Top5 高风险设备」，原因构成见「归因·绩效」区（规范 §3.4）">
                      <el-icon style="vertical-align:-2px;color:#b45309;cursor:help"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="warning">日视图 · 日粒度</el-tag>
                </div>
              </template>
              <BaseChart v-if="dailyTrendOption.series?.length" :option="dailyTrendOption" :height="isMobile ? '250px' : '340px'" />
              <el-empty v-else description="暂无日趋势数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>

      <!-- ★ #205: id=rr-risk 是「失败单数」KPI 卡的下钻目标（SN级明细终点） -->
      <section id="rr-risk" class="section-card mb-24">
        <div class="block-title mb-12">高风险设备 · 金额分布</div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="14" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    Top5 高风险设备 (按退款金额倒序)
                    <!-- ★ #205 数据说明 -->
                    <el-tooltip raw-content effect="dark" placement="top"
                      content="<b>来源</b>：/analytics/today/refund-recovery 的 top_risk_devices<br/><b>口径</b>：T+7 判定失败的SN按失败金额倒序取前 5；金额=该SN各失败明细(refunds/工单)中金额最大者；失败原因/状态取对应明细；接待人=该SN的接待人员<br/><b>时点</b>：窗口=今日接待SN的 T+7 窗口；缓存 60s<br/><b>「空」的含义</b>：当日无失败SN时显示「暂无高风险设备」<br/><b>下钻</b>：已是SN级明细（终态），不再下钻">
                      <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="danger">T+7 失败</el-tag>
                </div>
              </template>
              <el-table :data="recoveryData.top_risk_devices || []" border stripe size="small" max-height="320">
                <el-table-column prop="device_sn" label="设备SN" min-width="140" fixed>
                  <template #default="{row}"><span style="font-weight:600">{{ row.device_sn }}</span></template>
                </el-table-column>
                <el-table-column prop="reason" label="失败原因" width="120">
                  <template #default="{row}">
                    <el-tag v-if="row.reason" size="small" :type="reasonTagType(row.reason)" effect="light">{{ row.reason }}</el-tag>
                    <span v-else style="color:#9ca3af">—</span>
                  </template>
                </el-table-column>
                <el-table-column prop="amount" label="退款金额" width="120" align="right" sortable>
                  <template #default="{row}">
                    <span :style="{color: Number(row.amount)>1000?'#dc2626':'#374151', fontWeight:600}">¥{{ formatMoney(row.amount) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="operator_name" label="接待人" min-width="100">
                  <template #default="{row}">{{ row.operator_name || row.operator || '—' }}</template>
                </el-table-column>
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{row}">
                    <el-tag v-if="row.status" size="small" :type="statusTagType(row.status)" effect="plain">{{ row.status }}</el-tag>
                    <span v-else style="color:#9ca3af">—</span>
                  </template>
                </el-table-column>
              </el-table>
              <el-empty v-if="!(recoveryData.top_risk_devices && recoveryData.top_risk_devices.length)" description="暂无高风险设备" />
            </el-card>
          </el-col>
          <el-col :xs="24" :md="10" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    失败设备 · 金额分桶
                    <!-- ★ #205 数据说明 -->
                    <el-tooltip raw-content effect="dark" placement="top"
                      content="<b>来源</b>：/analytics/today/refund-recovery 的 amount_buckets<br/><b>口径</b>：失败SN按退款金额区间分桶；计数=设备SN数，金额=桶内失败金额合计<br/><b>时点</b>：随接口缓存 60s<br/><b>「空」的含义</b>：当日无失败SN时为空<br/><b>下钻</b>：分桶本身即构成视图（终态），不下钻">
                      <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="info">按退款金额区间</el-tag>
                </div>
              </template>
              <BaseChart v-if="amountBucketOption.series?.length" :option="amountBucketOption" height="320px" />
              <el-empty v-else description="暂无金额分桶数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>

      <!-- ★ #205: id=rr-attribution 是「总接待SN/挽留成功/挽留失败/挽回成功率/成功单数」KPI 卡的下钻目标（按人/按原因构成） -->
      <section id="rr-attribution" class="section-card">
        <div class="block-title mb-12">归因 · 绩效 (今日视角)</div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    挽留失败 SN 原因分布(归一化 7 大类)
                    <!-- ★ #205 数据说明 -->
                    <el-tooltip raw-content effect="dark" placement="top"
                      content="<b>来源</b>：/analytics/today/refund-recovery 的 reason_dist<br/><b>口径</b>：失败SN按 cs_records.problem_type 归一化为 7 大类计数（后端 _normalize_refund_reason）；占比=类内SN ÷ 失败SN总数；金额=类内失败金额合计<br/><b>时点</b>：随接口缓存 60s<br/><b>「空」的含义</b>：当日无失败SN时为空<br/><b>下钻</b>：不下钻 —— 原因归类已是归因终点；「类型」页签可看跨期趋势但不支持按原因过滤，做下钻会丢上下文（规范 §3.4）">
                      <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="info">柱=单量 / 线=占比%</el-tag>
                </div>
              </template>
              <BaseChart v-if="recoveryData.reason_dist?.length" :option="reasonBarOption" height="320px" />
              <el-empty v-else description="暂无原因分布数据" />
            </el-card>
          </el-col>
          <el-col :xs="24" :md="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">
                    退款专员 接待/挽回/操作 明细
                    <!-- ★ #205 数据说明 -->
                    <el-tooltip raw-content effect="dark" placement="top"
                      content="<b>来源</b>：/analytics/today/refund-recovery 的 staff_detail<br/><b>口径</b>：接待SN=该人名下退款类接待去重；成功/失败=T+7 判定；成功率=成功 ÷ 接待；退款操作SN/金额=该人作为 refunds.operator 或退款工单 handler 的执行量（T+7 窗口）；同一SN多人接待<b>各算各的</b>（工作负荷口径）<br/><b>时点</b>：窗口=今日；缓存 60s<br/><b>「空」的含义</b>：当日无退款类接待时显示「暂无人员数据」<br/><b>下钻</b>：已是人员级明细（终态），不再下钻">
                      <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-tag size="small" effect="plain" type="primary">按 接待SN 倒序</el-tag>
                </div>
              </template>
              <el-table :data="recoveryData.staff_detail || []" border stripe size="small" max-height="360">
                <el-table-column label="姓名" width="100" fixed><template #default="{row}"><span style="font-weight:600">{{ row.name }}</span></template></el-table-column>
                <el-table-column label="组" width="96"><template #default="{row}"><el-tag size="small" effect="plain" :type="grpTagType(row.group)">{{ row.group||'未分组' }}</el-tag></template></el-table-column>
                <el-table-column prop="total_sn" label="接待SN" width="82" align="right" sortable><template #default="{row}">{{ formatNum(row.total_sn) }}个</template></el-table-column>
                <el-table-column prop="success_sn" label="成功" width="64" align="right"><template #default="{row}"><span style="color:#16a34a">{{ formatNum(row.success_sn) }}</span></template></el-table-column>
                <el-table-column prop="failed_sn" label="失败" width="64" align="right"><template #default="{row}"><span style="color:#dc2626">{{ formatNum(row.failed_sn) }}</span></template></el-table-column>
                <el-table-column prop="success_rate" label="成功率" width="84" align="right" sortable :sort-method="(a,b)=>Number(a.success_rate??-1)-Number(b.success_rate??-1)">
                  <template #default="{row}">
                    <template v-if="row.success_rate==null"><span style="color:#9ca3af">—</span></template>
                    <template v-else>
                      <el-tag size="small" :effect="Number(row.success_rate||0)>=38?'dark':'plain'" :type="Number(row.success_rate||0)>=38?'success':(Number(row.success_rate||0)>=30?'warning':'danger')">
                        {{ Number(row.success_rate||0).toFixed(1) }}%
                      </el-tag>
                    </template>
                  </template>
                </el-table-column>
                <el-table-column prop="refund_op_cnt" label="退款操作SN" width="100" align="right" sortable><template #default="{row}"><span :style="{color:Number(row.refund_op_cnt||0)>0?'#1d4ed8':'#9ca3af',fontWeight:Number(row.refund_op_cnt||0)>0?600:400}">{{ formatNum(row.refund_op_cnt) }}个</span></template></el-table-column>
                <el-table-column prop="refund_op_amount" label="退款操作金额" width="116" align="right" sortable><template #default="{row}"><span :style="{color:Number(row.refund_op_amount||0)>0?'#b91c1c':'#9ca3af',fontWeight:Number(row.refund_op_amount||0)>0?600:400}">¥{{ formatMoney(row.refund_op_amount) }}</span></template></el-table-column>
              </el-table>
              <el-empty v-if="!recoveryData.staff_detail?.length" description="暂无人员数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>
    </template>

    <!-- ============ TAB2: 人员分析 ============ -->
    <PersonnelAnalysis v-else-if="tabMode==='personnel'" :start="effectiveStart" :end="effectiveEnd" />

    <!-- ============ TAB3: 设备分析 ============ -->
    <DeviceAnalysis v-else-if="tabMode==='device'" :start="effectiveStart" :end="effectiveEnd" />

    <!-- ============ TAB4: 类型趋势 ============ -->
    <!-- ★ #205: TypeTrend 的组间/人员类型卡可下钻到「人员」页签（§3.2① 同页跳转），
         switch-tab 事件携带目标页签名；窗口经 props(:start/:end) 透传，上下文不丢 -->
    <TypeTrend v-else :start="effectiveStart" :end="effectiveEnd" @switch-tab="onChildSwitchTab" />
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Search, QuestionFilled } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import BaseChart from '@/components/BaseChart.vue'
import PersonnelAnalysis from './refund/PersonnelAnalysis.vue'
import DeviceAnalysis from './refund/DeviceAnalysis.vue'
import TypeTrend from './refund/TypeTrend.vue'
import { today } from '@/api/analytics'
import { listGroups } from '@/api/group'
import { formatMoney } from '@/utils/format'

const tabMode = ref('today')
const rangeMode = ref('30')
const customRange = ref([])
const loading = ref(false)
const analyzed = ref(false)
const analyzing = ref(false)

// 移动端响应式: 简化筛选栏文字/隐藏次要控件
const isMobile = ref(false)
let _mq = null
function _onMediaChange(e) { isMobile.value = e.matches }
onMounted(() => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
  // #95: 拉分组列表以建立「组名 → 稳定 id」映射（用于标签配色）
  loadGroups()
})
onBeforeUnmount(() => { _mq?.removeEventListener('change', _onMediaChange) })

const todayDateStr = computed(() => dayjs().format('YYYY-MM-DD (dddd)'))

const effectiveStart = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  return dayjs().format('YYYY-MM-DD')
})

function onTabChange() {
  // Tab 切换不自动加载, 保持"点分析才装载"约定
  analyzed.value = false
}

// ★ #205 卡片下钻（规范 §3.2③ 同页区块）：滚动到目标区。
// 目标区数据来自同一次接口返回（today.refundRecovery），窗口/口径与卡片所见必然一致。
function drillToSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// ★ #205 接收 TypeTrend 的下钻事件：切到「人员」页签看按人明细。
// 不重置 analyzed（数据已装载），窗口经 effectiveStart/effectiveEnd props 透传。
function onChildSwitchTab(tab) {
  if (tab) tabMode.value = tab
}

function runAnalysis() {
  analyzed.value = true
  analyzing.value = true
  if (tabMode.value === 'today') {
    loadAll()
  }
  setTimeout(() => { analyzing.value = false }, 300)
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}
function round2(v) {
  const n = Number(v || 0)
  return Math.round(n * 10) / 10
}

// ---- 三元样式 helpers ----
function rateBg(val) {
  const n = Number(val || 0)
  return { background: n >= 38 ? '#f0fdf4' : n >= 30 ? '#fefce8' : '#fef2f2' }
}
function rateColor(val) {
  const n = Number(val || 0)
  return { color: n >= 38 ? '#16a34a' : n >= 30 ? '#ca8a04' : '#dc2626' }
}
function feeRatioBg(val) {
  const n = Number(val || 0)
  return { background: n >= 37 ? '#f0fdf4' : '#fef2f2' }
}
function feeRatioColor(val) {
  const n = Number(val || 0)
  return { color: n >= 37 ? '#16a34a' : '#dc2626' }
}

// ---- 分组标签配色 ----
//
// 设计说明（Design Rationale）· #95
// ─────────────────────────────────────────────────────────────
// 目的：替**退款挽回主管**回答「这一行/这一格属于哪个组」——
//       用颜色区分可减少晨会传阅时的逐字阅读成本。
//
// 逻辑：**不按组名映射颜色，只按「稳定标识」取色**。
//
// 口径（数据判据）：组名来自 `persons.group_name` / 分组接口，
//       是**可变业务数据** —— 2026-09-14 组织调整已把旧组名全部改掉：
//         朱海洋组→专项1组 / 董梦玲组→接待2组 / 吴晓龙组→接待3组
//         叶珍凤组→接待1组 / 张蕾组→投诉专项组
//
// ★ 取舍（为什么不做"按名字映射颜色"）：
//   原实现 `GRP_COLOR_MAP = { '朱海洋组': 'success', '董梦玲组': 'warning', ... }`
//   把**业务数据当代码常量**用。组改名后 4/5 个 key 立即失效
//   → 所有标签静默退化为灰色 `info`，**颜色区分功能完全丧失且无任何报错**
//   （生产实测：persons.group_name 现值全部是新组名，映射表 0 命中）。
//
//   现改为「按分组名排序后的**下标**取色板」：
//     · 组名增删改**不影响**已有分组的颜色稳定性（下标位置不变即颜色不变）
//     · 新增组自动获得一个颜色，无需改代码
//   代价：分组超过色板长度后会循环复用颜色（可接受 —— 本场景 5 组）。
//
// ⚠️ 禁止回退为「组名 → 颜色」的硬编码映射。组名是数据，不是常量。
//    同类问题排查见 #95 正文（Today.vue / OperationDashboard.vue 已用
//    动态 `position`，是正确做法，可作为范式）。
//
// ★ 为什么用**组 id**（而非组名哈希/出现顺序）：
//   · 组名哈希：5 组对 5 色板**实测仍会碰撞**（接待2组 与 投诉专项组 同色）
//     —— 因为名长短相近、同后缀「组」，哈希落点集中；
//   · 出现顺序：颜色依赖行序 → 换筛选/翻页变色，比碰撞更混乱；
//   · **组 id** 是数据库主键，稳定、唯一，且与名字完全解耦（改名不失色）。
//   色板 5 色对 5 组 → 用 id 取模必然一一对应（组数 ≤ 色板长度时无碰撞）。
const GRP_COLOR_PALETTE = ['primary', 'success', 'warning', 'danger', 'info']
// 组名 → 组 id 的映射（由 loadGroups 填充）
const grpIdByName = ref({})
async function loadGroups() {
  try {
    const res = await listGroups({ is_active: true })
    const rows = res?.data || []
    const m = {}
    // 按 id 升序保证取模顺序稳定（后端已按 display_order/id 排，这里再兜一层）
    rows
      .slice()
      .sort((a, b) => (a.id || 0) - (b.id || 0))
      .forEach((g) => { m[g.name] = g.id })
    grpIdByName.value = m
  } catch (e) {
    // 拉不到分组不影响主流程 —— 退化为统一 info 色（清晰可辨的"未着色"）
    grpIdByName.value = {}
  }
}
function grpTagType(g) {
  if (!g || g === '未分组') return 'info'
  const id = grpIdByName.value[g]
  // 未加载到分组（接口失败/新增未刷新）→ info，而非"猜一个颜色"
  if (id === undefined) return 'info'
  return GRP_COLOR_PALETTE[id % GRP_COLOR_PALETTE.length]
}
const REASON_TAG_MAP = { '不想用了': 'info', '充值错误': 'warning', '设备故障': 'danger', '服务原因': 'primary', '其他': 'info' }
function reasonTagType(r) {
  if (!r) return 'info'
  return REASON_TAG_MAP[r] || 'info'
}
function statusTagType(s) {
  if (!s) return 'info'
  const t = String(s)
  if (t.includes('成功') || t.includes('完成')) return 'success'
  if (t.includes('失败') || t.includes('驳回')) return 'danger'
  if (t.includes('处理') || t.includes('审核')) return 'warning'
  return 'info'
}

// ---- 今日挽回数据 ----
function emptyKpi() {
  return {
    total_sn: 0, success_sn: 0, failed_sn: 0, success_rate: 0,
    abandon_cnt: 0, fail_cnt: 0, success_cnt: 0, recharge_cnt: 0, target_total_cnt: 0,
    abandon_amount: 0, fail_amount: 0, success_amount: 0, recharge_amount: 0,
    target_total_amount: 0, actual_refund_amount: 0, fee_ratio: 0,
  }
}
const recoveryData = ref({
  kpi: emptyKpi(), reason_dist: [], staff_detail: [], daily_trend: [], hourly_today: [],
  top_risk_devices: [], amount_buckets: [],
})

function _buildDoubleYOption({ x, barSeries = [], lineSeries = [], y2Min = null, y2Max = null, y2Suffix = '', legend = [], stack = false }) {
  const y1 = { type: 'value', name: '数量', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } }
  const y2 = {
    type: 'value', name: y2Suffix ? `百分比(${y2Suffix})` : '右',
    axisLabel: { fontSize: 11, formatter: y2Suffix ? `{value}${y2Suffix}` : '{value}' },
    splitLine: { show: false },
  }
  if (y2Min !== null && y2Min !== undefined) y2.min = y2Min
  if (y2Max !== null && y2Max !== undefined) y2.max = y2Max
  const series = []
  for (const b of barSeries) {
    series.push({
      name: b.name, type: 'bar', data: b.data, barMaxWidth: 20,
      stack: stack ? 'total' : undefined,
      itemStyle: { color: b.color || '#93c5fd', borderRadius: stack ? [0, 0, 0, 0] : [4, 4, 0, 0] },
    })
  }
  // 堆叠模式: 最后一根柱子的顶部加圆角, 形成圆顶效果
  if (stack && series.length) {
    const lastBar = series[series.length - 1]
    lastBar.itemStyle = { ...lastBar.itemStyle, borderRadius: [4, 4, 0, 0] }
  }
  for (const l of lineSeries) {
    series.push({ name: l.name, type: 'line', yAxisIndex: 1, data: l.data, smooth: true, symbol: 'circle', symbolSize: 7, itemStyle: { color: l.color || '#15803d' }, lineStyle: { width: l.lineWidth || 3, color: l.color || '#15803d' } })
  }
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      formatter: stack ? (params) => {
        // 堆叠模式: 汇总柱状系列总量, 并附带成功率线
        if (!params || !params.length) return ''
        const bars = params.filter(p => p.seriesType === 'bar')
        const line = params.find(p => p.seriesType === 'line')
        let html = `<b>${params[0].axisValue}</b>`
        let total = 0
        for (const b of bars) {
          total += Number(b.value) || 0
          html += `<br/>${b.marker}${b.seriesName}: <b>${Number(b.value) || 0}</b>`
        }
        if (line) html += `<br/>${line.marker}${line.seriesName}: <b>${line.value}</b>`
        html += `<br/>合计: <b>${total}</b>`
        return html
      } : undefined,
    },
    legend: { data: legend || [], top: 0, type: 'scroll' },
    grid: { left: 56, right: 72, bottom: 44, top: 44 },
    xAxis: { type: 'category', data: x, boundaryGap: true, axisLabel: { fontSize: 11 } },
    yAxis: [y1, y2],
    series,
    animationDuration: 400,
  }
}

const todayHourlyOption = computed(() => {
  const hl = recoveryData.value?.hourly_today || []
  if (!hl || !hl.length) return {}
  // 客服工作时段: 只展示 08:00 ~ 23:59, 过滤凌晨 0~7 点(基本无接待,全是空)
  const workHours = hl.filter(r => {
    const h = parseInt(r.hour, 10)
    return !isNaN(h) && h >= 8 && h <= 23
  })
  if (!workHours.length) return {}
  const x = workHours.map(r => r.hour)
  const succ = workHours.map(r => Number(r.success_sn) || 0)
  const fail = workHours.map(r => Number(r.failed_sn) || 0)
  const rates = workHours.map(r => {
    const t = Number(r.total_sn) || 0
    return t ? round2(Number(r.success_sn) / t * 100) : 0
  })
  return _buildDoubleYOption({
    x, stack: true, barSeries: [
      // 失败在下(红)、成功在上(绿), 堆叠总高 = 总接待量
      { name: '失败数', data: fail, color: '#fca5a5' },
      { name: '成功数', data: succ, color: '#34d399' },
    ],
    lineSeries: [{ name: '成功率%', data: rates, color: '#047857', lineWidth: 3 }],
    y2Min: 0, y2Max: 100, y2Suffix: '%',
    legend: ['成功数', '失败数', '成功率%'],
  })
})

const dailyTrendOption = computed(() => {
  const dl = recoveryData.value?.daily_trend || []
  if (!dl || !dl.length) return {}
  const sliceN = Math.min(7, dl.length)
  const arr = dl.slice(-sliceN)
  const x = arr.map(r => r.date ? String(r.date).slice(5) : '')
  const succ = arr.map(r => Number(r.success_sn) || 0)
  const fail = arr.map(r => Number(r.failed_sn) || 0)
  const rates = arr.map(r => round2(Number(r.success_rate) || 0))
  return _buildDoubleYOption({
    x, stack: true, barSeries: [
      // 失败在下(红)、成功在上(绿), 堆叠总高 = 总接待量
      { name: '失败数', data: fail, color: '#f87171' },
      { name: '成功数', data: succ, color: '#4ade80' },
    ],
    lineSeries: [{ name: '成功率%', data: rates, color: '#059669', lineWidth: 3 }],
    y2Min: 0, y2Max: 100, y2Suffix: '%',
    legend: ['成功数', '失败数', '成功率%'],
  })
})

const reasonBarOption = computed(() => {
  const list = recoveryData.value?.reason_dist || []
  if (!list.length) return {}
  const labels = list.map(x => x.reason)
  const cnts = list.map(x => Number(x.order_cnt) || 0)
  const ratios = list.map(x => round2(Number(x.ratio) || 0))
  const pal = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#6366f1', '#ec4899', '#0ea5e9']
  const barData = cnts.map((v, i) => ({ value: v, itemStyle: { color: pal[i % pal.length], borderRadius: [4, 4, 0, 0] } }))
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        if (!params || !params.length) return ''
        const i = params[0].dataIndex
        const it = list[i] || {}
        return `<b>${it.reason}</b><br/>失败SN: <b>${Number(it.order_cnt)||0}</b> (占比 ${round2(Number(it.ratio)||0)}%)<br/>涉及金额: ¥${formatMoney(Number(it.amount)||0)}`
      }
    },
    grid: { left: 56, right: 64, top: 36, bottom: 56 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11, rotate: 12, interval: 0 } },
    yAxis: [
      { type: 'value', name: '失败SN数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '占比%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    legend: { data: ['失败SN数', '占比%'], top: 0, type: 'scroll' },
    series: [
      { name: '失败SN数', type: 'bar', data: barData, barMaxWidth: 34, label: { show: true, position: 'top', fontSize: 11, formatter: (p) => `${p.value} (${ratios[p.dataIndex]}%)` } },
      { name: '占比%', type: 'line', yAxisIndex: 1, data: ratios, smooth: true, symbol: 'circle', symbolSize: 7, itemStyle: { color: '#7c3aed' }, lineStyle: { width: 3, color: '#7c3aed' } },
    ],
  }
})

const amountBucketOption = computed(() => {
  const list = recoveryData.value?.amount_buckets || []
  if (!list.length) return {}
  const palette = ['#93c5fd', '#fbbf24', '#fb923c', '#dc2626']
  const data = list.map((b, i) => ({ name: b.bucket, value: b.sn_cnt, amount: b.amount, ratio: b.ratio, itemStyle: { color: palette[i % palette.length] } }))
  return {
    tooltip: { trigger: 'item', formatter: (p) => {
      const d = p.data || {}
      return `<b>${p.name}</b><br/>设备数: <b>${p.value}</b> 个 (${Number(d.ratio||0).toFixed(1)}%)<br/>金额合计: ¥${formatMoney(d.amount)}`
    } },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['42%', '68%'], center: ['50%', '46%'], avoidLabelOverlap: true,
      label: { show: true, formatter: (p) => `${p.name}\n${p.value}个`, fontSize: 11 },
      data,
    }],
  }
})

async function loadAll() {
  loading.value = true
  try {
    const params = { start: dayjs().format('YYYY-MM-DD'), end: dayjs().format('YYYY-MM-DD') }
    const res = await today.refundRecovery(params)
    recoveryData.value = Object.assign(
      { kpi: emptyKpi(), reason_dist: [], staff_detail: [], daily_trend: [], hourly_today: [], top_risk_devices: [], amount_buckets: [] },
      res.data || {}
    )
  } catch (e) {
    // 静默,页面保留空态
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.filter-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.flex-between { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.empty-hint { background: #fff; border-radius: 8px; padding: 48px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.empty-tip { text-align: center; }
.empty-main { font-size: 15px; color: #374151; margin-bottom: 8px; display: flex; align-items: center; justify-content: center; gap: 8px; }
.empty-sub { font-size: 12px; color: #9ca3af; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.block-title { font-size: 17px; font-weight: 700; color: #111827; margin-bottom: 4px; }
.block-sub { font-size: 12px; color: #6b7280; margin-bottom: 16px; }
.section-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.kpi-mini-card {
  border-radius: 10px; padding: 12px 14px 10px; border: 1px solid rgba(148,163,184,0.15);
  display: flex; flex-direction: column; gap: 3px; min-height: 88px;
  transition: transform .15s ease, box-shadow .15s ease;
}
.kpi-mini-card:hover { transform: translateY(-1px); box-shadow: 0 6px 16px -8px rgba(15,23,42,0.18); }
.kpi-mini-label { font-size: 12px; font-weight: 600; line-height: 1.3; }
.kpi-mini-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; opacity: 0.9; }
.kpi-mini-value { margin-top: 2px; font-size: 22px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-mini-unit { font-size: 13px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
.kpi-mini-money { margin-top: 1px; font-size: 11.5px; font-weight: 500; color: #374151; opacity: 0.92; }
/* ★ #205 可下钻卡片：cursor + hover 反馈（规范 §3.5），hover 位移复用 .kpi-mini-card:hover */
.kpi-clickable { cursor: pointer; }

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .filter-bar { gap: 8px; }
  .filter-bar.is-mobile .el-radio-group { margin-bottom: 2px; }
  .page-title { font-size: 18px; }
  .block-title { font-size: 15px; }
  .block-sub { font-size: 11px; }
  .section-card { padding: 12px; }
  .kpi-mini-card { padding: 8px 10px 8px; min-height: 72px; }
  .kpi-mini-label { font-size: 11px; }
  .kpi-mini-value { font-size: 19px; }
  .kpi-mini-sub { font-size: 9.5px; }
  .kpi-mini-money { font-size: 10.5px; }
  /* 表格在手机上保持可横向滚动 */
  :deep(.el-card) { overflow: hidden; }
  :deep(.el-table) { min-width: 560px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
}
</style>
