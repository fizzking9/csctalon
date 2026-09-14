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
        <BaseChart :option="trendOption" height="220px" />
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
           设计说明（Design Rationale）:
             目的: 替运营主管回答「AI 到底干了多少活、顶了几个真人的量」。
             逻辑: 单独一张小表列出非真人账户（AI 全自动 / 系统内部），
                   不与真人混排 —— 混排会挤掉真人名次，隐藏则会看不见 AI 规模。
             口径: 来源判据 = 操作人 + 说明文本（见后端 system_accounts），
                   这里的「会话数」= 该账户名下的接待记录条数。
             取舍: ① **不做** AI 转人工细分（平台无稳定标记，做了会返工）；
                   ② **不给 AI 排名名次** —— 它不是人，排名无意义，
                      只给绝对量级供「大致评估工作量」；
                   ③ 空则不渲染本区块（无 AI 数据时不占版面）。
             工单: #78 / 架构: docs/arch/A-20260913-001-AI接待口径分层.md -->
      <div v-if="virtualStaffList.length" class="virtual-staff-block">
        <div class="virtual-staff-title">
          <span>AI / 系统账户（非真人，不计入人效排名）</span>
          <el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：同上表（/analytics/today/staff）<br/><b>口径</b>：按「操作人 + 说明文本」判定为非真人；会话数 = 该账户名下接待记录条数（已按业务键去重，见 #77）<br/><b>时点</b>：与上方表格同一窗口<br/><b>「空」的含义</b>：本区块不显示 = 本窗口内无非真人账户产生接待<br/><br/><b>⚠️ 取舍</b>：不给名次（AI 不是人，排名无意义）；<b>不做</b>「AI 转人工」细分（平台字段不稳定，待 BMS/智能客服稳定后再精细化）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
        <el-table :data="virtualStaffList" size="small" border max-height="200">
          <el-table-column label="账户" width="130">
            <template #default="{ row }">
              <span style="font-weight:500">{{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain"
                      :style="{ color: sourceKindMeta(row.source_kind).color, borderColor: sourceKindMeta(row.source_kind).color }">
                {{ sourceKindMeta(row.source_kind).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reception_count" label="会话数(次)" width="100" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
          </el-table-column>
          <el-table-column prop="reception_device_count" label="接待数(个)" width="100" align="right" sortable>
            <template #default="{ row }">{{ formatNum(row.reception_device_count) }}</template>
          </el-table-column>
          <el-table-column label="占全站会话" width="100" align="right">
            <template #default="{ row }">
              <!-- 可控指标前置: AI 占比一眼可见 -->
              <span style="color:#f59e0b;font-weight:600">{{ aiSharePct(row) }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="180">
            <template #default>
              <span class="text-muted" style="font-size:11px">非真人账户，量级供「大致评估 AI 工作量」用</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>
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
// ★ #78 接待来源分层：配色/堆叠构造抽到共享模块，与「运营看板」共用
//   （规范 §五 禁止重复展示 —— 配色各页自写会漂移，使用者产生歧义）
import { SOURCE_KINDS, buildStack, stackTip, sourceKindMeta } from '@/utils/receptionSource'
// 周区间计算（周一起始）—— 共享单一来源
import { getWeekRange as _getWeekRange } from '@/utils/weekRange'
import dayjs from 'dayjs'

const loading = ref(false)
const lastUpdate = ref('')
const isMobile = ref(false)
let _mq = null

// 视图模式: day=日(默认), week=周(完整自然周 周一~周日)
const viewMode = ref('day')

// 数据
const kpiData = ref({})
const hourlyData = ref({})
const staffData = ref({})
const queueData = ref({})
const recoveryData = ref({})
const hasRecovery = computed(() => !!recoveryData.value?.kpi)

// 投诉专项
const complaintSummaryData = ref({})
const complaintList = ref([])
// F-P1-2: 明细窗口内 登记/处理 计数(标题展示)
const complaintDetailCount = ref({ registered: 0, handled: 0, total: 0 })
const complaintCollapseActive = ref(['detail'])
// 积压待处理(近30天窗口内登记的未完成, 不随今日/本周切换)
const backlogTotal = ref(0)

// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
const showAllStaff = ref(false)

// 实时趋势: 默认只显示接待,可勾选投诉/退款
const trendSelectedGroups = ref(['reception'])

// 审计弹窗(点击 SN / 人员 唤起)
const { openDeviceAudit, openPersonAudit } = useAudit()

// 退款挽回 SN 明细(折叠, 默认收起)
const recoveryCollapseActive = ref([])
// 退款SN筛选
const refundFilterResult = ref('')
const refundFilterHandler = ref('')
const refundFilterReason = ref('')
// 投诉筛选: 明细区仅按来源(**三源**)筛选
// ★★ 2026-09-11 (#53): 卡商**移出**筛选器 —— 卡商是独立统计体系(不属投诉总量)。
//    卡商明细请在独立的卡商区块查看。
const COMPLAINT_SOURCE_OPTIONS = ['内部投诉', '商户号投诉', '外部投诉']
const complaintFilterSource = ref('')

// 当前政策 (静态配置，后续可从接口获取)
const currentPolicies = ref([
  {
    name: '退款挽回专项行动',
    description: '针对近期退款率上升，启动退款挽回专项行动，目标挽回率提升至40%',
    status: '执行中',
    type: 'warning',
  },
  {
    name: '客户满意度提升计划',
    description: '加强售前咨询质量，确保响应时长<3分钟，解决率>95%',
    status: '进行中',
    type: 'success',
  },
])

// 格式化
function formatNum(v) {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'object') {
    // 防御:误把 {value,yoy_pct} 对象传进来
    if ('value' in v) return formatNum(v.value)
    return '-'
  }
  const n = Number(v)
  if (Number.isNaN(n)) return '-'
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return Math.round(n).toString()
}

function formatShortTime(t) {
  if (t === null || t === undefined || t === '' || t === '-') return '-'
  const d = dayjs(t)
  if (!d.isValid()) return '-'
  // 与 OperationDashboard 保持一致,跨日期范围可分清哪天
  return d.format('MM-DD HH:mm')
}

function formatMoney(v) {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'object') {
    if ('value' in v) return formatMoney(v.value)
    return '-'
  }
  const n = Number(v)
  if (Number.isNaN(n)) return '-'
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function scoreTagType(score, d = 500, w = 300, s = 100) {
  if (score >= d) return 'danger'
  if (score >= w) return 'warning'
  if (score >= s) return 'success'
  return 'info'
}

function complaintSourceType(source) {
  if (source === '内部投诉') return 'warning'
  if (source === '外部投诉') return 'danger'
  if (source === '商户号投诉') return 'primary'
  // #47 (2026-09-10): 卡商单列 —— 原先把卡商归入外部投诉, 现独立配色
  if (source === '(外)卡商' || source === '卡商群投诉') return 'success'
  return 'info'
}

function complaintStatusType(status) {
  if (!status) return 'info'
  if (status.includes('完成') || status.includes('已结') || status.includes('解决')) return 'success'
  if (status.includes('处理中') || status.includes('跟进')) return 'warning'
  if (status.includes('待')) return 'danger'
  return 'info'
}

// 投诉来源 登记/处理 双柱图 (F-P1-2)
// ★★ 2026-09-11 (#53): 卡商**移出** —— 用户要求"不纳入总量的管理, 整体独立统计分析"。
//    双柱图现为**三源**(内部/商户号/外部); 卡商见独立的卡商区块。
const complaintRegHandledOption = computed(() => {
  const src = complaintSummaryData.value
  const KEYS = [
    { name: '内部投诉', key: 'internal' },
    { name: '商户号投诉', key: 'merchant' },
    { name: '外部投诉', key: 'external' },
  ]
  const data = KEYS.map(({ name, key }) => {
    const s = src[key] || {}
    return {
      name,
      registered: Number(s.registered ?? s.total) || 0,
      handled: Number(s.handled) || 0,
    }
  })
  if (!data.some(d => d.registered || d.handled)) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 8, right: 8, bottom: 32, top: 8, containLabel: true },
    xAxis: { type: 'category', data: data.map(d => d.name), axisLabel: { fontSize: 10, interval: 0 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 }, minInterval: 1 },
    series: [
      { name: '登记', type: 'bar', data: data.map(d => d.registered),
        itemStyle: { color: '#2563eb', borderRadius: [3, 3, 0, 0] }, barMaxWidth: 16,
        label: { show: true, position: 'top', fontSize: 10 } },
      { name: '处理', type: 'bar', data: data.map(d => d.handled),
        itemStyle: { color: '#10b981', borderRadius: [3, 3, 0, 0] }, barMaxWidth: 16,
        label: { show: true, position: 'top', fontSize: 10 } },
    ],
  }
})

// 投诉分类柱状图选项
const complaintCategoryOption = computed(() => {
  // 合并三个来源的by_category
  const cats = {}
  const add = (arr) => {
    if (!arr) return
    arr.forEach(c => {
      const name = c.name || '未标注'
      cats[name] = (cats[name] || 0) + Number(c.count || 0)
    })
  }
  add(complaintSummaryData.value.internal?.by_category)
  add(complaintSummaryData.value.merchant?.by_category)
  add(complaintSummaryData.value.external?.by_category)
  add(complaintSummaryData.value.card?.by_category)
  const entries = Object.entries(cats).sort((a, b) => b[1] - a[1])
  const x = entries.map(e => e[0])
  const data = entries.map(e => e[1])
  if (data.length === 0 || data.every(v => v === 0)) return null
  const total = data.reduce((s, v) => s + v, 0)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const p = params[0]
        const v = p?.value || 0
        const pct = total ? ((v / total) * 100).toFixed(1) : '0.0'
        return `${p?.name}<br/>${v} 条 (${pct}%)`
      },
    },
    grid: { left: 8, right: 8, bottom: 28, top: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: x,
      axisLabel: { fontSize: 10, interval: 0, rotate: x.length > 4 ? 20 : 0 },
    },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    series: [{
      type: 'bar',
      data: data.map(v => ({ value: v, itemStyle: { color: '#f59e0b', borderRadius: [3, 3, 0, 0] } })),
      barMaxWidth: 24,
      label: { show: true, position: 'top', fontSize: 10 },
    }],
  }
})

// 明细表列头筛选选项(来源/分类/状态, el-table :filters 需要 {text,value})
const complaintSourceColumnFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.source).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})
const complaintCategoryColumnFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.category).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})
const complaintEntityColumnFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.entity || '—').filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})
const complaintStatusColumnFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.status).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})

// 明细区仅按来源(四类)筛选; 分类/状态筛选与排序在表格列头
const complaintFilteredList = computed(() => {
  if (!complaintFilterSource.value) return complaintList.value
  return complaintList.value.filter(d => d.source === complaintFilterSource.value)
})

// 时间字符串排序(ISO), 空值排后(供表格列 sort-method)
function sortTimeStr(a, b) {
  const va = a || ''
  const vb = b || ''
  if (va === vb) return 0
  return va < vb ? -1 : 1
}

// 补充数据源汇总文本(315/卡商, workbuddy推送)
const supplExternal = computed(() => complaintSummaryData.value?.supplementary?.external || { total: 0, matched: 0 })
const supplSummaryText = computed(() => {
  const s = complaintSummaryData.value?.supplementary || {}
  const ext = s.external || { total: 0, matched: 0 }
  const card = s.card || { total: 0, matched: 0 }
  if (!ext.total && !card.total) return ''
  // #47 (2026-09-10): 卡商单列展示(不再写成'卡商群投诉'并归入外部)
  return `补充数据源(按SN关联工单): 315外部投诉 ${formatNum(ext.total)} 条(已关联 ${formatNum(ext.matched)}) · 卡商群投诉 ${formatNum(card.total)} 条(已关联 ${formatNum(card.matched)})`
})

// #47: 卡商登记/处理(来源桶新增的第 4 类)
const supplCard = computed(() => complaintSummaryData.value?.supplementary?.card || { total: 0, matched: 0 })

// ★★ 2026-09-11 (#53): 卡商**独立统计** —— 不属 complaintSummaryData 的总量
//   用户: "(外)卡商 的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
//   后端返回 `card` 对象(与 reg_handled 平行), 此处独立取值。
const cardData = computed(() => complaintSummaryData.value?.card || {})

// 补充明细 tooltip 文案(来源/日期/内容/处理明细/完成时间)
function supplTooltip(row) {
  const list = row.supplementary || []
  if (!list.length) return '无补充明细'
  return list.map(s => {
    const parts = [`[${s.source}]${s.complaint_date || ''}`]
    if (s.problem) parts.push((s.problem || '').slice(0, 60))
    if (s.handle_detail) parts.push(`处理:${(s.handle_detail || '').slice(0, 40)}`)
    if (s.handle_time) parts.push(`完成:${s.handle_time}`)
    return parts.join(' ')
  }).join('\n')
}

// 影响事件状态映射 (后端 Incident.status: registered/processing/calmed/reviewed)
const INCIDENT_STATUS = {
  registered: { label: '待确认', type: 'warning' },
  processing: { label: '处理中', type: 'danger' },
  calmed: { label: '已平息', type: 'info' },
  reviewed: { label: '已复盘', type: 'info' },
}
function incidentStatusLabel(s) {
  return INCIDENT_STATUS[s]?.label || s || '一般'
}
function incidentTagType(s) {
  return INCIDENT_STATUS[s]?.type || 'warning'
}

// 首页突出: 当日活跃影响事件(周期性 / 未闭环 / 高严重度)
const activeIncidents = computed(() => {
  const list = queueData.value?.incidents || []
  return list.filter(inc => inc.is_recurring || !inc.is_closed || inc.severity === '高')
})
const hasHighSeverityIncident = computed(() => activeIncidents.value.some(inc => inc.severity === '高'))
const impactBannerTitle = computed(() => {
  const n = activeIncidents.value.length
  const recur = activeIncidents.value.filter(inc => inc.is_recurring).length
  const high = hasHighSeverityIncident.value
  if (high) return `⚠ 当日有 ${n} 项影响事件需关注(含高严重度)`
  if (recur) return `当日有 ${n} 项影响事件生效(含 ${recur} 项周期性)`
  return `当日有 ${n} 项影响事件`
})
// 点击横幅/事件chip滚动到影响事件区
function goToImpactSection() {
  const el = document.querySelector('.impact-banner')
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/* ══════════════ #76 统一卡片下钻 ══════════════
   设计说明（Design Rationale）
   目的：**让每个数字都能被追问** —— 使用者看到异常数字时，当场点进去看构成，
         不必截图找人。这是「决策分析看板」而非「展示看板」的分界线。
   逻辑：三类去向（规范 §3.2 优先级）：
         ① 跳明细页 + 带筛选参数（默认，本页全部下钻走这条）
         ② 弹抽屉（已有：SN/人名 → 审计抽屉，沿用 useAudit，不改）
         ③ 滚到同页区块（已有：影响事件横幅，沿用 goToImpactSection）
   口径：★ 下钻必须带**当前时间窗口**（规范 §3.3）—— 本页窗口由 getRange()
         产出（日视图=当日，周视图=本周），逐字透传给目标页，
         否则目标页落到默认近30天，数字与刚才所见对不上 = 下钻失效。
   取舍：❌ 不为每个数字单独开发抽屉 —— 明细页已具备筛选/排序/导出，
         重复造抽屉是「同指标两处各实现一遍」（违反界面设计规范 §四）。
         ❌ 不做「趋势图点击下钻到某小时明细」—— 明细接口按日筛选，
         无小时级筛选能力；强行做会落到「点了一天明细」的误导结果。
         已在图卡 ⓘ 中声明该限制。
   工单：#76 · 规范：docs/standards/看板卡片数据说明与下钻规范.md */

const router = useRouter()

/** 当前窗口（日=当日 / 周=本周），与所有卡片所见窗口一致 */
function drillRange() {
  return getRange()
}

/**
 * 跳到投诉分析页并带完整上下文。
 * @param {object} opt - { view, source } view=overview|channel|subject, source=internal|merchant|external|card
 */
function drillToComplaint({ view = 'overview', source = null } = {}) {
  const { start, end } = drillRange()
  const query = { tab: 'analysis', view, start, end }
  // 卡商/内部等渠道映射到目标页的 channelTab 取值（见 ComplaintAnalysis.vue 的 channelTab）
  if (source) query.source = source
  router.push({ path: '/dashboards/complaint-analysis', query })
}

/** 跳到执行跟踪（积压明细），带窗口 */
function drillToBacklog() {
  const { start, end } = drillRange()
  router.push({ path: '/dashboards/complaint-analysis', query: { tab: 'execute', start, end } })
}

/** 跳到退款挽回分析，带窗口 */
function drillToRefundRecovery() {
  const { start, end } = drillRange()
  router.push({ path: '/dashboards/refund-recovery', query: { start, end } })
}

/** 跳到运营看板（人均效率），带窗口 */
function drillToOperation() {
  const { start, end } = drillRange()
  router.push({ path: '/dashboards/operation', query: { start, end } })
}

/** KPI 卡 → 对应分析页（key 与 kpiCards 的 key 一一对应） */
function onKpiClick(key) {
  const map = {
    reception: () => router.push({ path: '/dashboards/reception', query: drillRange() }),
    session: () => router.push({ path: '/dashboards/reception', query: drillRange() }),
    refund_count: () => router.push({ path: '/dashboards/refund', query: drillRange() }),
    refund_amount: () => router.push({ path: '/dashboards/refund', query: drillRange() }),
    complaint: () => drillToComplaint({ view: 'overview' }),
    staff_on_duty: () => drillToOperation(),
    recovery_rate: () => drillToRefundRecovery(),
  }
  map[key]?.()
}

/** 投诉来源卡 → 渠道明细（source 用目标页 channelTab 的取值） */
const COMPLAINT_DRILL_SOURCE = {
  内部投诉: 'internal',
  商户号投诉: 'merchant',
  外部投诉: 'external',
  '(外)卡商': 'card',
}
function onComplaintSourceClick(label) {
  const src = COMPLAINT_DRILL_SOURCE[label]
  if (src) drillToComplaint({ view: 'channel', source: src })
}

// 专项任务状态映射 (后端 SpecialTask.status)
const TASK_STATUS = {
  pending_start: '待启动',
  executing: '执行中',
  following: '跟进中',
  done: '已完成',
  archived: '已归档',
}
function taskStatusLabel(s) {
  return TASK_STATUS[s] || s || '—'
}

// 选中的看板日期 (默认今天, 支持切换到历史任意一天)
const selectedDate = ref(dayjs().format('YYYY-MM-DD'))
const isToday = computed(() => selectedDate.value === dayjs().format('YYYY-MM-DD'))

// ── 前端内存缓存: 按 viewMode+start+end 缓存上次加载结果, 切回同窗口秒开 ──
// 背景(2026-08-31): 后端历史窗口缓存 TTL 已拉长到 24h, 但前端切日期仍会
// Promise.all 重拉 8 个接口。历史数据不变化, 切回同一天直接用内存数据即可,
// 不必等后端缓存命中(省一次网络往返)。仅「日视图·今天」手动/自动刷新强制重拉。
const _viewCacheKey = () => {
  const { start, end } = getRange()
  return `${viewMode.value}|${start}|${end}`
}
let _viewCache = null  // { key, data: {...8个响应} } 单条(最近一次加载的窗口)
// 是否强制刷新: 仅「日视图·今天」的手动刷新/自动刷新; 切换窗口一律走缓存
const _forceRefresh = () => viewMode.value === 'day' && isToday.value

// 周视图的起止日期(周一00:00 到 周日23:59;如果周日晚于今天则截止到今天结束)
// ⚠️ 已抽到 @/utils/weekRange 作为单一来源（运营看板此前各写一份，写法不一致
//    导致「周日当天本周算成下周」的 bug）。
function getWeekRange() {
  const { start, end } = _getWeekRange(selectedDate.value)
  let sunday = end
  const nowEnd = dayjs().endOf('day')
  if (sunday.isAfter(nowEnd)) sunday = nowEnd
  return { start, end: sunday }
}

// 周视图下,判断是否是"当前周"(即selectedDate所在周包含今天)
const isCurrentWeek = computed(() => {
  if (viewMode.value !== 'week') return false
  const { start, end } = getWeekRange()
  const now = dayjs()
  return (now.isSame(start, 'day') || now.isAfter(start)) && (now.isSame(end, 'day') || now.isBefore(end.add(1, 'second')))
})
// 实时数据标签: 日视图且是今天 才显示"实时数据"
const showRealtimeTag = computed(() => viewMode.value === 'day' && isToday.value)

// 展示日期文案
const displayDate = computed(() => {
  if (viewMode.value === 'week') {
    const { start, end } = getWeekRange()
    const sameMonth = start.month() === end.month()
    const range = sameMonth
      ? `${start.format('YYYY年MM月DD日')} - ${end.format('DD日')}`
      : `${start.format('YYYY年MM月DD日')} - ${end.format('MM月DD日')}`
    const wk = isCurrentWeek.value ? ' 本周' : ''
    return `${range}${wk}`
  }
  const d = dayjs(selectedDate.value)
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.day()]
  return `${d.format('YYYY年MM月DD日')} ${wd}`
})

// 看板标题
// ★ #80 正名: 「决策分析看板」→「今日值班看板」（用户 2026-09-13 拍板）。
//   理由: 本页内容是**值班台** —— 实时KPI + 值班组长 + 今日影响事件 + 我的待办 +
//   进行中专项 + 政策。它是「平时每天看」的那一个；
//   称为「决策分析」属**过度拔高** —— 本页**没有归因能力、没有目标达成对比、
//   没有异常分解**，三个决策分析的必要件一个都不具备。
//   真正的分析页是 `/dashboards/operation`（四时间粒度 + 全量人效 + 原因Top10），
//   已同步正名为「运营分析看板」。
//   ⚠️ 保留「今日」但主名固定不随视图跳动（原「本周看板」的写法会让使用者
//      以为是两个页面），日期/周次由标题右侧 `displayDate` 承载。
const pageTitle = '今日值班看板'
// 选中的日期/周对应的时间范围 (传给后端 start/end)
function getRange() {
  if (viewMode.value === 'week') {
    const { start, end } = getWeekRange()
    return {
      start: start.format('YYYY-MM-DD'),
      end: end.format('YYYY-MM-DD'),
    }
  }
  const d = dayjs(selectedDate.value)
  return {
    start: d.startOf('day').format('YYYY-MM-DD'),
    end: d.endOf('day').format('YYYY-MM-DD'),
  }
}

function onDateChange() {
  // 切换日期后重新加载全部数据
  loadAll()
}
function onViewModeChange() {
  loadAll()
}

// KPI卡片
const kpiCards = computed(() => {
  const d = kpiData.value
  // 后端 today_kpi 返回嵌套结构 {value, yoy_value, yoy_pct, ...}
  const reception = d.reception || {}
  const refundCnt = d.refund_count || {}
  const refundAmt = d.refund_amount || {}
  const complaint = d.complaint || {}
  const staffOnDuty = d.staff_on_duty || {}
  const recovery = recoveryData.value?.kpi || {}
  const recoveryRate = recovery.success_rate != null && !Number.isNaN(Number(recovery.success_rate))
    ? Number(recovery.success_rate).toFixed(1) + '%'
    : '-'
  return [
    {
      key: 'reception',
      label: '接待数',
      displayValue: formatNum(reception.device_count),
      icon: 'Service',
      colorClass: 'kpi-blue',
      // ★ #78: 按 SN(接待数)口径分层 —— 含 AI 独有的 SN
      stack: buildStack(reception.breakdown, 'devices'),
      yoy: (reception.device_count_yoy_pct != null && reception.device_count_yoy_pct !== 0)
        ? `${reception.device_count_yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(reception.device_count_yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (reception.device_count_yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'session',
      label: '会话数',
      displayValue: formatNum(reception.value),
      icon: 'ChatDotRound',
      colorClass: 'kpi-green',
      // ★ #78: 按会话口径分层（含 AI 全自动接待）
      stack: buildStack(reception.breakdown, 'sessions'),
      yoy: (reception.yoy_pct != null && reception.yoy_pct !== 0)
        ? `${reception.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(reception.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (reception.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'refund_count',
      label: '退款笔数',
      displayValue: formatNum(refundCnt.value),
      icon: 'Warning',
      colorClass: 'kpi-red',
      yoy: (refundCnt.yoy_pct != null && refundCnt.yoy_pct !== 0)
        ? `${refundCnt.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(refundCnt.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (refundCnt.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'refund_amount',
      label: '退款金额',
      displayValue: '¥' + formatMoney(refundAmt.value),
      icon: 'Money',
      colorClass: 'kpi-orange',
      yoy: (refundAmt.yoy_pct != null && refundAmt.yoy_pct !== 0)
        ? `${refundAmt.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(refundAmt.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (refundAmt.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'complaint',
      label: '投诉数',
      displayValue: formatNum(complaint.value),
      icon: 'Bell',
      colorClass: 'kpi-purple',
      yoy: (complaint.yoy_pct != null && complaint.yoy_pct !== 0)
        ? `${complaint.yoy_pct > 0 ? '↑' : '↓'} ${Math.abs(complaint.yoy_pct).toFixed(1)}%`
        : '',
      yoyClass: (complaint.yoy_pct ?? 0) >= 0 ? 'yoy-up' : 'yoy-down',
    },
    {
      key: 'recovery',
      label: '退款挽回',
      displayValue: recoveryRate,
      icon: 'RefreshLeft',
      colorClass: 'kpi-cyan',
      yoy: '',
      yoyClass: '',
    },
    {
      key: 'on_duty',
      label: '值班人员',
      displayValue: `${formatNum(staffOnDuty.value)}人`,
      icon: 'User',
      colorClass: 'kpi-teal',
      yoy: '',
      yoyClass: '',
    },
  ]
})

// 值班组长/主管 (仅显示岗位为主管/组长/副主管的人员; 注意: is_management=true不仅包含管理岗,
// 还包含投诉/退款挽回等享有跨类豁免的专项岗, 不能直接用is_management判断值班组长)
const DUTY_POSITIONS = ['主管', '组长', '副主管']

/** ★ 岗位 → 值班徽标（用户 2026-09-14：「组长」/「管理」要一眼可辨）
 *
 *  设计要点:
 *   · 主管级 → 文案「管理」（管理视角的词，而非职级名）
 *   · 组长/副主管 → 文案「组长」（副主管在值班语境下同属组级管理者）
 *   · 客服/投诉专员/退款处理专员 → 不进值班条（非决策岗）
 *   ⚠️ 与 `DUTY_POSITIONS` 必须保持同源 —— 两边不一致会出现
 *      "徽标为空的芯片"或"该显示却查不到标签"。
 */
const DUTY_ROLE_META = {
  主管: { label: '管理', tagType: 'danger', bg: '#b91c1c' },
  副主管: { label: '组长', tagType: 'warning', bg: '#b45309' },
  组长: { label: '组长', tagType: 'primary', bg: '#1d4ed8' },
}
const dutyLeaders = computed(() => {
  const staff = staffData.value?.data || []
  return staff
    .filter(s => DUTY_POSITIONS.includes(s.position) && !s.is_virtual)
    .map(s => {
      const meta = DUTY_ROLE_META[s.position] || {}
      return {
        name: s.name,
        group: s.group,
        department: s.department,
        position: s.position,
        roleLabel: meta.label || '',
        tagType: meta.tagType || 'info',
        roleBg: meta.bg || '#6b7280',
      }
    })
})

// 四路经营退款路线配色 (A 线上直退 / B 钱包取现 / C 闪猫退款 / D 手动退款)
const REFUND_ROUTES = [
  { key: 'a', label: 'A线上直退', short: 'A直退', color: '#ef4444' },
  { key: 'b', label: 'B钱包取现', short: 'B取现', color: '#f97316' },
  { key: 'c', label: 'C闪猫退款', short: 'C闪猫', color: '#a855f7' },
  { key: 'd', label: 'D手动退款', short: 'D手动', color: '#94a3b8' },
]

function _rrLabel(r) { return isMobile.value ? (r.short || r.label) : r.label }

// ★ #85 投诉两序列配色（登记=橙柱 / 处理=深青线）
//   登记用暖色（“进来了”）、处理用冷青色（“解决了”）—— 直觉上区分"进"与"出"。
//   ⚠️ 处理线**不能用 #10b981** —— 那是「接待数」柱的颜色，
//     同色会导致同屏两根“绿色”无法区分（实测像素采样发现），
//     故选 teal-700 (#0f766e)，与全图现有色均不撞。
const COMPLAINT_COLORS = { registered: '#f59e0b', handled: '#0f766e' }

// 将四路退款拆分 + 工作量/投诉 组织成 ECharts series
function buildTrendSeries(data) {
  const groups = trendSelectedGroups.value
  const series = []
  if (groups.includes('reception')) {
    series.push({ name: isMobile.value ? '会话' : '会话数', type: 'bar', data: data.reception || [], itemStyle: { color: '#3b82f6' }, barMaxWidth: isMobile.value ? 14 : 20 })
    series.push({ name: isMobile.value ? '接待' : '接待数', type: 'bar', data: data.reception_devices || [], itemStyle: { color: '#10b981' }, barMaxWidth: isMobile.value ? 14 : 20 })
  }
  if (groups.includes('complaint')) {
    // ★ #85: 投诉改为 **柱(登记) + 线(处理)**（用户 2026-09-14 选定）
    //   为什么拆：原单柱把「登记」与「处理」混为一种含义，主管无法判断
    //   「今天进的多」还是「今天清的多」—— 两者要采取的动作完全不同。
    //   口径（与后端同源，勿在前端另算）:
    //     · complaint      = 登记（工单 register_time 落桶，三源分桶，**不含卡商**）
    //     · complaint_done = 处理（finish_time 落桶 + 真实完成 ≥3min，**含往期单办结**）
    //   ★ 不写「投诉量」—— 避免又产生第三种语义（#85 的核心教训）
    series.push({
      name: isMobile.value ? '登记' : '投诉登记',
      type: 'bar', data: data.complaint || [],
      itemStyle: { color: COMPLAINT_COLORS.registered },
      barMaxWidth: isMobile.value ? 14 : 20,
    })
    series.push({
      name: isMobile.value ? '处理' : '投诉处理',
      type: 'line', data: data.complaint_done || [],
      smooth: true, symbol: 'circle', symbolSize: 5,
      itemStyle: { color: COMPLAINT_COLORS.handled },
      lineStyle: { width: 2, color: COMPLAINT_COLORS.handled },
    })
  }
  if (groups.includes('refund')) {
    REFUND_ROUTES.forEach(r => {
      // 退款笔数: 四路堆叠柱
      series.push({ name: _rrLabel(r), type: 'bar', stack: 'refund_cnt', data: data[`refund_count_${r.key}`] || [], itemStyle: { color: r.color }, barMaxWidth: isMobile.value ? 14 : 20 })
    })
    REFUND_ROUTES.forEach(r => {
      // 退款金额: 四路堆叠面积线 (名字后不带 legend, 单独在 tooltip 体现)
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
    // ★ #85: 图例写「登记」「处理」——**不写"投诉量"**，避免第三种语义
    items.push(isMobile.value ? '登记' : '投诉登记')
    items.push(isMobile.value ? '处理' : '投诉处理')
  }
  if (groups.includes('refund')) {
    REFUND_ROUTES.forEach(r => items.push(_rrLabel(r)))
  }
  return items
}

// 手机上合并 0~8点 (首桶 0~7 点合成一个), 展示 0~8点 + 08:00~23:00, 更适配窄屏
function mergeMobileTrend(data) {
  const labels = data?.hour_labels || data?.slot_labels || []
  const MERGE_N = 8
  const sumHead = (arr) => {
    if (!Array.isArray(arr)) return 0
    return arr.slice(0, MERGE_N).reduce((s, v) => s + (typeof v === 'number' ? v : 0), 0)
  }
  const tail = (arr) => (Array.isArray(arr) ? arr.slice(MERGE_N) : [])
  return {
    labels: ['0~8点', ...(Array.isArray(labels) ? labels.slice(MERGE_N) : [])],
    reception: [sumHead(data.reception), ...tail(data.reception)],
    reception_devices: [sumHead(data.reception_devices), ...tail(data.reception_devices)],
    complaint: [sumHead(data.complaint), ...tail(data.complaint)],
    // ★ #85: 处理序列同样需合并（否则手机端处理线为空）
    complaint_done: [sumHead(data.complaint_done), ...tail(data.complaint_done)],
    refund_count_a: [sumHead(data.refund_count_a), ...tail(data.refund_count_a)],
    refund_count_b: [sumHead(data.refund_count_b), ...tail(data.refund_count_b)],
    refund_count_c: [sumHead(data.refund_count_c), ...tail(data.refund_count_c)],
    refund_count_d: [sumHead(data.refund_count_d), ...tail(data.refund_count_d)],
    refund_amount_a: [sumHead(data.refund_amount_a), ...tail(data.refund_amount_a)],
    refund_amount_b: [sumHead(data.refund_amount_b), ...tail(data.refund_amount_b)],
    refund_amount_c: [sumHead(data.refund_amount_c), ...tail(data.refund_amount_c)],
    refund_amount_d: [sumHead(data.refund_amount_d), ...tail(data.refund_amount_d)],
  }
}

// 趋势图
const trendOption = computed(() => {
  const raw = hourlyData.value || {}
  const data = isMobile.value ? mergeMobileTrend(raw) : raw
  const labels = data?.hour_labels || data?.labels || data?.slot_labels || data?.hours || []
  const mob = isMobile.value
  const showRefund = trendSelectedGroups.value.includes('refund')
  return {
    tooltip: { trigger: 'axis' },
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
      : { left: 40, right: showRefund ? 60 : 20, bottom: 30, top: 40 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: mob ? 9 : 11, interval: 0, rotate: mob ? 30 : 0 },
    },
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
        axisLabel: { fontSize: mob ? 9 : 11 },
        splitLine: { show: false },
      },
    ],
    series: buildTrendSeries(data),
  }
})

// 判定"企微人员":部门为"企微" / 姓名/别名前缀为"企微-" / qw_user_id 已绑定
// (用于人均效率默认过滤, 比严格 qw_user_id 非空更贴近业务语义: 部门=企微就展示)
function isQwStaff(s) {
  if (!s) return false
  if ((s.qw_user_id || '').trim()) return true
  if ((s.department || s.dept || '') === '企微') return true
  const nm = (s.name || s.op || s.display_name || '').trim()
  return /^企微[-_·\s]?/.test(nm)
}

// 工作效率表: 各专项接待明细列 (按类型分开SN/接待数)
const SPECIALTY_COLS = [
  { key: '咨询接待', short: '咨询', color: '#3b82f6' },
  { key: '套餐专项', short: '套餐', color: '#f97316' },
  { key: '退款处理', short: '处理', color: '#ef4444' },
  { key: '退款挽回', short: '挽回', color: '#06b6d4' },
  { key: '内部投诉', short: '内诉', color: '#7c3aed' },
  { key: '外部投诉', short: '外诉', color: '#dc2626' },
]
// 从breakdown取某专项的接待数(个/SN去重后)
function getSpecialtyPeople(row, catKey) {
  const bd = row.breakdown || {}
  const info = bd[catKey]
  return info ? Number(info.people || 0) : 0
}

// 人均效率 (全量排序, 默认仅企微, 勾选后显示所有人)
const topStaffList = computed(() => {
  let items = staffData.value?.data || []
  items = items.filter(s => !s.is_virtual)
  // 只要有任意业务数据(会话或接待)就保留 (避免空行占位 与后端ops_union语义一致)
  items = items.filter(s => (Number(s.reception_count) || 0) + (Number(s.reception_device_count) || 0) + (Number(s.rc) || 0) + (Number(s.rdc) || 0) > 0)
  if (!showAllStaff.value) {
    items = items.filter(isQwStaff)
  }
  // 默认按接待效率(eff_score)降序排名
  items = [...items].sort((a, b) => (Number(b.eff_score) || 0) - (Number(a.eff_score) || 0))
  return items
})

// ★ #78 AI / 系统账户单列（用户明确：「区分出来，单列也可以」）
//   设计说明: 此前 `is_virtual` 的账户被 `topStaffList` 直接**过滤丢弃**，
//   导致 AI 的工作量在生产上完全不可见（只能从「会话数」虚高中间接感知）。
//   这里改为**单列展示**：AI 仍在参与工作，管理者需要看到它干了多少。
//   取舍: 不并入真人排名（会挤掉真人），也不隐藏（会看不见 AI 规模），
//   而是单独一张小表 —— 两者都能看到，互不干扰。
const virtualStaffList = computed(() => {
  const items = staffData.value?.data || []
  return items
    .filter(s => s.is_virtual)
    .filter(s => (Number(s.reception_count) || 0) + (Number(s.reception_device_count) || 0) > 0)
    .sort((a, b) => (Number(b.reception_count) || 0) - (Number(a.reception_count) || 0))
})
/** 该账户的来源标签/配色（与 KPI 卡 SOURCE_KINDS 同一套色） */
/** 该 AI/系统账户名下会话数占全站会话数的百分比（可控指标：AI 接管率） */
function aiSharePct(row) {
  const total = Number(kpiData.value?.reception?.value || 0)
  if (!total) return '0.0'
  return (Number(row.reception_count || 0) * 100 / total).toFixed(1)
}

// 部门列显示策略:移动端且仅企微时隐藏(表头已筛选); 显示所有人时保留以便区分
const showDeptColumn = computed(() => !isMobile.value || showAllStaff.value)

// 退款挽回SN列表 (按时间倒序)
const recoverySnList = computed(() => {
  const details = recoveryData.value?.sn_details || []
  return details.map(d => ({
    sn: d.sn,
    result: d.result,
    staff: d.staff,
    group: d.staff_group,
    reason: d.reason,
    time: d.time,
    amount: d.amount,
    status: d.status,
    src: d.src,
  })).sort((a, b) => {
    const ta = dayjs(a.time).valueOf()
    const tb = dayjs(b.time).valueOf()
    return tb - ta
  })
})

// 退款SN筛选选项
const refundHandlerOptions = computed(() => {
  const set = new Set()
  recoverySnList.value.forEach(d => { if (d.staff) set.add(d.staff) })
  return Array.from(set).sort()
})
const refundReasonOptions = computed(() => {
  const set = new Set()
  recoverySnList.value.forEach(d => { if (d.reason) set.add(d.reason) })
  return Array.from(set).sort()
})
const refundFilteredList = computed(() => {
  return recoverySnList.value.filter(d => {
    if (refundFilterResult.value && d.result !== refundFilterResult.value) return false
    if (refundFilterHandler.value && d.staff !== refundFilterHandler.value) return false
    if (refundFilterReason.value && d.reason !== refundFilterReason.value) return false
    return true
  })
})

// 退款原因分布: 堆叠柱状图(成功+失败)
const recoveryReasonStackOption = computed(() => {
  // 从 sn_details 聚合成功/失败数
  const map = {}
  recoverySnList.value.forEach(d => {
    const r = d.reason || '未知'
    if (!map[r]) map[r] = { success: 0, failed: 0 }
    if (d.result === 'success') map[r].success++
    else map[r].failed++
  })
  const entries = Object.entries(map).sort((a, b) => (b[1].success + b[1].failed) - (a[1].success + a[1].failed))
  const x = entries.map(e => e[0])
  const successData = entries.map(e => e[1].success)
  const failedData = entries.map(e => e[1].failed)
  const total = successData.reduce((s, v) => s + v, 0) + failedData.reduce((s, v) => s + v, 0)
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const reason = params[0]?.axisValue || ''
        let html = `<b>${reason}</b><br/>`
        let tot = 0
        params.forEach(p => {
          const v = p.value || 0
          tot += v
          const marker = `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${p.color};margin-right:4px"></span>`
          html += `${marker}${p.seriesName}: ${v} 单<br/>`
        })
        const pct = total ? ((tot / total) * 100).toFixed(1) : '0.0'
        html += `<span style="color:#909399">合计: ${tot} 单 (${pct}%)</span>`
        return html
      },
    },
    legend: { data: ['成功挽回', '挽回失败'], bottom: 0, textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8 },
    grid: { left: 8, right: 8, bottom: 28, top: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: x,
      axisLabel: { fontSize: 10, interval: 0, rotate: x.length > 4 ? 20 : 0 },
    },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    series: [
      {
        name: '成功挽回', type: 'bar', stack: 'total', data: successData,
        itemStyle: { color: '#22c55e', borderRadius: [0, 0, 0, 0] },
        barMaxWidth: 24,
        label: { show: true, position: 'inside', fontSize: 9, color: '#fff', formatter: (p) => p.value > 0 ? p.value : '' },
      },
      {
        name: '挽回失败', type: 'bar', stack: 'total', data: failedData,
        itemStyle: { color: '#ef4444', borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 24,
        label: { show: true, position: 'inside', fontSize: 9, color: '#fff', formatter: (p) => p.value > 0 ? p.value : '' },
      },
    ],
  }
})

// 成功率颜色
const successRateColor = computed(() => {
  const rate = Number(recoveryData.value?.kpi?.success_rate || 0)
  if (rate >= 38) return { background: '#f0fdf4' }
  if (rate >= 30) return { background: '#fefce8' }
  return { background: '#fef2f2' }
})

// 加载数据
// 前端内存缓存: 切回同窗口直接用上次结果(历史数据不变化), 不发请求秒开。
// 仅「日视图·今天」手动/自动刷新走 force=true 强制重拉。
async function loadAll({ force = false } = {}) {
  // 命中内存缓存且非强制刷新 → 直接渲染(不发网络请求)
  if (!force && !_forceRefresh() && _viewCache && _viewCache.key === _viewCacheKey()) {
    applyViewData(_viewCache.data)
    return
  }
  loading.value = true
  try {
    const { start, end } = getRange()
    const params = { start, end }
    const grain = viewMode.value === 'week' ? 'day' : 'hour'
    
    const [kpiRes, hourlyRes, staffRes, queueRes, recoveryRes, complaintSummaryRes, complaintDetailRes, backlogRes] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend({ ...params, trend_grain: grain }),
      today.staff({ ...params, aggregate_by: 'person', topn: 200 }),
      today.queue(params),
      today.refundRecovery(params),
      complaintSummary(params),
      complaintDetail({ ...params, limit: viewMode.value === 'week' ? 1000 : 500, include_supplementary: true }),
      // 积压水位: 固定近30天窗口(登记日期落在窗口内的未完成), 避免历史遗留无限堆叠
      complaintBacklog({ start: dayjs().subtract(30, 'day').format('YYYY-MM-DD'), end: dayjs().format('YYYY-MM-DD') }),
    ])
    
    const data = { kpiRes, hourlyRes, staffRes, queueRes, recoveryRes, complaintSummaryRes, complaintDetailRes, backlogRes }
    _viewCache = { key: _viewCacheKey(), data }
    applyViewData(data)
    
    lastUpdate.value = dayjs().format('HH:mm:ss')
  } catch (e) {
    console.error('加载今日数据失败', e)
  } finally {
    loading.value = false
  }
}

// 把一次加载结果应用到各响应式状态
function applyViewData({ kpiRes, hourlyRes, staffRes, queueRes, recoveryRes, complaintSummaryRes, complaintDetailRes, backlogRes }) {
  if (kpiRes.data) kpiData.value = kpiRes.data
  if (hourlyRes.data) hourlyData.value = hourlyRes.data
  if (staffRes.data) staffData.value = staffRes.data
  if (queueRes.data) queueData.value = queueRes.data
  if (recoveryRes.data) recoveryData.value = recoveryRes.data
  if (complaintSummaryRes.data) complaintSummaryData.value = complaintSummaryRes.data
  if (complaintDetailRes.data) {
    complaintList.value = complaintDetailRes.data?.items || []
    // F-P1-2: 明细窗口 登记/处理 计数(标题展示)
    const d = complaintDetailRes.data
    complaintDetailCount.value = {
      registered: Number(d.registered) || 0,
      handled: Number(d.handled) || 0,
      total: Number(d.total) || (d.items?.length || 0),
    }
  }
  if (backlogRes.data) backlogTotal.value = backlogRes.data?.total || 0
}

let autoRefreshTimer = null

function _onMediaChange(e) {
  isMobile.value = e.matches
}

onMounted(() => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
  loadAll()
  // 仅「日视图·今天」时每60秒自动刷新; 周视图/历史日期数据不需要轮询。
  // force=true: 今天是实时窗口, 自动刷新必须真正重拉(不走内存缓存)。
  autoRefreshTimer = setInterval(() => {
    if (viewMode.value === 'day' && isToday.value) loadAll({ force: true })
  }, 60000)
})

onBeforeUnmount(() => {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer)
  if (_mq) {
    _mq.removeEventListener('change', _onMediaChange)
    _mq = null
  }
})
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

/* 窄屏自适应 */
@media (max-width: 768px) {
  .td-header {
    flex-wrap: wrap;
    gap: 8px;
  }
  .header-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .page-title {
    font-size: 16px;
  }
  .duty-leader-bar {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .section-card {
    padding: 10px;
  }
  .section-header-row {
    flex-wrap: wrap;
    gap: 6px;
  }
  .kpi-strip-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .ksi-value {
    font-size: 15px;
  }
  .ck-value {
    font-size: 16px;
  }
  .bw-value {
    font-size: 26px;
  }
  .recovery-side {
    margin-top: 8px;
  }
  .backlog-watermark {
    margin-top: 8px;
    min-height: 80px;
    padding: 10px 12px;
  }
}
</style>
