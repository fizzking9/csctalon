<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ ★ #80 正名：「运营看板」→「运营分析看板」                                             │
  │                                                                                      │
  │ 目的：运营主管 / 经理在**周例会上做复盘**（周/月/季/年）时回答三个问题：               │
  │       ① 这段时间的产出规模与走向（接待/退款/投诉的量与结构）？                        │
  │       ② 钱退在哪条路上了（A线上直退 / B钱包取现 / C闪猫 / D手动）？                   │
  │       ③ 谁强谁弱、谁需要帮扶（人效排名 + 挽回成功率）。                               │
  │       使用频率：**低频高深度** —— 与「今日值班看板」正相反（那个是高频浅读，           │
  │       60s 自动刷新，服务值班）；本页手动刷新，服务复盘。                              │
  │                                                                                      │
  │ 逻辑：① 时间粒度前置（周/月/季/年四档）—— 复盘的第一动作是「选窗口」；                │
  │       ② 核心指标 → 趋势 → 分项 → 人效 → 专项，从「总量」逐层下潜到「人与原因」；      │
  │       ③ 可控指标前置：退款笔数卡展开**四路结构**（哪条路在漏），                      │
  │         挽回块给**成功/失败占比**（不是只给成功率一个数），让主管知道"够不够";          │
  │       ④ 趋势图按**构成**堆叠（退款四路 / 投诉三源），而非只看总量曲线 ——              │
  │         总量持平但结构恶化（如 D 手动退款占比上升）才是真正该报警的信号。              │
  │                                                                                      │
  │ 口径：接待 = cs_records（分层见 utils/receptionSource）；                             │
  │       退款 = 四路经营合计 A+B+C+D（笔数/金额），E = 实际现金流（对账基准，非经营口径）；│
  │       投诉 = 三源分桶（内部/商户号/外部），**(外)卡商完全独立、不入总量**（#53）；     │
  │       挽回 = 按 SN 计，T+7 窗口。                                                     │
  │       详见 docs/standards/投诉渠道口径与对账说明.md ·                                 │
  │            docs/standards/数据时间字段使用规范.md ·                                    │
  │            docs/standards/看板卡片数据说明与下钻规范.md                               │
  │                                                                                      │
  │ 取舍：❌ **不做**实时（复盘场景不需要 60s 刷新，缓存 24h 换性能）；                    │
  │       ❌ **不堆叠退款金额** —— ABCD 金额混入"退入钱包"等非现金退出，相加无业务意义，   │
  │          金额仅作半透明柱供量级参照（对账基准是 E，已在金额卡单列）；                  │
  │       ❌ **不做**图内点击下钻 —— 明细接口按【日】筛选，无小时级能力，                  │
  │          强行下钻会落到"那一天全部明细"与所点时段不符（规范 §3.4）；                  │
  │       ❌ **不做**按退款原因堆叠挽回 —— 本接口 staff_details 无 reason 维度；           │
  │       ❌ **不隐藏**「有任务但全失败」的人员 —— 那正是最该被看见的。                    │
  │                                                                                      │
  │ 工单：#80（本轮：命名 + 布局硬伤 + 四卡/趋势/挽回改造）                               │
  │       #81（阻塞项：投诉数口径统一，含 #65 黑洞）                                     │
  │       #78（接待来源分层）/ #53（卡商独立）/ #76（下钻与卡片规范）                     │
  └──────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="page-container operation-dashboard">
    <!-- 顶部标题 + 时间粒度切换 -->
    <div class="flex-between mb-16 td-header" style="position: relative; z-index: 2">
      <div class="page-title" style="margin: 0; white-space: nowrap">
        运营分析看板
        <span class="title-date-sep">·</span>
        <span class="title-date">{{ displayDate }}</span>
        <el-tag size="small" :type="grainTagType" effect="plain" style="margin-left: 8px">{{ grainLabel }}</el-tag>
      </div>
      <FilterBar
        v-model:model-value="quickGrainOpt"
        v-model:range="customDateRange"
        :quick-options="grainQuickOptions"
        :show-custom="true"
        show-grain="false"
        :loading="loading"
        @refresh="loadAll"
      >
        <template #left>
          <el-radio-group v-model="grain" size="small" style="margin-right: 12px">
            <el-radio-button value="week">周</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
            <el-radio-button value="quarter">季度</el-radio-button>
            <el-radio-button value="year">年</el-radio-button>
          </el-radio-group>
          <el-select v-if="grain === 'week'" v-model="weekOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="w in weekOptions" :key="w.value" :label="w.label" :value="w.value" />
          </el-select>
          <el-select v-if="grain === 'month'" v-model="monthOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="m in monthOptions" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
          <el-select v-if="grain === 'quarter'" v-model="quarterOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="q in quarterOptions" :key="q.value" :label="q.label" :value="q.value" />
          </el-select>
          <el-select v-if="grain === 'year'" v-model="yearOffset" size="small" style="width: 140px; margin-right: 8px">
            <el-option v-for="y in yearOptions" :key="y.value" :label="y.label" :value="y.value" />
          </el-select>
          <el-select v-if="grain === 'month' || grain === 'quarter' || grain === 'year'" v-model="trendGrain" size="small" style="width: 120px; margin-right: 8px">
            <el-option label="按日聚合" value="day" />
            <el-option label="按周聚合" value="week" />
            <el-option label="按月聚合" value="month" />
          </el-select>
        </template>
      </FilterBar>
    </div>

    <!-- 模块1: 核心指标KPI -->
    <!-- ★ #78 接待来源分层（设计说明 Design Rationale）
         目的: 替运营主管回答「AI 客服接管了多少接待？真人还剩多少工作量？」
         逻辑: 「接待数/会话数」两张卡加**堆叠比例条**（按来源配色）；
               下方工作效率表再加一张「AI / 系统账户」单列表，两者分离不混排。
         口径: 来源判据 = operator + description 文本模式（平台无 AI 标志列），
               见后端 app/services/system_accounts.py；配色与「今日值班看板」共用
               utils/receptionSource.js（规范 §五 禁止重复展示）。
         取舍: ① 不做「AI 转人工」细分（BMS 与智能客服仍在迭代，做了会返工）；
               ② 不删 AI 数据 —— 「AI 接管率」本身是可控指标；
               ③ 真人名下的「系统标记」行仍计入真人（实测过半与真人操作同秒，
                  属连带记录；拆出会虚减人工工作量约 18%）。
         工单: #78 / 架构: docs/arch/A-20260913-001-AI接待口径分层.md -->
    <section class="section-card mb-20">
      <SectionHeader :title="'核心指标'" :sub="'对比' + compareName + '同时段 · ' + periodName">
        <template #extra>
          <el-tooltip effect="dark" placement="top" raw-content :content="RECEPTION_BREAKDOWN_TIP">
            <el-icon style="color:#b0b4bb;cursor:help;font-size:14px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </template>
      </SectionHeader>
      <el-row :gutter="10">
        <el-col v-for="c in kpiCards" :key="c.key" :xs="12" :sm="8" :md="4">
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
            :stack="c.stack || null"
            :chips="c.chips || null"
            :sub-rows="c.subRows || null"
            :tip="c.tip"
            compact
          />
        </el-col>
      </el-row>
    </section>

    <!-- 模块2: 趋势分析
         ★ #80 改造（用户诉求 3）: 退款四路堆叠 + 投诉三源堆叠。
         口径: 退款 = today/hourly-trend 的 refund_count_a~d（按创建时间）；
               投诉 = complaints/summary 的 *.trend（含 channel 回退 + #58 软纠正），
                     仅按日粒度可拆，周/月自动退化为单一「投诉量」柱。
         取舍: ① **金额不堆积** —— ABCD 混入"退入钱包"等非现金退出，相加无业务意义
                 （对账基准是 E，已在「退款金额」卡给出）；金额仅作半透明柱供量级参照
               ② 不在图例写"合计" —— 投诉三源与「核心指标·投诉数」不同源（见 #81），
                 写合计会让人误以为两处应相等
               ③ 图内不做点击下钻 —— 明细接口按【日】筛选，无小时级能力，
                 强行下钻会落到"那一天全部明细"与所点时段不符（见看板卡片规范 §3.4） -->
    <section class="section-card mb-24">
      <SectionHeader title="趋势分析" :sub="`${periodName} · ${rhythmSub}`">
        <template #extra>
          <el-tooltip effect="dark" placement="top" raw-content :content="TREND_TIP" :show-after="200">
            <el-icon style="color:#b0b4bb;cursor:help;font-size:14px;margin-right:8px"><QuestionFilled /></el-icon>
          </el-tooltip>
          <el-checkbox-group v-model="trendSelectedGroups" size="small" class="trend-group-check">
            <el-checkbox-button value="reception">接待</el-checkbox-button>
            <el-checkbox-button value="complaint">投诉</el-checkbox-button>
            <el-checkbox-button value="refund">退款</el-checkbox-button>
          </el-checkbox-group>
        </template>
      </SectionHeader>
      <ChartCard body-style="padding: 8px 10px">
        <BaseChart :option="hourlyOption" height="360px" />
      </ChartCard>
    </section>

    <!-- 模块3: 接待与退款汇总 -->
    <el-row :gutter="12" class="mb-24">
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <SectionHeader title="接待分项汇总" :sub="periodName" />
          <ChartCard title="接待分类分布（累计）">
            <template #extra>
              <el-radio-group v-model="receptionPerspective" size="small">
                <el-radio-button value="count">接待量(次)</el-radio-button>
                <el-radio-button value="device">设备量(台)</el-radio-button>
              </el-radio-group>
            </template>
            <BaseChart :option="receptionPieOption" height="260px" />
          </ChartCard>
        </section>
      </el-col>
      <el-col :xs="24" :md="12">
        <section class="section-card">
          <SectionHeader title="退款分项汇总" :sub="periodName" />
          <ChartCard title="退款原因Top10（累计）">
            <BaseChart :option="refundReasonOption" height="260px" />
          </ChartCard>
        </section>
      </el-col>
    </el-row>

    <!-- 模块4: 工作效率值排名 -->
    <section class="section-card mb-24">
      <SectionHeader title="工作效率值排名" sub="按工作量得分排序 · 支持部门/分组筛选">
        <template #extra>
          <el-space wrap size="small">
            <el-select v-model="filterDepartment" placeholder="全部部门" clearable size="small" style="width: 120px">
              <el-option v-for="d in departmentOptions" :key="d" :label="d" :value="d" />
            </el-select>
            <el-select v-model="filterGroupId" placeholder="全部分组" clearable size="small" style="width: 140px">
              <el-option v-for="g in groupsList" :key="g.id" :label="g.name" :value="g.name" />
            </el-select>
            <el-radio-group v-model="staffFilter.aggregateBy" size="small">
              <el-radio-button value="person">按人</el-radio-button>
              <el-radio-button value="group">按分组</el-radio-button>
              <el-radio-button value="all">全员</el-radio-button>
            </el-radio-group>
            <el-checkbox
              v-model="showAllStaff"
              size="small"
              :disabled="staffFilter.aggregateBy !== 'person'"
            >
              显示所有人(含非企微)
            </el-checkbox>
          </el-space>
        </template>
      </SectionHeader>
      <el-card shadow="never">
        <el-table :data="staffRows" border stripe size="small" max-height="500"
          :default-sort="{ prop: 'eff_score', order: 'descending' }"
        >
          <el-table-column label="排名" width="64" align="center">
            <template #default="{ $index }">
              <el-tag v-if="$index < 3 && staffFilter.aggregateBy !== 'all'" size="small" :type="['danger','warning','success'][$index]" effect="dark">
                Top{{ $index + 1 }}
              </el-tag>
              <span v-else class="text-muted">{{ $index + 1 }}</span>
            </template>
          </el-table-column>
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="人员" width="140">
            <template #default="{ row }">
              <el-link type="primary" underline="never" @click="openPersonAudit(row.name, row.person_id)">{{ row.name }}</el-link>
            </template>
          </el-table-column>
          <el-table-column v-else-if="staffFilter.aggregateBy === 'group'" label="分组" width="140">
            <template #default="{ row }">
              <el-tag size="default" effect="dark" type="primary">{{ row.name || '未分组' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="专项" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.current_specialty" size="small" effect="plain" type="warning">{{ row.current_specialty }}</el-tag>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column v-if="showDeptColumn" label="部门" width="100">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.department === '未设置' || !row.department ? 'info' : 'success'">
                {{ row.department || '—' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reception_count" label="会话数(次)" width="90" align="right" sortable :sort-method="(a,b)=>(Number(a.reception_count)||0)-(Number(b.reception_count)||0)">
            <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
          </el-table-column>
          <!-- 各专项接待数列 (按类型分开SN) -->
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="各专项接待数(个)" align="center">
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
          <el-table-column prop="reception_device_count" label="接待合计" width="80" align="right" sortable :sort-method="(a,b)=>(Number(a.reception_device_count)||0)-(Number(b.reception_device_count)||0)">
            <template #default="{ row }">
              <span style="color:#409eff;font-weight:600">{{ formatNum(row.reception_device_count ?? 0) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="work_score" label="会话效率" width="90" align="right" sortable :sort-method="(a,b)=>(Number(a.work_score)||0)-(Number(b.work_score)||0)">
            <template #default="{ row }">
              <el-popover placement="top" :width="260" trigger="hover">
                <template #reference>
                  <el-tag size="small" effect="plain" :type="scoreTagType(row.work_score,500,300,100)" class="score-tag-hover">
                    <span style="font-weight:600">{{ formatNum(row.work_score) }}</span>
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
                  <el-tag size="small" effect="plain" :type="scoreTagType(row.eff_score,350,200,80)" class="score-tag-hover">
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
          <el-table-column label="核算" width="48" align="center">
            <template #default="{ row }">
              <el-popover placement="left" :width="300" trigger="click">
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
                  <div class="score-tip-note">专项难度系数: 咨询×1.0 套餐×1.8 退款处理×2.0 退款挽回×2.2 内诉×3.0 外诉×5.0 · 转接×0.2</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
        </el-table>

        <!-- ★ #78 AI / 系统账户单列（非真人，不计入人效排名）
             设计说明: 此前 is_virtual 账户被直接过滤丢弃 → AI 工作量完全不可见。
             这里单列展示：既能看到 AI 规模，也不挤占真人名次。
             取舍: 不给名次（AI 不是人，排名无意义）；空则不渲染整块。
             工单: #78 / 架构: docs/arch/A-20260913-001-AI接待口径分层.md -->
        <div v-if="virtualStaffList.length" class="virtual-staff-block">
          <div class="virtual-staff-title">
            <span>AI / 系统账户（非真人，不计入人效排名）</span>
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：同上表（/analytics/today/staff）<br/><b>口径</b>：按「操作人 + 说明文本」判定为非真人；会话数 = 该账户名下接待记录条数（已按业务键去重，见 #77）<br/><b>时点</b>：与上方表格同一窗口<br/><b>「空」的含义</b>：本区块不显示 = 本窗口内无非真人账户产生接待<br/><br/><b>⚠️ 取舍</b>：不给名次（AI 不是人，排名无意义）；<b>不做</b>「AI 转人工」细分（平台字段不稳定，待 BMS/智能客服稳定后再精细化）">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <el-table :data="virtualStaffList" border size="small" max-height="200">
            <el-table-column label="账户" width="130">
              <template #default="{ row }"><span style="font-weight:500">{{ row.name }}</span></template>
            </el-table-column>
            <el-table-column label="来源" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain"
                        :style="{ color: sourceKindMeta(row.source_kind).color, borderColor: sourceKindMeta(row.source_kind).color }">
                  {{ sourceKindMeta(row.source_kind).label }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reception_count" label="会话数(次)" width="110" align="right" sortable>
              <template #default="{ row }">{{ formatNum(row.reception_count) }}</template>
            </el-table-column>
            <el-table-column prop="reception_device_count" label="接待数(个)" width="110" align="right" sortable>
              <template #default="{ row }">{{ formatNum(row.reception_device_count) }}</template>
            </el-table-column>
            <el-table-column label="占全站会话" width="110" align="right">
              <template #default="{ row }">
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
      </el-card>
    </section>

    <!-- 模块5: 退款挽回专项分析 -->
    <section v-if="hasRecovery" class="section-card mb-24">
      <SectionHeader title="退款挽回专项分析" :sub="`${periodName} · 成功率统计`">
        <template #extra>
          <el-tag size="small" effect="dark" type="danger" style="margin-right:6px">{{ recoveryData.date }}~{{ recoveryData.date_end || recoveryData.date }}</el-tag>
          <el-button size="small" type="primary" plain @click="$router.push('/dashboards/refund')">
            查看退款分析详情 →
          </el-button>
        </template>
      </SectionHeader>

      <!-- KPI卡片行
           ★ #80 改造（用户诉求 4）: 仿「今日值班看板」的成功/失败堆叠呈现，并压缩占位。
             · 3 张卡 → 1 行**内联条**（数值+堆叠比例条同排），不再各占一张大卡
             · 成功/失败用堆叠条呈现占比，一眼看出失败部分有多大
           ★ #80 修复的硬伤: 原 `el-col :xs="8"` **缺 :sm/:md** → 默认 span=24
             → 桌面端每卡占满整行，这才是"3 个数字太占空间"的真实原因。
           口径: 按 **SN** 计（总接待SN = 成功 + 失败）；成功率 = 成功 / 总。
                 与「退款分析」的 ABCD **笔数**口径不同，勿混读。
           取舍: 不按 reason 堆叠 —— `today.refundRecovery` 的 staff_details
                 **不返回 reason 维度**（只有 sn_details 有），强行前端拼会算错；
                 原因维度已在「退款分析」页承接。 -->
      <div class="rec-compact">
        <div class="rec-cell" style="background:#eff6ff">
          <span class="rec-label" style="color:#2563eb">总接待SN</span>
          <span class="rec-value" style="color:#2563eb">{{ formatNum(recoveryData.kpi.total_sn) }}</span>
          <span class="rec-unit">个</span>
        </div>
        <div class="rec-cell" :style="{ background: rateBg }">
          <span class="rec-label" :style="{ color: rateColor }">成功率</span>
          <span class="rec-value" :style="{ color: rateColor }">{{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}</span>
          <span class="rec-unit">%</span>
        </div>
        <div class="rec-cell rec-cell-grow">
          <div class="rec-stack-head">
            <span class="rec-label" style="color:#16a34a">挽回成功 {{ formatNum(recoveryData.kpi.success_sn) }}</span>
            <span class="rec-label" style="color:#dc2626">挽回失败 {{ formatNum(recoveryData.kpi.failed_sn) }}</span>
          </div>
          <!-- 成功/失败堆叠比例条（仿「今日值班看板」的 100% 堆叠细条） -->
          <div class="rec-stack" :title="recoveryStackTip">
            <span class="rec-stack-seg" :style="{ width: recoverySuccessPct + '%', background: '#22c55e' }" />
            <span class="rec-stack-seg" :style="{ width: (100 - recoverySuccessPct) + '%', background: '#ef4444' }" />
          </div>
          <div class="rec-stack-note">
            成功占 {{ recoverySuccessPct }}% · 共 {{ formatNum(recoveryData.kpi.total_sn) }} 个SN
          </div>
        </div>
      </div>

      <!-- 人员挽回详情列表
           ★ #80（用户诉求 4 后半）: 「为 0 的人员不显示」—— 但**不能一刀切**。
             · 隐藏 `total_count == 0`（本期确实没接到挽回任务，属噪声，该隐藏）
             · **保留 `success_count == 0 且有任务`** —— 这是"接了活但全失败"，
               正是最该被主管看见的一类，滤掉就是**掩盖问题**（会让人以为没人失败）。
             · 全失败行标红前置，让问题浮到顶上。 -->
      <el-card shadow="never">
        <template #header>
          <div class="flex-between">
            <span style="font-weight:600">
              各人员挽回详情
              <span class="text-muted" style="font-weight:400;font-size:12px">
                （{{ recoveryStaffList.length }} 人
                <template v-if="recoveryZeroHidden">，已隐藏 {{ recoveryZeroHidden }} 名本期无挽回任务者</template>
                <template v-if="recoveryAllFailCount">，其中 <span style="color:#dc2626;font-weight:600">{{ recoveryAllFailCount }} 人全失败</span></template>）
              </span>
            </span>
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：/analytics/today/refund-recovery 的 staff_details<br/><b>口径</b>：按 SN 计；成功率 = 成功SN / 接待SN。T+7 窗口判定<br/><b>时点</b>：跟随顶部时间条<br/><b>「空」的含义</b>：本期无挽回任务的人员<b>不列出</b>（已隐藏，计数见标题）<br/><br/><b>⚠️ 取舍</b>：<b>不做</b>「按退款原因堆叠」—— 本接口 staff_details <b>无 reason 维度</b>，强行前端拼会算错；原因维度请用「退款分析」页。<b>不隐藏</b>「有任务但全失败」的人员 —— 那正是最该被看见的。">
              <el-icon style="color:#b0b4bb;cursor:help;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </template>
        <el-table :data="recoveryStaffList" size="small" border stripe
          :default-sort="{ prop: 'total_count', order: 'descending' }">
          <el-table-column label="人员" width="140">
            <template #default="{ row }">
              <el-link type="primary" underline="never" @click="openPersonAudit(row.name)">{{ row.name }}</el-link>
              <!-- 全失败标记: 有任务但成功 0 → 最该被看见 -->
              <el-tag v-if="row.total_count > 0 && row.success_count === 0"
                      size="small" type="danger" effect="dark" style="margin-left:4px">全失败</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="group" label="分组" width="120">
            <template #default="{ row }">{{ row.group || '—' }}</template>
          </el-table-column>
          <el-table-column prop="total_count" label="接待数" width="90" align="center" sortable />
          <el-table-column prop="success_count" label="成功数" width="90" align="center">
            <template #default="{ row }">
              <span style="color:#16a34a;font-weight:600">{{ row.success_count }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="failed_count" label="失败数" width="90" align="center">
            <template #default="{ row }">
              <span style="color:#dc2626;font-weight:600">{{ row.failed_count }}</span>
            </template>
          </el-table-column>
          <el-table-column label="成功率" width="100" align="center" sortable>
            <template #default="{ row }">
              <el-progress
                :percentage="Number((row.success_rate || 0) * 100).toFixed(1)"
                :color="row.success_rate >= 0.5 ? '#16a34a' : row.success_rate >= 0.3 ? '#ca8a04' : '#dc2626'"
                :stroke-width="8"
                style="width: 80px; display:inline-block"
              />
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </section>

    <!-- 模块6: 投诉专项分析 -->
    <section class="section-card mb-24">
      <SectionHeader title="投诉专项分析" :sub="`${periodName} · 三源统计`">
        <template #extra>
          <el-button size="small" type="primary" plain @click="$router.push('/dashboards/complaint-analysis')">
            投诉分析 →
          </el-button>
        </template>
      </SectionHeader>

      <!-- 三源 KPI (点击即作为下方明细表的来源筛选; 主值=登记, 副行=处理, F-P1-2)
           ★ #80 修复: 原 `el-col :xs="8"` **缺 :sm/:md** → 桌面端每个 col span=24
             → 用户反馈「四个居然占了 4 个大行」。补断点后桌面端四卡同行。
           ★ #80 口径声明: 本四卡的判据是 `_complaint_source_type_case()`（含 channel 回退
             与 #58 软纠正），**与上方「核心指标·投诉数」不同源**（那边用
             `_ticket_category_case()`）。两者数字对不上是**已知问题**，
             见工单 #81（统一口径）。修正前**不**在本区加「括弧三源」，
             否则括弧之和与总数不符会误导使用者。 -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="12" :sm="6" :md="6">
          <div
            class="kpi-mini-card kpi-clickable"
            :class="{ 'kpi-active': complaintSourceFilter === '内部投诉' }"
            style="background:#eff6ff"
            @click="toggleComplaintSourceFilter('内部投诉')"
          >
            <div class="kpi-mini-label" style="color:#2563eb">内部投诉</div>
            <div class="kpi-mini-value" style="color:#2563eb">{{ formatNum(complaintSummaryData.internal?.registered ?? complaintSummaryData.internal?.total) }}</div>
            <div class="kpi-mini-sub"><span class="rh-label">登记</span> · 处理 <span style="color:#16a34a;font-weight:600">{{ formatNum(complaintSummaryData.internal?.handled) }}</span></div>
            <div class="kpi-mini-hint">点击筛选明细</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="6">
          <div
            class="kpi-mini-card kpi-clickable"
            :class="{ 'kpi-active': complaintSourceFilter === '商户号投诉' }"
            style="background:#f5f3ff"
            @click="toggleComplaintSourceFilter('商户号投诉')"
          >
            <div class="kpi-mini-label" style="color:#7c3aed">商户号投诉</div>
            <div class="kpi-mini-value" style="color:#7c3aed">{{ formatNum(complaintSummaryData.merchant?.registered ?? complaintSummaryData.merchant?.total) }}</div>
            <div class="kpi-mini-sub"><span class="rh-label">登记</span> · 处理 <span style="color:#16a34a;font-weight:600">{{ formatNum(complaintSummaryData.merchant?.handled) }}</span></div>
            <div class="kpi-mini-hint">点击筛选明细</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="6" :md="6">
          <div
            class="kpi-mini-card kpi-clickable"
            :class="{ 'kpi-active': complaintSourceFilter === '外部投诉' }"
            style="background:#fef2f2"
            @click="toggleComplaintSourceFilter('外部投诉')"
          >
            <div class="kpi-mini-label" style="color:#dc2626">外部投诉</div>
            <div class="kpi-mini-value" style="color:#dc2626">{{ formatNum(complaintSummaryData.external?.registered ?? complaintSummaryData.external?.total) }}</div>
            <div class="kpi-mini-sub"><span class="rh-label">登记</span> · 处理 <span style="color:#16a34a;font-weight:600">{{ formatNum(complaintSummaryData.external?.handled) }}</span></div>
            <div class="kpi-mini-hint">点击筛选明细</div>
            <div v-if="supplExternal.total" class="kpi-mini-suppl">315补充 {{ formatNum(supplExternal.total) }}条(已关联 {{ formatNum(supplExternal.matched) }})</div>
          </div>
        </el-col>
        <!-- ★★ 2026-09-11 (#53): (外)卡商 **独立统计**(不纳入上方投诉总量) ★★ -->
        <el-col :xs="12" :sm="6" :md="6">
          <div class="kpi-mini-card" style="background:#ecfdf5;border-left:3px solid #059669">
            <div class="kpi-mini-label" style="color:#059669">(外)卡商登记</div>
            <div class="kpi-mini-value" style="color:#059669">{{ formatNum(complaintSummaryData.card?.registered ?? complaintSummaryData.card?.total) }}</div>
            <div class="kpi-mini-sub"><span class="rh-label">独立统计</span> · 处理 <span style="color:#16a34a;font-weight:600">{{ formatNum(complaintSummaryData.card?.handled) }}</span></div>
            <div class="kpi-mini-hint">不纳入投诉总量</div>
            <div v-if="supplCard.total" class="kpi-mini-suppl">卡商补充 {{ formatNum(supplCard.total) }}条(已关联 {{ formatNum(supplCard.matched) }})</div>
          </div>
        </el-col>
      </el-row>

      <!-- ★ #80 三源合计提示（防误加卡商）:
           四卡并排后使用者会本能相加 → 必须显式说明「卡商不算在总量里」。
           同时给出与「核心指标·投诉数」**不一致**的解释 ——
           不让使用者以为是自己看错了（根因见工单 #81）。 -->
      <div v-if="complaintTotalHint" class="complaint-total-hint">
        <span class="cth-sum">三源合计（不含卡商）<b>{{ formatNum(complaintThreeTotal) }}</b></span>
        <span class="cth-sep">·</span>
        <el-tooltip effect="dark" placement="top" raw-content :content="complaintScopeTip">
          <span class="cth-warn">
            <el-icon style="vertical-align:-2px"><WarningFilled /></el-icon>
            与上方「核心指标·投诉数」口径不同，数字不等属已知问题
          </span>
        </el-tooltip>
      </div>

      <!-- workbuddy 推送补充数据源汇总(315/卡商, 按SN关联工单) -->
      <div v-if="supplSummaryText" class="suppl-strip">
        <span class="suppl-dot" />
        <span>{{ supplSummaryText }}</span>
      </div>

      <!-- 来源分布 + 处理人Top + 被投诉主体Top -->
      <el-row :gutter="12" class="mb-16">
        <el-col :xs="24" :md="8">
          <ChartCard title="投诉来源分布">
            <BaseChart :option="complaintSourcePie" height="240px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="8">
          <ChartCard title="处理人 Top10">
            <BaseChart :option="complaintHandlerOption" height="240px" />
          </ChartCard>
        </el-col>
        <el-col :xs="24" :md="8">
          <ChartCard title="被投诉主体 Top5">
            <BaseChart v-if="complaintSubjectTopOption" :option="complaintSubjectTopOption" height="240px" />
            <el-empty v-else description="暂无数据" :image-size="40" />
          </ChartCard>
        </el-col>
      </el-row>

      <!-- 明细表 -->
      <el-card shadow="never">
        <template #header>
          <div class="flex-between">
            <span style="font-weight:600">
              投诉明细（{{ filteredComplaintList.length }} 条<span v-if="complaintList.length !== filteredComplaintList.length"> / 共 {{ complaintList.length }} 条</span>）
            </span>
            <div class="flex gap-8" style="flex-wrap:wrap">
              <el-tag v-if="complaintSourceFilter" closable size="small" effect="dark" @close="complaintSourceFilter = ''">来源: {{ complaintSourceFilter }}</el-tag>
              <el-tag v-if="complaintCategoryFilter" closable size="small" effect="plain" @close="complaintCategoryFilter = ''">分类: {{ complaintCategoryFilter }}</el-tag>
              <el-tag v-if="complaintStatusFilter" closable size="small" effect="plain" type="warning" @close="complaintStatusFilter = ''">状态: {{ complaintStatusFilter }}</el-tag>
              <el-button v-if="complaintSourceFilter || complaintCategoryFilter || complaintStatusFilter" text size="small" @click="clearComplaintFilters">清除筛选</el-button>
            </div>
          </div>
        </template>
        <!-- ★ #80 硬伤 B 修复: 明细接口固定 `limit: 500`，超出会**静默截断**，
             使用者无法分辨「业务就这么多」还是「被截了」→ 违反看板卡片规范
             「空必须可解释」。此处显式提示，并指向可看全量的分析页。
             取舍: 不做前端翻页 —— 本页定位是"概览"，需要全量应去投诉分析页
                   （那里有完整筛选与分页），此处只负责**说明清楚**不假装完整。 -->
        <el-alert
          v-if="complaintTruncated"
          type="warning"
          :closable="false"
          show-icon
          class="mb-8"
          :title="`明细仅展示前 ${COMPLAINT_DETAIL_LIMIT} 条（本期命中已达上限，可能仍有更多）`"
        >
          <template #default>
            <!-- ⚠️ el-alert 的默认插槽是**纯文本**，不解析 Markdown ——
                 原先写成 `**不代表…**` 会原样显示星号（实测确认）。
                 强调改用 <span style="font-weight:600">。 -->
            本区块接口固定返回前 {{ COMPLAINT_DETAIL_LIMIT }} 条，
            <span style="font-weight:600">不代表本期投诉只有这么多</span>。
            需要完整清单请用
            <el-link type="primary" underline="never" @click="$router.push('/dashboards/complaint-analysis')">投诉分析</el-link>
            页（支持按来源/分类/状态筛选与分页）。
          </template>
        </el-alert>
        <el-table :data="filteredComplaintList" size="small" border stripe max-height="400">
          <el-table-column label="来源" width="100" :filters="complaintSourceFilters" :filter-method="(v,row)=>row.source===v" filter-placement="bottom-end">
            <template #default="{ row }">
              <el-tag :type="complaintSourceType(row.source)" size="small" effect="plain">{{ row.source }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="130" sortable :sort-method="(a,b)=>sortTimeStr(a.time,b.time)">
            <template #default="{ row }">{{ formatShortTime(row.time) }}</template>
          </el-table-column>
          <el-table-column label="设备SN" width="140" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" underline="never" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn || '—' }}</el-link>
            </template>
          </el-table-column>
          <el-table-column label="被投诉主体" width="150" show-overflow-tooltip>
            <template #default="{ row }">
              <el-tag v-if="row.entity" size="small" effect="plain" type="warning">{{ row.entity }}</el-tag>
              <span v-else class="text-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="分类" width="150" show-overflow-tooltip :filters="complaintCategoryFilters" :filter-method="(v,row)=>row.category===v" filter-placement="bottom-end">
            <template #default="{ row }">
              <!-- ★ #65: 软纠正标注 —— 工单原登记为「工信部投诉」,
                   但 SN 在 315 属监所/市监局类 → 统计时已按「外部投诉」归集。
                   不标出会让人误以为分类错了。
                   ⚠️ 仅展示层标注, BMS 原数据未改。 -->
              <el-tooltip
                v-if="row.soft_corrected"
                content="BMS 原登记为「工信部投诉」。因该 SN 在 315 文档属监所/市监局类，已按「外部投诉」统计（原数据未改）。BMS 增加「监所/市监局」服务项后可取消此纠正。"
                placement="top"
              >
                <span>
                  <el-tag size="small" type="danger" effect="plain">{{ row.category }}</el-tag>
                  <el-tag size="small" type="success" effect="plain" style="margin-left:4px">已纠正</el-tag>
                </span>
              </el-tooltip>
              <span v-else>{{ row.category }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="problem" label="投诉内容" min-width="180" show-overflow-tooltip />
          <el-table-column label="处理/跟进" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" underline="never" @click="openPersonAudit(row.handler)">{{ row.handler || '—' }}</el-link>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="结果/状态" width="110" show-overflow-tooltip :filters="complaintStatusFilters" :filter-method="(v,row)=>row.status===v" filter-placement="bottom-end" />
        </el-table>
      </el-card>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { today } from '@/api/analytics'
import { listGroups } from '@/api/group'
import { complaintSummary, complaintDetail, complaintSubjectAnalysis } from '@/api/complaint'
import BaseChart from '@/components/BaseChart.vue'
import { useAudit } from '@/composables/useAudit'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import weekOfYear from 'dayjs/plugin/weekOfYear'
// ★ #78 接待来源分层：配色/堆叠构造与「今日值班看板」共用同一模块
//   （规范 §五 禁止重复展示 —— 配色各页自写会漂移，使用者产生歧义）
import { buildStack, sourceKindMeta, RECEPTION_BREAKDOWN_TIP } from '@/utils/receptionSource'
// 周区间计算（周一起始）—— 共享单一来源，修复「周日当天本周算成下周」的存量 bug
import { getWeekRange, getWeekRangeStr, weekLabel } from '@/utils/weekRange'
import { QuestionFilled, WarningFilled } from '@element-plus/icons-vue'
dayjs.extend(isoWeek)
dayjs.extend(quarterOfYear)
dayjs.extend(weekOfYear)

const loading = ref(false)
const isMobile = ref(false)
let _mq = null

// ========== 时间选择 ==========
const grain = ref('week')
const weekOffset = ref(0)
const monthOffset = ref(0)
const quarterOffset = ref(0)
const yearOffset = ref(0)
const customDateRange = ref(null)
const trendGrain = ref('day')
const quickGrainOpt = ref('all')
const grainQuickOptions = [{ label: '全部', value: 'all' }]

// ========== 人员筛选 ==========
const filterDepartment = ref('')
const filterGroupId = ref(null)
const groupsList = ref([])

const staffFilter = reactive({
  aggregateBy: 'person',
})

// 人均效率: 默认仅显示企微人员, 勾选后显示所有人
const showAllStaff = ref(false)

// 审计弹窗(点击人员/设备唤起)
const { openPersonAudit, openDeviceAudit } = useAudit()

// ========== 图表配置 ==========
const receptionPerspective = ref('count')

// 趋势图: 默认只显示接待,可勾选投诉/退款
const trendSelectedGroups = ref(['reception'])

// 工作效率表: 各专项接待明细列 (按类型分开SN/接待数)
const SPECIALTY_COLS = [
  { key: '咨询接待', short: '咨询', color: '#3b82f6' },
  { key: '套餐专项', short: '套餐', color: '#f97316' },
  { key: '退款处理', short: '处理', color: '#ef4444' },
  { key: '退款挽回', short: '挽回', color: '#06b6d4' },
  { key: '内部投诉', short: '内诉', color: '#7c3aed' },
  { key: '外部投诉', short: '外诉', color: '#dc2626' },
]
function getSpecialtyPeople(row, catKey) {
  const bd = row.breakdown || {}
  const info = bd[catKey]
  return info ? Number(info.people || 0) : 0
}

// ========== 数据存储 ==========
const kpiData = ref({})
const hourlyData = ref({})
const receptionData = ref({})
const refundData = ref({})
const staffData = ref({})
const recoveryData = ref({})
const hasRecovery = computed(() => recoveryData.value && recoveryData.value.kpi)

// 投诉专项数据
const complaintSummaryData = ref({})
const complaintList = ref([])
// 被投诉主体维度数据
const complaintSubjectData = ref({})

// workbuddy 推送补充数据源(315/卡商, 按SN关联工单)
const supplExternal = computed(() => complaintSummaryData.value?.supplementary?.external || { total: 0, matched: 0 })
// #47 (2026-09-10): 卡商补充数据源(单列, 原先并入外部)
const supplCard = computed(() => complaintSummaryData.value?.supplementary?.card || { total: 0, matched: 0 })
const supplSummaryText = computed(() => {
  const s = complaintSummaryData.value?.supplementary || {}
  const ext = s.external || { total: 0, matched: 0 }
  const card = s.card || { total: 0, matched: 0 }
  if (!ext.total && !card.total) return ''
  return `补充数据源(按SN关联工单): 315外部投诉 ${formatNum(ext.total)} 条(已关联 ${formatNum(ext.matched)}) · 卡商群投诉 ${formatNum(card.total)} 条(已关联 ${formatNum(card.matched)})`
})

// ★ #80 三源合计（当前窗口内，来自 reg_handled —— 与四卡同源，可安全相加）
const complaintThreeTotal = computed(() => {
  const r = complaintSummaryData.value?.reg_handled || {}
  const t = r.total
  if (t && typeof t === 'object') return Number(t.registered ?? t.total ?? 0)
  return ['内部投诉', '商户号投诉', '外部投诉']
    .reduce((s, k) => s + Number(r[k]?.registered ?? r[k]?.total ?? 0), 0)
})

/** 只有拿到三源数据才显示合计提示（避免显示 0 误导） */
const complaintTotalHint = computed(() => complaintThreeTotal.value > 0)

/** ★ #80 口径差异说明（页面 ⓘ）—— 这是使用者最容易困惑的地方，必须给解释 */
const complaintScopeTip = [
  '<b>为什么两个投诉数不一样？</b>（已知问题，见工单 #81）',
  '<b>核心指标·投诉数</b>：判据 = 工单服务分类为「投诉」+ 315 补充 + <b>卡商补充</b>',
  '　→ <b>含卡商</b>，且<b>不含</b> #58 软纠正',
  '<b>本区四卡</b>：判据 = 投诉来源分桶（含渠道回退 + #58 软纠正）',
  '　→ <b>不含卡商</b>（卡商按 #53 独立统计）',
  '<b>结论</b>：两处判据不同，<b>数字必然不等</b>，不是显示错误。',
  '修正方向（#81）：统一判据出口 + 核心指标剥离卡商 + 修 #65 工单黑洞。',
  '在此之前，<b>本页所有"合计"一律以四卡为准</b>（判据更完备）。',
].join('<br/>')

/** ★ #80: 趋势图数据说明（页面层 ⓘ，规范 §二 双强制之①） */
const TREND_TIP = [
  '<b>来源</b>：节拍 = /analytics/today/hourly-trend；投诉三源 = /complaints/summary 的 trend',
  '<b>口径</b>：',
  '· 接待 = 会话数(cs_records 计数) 与 接待数(去重设备SN)，两柱并列',
  '· 退款 = 四路经营笔数 A线上直退 + B钱包取现 + C闪猫退款 + D手动退款，**堆叠柱**',
  '　　（按创建时间；四路之和 = 上方「退款笔数」卡）',
  '· 退款金额 = 同样四路，**半透明堆叠柱**（右轴），仅作量级参照',
  '· 投诉 = 内部/商户号/外部 **三源堆叠柱**（含渠道回退 + #58 软纠正）',
  '<b>时点</b>：跟随顶部时间条；本页缓存 24h（复盘场景，非实时）',
  '<b>「空」的含义</b>：某段不显示 = 该时段该来源无记录（**不是**数据未加载）；',
  '　　整图全空 = 本窗口无数据，或所选粒度超出数据覆盖范围',
  '<b>下钻</b>：⚠️ <b>本图不做点击下钻</b> —— 明细接口按【日】筛选，无小时级能力，',
  '　　强行下钻会落到"那一天的全部明细"，与所点时段不符（规范 §3.4 声明）。',
  '　　请用下方各专项区块下钻（那里已带窗口）。',
  '<br/><b>⚠️ 取舍（不做什么及原因）</b>',
  '① <b>不堆叠金额</b> —— ABCD 金额混入"退入钱包"等<b>非现金退出</b>，相加无业务意义；',
  '　　（真正的对账基准是 E＝实际现金流，已在「退款金额」卡单列）',
  '② <b>投诉三源只在按日粒度拆</b> —— 补充表 trend 仅到日；周/月聚合会拆错，',
  '　　故自动退化为单一「投诉量」柱（宁可少拆，不可拆错）',
  '③ <b>图例不写"合计"</b> —— 投诉三源与「核心指标·投诉数」不同源（见 #81），',
  '　　写合计会让人误以为两处应相等',
].join('<br/>')

// 投诉明细表 筛选/排序状态 (KPI 卡片点击 = 来源筛选)
const complaintSourceFilter = ref('')
const complaintCategoryFilter = ref('')
const complaintStatusFilter = ref('')

/** ★ #80: 投诉明细接口固定取前 500 条（见 loadAll 的 complaintDetail 调用）。
 *  必须与调用处保持一致，否则提示文案会与实际截断点不符。 */
const COMPLAINT_DETAIL_LIMIT = 500

/** ★ #80 硬伤 B: 是否触达截断上限 → 用于显示"明细可能不全"警告。
 *  判据: 返回条数 >= 上限（后端是 LIMIT，达到上限即可能还有更多）。 */
const complaintTruncated = computed(() => complaintList.value.length >= COMPLAINT_DETAIL_LIMIT)

function toggleComplaintSourceFilter(src) {
  complaintSourceFilter.value = complaintSourceFilter.value === src ? '' : src
}
function clearComplaintFilters() {
  complaintSourceFilter.value = ''
  complaintCategoryFilter.value = ''
  complaintStatusFilter.value = ''
}
// 时间字符串排序(ISO), 支持空值排在后
function sortTimeStr(a, b) {
  const va = a || ''
  const vb = b || ''
  if (va === vb) return 0
  return va < vb ? -1 : 1
}

// 来源/分类/状态 可选项(el-table :filters 需要 {text,value} 形式)
const complaintSourceFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.source).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})
const complaintCategoryFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.category).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})
const complaintStatusFilters = computed(() => {
  const set = new Set(complaintList.value.map(r => r.status).filter(Boolean))
  return [...set].map(v => ({ text: v, value: v }))
})

// 明细表最终数据 = 来源(KPI点击/列内筛选二者合并) + 分类 + 状态 三级筛选
const filteredComplaintList = computed(() => {
  let list = complaintList.value || []
  if (complaintSourceFilter.value) {
    list = list.filter(r => r.source === complaintSourceFilter.value)
  }
  if (complaintCategoryFilter.value) {
    list = list.filter(r => r.category === complaintCategoryFilter.value)
  }
  if (complaintStatusFilter.value) {
    list = list.filter(r => r.status === complaintStatusFilter.value)
  }
  return list
})

// ========== 选项 ==========
// ⚠️ 2026-09-13 修复存量 bug: 原用 `startOf('week').add(1,'day')`,
//    而 dayjs 的周起始是**周日** → 周日当天「本周」会被算成下周（09-14~09-20，未来）
//    → 指标全为 0。改用共享 getWeekRange（周一起始，单一来源）。
const weekOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const { start, end } = getWeekRange(undefined, i)
    opts.push({ label: weekLabel(i), value: i, range: `${start.format('MM-DD')}~${end.format('MM-DD')}` })
  }
  return opts
})
const monthOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 12; i++) {
    const d = dayjs().subtract(i, 'month')
    const label = i === 0 ? '本月' : i === 1 ? '上月' : d.format('YYYY年MM月')
    opts.push({ label, value: i })
  }
  return opts
})
const quarterOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 8; i++) {
    const d = dayjs().subtract(i, 'quarter')
    const label = i === 0 ? '本季度' : i === 1 ? '上季度' : `${d.year()}年Q${d.quarter()}`
    opts.push({ label, value: i })
  }
  return opts
})
const yearOptions = computed(() => {
  const opts = []
  for (let i = 0; i < 3; i++) {
    const d = dayjs().subtract(i, 'year')
    const label = i === 0 ? '今年' : i === 1 ? '去年' : `${d.year()}年`
    opts.push({ label, value: i })
  }
  return opts
})

// ========== 标签 ==========
const grainLabel = computed(() => {
  const gMap = { week: '周视图', month: '月视图', quarter: '季度视图', year: '年视图' }
  const tMap = { day: '·按日', week: '·按周', month: '·按月' }
  return (gMap[grain.value] || '周视图') + (tMap[trendGrain.value] || '')
})
const grainTagType = computed(() => ({ week: 'primary', month: 'warning', quarter: 'danger', year: 'info' }[grain.value] || 'info'))

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
  }
  return ''
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
  }
  return ''
})

const displayDate = computed(() => periodName.value)

const rhythmSub = computed(() => {
  if (grain.value === 'week') return '按日聚合'
  if (grain.value === 'month') return '按日聚合'
  return `按${trendGrain.value === 'day' ? '日' : trendGrain.value === 'week' ? '周' : '月'}聚合`
})

// ========== 日期范围计算 ==========
function getDateRange() {
  if (customDateRange.value) {
    return { start: customDateRange.value[0], end: customDateRange.value[1] }
  }
  const now = dayjs()
  let start, end
  switch (grain.value) {
    case 'week': {
      // ⚠️ 2026-09-13 修复: 原 `now.subtract(offset,'week').startOf('week').add(1,'day')`
      //   在周日当天会把「本周」算成下一周（未来）→ 全部指标为 0。
      //   改用共享 getWeekRange（周一起始）+ clamp 到今天，避免拉取未来日期。
      const r = getWeekRangeStr(undefined, weekOffset.value, true)
      start = r.start
      end = r.end
      break
    }
    case 'month': {
      const offset = monthOffset.value
      const monthDate = now.subtract(offset, 'month')
      start = monthDate.startOf('month').format('YYYY-MM-DD')
      end = monthDate.endOf('month').format('YYYY-MM-DD')
      break
    }
    case 'quarter': {
      const offset = quarterOffset.value
      const quarterDate = now.subtract(offset, 'quarter')
      start = quarterDate.startOf('quarter').format('YYYY-MM-DD')
      end = quarterDate.endOf('quarter').format('YYYY-MM-DD')
      break
    }
    case 'year': {
      const offset = yearOffset.value
      const yearDate = now.subtract(offset, 'year')
      start = yearDate.startOf('year').format('YYYY-MM-DD')
      end = yearDate.endOf('year').format('YYYY-MM-DD')
      break
    }
  }
  return { start, end }
}

// ========== KPI卡 ==========
function _pctDelta(cur, prev) {
  if (prev == null || Number(prev) === 0) return null
  return Math.round(((Number(cur) - Number(prev)) / Number(prev)) * 1000) / 10
}
const kpiCards = computed(() => {
  const d = kpiData.value || {}
  const reception = d.reception || {}
  const refundCnt = d.refund_count || {}
  const refundAmt = d.refund_amount || {}
  const complaint = d.complaint || {}
  const recoveryRate = recoveryData.value?.kpi?.success_rate
  // ★ #80: 四路经营退款拆分（后端 `cash_routes_today` 已返回 route_a~d 的 count/amount）。
  //   用途: 「退款笔数」卡下方括弧显示 A/B/C/D 各自笔数，
  //        「退款金额」卡下方显示四路金额 —— 让「四路合计」不再是黑箱。
  //   ⚠️ 金额是元（后端已 rounded 2 位），前端统一折算为万元显示，避免长数字撑破卡片。
  const routes = d.cash_routes_today || {}
  const routeOf = (k) => routes[k] || { count: 0, amount: 0 }
  const _wan = (v) => `${(Number(v || 0) / 10000).toFixed(2)}万`
  const routeChips = REFUND_ROUTES.map((r) => ({
    label: r.short,
    value: routeOf(`route_${r.key}`).count,
    color: r.color,
  }))
  const routeAmtRows = REFUND_ROUTES.map((r) => ({
    label: r.short,
    text: _wan(routeOf(`route_${r.key}`).amount),
    color: r.color,
  }))
  // 实际现金流 E —— 与 ABCD 经营估算口径不同（E 才是真实现金退出）
  const cashE = d.actual_cashflow || { value: 0 }
  return [
    // ★ #78: stack 由后端 reception.breakdown 构造（null 时不渲染）
    { key: 'reception', label: '接待数', value: reception.device_count, unit: '个', icon: 'Service', bg: '#eff6ff', color: '#2563eb', yoy_key: 'reception', yoy_pct: _pctDelta(reception.device_count, reception.device_count_yoy), yoy_value: reception.device_count_yoy, device_count: null, stack: buildStack(reception.breakdown, 'devices'), tip: '接待设备去重数' },
    { key: 'session', label: '会话数', value: reception.value, unit: '次', icon: 'ChatDotRound', bg: '#f0fdf4', color: '#16a34a', yoy_key: 'session', yoy_pct: reception.yoy_pct, yoy_value: reception.yoy_value, stack: buildStack(reception.breakdown, 'sessions'), tip: '含客服接待+系统处理' },
    // ★ #80: 退款笔数 —— 括弧展开四路笔数（A线上直退/B钱包取现/C闪猫/D手动）
    //   四路之和 == 主值（后端 operational.count 就是 A+B+C+D）。
    {
      key: 'refund_count',
      label: '退款笔数',
      value: refundCnt.value,
      unit: '笔',
      icon: 'Warning',
      bg: '#fef2f2',
      color: '#dc2626',
      yoy_key: 'refund_count',
      yoy_pct: refundCnt.yoy_pct,
      yoy_value: refundCnt.yoy_value,
      chips: routeChips,
      tip: '四路经营合计 A线上直退+B钱包取现+C闪猫退款+D手动退款（按创建时间）。下方颜色与趋势图四路堆叠柱一致',
    },
    // ★ #80: 退款金额 —— 副行给四路金额；并补一条「实际现金流E」对照
    //   ⚠️ 铁律: ABCD 是**经营记账**（含退入钱包这类非现金退出），E 才是**真实现金流出**。
    //     两者不相等是正常的，不是 bug（见 docs/standards 与 #20）。
    {
      key: 'refund_amount',
      label: '退款金额',
      value: refundAmt.value,
      money: true,
      icon: 'Money',
      bg: '#fff7ed',
      color: '#ea580c',
      yoy_key: 'refund_amount',
      yoy_pct: refundAmt.yoy_pct,
      yoy_value: refundAmt.yoy_value,
      subRows: [
        ...routeAmtRows,
        { label: '实际现金流E', text: _wan(cashE.value ?? cashE), color: '#0f766e' },
      ],
      tip: '退款金额 = 四路经营合计 A+B+C+D（登记侧所有退款出款，含退入钱包等非现金退出）；「实际现金流E」= 退款记录报表退款成功合计（真实现金退出）。两者不等属正常',
    },
    { key: 'complaint', label: '投诉数', value: complaint.value, unit: '条', icon: 'Bell', bg: '#faf5ff', color: '#7c3aed', yoy_key: 'complaint', yoy_pct: complaint.yoy_pct, yoy_value: complaint.yoy_value, tip: '投诉合计(内部/商户号/外部 + 315/卡商推送)' },
    { key: 'recovery', label: '挽回率', value: Number(recoveryRate || 0), unit: '%', icon: 'SuccessFilled', bg: '#ecfdf5', color: '#059669', yoy_key: 'recovery', yoy_pct: null, yoy_value: null, tip: '退款挽回成功率' },
  ]
})

// ========== 图表配置 ==========
// 四路经营退款路线配色 (A 线上直退 / B 钱包取现 / C 闪猫退款 / D 手动退款)
const REFUND_ROUTES = [
  { key: 'a', label: 'A线上直退', short: 'A直退', color: '#ef4444' },
  { key: 'b', label: 'B钱包取现', short: 'B取现', color: '#f97316' },
  { key: 'c', label: 'C闪猫退款', short: 'C闪猫', color: '#a855f7' },
  { key: 'd', label: 'D手动退款', short: 'D手动', color: '#94a3b8' },
]
function _rrLabel(r) { return isMobile.value ? (r.short || r.label) : r.label }

// ★ #80 投诉三源趋势配色（与投诉专项四卡、饼图共用同一套色，规范 §五 禁止漂移）
const COMPLAINT_TREND_SOURCES = [
  { key: 'internal', name: '内部投诉', short: '内部', color: '#2563eb' },
  { key: 'merchant', name: '商户号投诉', short: '商户号', color: '#7c3aed' },
  { key: 'external', name: '外部投诉', short: '外部', color: '#dc2626' },
]

/**
 * ★ #80 把「按日」的三源投诉趋势对齐到趋势图槽位（用户诉求 3）。
 *
 * 为什么需要对齐（而不是直接用接口顺序）：
 *   退款走 `today/hourly-trend`（槽位可能是小时/日/周/月）；
 *   而投诉三源只有**按日**趋势（`complaints/summary.*.trend`，源于补充表逐日聚合）。
 *   → 只有槽位标签本身就是「MM-DD」时才能一一对齐；否则返回 null，
 *     由调用方退化为单一「投诉量」柱。
 *   ★ 宁可少拆，不可拆错 —— 错配会把 A 天的投诉画到 B 天，比不拆更糟。
 *
 * @returns {null | Object} null=不可拆（非按日粒度）; 否则 {internal:[],merchant:[],external:[]}
 */
function buildComplaintBySource(labels) {
  const src = complaintSummaryData.value || {}
  const trends = COMPLAINT_TREND_SOURCES.map((s) => src[s.key]?.trend || [])
  if (!trends.some((t) => Array.isArray(t) && t.length)) return null
  if (!labels.length || !labels.every((l) => /^\d{2}-\d{2}$/.test(String(l)))) return null
  const out = {}
  COMPLAINT_TREND_SOURCES.forEach((s, i) => {
    const map = {}
    ;(trends[i] || []).forEach((row) => {
      if (row && row.day) map[String(row.day).slice(5, 10)] = Number(row.count || 0)
    })
    out[s.key] = labels.map((l) => map[l] ?? 0)
  })
  return out
}

/**
 * 组织趋势图 series。
 * ★ #80 改造（用户诉求 3）：
 *   ① 退款 —— A/B/C/D **四路堆叠柱**（配色与「退款笔数」卡 chips 一致）
 *   ② 投诉 —— **内部/商户号/外部 三源堆叠柱**（按日粒度时）
 * 取舍：
 *   ① **只堆叠笔数，金额改为半透明堆叠柱** —— 原来金额是四路面积线，
 *      与笔数柱叠在同一张图上互相遮挡；且 ABCD 金额混入非现金退出，
 *      相加无对账意义（真正的对账基准是 E，已在「退款金额」卡给出）。
 *   ② 三源堆叠仅在按日粒度可用，周/月聚合自动退化为单一「投诉量」柱，不强行拆。
 *   ③ 不在图例写「合计」字样 —— 投诉三源与「核心指标·投诉数」不同源（见 #81），
 *      写合计会让人误以为两处应相等。
 */
function buildTrendSeries(data, complaintBySource) {
  const groups = trendSelectedGroups.value
  const series = []
  if (groups.includes('reception')) {
    series.push({ name: isMobile.value ? '会话' : '会话数', type: 'bar', data: data.reception || [], itemStyle: { color: '#3b82f6' }, barMaxWidth: isMobile.value ? 14 : 20 })
    series.push({ name: isMobile.value ? '接待' : '接待数', type: 'bar', data: data.reception_devices || [], itemStyle: { color: '#10b981' }, barMaxWidth: isMobile.value ? 14 : 20 })
  }
  if (groups.includes('complaint')) {
    if (complaintBySource) {
      COMPLAINT_TREND_SOURCES.forEach((s) => {
        series.push({
          name: isMobile.value ? s.short : s.name,
          type: 'bar',
          stack: 'complaint_src',
          data: complaintBySource[s.key] || [],
          itemStyle: { color: s.color },
          barMaxWidth: isMobile.value ? 14 : 20,
        })
      })
    } else {
      series.push({ name: isMobile.value ? '投诉' : '投诉量', type: 'bar', data: data.complaint || [], itemStyle: { color: '#f59e0b' }, barMaxWidth: isMobile.value ? 14 : 20 })
    }
  }
  if (groups.includes('refund')) {
    REFUND_ROUTES.forEach(r => {
      series.push({ name: _rrLabel(r), type: 'bar', stack: 'refund_cnt', data: data[`refund_count_${r.key}`] || [], itemStyle: { color: r.color }, barMaxWidth: isMobile.value ? 14 : 20 })
    })
    REFUND_ROUTES.forEach(r => {
      series.push({ name: `${_rrLabel(r)}·金额`, type: 'bar', stack: 'refund_amt', yAxisIndex: 1, data: data[`refund_amount_${r.key}`] || [], itemStyle: { color: r.color, opacity: 0.35 }, barMaxWidth: isMobile.value ? 10 : 14 })
    })
  }
  return series
}

function buildTrendLegend(complaintBySource) {
  const groups = trendSelectedGroups.value
  const items = []
  if (groups.includes('reception')) {
    items.push(isMobile.value ? '会话' : '会话数')
    items.push(isMobile.value ? '接待' : '接待数')
  }
  if (groups.includes('complaint')) {
    if (complaintBySource) {
      COMPLAINT_TREND_SOURCES.forEach((s) => items.push(isMobile.value ? s.short : s.name))
    } else {
      items.push(isMobile.value ? '投诉' : '投诉量')
    }
  }
  if (groups.includes('refund')) {
    REFUND_ROUTES.forEach(r => items.push(_rrLabel(r)))
  }
  return items
}

const hourlyOption = computed(() => {
  const data = hourlyData.value || {}
  const labels = data?.slot_labels || data?.hour_labels || data?.hours || []
  const mob = isMobile.value
  const showRefund = trendSelectedGroups.value.includes('refund')
  // ★ #80: 投诉三源堆叠（仅按日粒度可用，否则为 null → 退化单一柱）
  const complaintBySource = trendSelectedGroups.value.includes('complaint')
    ? buildComplaintBySource(labels)
    : null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: {
      data: buildTrendLegend(complaintBySource),
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
      : { left: 50, right: showRefund ? 60 : 20, bottom: 30, top: 40 },
    xAxis: [{
      type: 'category',
      data: labels,
      axisPointer: { type: 'shadow' },
      axisLabel: { fontSize: mob ? 9 : 11, interval: 0, rotate: mob ? 30 : 0 },
    }],
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
        axisLine: { show: !mob, lineStyle: { color: '#ea580c' } },
        axisLabel: { fontSize: mob ? 9 : 11 },
        splitLine: { show: false },
      },
    ],
    series: buildTrendSeries(data, complaintBySource),
  }
})

const receptionPieOption = computed(() => {
  const data = receptionData.value
  const items = receptionPerspective.value === 'count'
    ? (data?.categories || [])
    : (data?.categories_device || [])
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', right: 10, top: 'center' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      label: { show: false },
      data: items.map(d => ({ name: d.name, value: d.value || 0 })),
    }],
  }
})

const refundReasonOption = computed(() => {
  const data = refundData.value?.reasons || []
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 120, right: 60, bottom: 30, top: 10 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: data.map(d => d.name).reverse() },
    series: [
      { name: '订单数', type: 'bar', data: data.map(d => d.count).reverse(), itemStyle: { color: '#3b82f6' }, barMaxWidth: 16 },
      { name: '金额(元)', type: 'bar', data: data.map(d => d.amount).reverse(), itemStyle: { color: '#ea580c' }, barMaxWidth: 16 },
    ],
  }
})

// ========== 人均效率表 ==========
const departmentOptions = computed(() => {
  const depts = new Set()
  ;(staffData.value?.data || []).forEach(r => {
    if (r.department && r.department !== '未分配') depts.add(r.department)
  })
  return Array.from(depts).sort()
})

// 判定"企微人员":部门=企微 / 姓名前缀"企微-" / qw_user_id 已绑定
function isQwStaff(s) {
  if (!s) return false
  if ((s.qw_user_id || '').trim()) return true
  if ((s.department || s.dept || '') === '企微') return true
  const nm = (s.name || s.op || s.display_name || '').trim()
  return /^企微[-_·\s]?/.test(nm)
}

const staffRows = computed(() => {
  let rows = staffData.value?.data || []
  // ★ #78: AI/系统账户不进真人表（它们在下方单独一张小表展示）
  rows = rows.filter(r => !r.is_virtual)
  // 按人视图下默认仅显示企微人员(宽松判定), 勾选后显示所有人
  if (staffFilter.aggregateBy === 'person' && !showAllStaff.value) {
    rows = rows.filter(isQwStaff)
  }
  if (filterDepartment.value) {
    rows = rows.filter(r => r.department === filterDepartment.value)
  }
  if (filterGroupId.value) {
    rows = rows.filter(r => r.group === filterGroupId.value)
  }
  return rows
})

// ★ #78 AI / 系统账户单列（用户要求：「区分出来，单列也可以」）
//   设计说明: 此前 `is_virtual` 账户被各页直接过滤丢弃，AI 工作量完全不可见。
//   这里单列展示 —— 既能看到 AI 规模，也不挤占真人排名。
//   取舍: 不给名次（AI 不是人，排名无意义）；空则不渲染整块。
const virtualStaffList = computed(() => {
  const rows = staffData.value?.data || []
  return rows
    .filter(r => r.is_virtual)
    .filter(r => (Number(r.reception_count) || 0) + (Number(r.reception_device_count) || 0) > 0)
    .sort((a, b) => (Number(b.reception_count) || 0) - (Number(a.reception_count) || 0))
})

/** 该 AI/系统账户名下会话数占全站会话数的百分比（可控指标：AI 接管率） */
function aiSharePct(row) {
  const total = Number(kpiData.value?.reception?.value || 0)
  if (!total) return '0.0'
  return ((Number(row.reception_count || 0) * 100) / total).toFixed(1)
}

// 部门列显示策略:移动端 + 按人视图 + 仅企微时隐藏(表头已筛选); 其余保留
const showDeptColumn = computed(
  () => !(isMobile.value && staffFilter.aggregateBy === 'person' && !showAllStaff.value)
)

// ========== 退款挽回 ==========
// ★ #80（用户诉求 4）：原样返回 staff_details 所有人 → 「为 0 的不显示」。
//   ⚠️ 关键区分（不能一刀切"为0就隐藏"）：
//     · total_count == 0        → 本期**确实没接到挽回任务**，属噪声 → 隐藏
//     · total_count > 0 但成功 = 0 → **接了活但全失败** → 必须保留！
//       这正是主管最该看见的一类；滤掉就是掩盖问题。
//   排序：全失败者前置（用 allFail 作为一级排序键），让问题浮到顶上。
const recoveryStaffList = computed(() => {
  const details = recoveryData.value?.staff_details || recoveryData.value?.staff_detail || []
  return details
    .map(d => ({
      name: d.name,
      group: d.group,
      total_count: Number(d.total_sn || 0),
      success_count: Number(d.success_sn || 0),
      failed_count: Number(d.failed_sn || 0),
      success_rate: d.total_sn > 0 ? d.success_sn / d.total_sn : 0,
    }))
    // 隐藏「本期无挽回任务」（total=0）—— 这些是噪声，不是问题
    .filter((d) => d.total_count > 0)
    // 全失败（有任务、成功=0）置顶：主管一进来就看到谁需要帮扶
    .sort((a, b) => {
      const af = a.success_count === 0 ? 1 : 0
      const bf = b.success_count === 0 ? 1 : 0
      if (af !== bf) return bf - af
      return b.total_count - a.total_count
    })
})

/** ★ #80: 本期无挽回任务（total=0）而被隐藏的人数 —— 必须告知，否则使用者
 *  会以为"人少了"是系统漏数据。规范 §二「空必须可解释」。 */
const recoveryZeroHidden = computed(() => {
  const details = recoveryData.value?.staff_details || recoveryData.value?.staff_detail || []
  return details.filter((d) => Number(d.total_sn || 0) <= 0).length
})

/** ★ #80: 全失败人数（有任务但成功 0）—— 标红提示 */
const recoveryAllFailCount = computed(
  () => recoveryStaffList.value.filter((d) => d.success_count === 0).length,
)

/** ★ #80: 成功占比（堆叠条用）。total=0 时给 0，避免除零。 */
const recoverySuccessPct = computed(() => {
  const k = recoveryData.value?.kpi || {}
  const total = Number(k.total_sn || 0)
  if (!total) return 0
  return Math.round((Number(k.success_sn || 0) * 100) / total)
})

/** ★ #80: 堆叠条悬浮说明（各段量级，规范 §二：视觉元素须说清含义） */
const recoveryStackTip = computed(() => {
  const k = recoveryData.value?.kpi || {}
  return `挽回成功 ${formatNum(k.success_sn)} 个 (${recoverySuccessPct.value}%)\n`
    + `挽回失败 ${formatNum(k.failed_sn)} 个 (${100 - recoverySuccessPct.value}%)\n`
    + `合计 ${formatNum(k.total_sn)} 个SN（按 SN 计，T+7 窗口）`
})

/** ★ #80: 成功率阈值配色（达标≥38% / 预警30-38% / 告警<30%）—— 与今日看板同阈值 */
const recoveryRate = computed(() => Number(recoveryData.value?.kpi?.success_rate || 0))
const rateColor = computed(() => (recoveryRate.value >= 38 ? '#16a34a' : recoveryRate.value >= 30 ? '#ca8a04' : '#dc2626'))
const rateBg = computed(() => (recoveryRate.value >= 38 ? '#f0fdf4' : recoveryRate.value >= 30 ? '#fefce8' : '#fef2f2'))

// ========== 投诉专项 ==========
const complaintSourcePie = computed(() => {
  const s = complaintSummaryData.value || {}
  // ★★ 2026-09-11 (#53): (外)卡商 **移出饼图** ★★
  //   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
  //   卡商改为独立区块展示(见模板 card 区块)。
  const data = [
    { name: '内部投诉', value: Number(s.internal?.total || 0), itemStyle: { color: '#2563eb' } },
    { name: '商户号投诉', value: Number(s.merchant?.total || 0), itemStyle: { color: '#7c3aed' } },
    { name: '外部投诉', value: Number(s.external?.total || 0), itemStyle: { color: '#dc2626' } },
  ]
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['40%', '65%'],
      center: ['50%', '45%'],
      label: { show: false },
      labelLine: { show: false },
      data,
    }],
  }
})

const complaintHandlerOption = computed(() => {
  const s = complaintSummaryData.value || {}
  const map = {}
  ;[
    ...(s.internal?.by_handler || []),
    ...(s.merchant?.by_handler || []),
    ...(s.external?.by_handler || []),
  ].forEach(h => {
    const key = h.name || '未标注'
    map[key] = (map[key] || 0) + Number(h.count || 0)
  })
  const rows = Object.entries(map)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10)
    .reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 100, right: 40, bottom: 30, top: 10 },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: rows.map(d => d.name) },
    series: [{ name: '投诉数', type: 'bar', data: rows.map(d => d.count), itemStyle: { color: '#7c3aed' }, barMaxWidth: 16 }],
  }
})

function complaintSourceType(source) {
  if (source === '内部投诉') return 'warning'
  if (source === '外部投诉') return 'danger'
  if (source === '商户号投诉') return 'primary'
  return 'info'
}

// 被投诉主体 Top5 (商户号+外部 堆叠, 仅统计有主体的来源)
const complaintSubjectTopOption = computed(() => {
  const rows = (complaintSubjectData.value?.subject_ranking || []).slice(0, 5)
  if (!rows.length) return null
  const items = [...rows].reverse()
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const p = ps[0]
        const row = rows.find(x => x.name === p?.name)
        if (!row) return ''
        return `${row.name}<br/>商户号: ${row.merchant_count}<br/>外部: ${row.external_count}<br/>关联退款: ${row.refund_count}笔 ¥${Number(row.refund_amount || 0).toLocaleString('zh-CN')}`
      }
    },
    legend: { top: 0, right: 0, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10 } },
    grid: { left: 10, right: 10, bottom: 8, top: 28, containLabel: true },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: items.map(x => x.name), axisLabel: { fontSize: 11 } },
    series: [
      { name: '商户号投诉', type: 'bar', stack: 's', data: items.map(x => x.merchant_count), itemStyle: { color: '#7c3aed' }, barMaxWidth: 16 },
      { name: '外部投诉', type: 'bar', stack: 's', data: items.map(x => x.external_count), itemStyle: { color: '#dc2626' }, barMaxWidth: 16 },
    ],
  }
})

function formatShortTime(t) {
  if (t === null || t === undefined || t === '' || t === '-') return '-'
  const d = dayjs(t)
  if (!d.isValid()) return '-'
  return d.format('MM-DD HH:mm')
}

function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

function scoreTagType(score, a, b, c) {
  if (score >= a) return 'success'
  if (score >= b) return 'warning'
  if (score >= c) return 'danger'
  return 'info'
}

// ========== 数据加载 ==========
async function loadAll() {
  loading.value = true
  const { start, end } = getDateRange()
  const params = { start, end, trend_grain: trendGrain.value }

  try {
    const [kpiRes, hourlyRes, receptionRes, refundRes, staffRes, recoveryRes, complaintSummaryRes, complaintDetailRes, subjectRes] = await Promise.all([
      today.kpi(params),
      today.hourlyTrend(params),
      today.reception(params),
      today.refund(params),
      today.staff({ ...params, aggregate_by: staffFilter.aggregateBy, topn: 200 }),
      today.refundRecovery(params),
      complaintSummary({ start, end }),
      complaintDetail({ start, end, limit: 500, include_supplementary: true }),
      complaintSubjectAnalysis({ start, end }),
    ])

    if (kpiRes.data) kpiData.value = kpiRes.data
    if (hourlyRes.data) hourlyData.value = hourlyRes.data
    if (receptionRes.data) receptionData.value = receptionRes.data
    if (refundRes.data) refundData.value = refundRes.data
    if (staffRes.data) staffData.value = staffRes.data
    if (recoveryRes.data) recoveryData.value = recoveryRes.data
    if (complaintSummaryRes.data) complaintSummaryData.value = complaintSummaryRes.data
    if (complaintDetailRes.data) complaintList.value = complaintDetailRes.data?.items || []
    if (subjectRes.data) complaintSubjectData.value = subjectRes.data
  } catch (e) {
    console.error('加载数据失败', e)
  } finally {
    loading.value = false
  }
}

async function loadGroups() {
  try {
    const res = await listGroups()
    groupsList.value = res.data || []
  } catch {}
}

function _onMediaChange(e) {
  isMobile.value = e.matches
}

onMounted(async () => {
  _mq = window.matchMedia('(max-width: 768px)')
  isMobile.value = _mq.matches
  _mq.addEventListener('change', _onMediaChange)
  await Promise.all([loadAll(), loadGroups()])
})

onBeforeUnmount(() => {
  if (_mq) {
    _mq.removeEventListener('change', _onMediaChange)
    _mq = null
  }
})

watch([grain, weekOffset, monthOffset, quarterOffset, yearOffset, customDateRange, trendGrain, filterDepartment, filterGroupId, staffFilter.aggregateBy], () => {
  loadAll()
})
</script>

<style scoped>
.operation-dashboard {
  padding-bottom: 24px;
}
.section-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}
.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb-16 { margin-bottom: 16px; }
.mb-20 { margin-bottom: 20px; }
.mb-24 { margin-bottom: 24px; }
.kpi-mini-card {
  padding: 12px 16px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}
/* 投诉KPI卡: 可点击筛选明细表 */
.kpi-clickable {
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}
.kpi-clickable:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
  border-color: #c7c7c7;
}
.kpi-clickable.kpi-active {
  outline: 2px solid #2563eb;
  outline-offset: -2px;
  box-shadow: 0 0 0 2px rgba(37,99,235,0.18);
}
.kpi-mini-hint {
  font-size: 10px;
  color: #9ca3af;
  margin-top: 4px;
  letter-spacing: 0.5px;
}
.kpi-clickable.kpi-active .kpi-mini-hint { color: #2563eb; }
/* 外部投诉KPI下的 315补充数据(workbuddy推送) */
.kpi-mini-suppl {
  font-size: 11px;
  color: #b91c1c;
  margin-top: 3px;
  font-weight: 600;
}
/* F-P1-2: 投诉三源卡 登记/处理 副行 */
.kpi-mini-sub {
  font-size: 12px;
  color: #6b7280;
  margin-top: 2px;
  line-height: 1.4;
}
.rh-label {
  color: #6b7280;
  font-weight: 600;
}
/* workbuddy 推送补充数据源汇总条 */
.suppl-strip {
  display: flex;
  align-items: center;
  font-size: 12px;
  color: #92400e;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
  padding: 4px 10px;
  margin-bottom: 12px;
}
.suppl-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #f59e0b;
  margin-right: 6px;
  flex-shrink: 0;
}
/* ★ #80: 投诉源卡"不纳入投诉总量"的显式声明（原来是灰色 hint，易被当装饰忽略）。
   口径声明必须比操作提示更醒目 —— 否则使用者会把四卡相加当总数。 */
.kpi-mini-total-note {
  font-size: 11px;
  color: #059669;
  font-weight: 600;
  margin-top: 3px;
  padding-top: 3px;
  border-top: 1px dashed #d1fae5;
}
/* ★ #80 三源合计提示条: 四卡并排后必须显式给出合计与口径差异警告，
   否则使用者会自行相加（连卡商一起加）或以为两个数对不上是 bug。 */
.complaint-total-hint {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12px;
  color: #6b7280;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 4px 10px;
  margin-bottom: 12px;
}
.cth-sum b { color: #2563eb; font-size: 14px; margin-left: 4px; }
.cth-sep { color: #d1d5db; }
.cth-warn {
  color: #b45309;
  cursor: help;
  text-decoration: underline dotted;
}
.kpi-mini-label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.kpi-mini-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}
.kpi-mini-unit {
  font-size: 14px;
  font-weight: 400;
  color: #9ca3af;
  margin-left: 4px;
}
.text-muted { color: #9ca3af; }

/* ★ #80 退款挽回紧凑条: 3 个大卡 → 1 行内联条（含成功/失败堆叠比例条）
   设计说明: 用户反馈「3 个数字太占空间」。原实现每卡一个 el-col，
   在桌面端（缺断点→span=24）会各占一整行，共三大行。
   改为 flex 内联条后：数字与堆叠条同排，总高度约为原来的 1/3，
   且新增了「成功/失败占比」这一个原来没有的信息（可控指标前置）。 */
.rec-compact {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 10px;
  margin-bottom: 16px;
}
.rec-cell {
  display: flex;
  align-items: baseline;
  gap: 5px;
  padding: 8px 14px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  background: #fff;
  flex-shrink: 0;
}
/* 堆叠条所在单元占满剩余宽度（自适应，窄屏自动换行） */
.rec-cell-grow {
  flex: 1 1 260px;
  flex-direction: column;
  align-items: stretch;
  gap: 5px;
  padding: 8px 14px;
}
.rec-label { font-size: 12px; color: #6b7280; }
.rec-value { font-size: 22px; font-weight: 700; line-height: 1.1; font-variant-numeric: tabular-nums; }
.rec-unit { font-size: 12px; color: #9ca3af; }
.rec-stack-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.rec-stack {
  display: flex;
  height: 8px;
  border-radius: 4px;
  overflow: hidden;
  background: #f0f1f3;
}
.rec-stack-seg { display: block; height: 100%; }
.rec-stack-note { font-size: 11px; color: #9ca3af; }

/* 窄屏: 精简排版, 避免文字/数字被挤成两行 */
@media (max-width: 768px) {
  .td-header {
    flex-wrap: wrap;
    gap: 8px;
  }
  .td-header :deep(.filter-bar-wrap) {
    width: 100%;
  }
  .section-card {
    padding: 12px;
  }
  .kpi-mini-card {
    padding: 10px 12px;
  }
  .kpi-mini-value {
    font-size: 18px;
  }
  .kpi-mini-unit {
    font-size: 12px;
  }
  :deep(.kpi-card.is-compact .kpi-value) {
    font-size: 16px;
  }
  :deep(.kpi-card.is-compact .kpi-yoy) {
    flex-wrap: nowrap;
  }
  :deep(.kpi-card.is-compact .kpi-yoy-val) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
  }
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

/* 趋势类型勾选 */
.trend-group-check :deep(.el-checkbox-button__inner) {
  padding: 4px 10px;
  font-size: 12px;
}

/* ★ #78 AI / 系统账户单列区块（与「决策分析看板」同款视觉） */
.virtual-staff-block {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
}
.virtual-staff-title {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 6px;
}
</style>
