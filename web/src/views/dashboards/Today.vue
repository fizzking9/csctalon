<template>
  <div class="page-container today-dashboard">
    <!-- 顶部标题区 -->
    <div class="flex-between mb-12 td-header">
      <div class="page-title" style="margin: 0">
        {{ pageTitle }}
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag v-if="showRealtimeTag" size="small" type="success" effect="plain" style="margin-left: 8px">实时数据</el-tag>
      </div>
      <div class="header-actions">
        <el-segmented v-model="viewMode" size="small" :options="[{ label: '日', value: 'day' }, { label: '周', value: 'week' }]" @change="onViewModeChange" />
        <el-date-picker
          v-model="selectedDate"
          type="date"
          value-format="YYYY-MM-DD"
          format="YYYY-MM-DD"
          :clearable="false"
          :disabled-date="(d) => d > new Date()"
          size="small"
          style="width: 150px"
          @change="onDateChange"
        />
        <el-button size="small" :loading="loading" @click="loadAll({ force: true })">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-tag v-if="lastUpdate && showRealtimeTag" size="small" effect="plain" type="info">
          更新于 {{ lastUpdate }}
        </el-tag>
      </div>
    </div>

    <!-- 值班组长提示条(仅日视图)
         ★ 2026-09-14 用户要求: 「叶珍凤、董梦玲、吴晓龙、黄亚康、洪佳都是组长；
           我希望无论是今日值班看板还是运营分析 都能很明显的标识为组长。
           陈卓、朱海洋 都是主管级别的；可以标识为 管理。」

         设计说明（Design Rationale）
         ─────────────────────────────────────────────
         目的（替谁回答什么问题）
           替**值班主管**回答「现在当班的是谁、他是什么角色」。
           原实现只显示姓名+组名，读者无法一眼分辨谁是组长、谁是主管 ——
           而"找谁决策"恰恰取决于角色（组长管本组，主管管跨组）。

         逻辑（为什么这样组织）
           · 角色徽标**前置**于姓名：视觉扫描顺序是"角色→人"，与找人的心理顺序一致
           · 三个层级用**不同色系**区分（主管=红/副主管=橙/组长=蓝），
             不靠读字也能分辨，减少晨会传阅时的误读
           · 徽标文案用**管理视角**的词（主管→「管理」），而非职级名 ——
             使用者关心的是"他能拍板什么"，不是"他叫什么级别"

         口径（数据判据）
           · 取自 `staff` 接口的 `position`（客服/投诉专员/组长/副主管/主管）
           · ⚠️ 不能直接用 `is_management` —— 它含"专员"类跨类豁免岗（投诉专员等），
             那些**不是**值班决策人（见下方 DUTY_POSITIONS 注释）
           · 只显示 `is_virtual=false` 的真人

         取舍（不做什么及原因）
           · 不显示"客服"徽标 —— 值班条只列管理岗，客服本就不在此处，
             给全员加徽标会让这条窄横幅信息过载
           · 不用图标代替文字 —— 职级图标无通用语义，需图例反而增加理解成本
           · 不做点击筛选 —— 值班条是"看一眼"的组件，交互留给下方人员表 -->
    <div v-if="viewMode === 'day' && dutyLeaders.length" class="duty-leader-bar mb-12">
      <div class="duty-label">
        <el-icon><Timer /></el-icon>
        <span>今日值班</span>
      </div>
      <el-space wrap size="small" class="duty-chips">
        <el-tag
          v-for="(l, i) in dutyLeaders"
          :key="i"
          :type="l.tagType"
          effect="dark"
          size="small"
          class="duty-chip"
          @click="openPersonAudit(l.name)"
        >
          <!-- 角色徽标（前置，先看见角色再看见人） -->
          <span v-if="l.roleLabel" class="chip-role" :style="{ background: l.roleBg }">
            {{ l.roleLabel }}
          </span>
          <span class="chip-name">{{ l.name }}</span>
          <span class="chip-sep">·</span>
          <span class="chip-group">{{ l.group || l.department }}</span>
        </el-tag>
      </el-space>
    </div>

    <!-- 首页突出: 当日影响事件横幅(仅日视图显示) -->
    <el-alert
      v-if="viewMode === 'day' && activeIncidents.length"
      :title="impactBannerTitle"
      :type="hasHighSeverityIncident ? 'error' : 'warning'"
      show-icon
      :closable="false"
      class="impact-banner mb-16"
    >
      <div class="impact-banner-body">
        <el-tag
          v-for="inc in activeIncidents.slice(0, 4)"
          :key="inc.id"
          :type="inc.severity === '高' ? 'danger' : (inc.is_recurring ? 'warning' : 'info')"
          size="small"
          :effect="inc.severity === '高' ? 'dark' : 'plain'"
          class="impact-chip"
          @click="goToImpactSection"
        >
          <span v-if="inc.is_recurring" class="impact-recur-mark">周期</span>
          {{ inc.title }}
        </el-tag>
        <el-link v-if="activeIncidents.length > 4" type="primary" underline="never" style="margin-left:4px" @click="goToImpactSection">
          +{{ activeIncidents.length - 4 }} 更多
        </el-link>
      </div>
    </el-alert>

    <!-- 模块1: 核心KPI (紧凑横排)
         ★ #76: 每张卡可点下钻 → 对应分析页(带当前窗口)。
           按规范 §3.5 加 cursor:pointer 与 hover 反馈，并有 title 提示。 -->
    <!-- ★ #78 接待来源分层（设计说明 Design Rationale）
         目的: 替运营主管回答「AI 客服接管了多少接待？真人还剩多少工作量？」
         逻辑: 「接待数/会话数」两张卡下方加一条**堆叠比例条**，按来源配色；
               卡片副行显示「AI 接管 X%」。AI 卡单独列出，不与真人混排。
         口径: 来源判据 = operator + description 文本模式（平台无 AI 标志列），
               详见 app/services/system_accounts.classify_cs_record。
         取舍: ① 不做「AI 转人工」细分（BMS 与智能客服仍在迭代，字段不稳，做了会返工）；
               ② 不删 AI 数据 —— 只分层展示，因为「AI 接管率」本身是可控指标；
               ③ 真人名下的「系统标记」行保留在真人（实测 >50% 与真人操作同行同时刻，
                  属连带记录；拆出会虚减人工工作量 18%）。
         工单: #78 / 架构: docs/arch/A-20260913-001-AI接待口径分层.md -->
    <section class="section-card kpi-strip mb-12">
      <div class="kpi-strip-title">
        <span>核心指标</span>
        <!-- ★ #78 数据说明（规范 §二 双强制：① 有 ⓘ 说明 ② 可下钻）
             必含「取舍」段（界面设计说明规范 §一）。 -->
        <el-tooltip effect="dark" placement="top" raw-content
          content="<b>来源</b>：/analytics/today/kpi（接待=cs_records）<br/><b>口径</b>：<br/>· 接待数 = 去重设备 SN（1 台设备多次进线算 1 次）<br/>· 会话数 = 接待记录条数（2026-09-13 起已按业务键去重，见 #77）<br/>· 来源分层 = 按「操作人 + 说明文本」判定：<b>真人</b>（企微/店铺客服，含人工点 AI 助手）· <b>AI 全自动</b>（ai-acc 平台 WeChat 账户，全程无人工）· <b>系统内部</b>（无人工痕迹的标记/执行流水）· <b>客户自助</b>（客户在公众号自行操作）<br/><b>时点</b>：今日视图 60s 自动刷新；历史窗口缓存 24h<br/><b>「空」的含义</b>：分层条不显示 = 该来源本期无记录；四项全空 = 本期无接待数据<br/><b>下钻</b>：点卡片 → 接待分析页（带当前时间窗口）<br/><br/><b>⚠️ 取舍（不做什么及原因）</b><br/>① <b>不做</b>「AI 转人工」细分 —— BMS 与智能客服仍在迭代，字段不稳定，现在做会返工；待平台提供稳定标记列后再加<br/>② <b>不删</b> AI 记录，只分层展示 —— 「AI 接管率」本身是可控指标，删了就看不见<br/>③ 真人客服名下若有「系统标记」行，<b>仍计入真人</b> —— 实测过半与真人操作同秒发生，属连带记录；拆出会虚减人工工作量约 18%<br/>④ <b>不与历史直接比较</b> —— 会话数自 2026-09-13 起才按去重口径，前期数字偏大">
          <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
        </el-tooltip>
      </div>
      <div class="kpi-strip-grid">
        <div v-for="c in kpiCards" :key="c.key" class="kpi-strip-item kpi-drillable" :class="c.colorClass"
             :title="`点击查看「${c.label}」的构成明细`"
             @click="onKpiClick(c.key)">
          <div class="ksi-icon"><el-icon :size="16"><component :is="c.icon" /></el-icon></div>
          <div class="ksi-body">
            <div class="ksi-label">{{ c.label }}</div>
            <div class="ksi-value-row">
              <span class="ksi-value">{{ c.displayValue }}</span>
              <span v-if="c.yoy" class="ksi-yoy" :class="c.yoyClass">{{ c.yoy }}</span>
            </div>
            <!-- ★ #78 来源堆叠比例条(仅接待数/会话数两张卡) -->
            <div v-if="c.stack && c.stack.total > 0" class="ksi-stack"
                 :title="stackTip(c.stack)">
              <span v-for="seg in c.stack.segs" :key="seg.key"
                    class="ksi-stack-seg"
                    :style="{ width: seg.pct + '%', background: seg.color }" />
            </div>
            <!-- ★ #78 AI 接管率(可控指标前置) -->
            <div v-if="c.stack && c.stack.total > 0" class="ksi-sub">
              <span v-for="seg in c.stack.segs.filter(s => s.value > 0)" :key="seg.key"
                    class="ksi-sub-item">
                <i class="ksi-dot" :style="{ background: seg.color }" />{{ seg.short }}{{ seg.pct }}%
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>


    <!-- 模块2: 趋势(日=小时粒度,周=日粒度) -->
    <section class="section-card mb-16">
      <div class="section-header-row">
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
          <h3 class="section-title">
            {{ viewMode === 'week' ? '本周趋势' : '实时趋势' }}
            <!-- ★ #76 数据说明（规范 §二）: 来源 / 口径 / 时点 -->
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/today/hourly（日）· /today/kpi（周）<br/><b>口径</b>：日=按小时切面，接待/投诉/退款三类分别计数；周=按日切面<br/>· 接待 = 会话数（去重设备SN）<br/>· <b>投诉 = 登记(柱) + 处理(线)</b>：登记=工单登记时间落桶；处理=完成时间落桶且真实完成(≥3min)，<b>含往期单在本时段办结</b><br/>　→ 柱&gt;线=在累积，柱&lt;线=在消化<br/>　→ 口径与「核心指标·投诉数」及下方四卡<b>同源</b>（不含卡商，已扣补充表重叠）<br/><b>时点</b>：今日视图 60s 自动刷新，历史窗口缓存 24h<br/><b>下钻</b>：图内暂不支持点击下钻（明细接口按日筛选，无小时级），请用下方各专项卡片下钻">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip>
          </h3>
          <div class="section-desc">{{ viewMode === 'week' ? '本周每日接待/投诉/退款情况' : '今日各时段接待与退款情况' }}</div>
        </div>
        <el-checkbox-group v-model="trendSelectedGroups" size="small" class="trend-group-check">
          <el-checkbox-button value="reception">接待</el-checkbox-button>
          <el-checkbox-button value="complaint">投诉</el-checkbox-button>
          <el-checkbox-button value="refund">退款</el-checkbox-button>
        </el-checkbox-group>
      </div>
      <!-- ⚠️ 不下钻声明（规范 §3.4）: 趋势图为「小时/日」粒度切面，
           而各明细接口按【日】筛选，无小时级下钻能力。若强行点击下钻，
           只能落到「那一天的全部明细」——与用户点击的"某个时段"不一致，属误导。
           故此处**不做图内点击下钻**，改用下方各专项卡片（按日粒度）承接。
           若未来明细接口支持小时筛选，再开放（见 #68 指标体系研究）。 -->
      <ChartCard body-style="padding: 12px">
        <BaseChart :option="trendOption" height="220px" @chartClick="onChartClick" />
      </ChartCard>
    </section>

    <!-- 模块3: 影响事件 & 当前政策 (周视图只显示政策) -->
    <el-row :gutter="12" class="mb-16">
      <!-- 影响事件(仅日视图) -->
      <el-col v-if="viewMode === 'day'" :xs="24" :md="12">
        <section class="section-card h-full">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#dc2626; margin-right:4px"><WarningFilled /></el-icon>
              今日影响事件
            </h3>
            <el-tag v-if="queueData.incidents && queueData.incidents.length" type="danger" size="small" effect="dark">
              {{ queueData.incidents.length }} 条待处理
            </el-tag>
          </div>
          <div v-if="queueData.incidents && queueData.incidents.length" class="incident-list">
            <div v-for="(inc, i) in queueData.incidents.slice(0, 5)" :key="i" class="incident-item">
              <el-tag v-if="inc.is_recurring" type="warning" size="small" effect="dark" title="周期性事件">周期</el-tag>
              <el-tag :type="incidentTagType(inc.status)" size="small" effect="plain">
                {{ incidentStatusLabel(inc.status) }}
              </el-tag>
              <el-tag v-if="inc.severity === '高'" type="danger" size="small" effect="dark">高</el-tag>
              <span class="incident-title">{{ inc.title }}</span>
              <span class="incident-time">{{ formatShortTime(inc.occurred_at) }}</span>
            </div>
          </div>
          <div v-else class="empty-inline">
            <el-icon><CircleCheckFilled /></el-icon>
            <span>今日暂无影响事件</span>
          </div>
        </section>
      </el-col>

      <!-- 当前政策 (日=半宽,周=全宽) -->
      <el-col :xs="24" :md="viewMode === 'week' ? 24 : 12">
        <section class="section-card h-full">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#2563eb; margin-right:4px"><Document /></el-icon>
              当前执行政策
            </h3>
          </div>
          <div v-if="currentPolicies.length" class="policy-list">
            <div v-for="(p, i) in currentPolicies" :key="i" class="policy-item">
              <el-icon class="policy-icon"><CircleCheckFilled /></el-icon>
              <div class="policy-info">
                <div class="policy-name">{{ p.name }}</div>
                <div class="policy-desc">{{ p.description }}</div>
              </div>
              <el-tag size="small" :type="p.type === 'warning' ? 'warning' : 'success'">
                {{ p.status }}
              </el-tag>
            </div>
          </div>
          <div v-else class="empty-inline">
            <el-icon><Document /></el-icon>
            <span>暂无正在执行的政策</span>
          </div>
        </section>
      </el-col>
    </el-row>

    <!-- 模块4: 待办 & 专项任务 (仅日视图) -->
    <el-row v-if="viewMode === 'day'" :gutter="12" class="mb-16">
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#f59e0b; margin-right:4px"><Bell /></el-icon>
              我的待办
            </h3>
            <el-link type="primary" underline="never" @click="$router.push('/todos')">全部 →</el-link>
          </div>
          <div v-if="queueData.todos && queueData.todos.length" class="todo-list">
            <div v-for="(t, i) in queueData.todos.slice(0, 3)" :key="i" class="todo-item">
              <el-tag :type="t.priority === 'high' ? 'danger' : 'info'" size="small">
                {{ t.priority === 'high' ? '高' : '中' }}
              </el-tag>
              <span class="todo-title">{{ t.title }}</span>
            </div>
          </div>
          <div v-else class="empty-inline">
            <el-icon><CircleCheckFilled /></el-icon>
            <span>暂无待办事项</span>
          </div>
        </section>
      </el-col>
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <div class="section-header-row">
            <h3 class="section-title">
              <el-icon style="color:#7c3aed; margin-right:4px"><List /></el-icon>
              进行中专项
            </h3>
            <el-link type="primary" underline="never" @click="$router.push('/tasks')">全部 →</el-link>
          </div>
          <div v-if="queueData.tasks && queueData.tasks.length" class="task-list">
            <div v-for="(t, i) in queueData.tasks.slice(0, 3)" :key="i" class="task-item">
              <el-tag type="primary" size="small">{{ taskStatusLabel(t.status) }}</el-tag>
              <span class="task-title">{{ t.title }}</span>
              <el-tag v-if="t.type" size="small" type="info" effect="plain">{{ t.type }}</el-tag>
            </div>
          </div>
          <div v-else class="empty-inline">
            <el-icon><CircleCheckFilled /></el-icon>
            <span>暂无进行中专项</span>
          </div>
        </section>
      </el-col>
    </el-row>

    <!-- 模块5: 退款挽回快照
         ★ #76: 4 个数字格均可点 → 挽回分析页（带当前窗口）。
           ⚠️ 原「详细分析 →」指向 /dashboards/refund（退款分析），
              但本区块数据来自**挽回**（recoveryData），两页不同 —— 已按数据源
              把新增下钻指向 /dashboards/refund-recovery（挽回分析），避免口径错配。 -->
    <section v-if="hasRecovery" class="section-card mb-12">
      <div class="section-header-row">
        <h3 class="section-title">
          {{ viewMode === 'week' ? '退款挽回本周快照' : '退款挽回今日快照' }}
          <el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：退款挽回接口（refund-recovery）<br/><b>口径</b>：按 SN 计挽回成功/失败；成功率 = 成功/申请<br/><b>时点</b>：今日视图 60s 自动刷新<br/><b>下钻</b>：点击任一数字 → 挽回分析页（带当前窗口）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </h3>
        <el-link type="primary" underline="never" @click="drillToRefundRecovery()">详细分析 →</el-link>
      </div>
      <el-row :gutter="10" class="mb-8">
        <!-- 左侧: 4个紧凑数字 -->
        <el-col :xs="24" :md="14">
          <div class="compact-kpi-row">
            <div class="ck-cell ck-drillable" style="background:#eff6ff"
                 title="点击查看挽回申请明细（带当前时间窗口）" @click="drillToRefundRecovery()">
              <div class="ck-label">{{ viewMode === 'week' ? '本周申请' : '今日申请' }}</div>
              <div class="ck-value" style="color:#1d4ed8">{{ formatNum(recoveryData.kpi.total_sn) }}</div>
            </div>
            <div class="ck-cell ck-drillable" style="background:#f0fdf4"
                 title="点击查看挽回成功明细（带当前时间窗口）" @click="drillToRefundRecovery()">
              <div class="ck-label">挽回成功</div>
              <div class="ck-value" style="color:#16a34a">{{ formatNum(recoveryData.kpi.success_sn) }}</div>
            </div>
            <div class="ck-cell ck-drillable" style="background:#fef2f2"
                 title="点击查看挽回失败明细（带当前时间窗口）" @click="drillToRefundRecovery()">
              <div class="ck-label">挽回失败</div>
              <div class="ck-value" style="color:#dc2626">{{ formatNum(recoveryData.kpi.failed_sn) }}</div>
            </div>
            <div class="ck-cell ck-drillable" :style="successRateColor"
                 title="点击查看成功率构成（带当前时间窗口）" @click="drillToRefundRecovery()">
              <div class="ck-label">成功率</div>
              <div class="ck-value">{{ Number(recoveryData.kpi.success_rate || 0).toFixed(1) }}%</div>
            </div>
          </div>
          <div class="compact-pie-wrap">
            <div class="compact-pie-title">退款原因分布（成功/失败堆叠）</div>
            <BaseChart :option="recoveryReasonStackOption" height="150px" />
          </div>
        </el-col>
        <!-- 右侧: SN明细折叠入口提示(直接显示折叠在下方) -->
        <el-col :xs="24" :md="10">
          <div class="recovery-side">
            <div class="rs-title">成功率解读</div>
            <div class="rs-row"><span class="rs-dot" style="background:#16a34a"></span>达标 (≥38%)</div>
            <div class="rs-row"><span class="rs-dot" style="background:#ca8a04"></span>预警 (30%-38%)</div>
            <div class="rs-row"><span class="rs-dot" style="background:#dc2626"></span>告警 (&lt;30%)</div>
            <div class="rs-hint">
              {{ Number(recoveryData.kpi.success_rate || 0) >= 38 ? '当前挽回率达标,继续保持' : (Number(recoveryData.kpi.success_rate || 0) >= 30 ? '挽回率处于预警区间,请关注重点原因' : '挽回率偏低,需主管介入') }}
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 折叠: 退款挽回 SN 明细 (默认收起) -->
      <el-collapse v-if="recoverySnList.length" v-model="recoveryCollapseActive" class="recovery-sn-collapse">
        <el-collapse-item name="sn">
          <template #title>
            <span class="collapse-title">退款挽回 SN 明细（{{ refundFilteredList.length }}/{{ recoverySnList.length }} 条）</span>
          </template>
          <div class="sn-filter-bar">
            <el-select v-model="refundFilterResult" placeholder="结果" size="small" clearable style="width:100px;margin-right:6px">
              <el-option label="成功" value="success" />
              <el-option label="失败" value="failed" />
            </el-select>
            <el-select v-model="refundFilterHandler" placeholder="处理人" size="small" clearable filterable style="width:110px;margin-right:6px">
              <el-option v-for="h in refundHandlerOptions" :key="h" :label="h" :value="h" />
            </el-select>
            <el-select v-model="refundFilterReason" placeholder="退款原因" size="small" clearable filterable style="width:140px">
              <el-option v-for="r in refundReasonOptions" :key="r" :label="r" :value="r" />
            </el-select>
          </div>
          <el-table :data="refundFilteredList" size="small" border stripe max-height="320">
            <el-table-column label="时间" width="100">
              <template #default="{ row }">{{ formatShortTime(row.time) }}</template>
            </el-table-column>
            <el-table-column label="设备SN" min-width="140">
              <template #default="{ row }">
                <el-link type="primary" underline="never" @click="openDeviceAudit(row.sn)">{{ row.sn }}</el-link>
              </template>
            </el-table-column>
            <el-table-column label="结果" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.result === 'success' ? 'success' : 'danger'" size="small">
                  {{ row.result === 'success' ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="退款原因" min-width="120" show-overflow-tooltip />
            <el-table-column label="处理人" width="90">
              <template #default="{ row }">
                <el-link type="primary" underline="never" @click="openPersonAudit(row.staff)">{{ row.staff || '—' }}</el-link>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </section>

    <!-- 模块6: 投诉专项(含明细) -->
    <section class="section-card mb-16">
      <div class="section-header-row">
        <h3 class="section-title">
          <el-icon style="color:#7c3aed; margin-right:4px"><Bell /></el-icon>
          投诉专项
        </h3>
        <el-link type="primary" underline="never" @click="$router.push('/dashboards/complaint-analysis')">投诉分析 →</el-link>
      </div>

      <!-- 第一行: 今日/本周投诉来源(3个, 每个展示 登记/处理 双值) + 积压水位(独立维度)
           ★ #76: 每个来源卡可点 → 对应渠道明细（带当前窗口），规范 §3.2① -->
      <el-row :gutter="10" class="mb-8">
        <el-col :xs="24" :md="16">
          <div class="compact-kpi-row">
            <div class="ck-cell ck-drillable" style="background:#eff6ff"
                 title="点击查看「内部投诉」明细（带当前时间窗口）"
                 @click="onComplaintSourceClick('内部投诉')">
              <div class="ck-label">内部投诉</div>
              <div class="ck-rh">
                <div class="ck-rh-item">
                  <span class="ck-rh-num" style="color:#1d4ed8">{{ formatNum(complaintSummaryData.internal?.registered ?? complaintSummaryData.internal?.total) }}</span>
                  <span class="ck-rh-tag">登记</span>
                </div>
                <div class="ck-rh-divider" />
                <div class="ck-rh-item">
                  <span class="ck-rh-num" style="color:#16a34a">{{ formatNum(complaintSummaryData.internal?.handled) }}</span>
                  <span class="ck-rh-tag">处理</span>
                </div>
              </div>
            </div>
            <div class="ck-cell ck-drillable" style="background:#f5f3ff"
                 title="点击查看「商户号投诉」明细（带当前时间窗口）"
                 @click="onComplaintSourceClick('商户号投诉')">
              <div class="ck-label">商户号投诉</div>
              <div class="ck-rh">
                <div class="ck-rh-item">
                  <span class="ck-rh-num" style="color:#6d28d9">{{ formatNum(complaintSummaryData.merchant?.registered ?? complaintSummaryData.merchant?.total) }}</span>
                  <span class="ck-rh-tag">登记</span>
                </div>
                <div class="ck-rh-divider" />
                <div class="ck-rh-item">
                  <span class="ck-rh-num" style="color:#16a34a">{{ formatNum(complaintSummaryData.merchant?.handled) }}</span>
                  <span class="ck-rh-tag">处理</span>
                </div>
              </div>
            </div>
            <div class="ck-cell ck-drillable" style="background:#fef2f2"
                 title="点击查看「外部投诉」明细（带当前时间窗口）"
                 @click="onComplaintSourceClick('外部投诉')">
              <div class="ck-label">外部投诉</div>
              <div class="ck-rh">
                <div class="ck-rh-item">
                  <span class="ck-rh-num" style="color:#dc2626">{{ formatNum(complaintSummaryData.external?.registered ?? complaintSummaryData.external?.total) }}</span>
                  <span class="ck-rh-tag">登记</span>
                </div>
                <div class="ck-rh-divider" />
                <div class="ck-rh-item">
                  <span class="ck-rh-num" style="color:#16a34a">{{ formatNum(complaintSummaryData.external?.handled) }}</span>
                  <span class="ck-rh-tag">处理</span>
                </div>
              </div>
              <div v-if="supplExternal.total" class="ck-sub">315补充 {{ formatNum(supplExternal.total) }}条</div>
            </div>
          </div>
          <div v-if="complaintRegHandledOption" class="compact-pie-wrap">
            <div class="compact-pie-title">投诉来源 · 登记/处理（双柱）</div>
            <BaseChart :option="complaintRegHandledOption" height="170px" />
          </div>
          <div v-else-if="complaintCategoryOption" class="compact-pie-wrap">
            <div class="compact-pie-title">投诉分类分布</div>
            <BaseChart :option="complaintCategoryOption" height="150px" />
          </div>
        </el-col>
        <!-- 积压待处理(独立水位维度, 近30天窗口内登记的未完成)
             ★ #76: 可点下钻到执行跟踪页（带当前窗口） -->
        <el-col :xs="24" :md="8">
          <div class="backlog-watermark ck-drillable"
               :class="{ 'backlog-warn': (backlogTotal || 0) >= 20, 'backlog-danger': (backlogTotal || 0) >= 50 }"
               title="点击查看积压明细（执行跟踪，带当前时间窗口）"
               @click="drillToBacklog()">
            <div class="bw-head">
              <el-icon :size="16"><WarningFilled /></el-icon>
              <span class="bw-label">积压待处理</span>
              <el-tooltip content="近30天登记的未完成投诉工单数(不含更早历史遗留),反映近期投诉消化情况。点击查看明细。" placement="top">
                <el-icon style="color:inherit;opacity:.7"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
            <div class="bw-value">{{ formatNum(backlogTotal) }}</div>
            <div class="bw-sub">件未结案</div>
          </div>
        </el-col>
      </el-row>

      <!-- 补充数据源汇总(315/卡商, workbuddy推送, 按SN关联工单) -->
      <div v-if="supplSummaryText" class="suppl-strip">
        <el-icon style="margin-right:4px"><WarningFilled /></el-icon>
        <span>{{ supplSummaryText }}</span>
      </div>

      <!-- ★★ 2026-09-11 (#53): (外)卡商 **独立统计区块** ★★
           用户: "(外部)卡商 的投诉 不纳入总量的管理, 整体独立统计分析;
                  无论是界面上 还是各项指标的分析 统计 接口 业务逻辑 等等 全部单算"。
           → 与上方「投诉专项」平行, 不属其总量; 数据来自 card_complaints 补充表。 -->
      <div class="section-card mb-16" style="border-left:3px solid #059669">
        <div class="section-header-row">
          <h3 class="section-title">
            <el-icon style="color:#059669; margin-right:4px"><Bell /></el-icon>
            (外)卡商投诉（独立统计 · 不纳入投诉总量）
          </h3>
          <el-tooltip content="卡商投诉来自 card_complaints 补充表, 在工单侧无独立分类(实证 item/category 含「卡商」均为 0 行), 故独立统计、不与工单投诉相加" placement="top">
            <el-icon style="opacity:.6"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
        <div class="compact-kpi-row">
          <div class="ck-cell ck-drillable" style="background:#ecfdf5"
               title="点击查看卡商渠道明细（带当前时间窗口）"
               @click="onComplaintSourceClick('(外)卡商')">
            <div class="ck-label">卡商登记</div>
            <div class="ck-rh">
              <div class="ck-rh-item">
                <span class="ck-rh-num" style="color:#059669">{{ formatNum(cardData.registered ?? cardData.total) }}</span>
                <span class="ck-rh-tag">登记</span>
              </div>
            </div>
          </div>
          <div class="ck-cell ck-drillable" style="background:#f0fdf4"
               title="点击查看卡商渠道明细（带当前时间窗口）"
               @click="onComplaintSourceClick('(外)卡商')">
            <div class="ck-label">卡商处理</div>
            <div class="ck-rh">
              <div class="ck-rh-item">
                <span class="ck-rh-num" style="color:#16a34a">{{ formatNum(cardData.handled) }}</span>
                <span class="ck-rh-tag">处理</span>
              </div>
            </div>
          </div>
        </div>
        <div v-if="supplCard.total" class="ck-sub" style="padding:4px 8px;color:#059669">
          卡商补充数据源 {{ formatNum(supplCard.total) }} 条（已关联工单 {{ formatNum(supplCard.matched) }}）
        </div>
      </div>

      <el-collapse v-model="complaintCollapseActive" class="complaint-collapse">
        <el-collapse-item name="detail">
          <template #title>
            <span class="collapse-title">
              投诉明细
              <span style="font-weight:600;color:#2563eb">登记 {{ formatNum(complaintDetailCount.registered) }}</span>
              <span style="margin:0 3px;color:#d1d5db">·</span>
              <span style="font-weight:600;color:#16a34a">处理 {{ formatNum(complaintDetailCount.handled) }}</span>
              <span style="margin:0 3px;color:#d1d5db">·</span>
              <span class="dim-text">共 {{ complaintDetailCount.total }} 条 · 当前 {{ complaintFilteredList.length }} 条</span>
            </span>
          </template>
          <div class="sn-filter-bar">
            <el-select v-model="complaintFilterSource" placeholder="来源" size="small" clearable filterable style="width:140px;margin-right:6px">
              <el-option v-for="s in COMPLAINT_SOURCE_OPTIONS" :key="s" :label="s" :value="s" />
            </el-select>
            <span class="sn-filter-hint">分类/状态可在表格列头筛选排序</span>
          </div>
          <el-table :data="complaintFilteredList" size="small" border stripe max-height="360">
            <el-table-column label="登记时间" width="88" sortable :sort-method="(a,b)=>sortTimeStr(a.register_time||a.time||'', b.register_time||b.time||'')">
              <template #default="{ row }">
                <span>{{ formatShortTime(row.register_time || row.time) }}</span>
                <div v-if="row.finish_time && row.register_time && row.register_time.slice(0,10) !== row.finish_time.slice(0,10)"
                     class="text-muted" style="font-size:10px;line-height:1">历史单</div>
              </template>
            </el-table-column>
            <el-table-column label="完成时间" width="88" sortable :sort-method="(a,b)=>sortTimeStr(a.finish_time||a.suppl_finish_time||'', b.finish_time||b.suppl_finish_time||'')">
              <template #default="{ row }">
                <span v-if="row.finish_time">{{ formatShortTime(row.finish_time) }}</span>
                <template v-else-if="row.suppl_finish_time">
                  <span>{{ formatShortTime(row.suppl_finish_time) }}</span>
                  <div class="text-muted" style="font-size:10px;line-height:1">补</div>
                </template>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="设备SN" min-width="130" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" underline="never" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="结果/状态" width="90" align="center" sortable
              :filters="complaintStatusColumnFilters"
              :filter-method="(v,row)=>row.status===v" filter-placement="bottom-end">
              <template #default="{ row }">
                <el-tag :type="complaintStatusType(row.status)" size="small">{{ row.status || '—' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="92" align="center"
              :filters="complaintSourceColumnFilters"
              :filter-method="(v,row)=>row.source===v" filter-placement="bottom-end">
              <template #default="{ row }">
                <el-tag :type="complaintSourceType(row.source)" size="small" effect="plain">{{ row.source }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="被投诉主体" width="130" show-overflow-tooltip :filters="complaintEntityColumnFilters"
              :filter-method="(v,row)=>(row.entity||'—')===v" filter-placement="bottom-end">
              <template #default="{ row }">
                <el-tag v-if="row.entity" size="small" effect="plain" type="warning">{{ row.entity }}</el-tag>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="分类" width="100" show-overflow-tooltip sortable
              :filters="complaintCategoryColumnFilters"
              :filter-method="(v,row)=>row.category===v" filter-placement="bottom-end">
              <template #default="{ row }">
                <span v-if="row.category">{{ row.category }}</span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="投诉内容" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                <span>{{ row.problem || row.suppl_problem || '—' }}</span>
                <el-tag v-if="row.suppl_problem && !row.problem" size="small" type="warning" effect="plain" style="margin-left:4px">补</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="补充" width="70" align="center">
              <template #default="{ row }">
                <el-tooltip v-if="row.suppl_count" placement="top" :content="supplTooltip(row)">
                  <el-tag size="small" type="warning" effect="plain" style="cursor:pointer">补{{ row.suppl_count }}</el-tag>
                </el-tooltip>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="处理/跟进" width="90" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link v-if="row.handler && row.handler !== '未分配'" type="primary" underline="never" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="text-muted">未分配</span>
              </template>
            </el-table-column>
          </el-table>
        </el-collapse-item>
      </el-collapse>
    </section>

    <!-- 模块7: 人均效率 (全量, 默认仅企微) -->
    <section class="section-card">
      <div class="section-header-row">
        <h3 class="section-title">{{ viewMode === 'week' ? '本周工作效率值' : '今日工作效率值' }}</h3>
        <div class="section-header-actions">
          <el-checkbox v-model="showAllStaff" size="small">显示所有人(含非企微)</el-checkbox>
          <el-link type="primary" underline="never" @click="$router.push('/dashboards/operation')">运营看板 →</el-link>
        </div>
      </div>
      <el-table :data="topStaffList" size="small" border stripe max-height="420"
        :default-sort="{ prop: 'eff_score', order: 'descending' }"
      >
        <el-table-column label="#" width="50" align="center">
          <template #default="{ $index }">
            <el-tag v-if="$index < 3" size="small" :type="['warning','success','info'][$index]" effect="dark">
              {{ $index + 1 }}
            </el-tag>
            <span v-else>{{ $index + 1 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="人员" width="100">
          <template #default="{ row }">
            <el-link type="primary" underline="never" @click="openPersonAudit(row.name, row.person_id)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="专项/小组" width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.current_specialty" size="small" effect="plain" type="warning" style="margin-right:4px">{{ row.current_specialty }}</el-tag>
            <span class="text-muted">{{ row.group && row.group !== '未分组' ? row.group : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="showDeptColumn" prop="department" label="部门" width="80">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.department || '—' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="reception_count" label="会话数(次)" width="80" align="right" sortable :sort-method="(a,b)=>(Number(a.reception_count)||0)-(Number(b.reception_count)||0)">
          <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
        </el-table-column>
        <!-- 各专项接待数列 (按类型分开SN) - 表头分组 -->
        <el-table-column label="各专项接待数(个)" align="center">
          <el-table-column
            v-for="col in SPECIALTY_COLS"
            :key="col.key"
            :label="col.short"
            width="48"
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
        <el-table-column prop="reception_device_count" label="接待合计" width="72" align="right" sortable :sort-method="(a,b)=>(Number(a.reception_device_count)||0)-(Number(b.reception_device_count)||0)">
          <template #default="{ row }">
            <span style="font-weight:600;color:#2563eb">{{ formatNum(row.reception_device_count) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="work_score" label="会话效率" width="90" align="right" sortable :sort-method="(a,b)=>(Number(a.work_score)||0)-(Number(b.work_score)||0)">
          <template #default="{ row }">
            <el-popover placement="top" :width="260" trigger="hover">
              <template #reference>
                <el-tag size="small" effect="plain" :type="scoreTagType(row.work_score)" class="score-tag-hover">
                  {{ formatNum(row.work_score) }}
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
                <el-tag size="small" effect="plain" :type="scoreTagType(row.eff_score, 350, 200, 80)" class="score-tag-hover">
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
        <el-table-column label="核算说明" width="64" align="center">
          <template #default="{ row }">
            <el-popover placement="left" :width="320" trigger="click">
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
                <div v-if="row._calc_note" class="score-tip-note">
                  <div v-if="row._calc_note.sessions_merge">• {{ row._calc_note.sessions_merge }}</div>
                  <div v-if="row._calc_note.sn_merge_rule">• {{ row._calc_note.sn_merge_rule }}</div>
                  <div v-if="row._calc_note.weight_rule">• {{ row._calc_note.weight_rule }}</div>
                  <div v-if="row._calc_note.recovery_def">• {{ row._calc_note.recovery_def }}</div>
                  <div v-if="row._calc_note.processing_def">• {{ row._calc_note.processing_def }}</div>
                </div>
                <div class="score-tip-note">专项难度系数: 咨询×1.0 套餐×1.8 退款处理×2.0 退款挽回×2.2 内诉×3.0 外诉×5.0 · 转接×0.2</div>
              </div>
            </el-popover>
          </template>
        </el-table-column>
      </el-table>

      <!-- ★ #78 AI / 系统账户单列
           ★ 2026-09-14 用户要求：本表**列结构与上方效率值表对齐**，
             以便「AI 与真人同口径比对」，不再只是"量级参考"。

           设计说明（Design Rationale）:
             目的: 替运营主管回答「AI 顶几个真人」—— 此前只有会话数/接待数，
                   缺**专项接待数**与**效率值** → 无法与真人横向比。
             逻辑: 单独一张小表（不与真人混排，混排会挤掉真人名次），
                   但**列名与列序刻意与真人表一致**（会话数 → 各专项接待数 →
                   接待合计 → 会话效率 → 接待效率），以便上下对读。
             口径: 来源判据 = 操作人 + 说明文本（见后端 system_accounts）；
                   效率值与真人**同一张权重表**（各专项默认系数逐桶累加），
                   语义 = "折合多少标准人力工作量" → 故可直接比对。
             取舍: ① **不做** AI 转人工细分（平台无稳定标记，做了会返工）；
                   ② **不给 AI 名次** —— 它不是人，排名无意义；
                   ③ **不并入真人表** —— 会挤占名次，且真人表的筛选/聚合对 AI 无意义；
                   ④ 空则不渲染本区块（无 AI 数据时不占版面）。
             工单: #78 / 本次调整: 2026-09-14 用户要求 -->
      <div class="virtual-staff-block">
        <div class="virtual-staff-title">
          <span>AI / 系统账户（非真人，不计入人效排名 · 列结构与上方一致，可对读）</span>
          <el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：同上表（/analytics/today/staff）<br/><b>口径</b>：按「操作人 + 说明文本」判定为非真人；<b>会话效率/接待效率采用与真人完全相同的权重表</b>（按各专项默认系数逐桶累加），故可与上方真人表直接比对 —— 语义是「折合多少标准人力工作量」。<br/><b>时点</b>：与上方表格同一窗口<br/><b>「空」的含义</b>：本区块不显示 = 本窗口内无非真人账户产生接待；某列为 0 = 该账户本期无该类工作<br/><br/><b>⚠️ 取舍</b>：① 不并入上方真人表（会挤占名次、且筛选/聚合对 AI 无意义）② 不给名次（AI 不是人，排名无意义）③ <b>不做</b>「AI 转人工」细分（平台字段不稳定，待 BMS/智能客服稳定后再精细化）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
        <el-table :data="virtualStaffList" size="small" border max-height="260">
          <el-table-column label="账户" width="120">
            <template #default="{ row }">
              <span style="font-weight:600">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="92">
            <template #default="{ row }">
              <el-tag size="small" effect="plain"
                      :style="{ color: sourceKindMeta(row.source_kind).color, borderColor: sourceKindMeta(row.source_kind).color }">
                {{ sourceKindMeta(row.source_kind).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reception_count" label="会话数(次)" width="104" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
          </el-table-column>
          <!-- 各专项接待数(个) —— 与真人表同名同序 -->
          <el-table-column label="各专项接待数(个)" align="center">
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
          <el-table-column prop="reception_device_count" label="接待合计" width="80" align="right" sortable>
            <template #default="{ row }">
              <span style="color:#409eff;font-weight:600">{{ formatNum(row.reception_device_count ?? 0) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="work_score" label="会话效率" width="90" align="right" sortable>
            <template #default="{ row }">
              <el-popover placement="top" :width="280" trigger="hover">
                <template #reference>
                  <el-tag size="small" effect="plain" type="warning" class="score-tag-hover">
                    <span style="font-weight:600">{{ formatNum(row.work_score) }}</span>
                  </el-tag>
                </template>
                <div class="score-tip">
                  <div class="score-tip-title">会话效率构成（与真人同权重表）</div>
                  <div v-for="(v, k) in row.breakdown || {}" :key="k" class="score-tip-row">
                    <span class="score-tip-cat">{{ k }}</span>
                    <span class="score-tip-val">{{ v.sessions }}次 × {{ v.coef }}</span>
                    <span class="score-tip-sub">=</span>
                    <span class="score-tip-contrib">{{ v.work_contrib }}</span>
                  </div>
                  <div class="score-tip-total">合计 = {{ row.work_score }}</div>
                  <div class="score-tip-note">{{ row._calc_note?.weight_rule }}</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
          <el-table-column prop="eff_score" label="接待效率" width="90" align="right" sortable>
            <template #default="{ row }">
              <el-popover placement="top" :width="280" trigger="hover">
                <template #reference>
                  <el-tag size="small" effect="plain" type="primary" class="score-tag-hover">
                    <span style="font-weight:600;color:#2563eb">{{ formatNum(row.eff_score) }}</span>
                  </el-tag>
                </template>
                <div class="score-tip">
                  <div class="score-tip-title">接待效率构成（与真人同权重表）</div>
                  <div v-for="(v, k) in row.breakdown || {}" :key="k" class="score-tip-row">
                    <span class="score-tip-cat">{{ k }}</span>
                    <span class="score-tip-val">{{ v.people }}个 × {{ v.coef }}</span>
                    <span class="score-tip-sub">=</span>
                    <span class="score-tip-contrib">{{ v.eff_contrib }}</span>
                  </div>
                  <div class="score-tip-total">合计 = {{ row.eff_score }}</div>
                  <div class="score-tip-note">{{ row._calc_note?.weight_rule }}</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
          <el-table-column label="占全站会话" width="104" align="right">
            <template #default="{ row }">
              <!-- 可控指标前置: AI 占比一眼可见 -->
              <span style="color:#f59e0b;font-weight:600">{{ aiSharePct(row) }}%</span>
            </template>
          </el-table-column>
          <!-- 空态：显式说明，不靠隐藏整块来表达 -->
          <template #empty>
            <span style="color:#909399;font-size:12px">
              本窗口内<b>无 AI / 系统账户产生接待</b>（不是功能未启用）
            </span>
          </template>
        </el-table>
      </div>
    </section>

    <!-- #233 B1.5-2 / #234 B1.5-3 事件标记详情(点击趋势图 markPoint/markLine/markArea 弹出) -->
    <el-dialog v-model="incidentDetailVisible" title="事件详情" width="640px">
      <el-descriptions v-if="activeIncidentDetail" :column="1" border>
        <el-descriptions-item label="事件类型">
          <el-tag :type="activeIncidentDetail.event_type === '经营事件' ? 'success' : 'danger'" size="small" effect="dark">
            {{ activeIncidentDetail.event_type }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="事项名">{{ activeIncidentDetail.title }}</el-descriptions-item>
        <el-descriptions-item label="主体">{{ activeIncidentDetail.subject || '-' }}</el-descriptions-item>
        <el-descriptions-item label="发生时间">{{ formatTime(activeIncidentDetail.occurred_at) }}</el-descriptions-item>
        <el-descriptions-item v-if="activeIncidentDetail.event_type === '经营事件' && activeIncidentDetail.start_at" label="生效起止">
          {{ formatTime(activeIncidentDetail.start_at) }}
          <span v-if="activeIncidentDetail.end_at"> ~ {{ formatTime(activeIncidentDetail.end_at) }}</span>
          <span v-else> ~ (点事件)</span>
        </el-descriptions-item>
        <el-descriptions-item label="严重程度">{{ activeIncidentDetail.severity || '-' }}</el-descriptions-item>
        <el-descriptions-item label="影响">{{ activeIncidentDetail.impact || '-' }}</el-descriptions-item>
        <el-descriptions-item label="明细">{{ activeIncidentDetail.detail || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="activeIncidentDetail.event_type === '经营事件' && activeIncidentDetail.target_audience" label="目标群体">
          {{ activeIncidentDetail.target_audience }}
        </el-descriptions-item>
        <el-descriptions-item v-if="activeIncidentDetail.event_type === '经营事件' && activeIncidentDetail.impact_metrics" label="影响指标">
          <div v-for="(v, k) in activeIncidentDetail.impact_metrics" :key="k" style="font-size:13px;line-height:1.6">· {{ k }}: {{ v }}</div>
        </el-descriptions-item>
        <!-- #235 B1.5-4 排班调整建议(经营事件用) -->
        <el-descriptions-item v-if="activeIncidentDetail.event_type === '经营事件' && activeIncidentDetail.scheduling_suggestion" label="排班调整建议">
          {{ activeIncidentDetail.scheduling_suggestion }}
        </el-descriptions-item>
      </el-descriptions>

      <!-- #234 B1.5-3 事件影响对照: 前后窗口的接待/投诉/退款量变化 -->
      <el-divider content-position="left">事件影响对照</el-divider>
      <div v-loading="incidentImpactLoading" style="min-height: 60px">
        <el-table v-if="incidentImpactComparison" :data="incidentImpactComparisonRows" border size="small" style="width: 100%">
          <el-table-column prop="metric" label="指标" width="120" />
          <el-table-column prop="pre" label="事件前" align="center" />
          <el-table-column v-if="incidentImpactComparison.during_window" prop="during" label="事件期间" align="center" />
          <el-table-column prop="post" label="事件后" align="center" />
          <el-table-column label="变化(前→后)" align="center">
            <template #default="{ row }">
              <span :style="{ color: row.delta > 0 ? '#f56c6c' : (row.delta < 0 ? '#67c23a' : '#909399'), fontWeight: 600 }">
                {{ row.delta > 0 ? '↑' : (row.delta < 0 ? '↓' : '→') }} {{ Math.abs(row.delta) }}
              </span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else-if="!incidentImpactLoading" description="暂无对照数据(可能事件无时间锚点)" :image-size="40" />
      </div>
      <div v-if="incidentImpactComparison" class="impact-hint">
        窗口: 事件{{ incidentImpactComparison.window_minutes }}分钟前/后 ·
        锚点: {{ formatTime(incidentImpactComparison.anchor_at) }}
      </div>

      <template #footer>
        <el-button @click="incidentDetailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Timer, Refresh, WarningFilled, Document, Bell, List, CircleCheckFilled, QuestionFilled } from '@element-plus/icons-vue'
import { today } from '@/api/analytics'
import { complaintSummary, complaintDetail, complaintBacklog } from '@/api/complaint'
import BaseChart from '@/components/BaseChart.vue'
import { useAudit } from '@/composables/useAudit'
import { SOURCE_KINDS, buildStack, stackTip, sourceKindMeta } from '@/utils/receptionSource'
import { getWeekRange as _getWeekRange } from '@/utils/weekRange'
import dayjs from 'dayjs'
import { useToday } from './useToday'

// #233 B1.5-2 事件详情对话框时间格式化(轻量, 不污染 useToday)
const formatTime = (t) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-')

const {
  loading,
  lastUpdate,
  isMobile,
  _mq,
  viewMode,
  kpiData,
  hourlyData,
  staffData,
  queueData,
  recoveryData,
  hasRecovery,
  complaintSummaryData,
  complaintList,
  complaintDetailCount,
  complaintCollapseActive,
  backlogTotal,
  showAllStaff,
  trendSelectedGroups,
  openDeviceAudit,
  openPersonAudit,
  recoveryCollapseActive,
  refundFilterResult,
  refundFilterHandler,
  refundFilterReason,
  COMPLAINT_SOURCE_OPTIONS,
  complaintFilterSource,
  currentPolicies,
  formatNum,
  formatShortTime,
  formatMoney,
  scoreTagType,
  complaintSourceType,
  complaintStatusType,
  complaintRegHandledOption,
  complaintCategoryOption,
  complaintSourceColumnFilters,
  complaintCategoryColumnFilters,
  complaintEntityColumnFilters,
  complaintStatusColumnFilters,
  complaintFilteredList,
  sortTimeStr,
  supplExternal,
  supplSummaryText,
  supplCard,
  cardData,
  supplTooltip,
  INCIDENT_STATUS,
  incidentStatusLabel,
  incidentTagType,
  activeIncidents,
  hasHighSeverityIncident,
  impactBannerTitle,
  goToImpactSection,
  router,
  drillRange,
  drillToComplaint,
  drillToBacklog,
  drillToRefundRecovery,
  drillToOperation,
  onKpiClick,
  COMPLAINT_DRILL_SOURCE,
  onComplaintSourceClick,
  TASK_STATUS,
  taskStatusLabel,
  selectedDate,
  isToday,
  _viewCacheKey,
  _viewCache,
  _forceRefresh,
  getWeekRange,
  isCurrentWeek,
  showRealtimeTag,
  displayDate,
  pageTitle,
  getRange,
  onDateChange,
  onViewModeChange,
  kpiCards,
  DUTY_POSITIONS,
  DUTY_ROLE_META,
  dutyLeaders,
  REFUND_ROUTES,
  _rrLabel,
  COMPLAINT_COLORS,
  buildTrendSeries,
  buildTrendLegend,
  mergeMobileTrend,
  trendOption,
  // #233 B1.5-2 + #234 B1.5-3 时间轴标注 + 事件影响对照
  incidentMarkers,
  incidentMarkerMap,
  activeIncidentDetail,
  incidentDetailVisible,
  incidentImpactComparison,
  incidentImpactComparisonRows,
  incidentImpactLoading,
  onChartClick,
  isQwStaff,
  SPECIALTY_COLS,
  getSpecialtyPeople,
  topStaffList,
  virtualStaffList,
  aiSharePct,
  showDeptColumn,
  recoverySnList,
  refundHandlerOptions,
  refundReasonOptions,
  refundFilteredList,
  recoveryReasonStackOption,
  successRateColor,
  loadAll,
  applyViewData,
  _onMediaChange,
} = useToday()
</script>

<style scoped>
.today-dashboard {
  padding-bottom: 24px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 值班组长 */
.duty-leader-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 16px;
  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
  border-radius: 8px;
  border: 1px solid #fde68a;
}
.duty-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: #92400e;
}
.duty-chips {
  flex: 1;
}
.duty-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}
.chip-name { font-weight: 500; }
.chip-sep { color: #d1d5db; }
.chip-group { color: #6b7280; font-size: 11px; }

/* ★ 2026-09-14 角色徽标（组长 / 管理）—— 前置在姓名前，让角色先于姓名被看见 */
.chip-role {
  display: inline-block;
  padding: 0 5px;
  margin-right: 4px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.5px;
  vertical-align: 1px;
}

/* 退款挽回 SN 折叠明细 */
.recovery-sn-collapse {
  margin-top: 8px;
}
.recovery-sn-collapse :deep(.el-collapse-item__header) {
  height: 40px;
  padding: 0 8px;
}
.collapse-title {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}
.complaint-collapse {
  margin-top: 4px;
}

/* KPI 紧凑横条 */
.kpi-strip { padding: 8px 12px; }
.kpi-strip-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #6b7280;
  margin-bottom: 6px;
}
.kpi-strip-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 6px 10px;
}
.kpi-strip-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 6px;
  background: #fafafa;
  border-left: 3px solid #9ca3af;
}
.kpi-strip-item.kpi-blue { border-left-color: #2563eb; }
.kpi-strip-item.kpi-green { border-left-color: #059669; }
.kpi-strip-item.kpi-red { border-left-color: #dc2626; }
.kpi-strip-item.kpi-orange { border-left-color: #ea580c; }
.kpi-strip-item.kpi-purple { border-left-color: #7c3aed; }
.kpi-strip-item.kpi-teal { border-left-color: #0d9488; }
.kpi-strip-item.kpi-cyan { border-left-color: #0891b2; }

/* ★ #76 卡片可下钻的统一视觉（规范 §3.5: 可点元素须有 pointer 与 hover 反馈） */
.kpi-drillable,
.ck-drillable {
  cursor: pointer;
  transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease;
}
.kpi-drillable:hover {
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(0, 0, 0, .08);
}
.ck-drillable:hover {
  transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(0, 0, 0, .08);
  outline: 1px solid rgba(37, 99, 235, .28);
}
.ksi-icon {
  width: 24px; height: 24px;
  border-radius: 5px;
  display: flex; align-items: center; justify-content: center;
  color: #fff; flex-shrink: 0;
  font-size: 12px;
}
.kpi-blue .ksi-icon { background: #2563eb; }
.kpi-green .ksi-icon { background: #059669; }
.kpi-red .ksi-icon { background: #dc2626; }
.kpi-orange .ksi-icon { background: #ea580c; }
.kpi-purple .ksi-icon { background: #7c3aed; }
.kpi-teal .ksi-icon { background: #0d9488; }
.kpi-cyan .ksi-icon { background: #0891b2; }
.ksi-body { flex: 1; min-width: 0; }
.ksi-label {
  font-size: 11px;
  color: #6b7280;
  line-height: 1.2;
}
.ksi-value-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 1px;
}
.ksi-value {
  font-size: 17px;
  font-weight: 700;
  color: #111827;
  line-height: 1.2;
}
.ksi-yoy {
  font-size: 11px;
  font-weight: 500;
}
.yoy-up { color: #16a34a; }
.yoy-down { color: #dc2626; }

/* ★ #78 接待来源堆叠比例条 + 图例（仅「接待数」「会话数」两张卡）
   设计说明: 用一条 100% 堆叠细条替代额外图表 —— 卡片高度不增，
   但一眼可看出「AI 占了多少」。取舍: 不做饼图（8 张卡里塞饼图会挤压信息密度）。 */
.ksi-stack {
  display: flex;
  height: 4px;
  border-radius: 2px;
  overflow: hidden;
  margin-top: 4px;
  background: #f0f1f3;
}
.ksi-stack-seg { display: block; height: 100%; }
.ksi-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 3px;
  font-size: 10px;
  color: #6b7280;
  line-height: 1.3;
}
.ksi-sub-item { display: inline-flex; align-items: center; gap: 3px; white-space: nowrap; }
.ksi-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}

/* ★ #78 AI / 系统账户单列区块 */
.virtual-staff-block {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
}
.virtual-staff-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #6b7280;
  margin-bottom: 6px;
}

/* 区块样式 */
.section-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  border: 1px solid #f3f4f6;
}
.section-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.section-header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.section-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #111827;
  display: flex;
  align-items: center;
}
.section-desc {
  font-size: 12px;
  color: #9ca3af;
}

/* 影响事件 */
.impact-banner {
  border-radius: 8px;
  font-weight: 500;
}
.impact-banner :deep(.el-alert__content) { width: 100%; }
.impact-banner-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.impact-chip {
  cursor: pointer;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.impact-recur-mark {
  font-weight: 700;
  margin-right: 2px;
}
.incident-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.incident-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #fef2f2;
  border-radius: 6px;
  border-left: 3px solid #dc2626;
}
.incident-title {
  flex: 1;
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.incident-time {
  font-size: 11px;
  color: #9ca3af;
  font-family: ui-monospace, monospace;
}

/* 政策列表 */
.policy-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.policy-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px;
  background: #eff6ff;
  border-radius: 6px;
}
.policy-icon {
  color: #2563eb;
  font-size: 18px;
  margin-top: 2px;
}
.policy-info {
  flex: 1;
}
.policy-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e40af;
  margin-bottom: 2px;
}
.policy-desc {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.4;
}

/* 待办/任务列表 */
.todo-list, .task-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.todo-item, .task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: #f9fafb;
  border-radius: 6px;
}
.todo-title, .task-title {
  flex: 1;
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 紧凑空态 (一行/两行内解决, 不占位大量留白) */
.empty-inline {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  min-height: 28px;
  font-size: 12px;
  color: #9ca3af;
  background: #fafafa;
  border-radius: 6px;
}
.empty-inline .el-icon {
  font-size: 14px;
  color: #10b981;
  flex-shrink: 0;
}

/* 紧凑KPI行(退款/投诉共用) */
.compact-kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 6px 8px;
  margin-bottom: 8px;
}
.ck-cell {
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid #f3f4f6;
}
.ck-label {
  font-size: 11px;
  color: #6b7280;
  line-height: 1.2;
}
.ck-value {
  font-size: 18px;
  font-weight: 700;
  line-height: 1.2;
  margin-top: 2px;
  color: #111827;
}
.ck-sub {
  font-size: 10px;
  color: #b91c1c;
  line-height: 1.2;
  margin-top: 2px;
}
/* F-P1-2: 投诉三源卡 登记/处理 双值并排 */
.ck-rh {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 3px;
}
.ck-rh-item {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
}
.ck-rh-num {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.1;
}
.ck-rh-tag {
  font-size: 10px;
  color: #9ca3af;
}
.ck-rh-divider {
  width: 1px;
  align-self: stretch;
  background: #e5e7eb;
}
.compact-pie-wrap {
  border-radius: 6px;
  border: 1px solid #f3f4f6;
  padding: 4px 8px;
}
.compact-pie-title {
  font-size: 11px;
  color: #6b7280;
  padding: 2px 0 0;
}
/* 退款右侧解读 */
.recovery-side {
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 6px 12px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px dashed #e5e7eb;
}
.rs-title {
  font-size: 12px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 8px;
}
.rs-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #4b5563;
  line-height: 1.8;
}
.rs-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.rs-hint {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e5e7eb;
  font-size: 12px;
  color: #6b7280;
  line-height: 1.5;
}
/* 投诉积压水位卡 */
.backlog-watermark {
  height: 100%;
  border-radius: 8px;
  padding: 12px 14px;
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  border: 1px solid #fcd34d;
  color: #92400e;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 120px;
}
.suppl-strip {
  display: flex;
  align-items: center;
  font-size: 12px;
  color: #92400e;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
  padding: 4px 10px;
  margin-bottom: 8px;
}
.backlog-watermark.backlog-warn {
  background: linear-gradient(135deg, #fff7ed, #fed7aa);
  border-color: #fb923c;
  color: #9a3412;
}
.backlog-watermark.backlog-danger {
  background: linear-gradient(135deg, #fef2f2, #fecaca);
  border-color: #f87171;
  color: #991b1b;
}
.bw-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
}
.bw-label { font-size: 13px; }
.bw-value {
  font-size: 34px;
  font-weight: 800;
  line-height: 1.1;
  margin: 4px 0 2px;
  letter-spacing: -1px;
}
.bw-sub {
  font-size: 12px;
  opacity: 0.8;
}
.recovery-sn-collapse {
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  padding: 4px 8px;
}
.recovery-sn-collapse :deep(.el-collapse-item__header) {
  font-weight: 600;
  font-size: 13px;
  padding: 0 8px;
  height: 32px;
  line-height: 32px;
  border-bottom: none;
}
.recovery-sn-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}
.recovery-sn-collapse :deep(.el-collapse-item__content) {
  padding: 4px 0 8px;
}
.mono-small {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
.dim { color: #9ca3af; }

/* 辅助 */
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.h-full { height: 100%; }

/* 趋势类型勾选 */
.trend-group-check :deep(.el-checkbox-button__inner) {
  padding: 4px 10px;
  font-size: 12px;
}

/* SN/投诉明细筛选栏 */
.sn-filter-bar {
  display: flex;
  align-items: center;
  padding: 6px 0 8px;
  gap: 4px;
  flex-wrap: wrap;
}
.sn-filter-hint {
  font-size: 11px;
  color: #9ca3af;
  margin-left: 2px;
}

/* 窄屏自适应（#225 移动端试点）
   设计说明（Design Rationale）
   目的：替**不在工位的值班主管**保住 Today 页这条最高频看板路径 ——
         手机上能看清核心 KPI、知道此刻有无影响事件、能点开下钻明细，
         不追求与桌面端信息密度一致。
   逻辑：全部用 media query 覆盖桌面样式，DOM 结构零改动；
         断点 768px 与 main.css / MainLayout 同源。
         布局主线 = 「标题区换行 → KPI 2 列网格 → 图表区竖排 →
         双列卡片堆叠为单列 → 表格横向滚动」。
   口径：仅约束 ≤768px 视口；桌面端（≥769px）下列规则全部不生效。
   取舍（不做什么及原因）：
     · ❌ 不为窄屏删减 KPI 卡数量 —— 5 张卡每张都对应一个下钻动作，
       删了等于砍功能；改为 2 列网格压缩高度；
     · ❌ 不在窄屏隐藏趋势图 —— 趋势是值班判断"现在忙不忙"的核心依据，
       改为压缩高度与字号；
     · ❌ 不为效率表做"卡片式重排" —— 15+ 列的核算表卡片化会丢列间对比能力，
       保留表格横向滚动（Element 自带），department 列已由 useToday 在窄屏
       默认隐藏（isMobile 联动）；
     · ❌ 不为抽屉/弹窗在本页单独适配 —— 已上移到 main.css 全局覆盖。
   工单：#225 */
@media (max-width: 768px) {
  /* ── 标题区：日期/切换/刷新挤不下，允许换行并把操作区推到第二行 ── */
  .td-header {
    flex-wrap: wrap;
    gap: 8px;
  }
  .header-actions {
    width: 100%;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 6px;
  }
  /* 日期选择器在窄屏压缩，避免独占一行 */
  .header-actions :deep(.el-date-editor) {
    width: 128px !important;
  }
  .page-title {
    font-size: 16px;
  }
  /* 标题右侧"实时数据/更新于"标签在窄屏换行显示 */
  .title-date-sep,
  .title-date {
    display: inline;
  }

  /* ── 值班组长条：多 chip 换行不挤压 ── */
  .duty-leader-bar {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .duty-chips {
    width: 100%;
  }
  .duty-chip {
    /* chip 触控目标保底 32px（原 small 约 24px 偏小） */
    min-height: 28px;
    padding: 4px 8px;
  }

  /* ── 影响事件横幅：chip 换行 ── */
  .impact-banner-body {
    flex-wrap: wrap;
    gap: 4px;
  }

  /* ── 卡片通用：收窄内边距换可见行数 ── */
  .section-card {
    padding: 10px;
  }
  .section-header-row {
    flex-wrap: wrap;
    gap: 6px;
  }
  .section-title {
    font-size: 14px;
  }
  .section-desc {
    /* 描述文字在窄屏隐藏 —— 与标题语义重复，占位换可见行数 */
    display: none;
  }
  .section-header-actions {
    flex-wrap: wrap;
    gap: 8px;
  }

  /* ── 核心 KPI：5 列 → 2 列网格，值字号压缩防溢出 ── */
  .kpi-strip-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 6px;
  }
  .kpi-strip-item {
    padding: 6px 8px;
    gap: 6px;
  }
  .ksi-icon {
    width: 20px;
    height: 20px;
    font-size: 11px;
  }
  .ksi-value {
    font-size: 15px;
  }
  .ksi-yoy {
    font-size: 10px;
  }
  .ksi-label {
    font-size: 10px;
  }
  /* 堆叠比例条下的图例在窄屏允许换行（原 flex-wrap:wrap 已有，此处仅压字号） */
  .ksi-sub {
    font-size: 9px;
    gap: 4px;
  }

  /* ── 趋势图区：勾选组在窄屏独占一行（原与标题同行会挤压） ── */
  .trend-group-check {
    width: 100%;
  }
  .trend-group-check :deep(.el-checkbox-button__inner) {
    padding: 4px 8px;
    font-size: 12px;
  }

  /* ── 影响事件/政策/待办/专项（el-col :xs="24" 已自动堆叠为单列），
        此处仅压缩内部行距 ── */
  .incident-item,
  .policy-item,
  .todo-item,
  .task-item {
    font-size: 12px;
    padding: 4px 0;
  }
  .incident-title,
  .todo-title,
  .task-title {
    /* 长标题省略号，避免挤压 tag */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  /* ── 退款挽回 / 投诉三源 / 卡商：紧凑 KPI 行 2 列网格 ── */
  .compact-kpi-row {
    grid-template-columns: repeat(2, 1fr);
    gap: 6px;
  }
  .ck-value {
    font-size: 16px;
  }
  .ck-rh-num {
    font-size: 15px;
  }
  /* 退款原因分布图与"成功率解读"在窄屏上下堆叠（el-col :xs="24" 已处理），
     解读区不再垂直居中（内容少时上方留白过大） */
  .recovery-side {
    margin-top: 8px;
    justify-content: flex-start;
  }
  .compact-pie-wrap {
    margin-top: 6px;
  }

  /* ── 积压水位卡：在窄屏独占一行（el-col :xs="24"），高度压缩 ── */
  .backlog-watermark {
    margin-top: 8px;
    min-height: 80px;
    padding: 10px 12px;
  }
  .bw-value {
    font-size: 26px;
  }

  /* ── 表格：窄屏下列宽总和远超屏宽，Element 自带横向滚动接管。
        此处仅保证表格外层 section-card 不裁剪（padding 已收窄），
        表格字号下探一档以在横滚时仍保持可读密度 ── */
  .section-card :deep(.el-table) {
    font-size: 12px;
  }
  .section-card :deep(.el-table .cell) {
    padding: 0 6px;
    line-height: 1.4;
  }
  /* 筛选栏在窄屏允许换行，select 不挤压 */
  .sn-filter-bar {
    flex-wrap: wrap;
    gap: 6px;
  }
  .sn-filter-bar :deep(.el-select) {
    flex: 1 1 30%;
    min-width: 100px;
  }
  .sn-filter-hint {
    width: 100%;
  }

  /* ── AI/系统账户单列区块：标题文字在窄屏允许换行 ── */
  .virtual-staff-title {
    flex-wrap: wrap;
    line-height: 1.4;
  }

  /* ── 页内折叠面板：头部文字在窄屏字号下探，防止登记/处理计数溢出 ── */
  .collapse-title {
    font-size: 12px;
  }
}

/* #234 B1.5-3 事件影响对照提示文本 */
.impact-hint {
  margin-top: 8px;
  padding: 6px 10px;
  font-size: 12px;
  color: #909399;
  background: #f4f4f5;
  border-radius: 4px;
}
</style>
