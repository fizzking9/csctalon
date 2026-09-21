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
  │       #81（★ 已完成：投诉数口径统一为单一出口，核心指标 ≡ 三源合计）                  │
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
            :metric-level="c.metricLevel || null"
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
          <el-table-column v-if="staffFilter.aggregateBy === 'person'" label="岗位" width="96">
            <!-- ★ 2026-09-14 用户要求：「叶珍凤、董梦玲、吴晓龙、黄亚康、洪佳
                 都是组长；希望今日值班看板与运营分析都能很明显标识为组长。
                 陈卓、朱海洋 主管级 → 标识为 管理。」

                 设计说明（Design Rationale）
                 目的：替**运营主管**回答「这行是谁、他能不能拍板」。
                       此前表格有"专项"列但**无岗位列** → 组长/主管混在客服里
                       看不出层级，无法快速定位该找谁。

                 逻辑：岗位列紧邻姓名（第 2 列），管理岗徽标**高亮实底**，
                       非管理岗用浅色 —— 让管理岗在长表中自然浮出。
                       文案用管理视角词（主管→「管理」）而非职级名。

                 口径：取自后端 `position`（客服/投诉专员/组长/副主管/主管）。
                       ⚠️ 与「专项」列**含义不同**：岗位=层级，专项=干什么。
                       某人可以是"客服+投诉专项"（兼办）或"组长+无专项"。

                 取舍：① 不把管理岗数据"单独成表"—— 岗位只是人的属性之一，
                          拆表会切断与专项/效率值的对照（那正是本表价值）；
                       ② 不用 `is_management` 判断 —— 它含专员类跨类豁免岗
                          （投诉专员等），那些不是管理岗（见 Today.vue 同款注释）；
                       ③ AI/系统行不给岗位徽标 —— 它们没有职级，
                          已在"来源"列标注为非真人。 -->
            <template #default="{ row }">
              <el-tag v-if="row.is_virtual" size="small" effect="plain" type="info">非真人</el-tag>
              <el-tag
                v-else-if="positionMeta(row.position)"
                size="small"
                effect="dark"
                :style="{ background: positionMeta(row.position).bg, borderColor: positionMeta(row.position).bg }"
              >{{ positionMeta(row.position).label }}</el-tag>
              <span v-else-if="row.position" class="pos-plain">{{ row.position }}</span>
              <span v-else class="text-muted">—</span>
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
                  <div class="score-tip-note">专项难度系数: 咨询×1.0 套餐×1.8 退款处理×2.0 退款挽回×2.2 内诉×3.0 外诉×5.0 **投诉专项×5.0** · 转接×0.2</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
        </el-table>

        <!-- ★ #78 AI / 系统账户单列（非真人，不计入人效排名）
             ★ 2026-09-14 用户要求：本表**列结构与上方效率值表对齐**，
               以便「AI 与真人同口径比对」，不再只是"量级参考"。

             设计说明（Design Rationale）
             ─────────────────────────────────────────────
             目的（替谁回答什么问题）
               替**运营主管**回答「AI 顶几个客服」。
               此前 AI 只有"会话数/接待数/占全站 %"三列，
               缺**专项接待数**与**效率值** → 无法与真人横向比，
               "AI 效率如何"这个问题答不了。

             逻辑（为什么这样组织）
               · 列顺序、列名、单位**刻意与上方真人表一致**
                 （会话数 → 各专项接待数 → 接待合计 → 会话效率 → 接待效率）。
                 同名同序才能**上下对读**，否则读者要自己做映射。
               · 保留「占全站会话」列 —— 它是 AI 独有的可控指标（接管率），
                 真人表没有也不该有。
               · 不显示「排名」：AI 不是人，名次无意义（#78 既有决策）。

             口径（与真人完全同源，详见后端 `_ai_scores`）
               · 专项接待数 = 各桶 SN 去重（判据 = 同一 `_category_case()`）
               · 会话效率/接待效率 = **同一张权重表**（`CAT_DEFAULT_WEIGHT`）
                 按 AI 实际接待构成逐桶累加，语义 = "折合多少标准人力工作量"
               · 故本表数字与真人表**可直接比对**

             取舍（不做什么及原因）
               · **不并入上方真人表** —— 并入会：① 挤占真人名次
                 ② `v-if` 的聚合模式（按专项/分组）对 AI 无意义
                 ③ 真人表的筛选/排序控件会作用于 AI 行，语义混乱
               · **不显示 AI 的"转接"标记** —— AI 无专项任职，
                 不存在"本职 vs 转接"之分（全部按接待构成计）
               · 不加下钻 —— AI 无 Person 明细页可落

             ★ 2026-09-14 用户追问：「不需要等有数据的时候再来显示吧？」
               → **本块始终渲染**。原来 `v-if="virtualStaffList.length"` 时，
                 无 AI 数据则整块消失，读者**无法区分**
                 「AI 本期确实零工作」和「这个功能还在/没做」——
                 违反看板规范「空必须可解释」。现改为空态显式说明。

             工单: #78 / 本次调整: 2026-09-14 用户要求 -->
        <div class="virtual-staff-block">
          <div class="virtual-staff-title">
            <span>AI / 系统账户（非真人，不计入人效排名 · 列结构与上方一致，可对读）</span>
            <el-tooltip effect="dark" placement="top" raw-content
              content="<b>来源</b>：同上表（/analytics/today/staff）<br/><b>口径</b>：按「操作人 + 说明文本」判定为非真人；<b>会话效率/接待效率采用与真人完全相同的权重表</b>（按各专项默认系数逐桶累加），故可与上方真人表直接比对 —— 语义是「折合多少标准人力工作量」。<br/><b>时点</b>：与上方表格同一窗口<br/><b>「空」的含义</b>：本区块不显示 = 本窗口内无非真人账户产生接待；某列为 0 = 该账户本期无该类工作<br/><br/><b>⚠️ 取舍</b>：① 不并入上方真人表（会挤占名次、且聚合模式/筛选对 AI 无意义）② 不给名次（AI 不是人，排名无意义）③ <b>不做</b>「AI 转人工」细分（平台字段不稳定，待 BMS/智能客服稳定后再精细化）">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <el-table :data="virtualStaffList" border size="small" max-height="260">
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
            <!-- 各专项接待数(个) —— 与上方真人表同名同序，便于上下对读 -->
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
            <!-- 效率值列：口径与真人一致（后端同一张权重表），只是 AI 无"转接"概念 -->
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
            <!-- AI 独有的可控指标（接管率）—— 真人表无此列 -->
            <el-table-column label="占全站会话" width="104" align="right">
              <template #default="{ row }">
                <span style="color:#f59e0b;font-weight:600">{{ aiSharePct(row) }}%</span>
              </template>
            </el-table-column>
            <!-- 空态：显式说明「本窗口无 AI 工作」，不靠隐藏整块来表达 -->
            <template #empty>
              <span style="color:#909399;font-size:12px">
                本窗口内<b>无 AI / 系统账户产生接待</b>（不是功能未启用）
              </span>
            </template>
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

      <!-- ★★ #87 投诉健康度指标（第一批）★★

           设计说明（Design Rationale）
           ─────────────────────────────────────────────
           目的（替谁回答什么问题）
             替**运营主管**回答三个此前无法回答的问题：
               ① 投诉的**相对水平**如何？（投诉数 8 的绝对值无法判断好坏）
               ② 有多少是**反复投诉**（第一次没解决）？
               ③ 各岗位**负荷**是否失衡（谁在扛、扛多少）？

           逻辑（为什么这样组织）
             · 绝对数 → 比率的转变：新增「活跃 SN 基数」作分母基底，
               把「投诉数」变成「投诉率」。**这解决一个经典误判** ——
               「投诉少了」到底是服务水平提升，还是客户流失？
               投诉↓ + 活跃↓ = 可能是客户流失（危险）；投诉↓ + 活跃→ = 真改善。
             · 「重复投诉率」放最前：它是当前**升级的唯一可观测代理**。
               同一台设备 30 天内被投诉 ≥2 次 ≈ 第一次没解决好。
             · 岗位负荷按**岗位归因**（组长 / 投诉专员 / 客服），
               因为分子口径不同：组长与专员=投诉结案数，客服=接待数。

           口径（数据判据 + 文档指针）
             · 活跃SN = 窗口内 cs_records 有接待记录的去重设备SN
             · 投诉率 = 投诉SN / 活跃SN（三维：全站 / 对外 / 对内）
             · 重复投诉率 = 30 天内同SN投诉≥2次占比（代理指标，非升级率）
             · 岗位归属取 person.position / specialty（与 SCD 任职源同步）；
               投诉专员 = 专项∈(内部投诉, 外部投诉)；组长含副主管
             · 完整口径: docs/arch/_pending/A-20260914-001-投诉升级链路指标体系设计.md
             · ⚠️ 投诉判据 = 服务类别**空白或'投诉'**（不可用 LIKE '%投诉%'）

           取舍（不做什么及原因）
             · 不叫「升级率」—— 只有「重复投诉率」这个代理口径，
               真实升级率需升级投诉表（申报/化解字段）到位，**混用即口径漂移**（#81/#83 教训）
             · 不做「投诉专员人均负荷」的名称替换 —— 专员岗位真实存在（4 人），
               某轮我误判为"无此岗位"，经用户纠正后修正（见设计文档 §9.1）
             · 不加趋势图 —— 第一批先把「当期水位」看清；趋势属第二批
             · 分母为 0 时显示「—」而非 0% —— 避免被读成"零投诉"（规范：空必须可解释）

           工单: #87 -->
      <div v-if="healthMetrics" class="health-block mb-16">
        <div class="hb-head">
          <span class="hb-title">投诉健康度（相对水平 · 重复投诉 · 岗位负荷）</span>
          <el-tooltip effect="dark" placement="top" raw-content :content="healthTip">
            <el-icon style="color:#b0b4bb;cursor:help;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>

        <!-- ① 投诉率三维 + 重复投诉率（比率组，放最前 —— 相对水平比绝对数重要） -->
        <el-row :gutter="12" class="mb-8">
          <el-col :xs="12" :sm="8" :md="4">
            <div class="hb-card hb-clickable" style="background:#eff6ff" @click="drillToComplaintAnalysis()">
              <div class="hb-label" style="color:#2563eb">投诉率(全站)</div>
              <div class="hb-value" style="color:#2563eb">{{ fmtRate(healthMetrics.rates?.overall) }}</div>
              <div class="hb-sub">投诉SN ÷ 活跃SN</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="hb-card hb-clickable" style="background:#fef2f2" @click="drillToComplaintAnalysis('外部投诉')">
              <div class="hb-label" style="color:#dc2626">投诉率(对外)</div>
              <div class="hb-value" style="color:#dc2626">{{ fmtRate(healthMetrics.rates?.external) }}</div>
              <div class="hb-sub">走到外部监管占比</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="hb-card hb-clickable" style="background:#f5f3ff" @click="drillToComplaintAnalysis('内部投诉')">
              <div class="hb-label" style="color:#7c3aed">投诉率(对内)</div>
              <div class="hb-value" style="color:#7c3aed">{{ fmtRate(healthMetrics.rates?.internal) }}</div>
              <div class="hb-sub">内部渠道占比</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="hb-card hb-clickable" style="background:#fff7ed" @click="drillToRepeatComplaint()">
              <div class="hb-label" style="color:#ea580c">
                重复投诉率
                <el-tooltip effect="dark" placement="top" raw-content
                  content="<b>这是升级的代理信号，不是真实升级率</b><br/>同设备(SN)30天内被投诉≥2次 ≈ 第一次没解决好。<br/>真实升级率需升级投诉表（申报/化解字段）到位后才可计算。">
                  <el-icon style="vertical-align:-2px;font-size:11px"><QuestionFilled /></el-icon>
                </el-tooltip>
              </div>              <div class="hb-value" style="color:#ea580c">{{ fmtRate(healthMetrics.repeat?.repeat_rate) }}</div>
              <div class="hb-sub">{{ formatNum(healthMetrics.repeat?.repeat_tickets) }} 条重复</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="hb-card" style="background:#f0fdf4">
              <div class="hb-label" style="color:#16a34a">活跃 SN 基数</div>
              <div class="hb-value" style="color:#16a34a">{{ formatNum(healthMetrics.active_sn) }}</div>
              <div class="hb-sub">所有比率的<b>分母基底</b></div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <div class="hb-card" style="background:#f8fafc">
              <div class="hb-label" style="color:#475569">投诉 SN / 工单</div>
              <div class="hb-value" style="color:#475569;font-size:16px">
                {{ formatNum(healthMetrics.complaint_sn) }} / {{ formatNum(healthMetrics.complaint_tickets) }}
              </div>
              <div class="hb-sub">去重SN / 总条数</div>
            </div>
          </el-col>
        </el-row>

        <!-- ② 岗位负荷（按岗位归因；分子口径不同，已在列内声明） -->
        <div class="hb-pos">
          <div class="hb-pos-title">
            岗位负荷
            <el-tooltip effect="dark" placement="top" raw-content :content="healthPositionTip">
              <el-icon style="color:#b0b4bb;cursor:help;font-size:12px"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <div class="hb-pos-grid">
            <div
              v-for="p in healthMetrics.by_position"
              :key="p.bucket"
              class="hb-pos-card"
              :class="{ 'hb-pos-card--active': p.numerator > 0 }"
            >
              <div class="hb-pos-name">{{ p.bucket }}</div>
              <!-- 无在岗人数（兼办投诉行）时：大数字直接显示工作量，不显示「—」，
                   避免看起来像"数据缺失" -->
              <div class="hb-pos-per">
                <template v-if="p.headcount === null">
                  {{ formatNum(p.numerator) }}<span class="hb-pos-unit">{{ p.unit }}</span>
                </template>
                <template v-else>
                  {{ p.per_capita === null ? '—' : formatNum(p.per_capita) }}
                  <span class="hb-pos-unit">{{ p.unit }}/人</span>
                </template>
              </div>
              <div class="hb-pos-detail">
                <template v-if="p.headcount === null">
                  {{ formatNum(p.numerator) }}{{ p.unit }}（不计算人均）
                </template>
                <template v-else>
                  {{ formatNum(p.numerator) }}{{ p.unit }} ÷ {{ p.headcount }} 人
                </template>
              </div>
              <div class="hb-pos-basis">{{ p.numerator_basis }}</div>
            </div>
          </div>
          <div class="hb-pos-foot">
            归因合计 <b>{{ formatNum(healthMetrics.attribution?.total_handled) }}</b> 条
            <span class="hb-dim">（含未登记岗位/主管 {{ formatNum(healthMetrics.attribution?.by_bucket?.其他) }} 条）</span>
          </div>
          <!-- ★ 兼办投诉提示：让「非专职人员在办投诉」可见（岗位临时调配信号）。
               实测约 7% 投诉由非组长/专员完成，工单未按新流程归口 ——
               这是第一批指标要暴露的问题，不隐藏。 -->
          <div v-if="healthOtherDuty" class="hb-notice">
            <el-icon style="vertical-align:-2px"><InfoFilled /></el-icon>
            <span>
              有 <b>{{ formatNum(healthOtherDuty) }}</b> 条投诉由<span class="hb-dim">非组长/专员</span>完成
              （{{ healthOtherDutyPct }}%），提示存在<b>岗位临时调配</b> ——
              上方岗位负荷只反映岗位本职，不含此类兼办。
            </span>
          </div>
        </div>
      </div>

      <!-- 三源 KPI (点击即作为下方明细表的来源筛选; 主值=登记, 副行=处理, F-P1-2)
           ★ #80 修复: 原 `el-col :xs="8"` **缺 :sm/:md** → 桌面端每个 col span=24
             → 用户反馈「四个居然占了 4 个大行」。补断点后桌面端四卡同行。
           ★ #81 口径统一: 本四卡与上方「核心指标·投诉数」现为**同源**（后端
             today_kpi 直接调用 complaints._sources_reg_handled）→ **数字逐位一致**。
             因此 #80 诉求 2「投诉括弧三源」已可安全实现（括弧之和 == 主值）。
             卡商仍独立（#53），不计入本区合计。 -->
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

      <!-- ★ #81 三源合计校验条（防误加卡商 + 防误以为"两处对不上"）:
           #81 口径统一后，本区三源合计与「核心指标·投诉数」**逐位一致**。
           此处显式给出合计并声明一致 —— 使用者不必再怀疑该信哪个。
           ⚠️ 卡商仍独立（#53），不计入本合计。 -->
      <div v-if="complaintTotalHint" class="complaint-total-hint">
        <span class="cth-sum">三源合计（不含卡商）<b>{{ formatNum(complaintThreeTotal) }}</b></span>
        <span class="cth-sep">·</span>
        <el-tooltip effect="dark" placement="top" raw-content :content="complaintScopeTip">
          <span class="cth-ok">
            <el-icon style="vertical-align:-2px"><CircleCheckFilled /></el-icon>
            与上方「核心指标·投诉数」同源同口径，数字一致
          </span>
        </el-tooltip>
      </div>

      <!-- workbuddy 推送补充数据源汇总(315/卡商, 按SN关联工单) -->
      <div v-if="supplSummaryText" class="suppl-strip">
        <span class="suppl-dot" />
        <span>{{ supplSummaryText }}</span>
      </div>

      <!-- ★ #82 积压账龄分档（把「该不该管」显示出来）
           目的: 替主管回答「积压是在恶化还是在消化？陈年烂账有多少？」
           逻辑: 总量看不出结构 —— **新单涌入**与**老单在烂**是完全不同的问题，
                 处理动作也不同（前者要加人，后者要决策清理/关单）。
                 故按账龄分档并列，一眼看出陈旧部分占比。
           口径: **全量**未完成投诉工单按 register_time 距今账龄分档；
                 ⚠️ **不含 (外)卡商**（#53 独立统计）；与上方「近30天积压」口径不同（有意保留）。
           取舍: ① 不改上方「积压待处理」卡 —— 两者含义确实不同
                   （近30天=近期消化压力；本卡=历史包袱），改口径会造成数字变动争议；
                 ② 不做饼图 —— 四档是**递进**关系（越老越糟），横向条更易比较；
                 ③ 每档不做独立下钻 —— 明细页按【日】筛选，账龄是计算列无法透传，
                   故整卡下钻到投诉分析页（已在 ⓘ 中声明）。
           工单: #82 -->
      <div v-if="backlogAging" class="backlog-aging">
        <div class="ba-head">
          <span class="ba-title">积压账龄分档（全量 · 不含卡商）</span>
          <el-tooltip effect="dark" placement="top" raw-content :content="backlogAgingTip">
            <el-icon style="color:#b0b4bb;cursor:help;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
          <span class="ba-total">共 <b>{{ formatNum(backlogAging.total) }}</b> 条未结</span>
          <span v-if="backlogAging.max_age_days" class="ba-oldest">
            最老 {{ backlogAging.max_age_days }} 天
          </span>
        </div>
        <div class="ba-bars">
          <div
            v-for="s in BACKLOG_AGING_SEGMENTS"
            :key="s.key"
            class="ba-seg"
            :style="{ flex: Math.max(Number(backlogAging[s.key]) || 0, 0.6) }"
            :title="`${s.label}：${formatNum(backlogAging[s.key])} 条（${backlogAgingTotal ? Math.round((backlogAging[s.key] * 100) / backlogAgingTotal) : 0}%）`"
          >
            <div class="ba-seg-bar" :style="{ background: s.color, opacity: backlogAging[s.key] ? 1 : 0.25 }" />
            <div class="ba-seg-label" :style="{ color: s.color }">{{ s.short }}</div>
            <div class="ba-seg-value">{{ formatNum(backlogAging[s.key]) }}</div>
          </div>
        </div>
        <div class="ba-note">
          C/D 档（>30 天）共 <b :style="{ color: overdueCount ? '#dc2626' : '#16a34a' }">{{ formatNum(overdueCount) }}</b> 条，
          {{ overdueCount > 0 ? '需决策：清理 / 关单 / 转专项' : '无需介入' }}
        </div>
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
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch, getCurrentInstance } from 'vue'
import { today } from '@/api/analytics'
import { listGroups } from '@/api/group'
import { complaintSummary, complaintDetail, complaintSubjectAnalysis, complaintHealthMetrics } from '@/api/complaint'
import BaseChart from '@/components/BaseChart.vue'
import { useAudit } from '@/composables/useAudit'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import quarterOfYear from 'dayjs/plugin/quarterOfYear'
import weekOfYear from 'dayjs/plugin/weekOfYear'
import { buildStack, sourceKindMeta, RECEPTION_BREAKDOWN_TIP } from '@/utils/receptionSource'
import { getWeekRange, getWeekRangeStr, weekLabel } from '@/utils/weekRange'
import { QuestionFilled, CircleCheckFilled, InfoFilled } from '@element-plus/icons-vue'

import { useOperationDashboard } from './useOperationDashboard'

const {
  loading,
  isMobile,
  _mq,
  grain,
  weekOffset,
  monthOffset,
  quarterOffset,
  yearOffset,
  customDateRange,
  trendGrain,
  quickGrainOpt,
  grainQuickOptions,
  filterDepartment,
  filterGroupId,
  groupsList,
  staffFilter,
  showAllStaff,
  openPersonAudit,
  openDeviceAudit,
  receptionPerspective,
  trendSelectedGroups,
  SPECIALTY_COLS,
  POSITION_META,
  positionMeta,
  getSpecialtyPeople,
  kpiData,
  hourlyData,
  receptionData,
  refundData,
  staffData,
  recoveryData,
  hasRecovery,
  complaintSummaryData,
  complaintList,
  proxy,
  complaintSubjectData,
  healthMetrics,
  supplExternal,
  supplCard,
  supplSummaryText,
  complaintThreeTotal,
  complaintTotalHint,
  complaintScopeTip,
  TREND_TIP,
  COMPLAINT_SOURCE_CHIPS,
  complaintChips,
  complaintTip,
  backlogAging,
  backlogAgingTotal,
  overdueCount,
  BACKLOG_AGING_SEGMENTS,
  healthTip,
  healthPositionTip,
  fmtRate,
  drillToComplaintAnalysis,
  drillToRepeatComplaint,
  healthOtherDuty,
  healthOtherDutyPct,
  backlogAgingTip,
  complaintSourceFilter,
  complaintCategoryFilter,
  complaintStatusFilter,
  COMPLAINT_DETAIL_LIMIT,
  complaintTruncated,
  toggleComplaintSourceFilter,
  clearComplaintFilters,
  sortTimeStr,
  complaintSourceFilters,
  complaintCategoryFilters,
  complaintStatusFilters,
  filteredComplaintList,
  weekOptions,
  monthOptions,
  quarterOptions,
  yearOptions,
  grainLabel,
  grainTagType,
  periodName,
  compareName,
  displayDate,
  rhythmSub,
  getDateRange,
  _pctDelta,
  kpiCards,
  metricLevelByCode,
  recoveryMetricLevel,
  recoveryMetricTip,
  REFUND_ROUTES,
  _rrLabel,
  COMPLAINT_TREND_SOURCES,
  buildComplaintBySource,
  buildTrendSeries,
  buildTrendLegend,
  hourlyOption,
  receptionPieOption,
  refundReasonOption,
  departmentOptions,
  isQwStaff,
  staffRows,
  virtualStaffList,
  aiSharePct,
  showDeptColumn,
  recoveryStaffList,
  recoveryZeroHidden,
  recoveryAllFailCount,
  recoverySuccessPct,
  recoveryStackTip,
  recoveryRate,
  rateColor,
  rateBg,
  complaintSourcePie,
  complaintHandlerOption,
  complaintSourceType,
  complaintSubjectTopOption,
  formatShortTime,
  formatNum,
  scoreTagType,
  loadAll,
  loadGroups,
  _onMediaChange,
} = useOperationDashboard()
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
/* ★ #81: 口径统一后改为「一致」阳性提示（原为不一致的告警色） */
.cth-ok {
  color: #059669;
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

/* ★ 非管理岗的岗位文字（客服/专员）—— 浅色，避免与高亮的管理岗徽标争视觉焦点 */
.pos-plain { color: #6b7280; font-size: 12px; }

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

/* ★ #82 积压账龄分档
   设计说明: 用横向条 + 数字并列，让「越老越红」自然浮出。
   宽度按数量 flex 分配（最少 0.6 保证标签不被挤没）。 */
.backlog-aging {
  margin-top: 4px;
  margin-bottom: 14px;
  padding: 10px 12px;
  background: #fafbfc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}
.ba-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.ba-title { font-size: 12px; font-weight: 600; color: #303133; }
.ba-total { font-size: 12px; color: #6b7280; margin-left: auto; }
.ba-total b { color: #303133; font-size: 14px; }
.ba-oldest { font-size: 11px; color: #dc2626; font-weight: 600; }
.ba-bars { display: flex; gap: 6px; align-items: flex-end; }
.ba-seg { min-width: 0; text-align: center; }
.ba-seg-bar { height: 8px; border-radius: 4px; margin-bottom: 4px; }
.ba-seg-label { font-size: 11px; font-weight: 600; white-space: nowrap; }
.ba-seg-value { font-size: 13px; font-weight: 700; color: #303133; font-variant-numeric: tabular-nums; }
.ba-note {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #e5e7eb;
  font-size: 12px;
  color: #6b7280;
}
.ba-note b { font-size: 14px; margin: 0 2px; }

/* ★★ #87 投诉健康度指标（比率 / 重复投诉 / 岗位负荷）
   设计说明: 
     · 比率卡在前 —— 「相对水平」比绝对数更接近管理判断；
     · 岗位负荷用独立小格（3 列），因为它有 3 个维度（人均/分子÷人数/口径），
       塞进比率卡会让信息拥挤；
     · 高亮仅用于「有工作量」的岗位（numerator>0），让 0 工作量自然淡出而非消失
       （消失会让读者以为该岗位不存在）。 */
.health-block {
  padding: 10px 12px;
  background: #fafbfc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}
.hb-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}
.hb-title { font-size: 12px; font-weight: 600; color: #303133; }

.hb-card {
  padding: 8px 10px;
  border-radius: 6px;
  height: 100%;
  box-sizing: border-box;
}
.hb-clickable { cursor: pointer; transition: box-shadow 0.15s, transform 0.15s; }
.hb-clickable:hover { box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1); transform: translateY(-1px); }
.hb-label {
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 3px;
  margin-bottom: 2px;
}
.hb-value {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
.hb-sub { font-size: 11px; color: #6b7280; margin-top: 1px; }
.hb-sub b { color: #303133; }

.hb-pos {
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px dashed #e5e7eb;
}
.hb-pos-title {
  font-size: 11px;
  font-weight: 600;
  color: #6b7280;
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
}
.hb-pos-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.hb-pos-card {
  padding: 8px 10px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  opacity: 0.55;
}
.hb-pos-card--active {
  opacity: 1;
  border-color: #cbd5e1;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.hb-pos-name { font-size: 11px; font-weight: 600; color: #303133; }
.hb-pos-per {
  font-size: 17px;
  font-weight: 700;
  color: #1f2937;
  font-variant-numeric: tabular-nums;
  line-height: 1.3;
}
.hb-pos-unit { font-size: 11px; font-weight: 500; color: #9ca3af; margin-left: 2px; }
.hb-pos-detail { font-size: 11px; color: #6b7280; }
.hb-pos-basis { font-size: 10px; color: #9ca3af; margin-top: 2px; line-height: 1.35; }
.hb-pos-foot {
  margin-top: 6px;
  font-size: 11px;
  color: #6b7280;
}
.hb-pos-foot b { color: #303133; font-size: 13px; }
.hb-dim { color: #9ca3af; }

/* ★ 兼办投诉提示：让「非专职人员在办投诉」可见（岗位临时调配信号） */
.hb-notice {
  margin-top: 6px;
  padding: 5px 8px;
  background: #fffbeb;
  border-left: 3px solid #f59e0b;
  border-radius: 3px;
  font-size: 11px;
  color: #92400e;
  line-height: 1.45;
}
.hb-notice b { color: #b45309; }
.hb-notice .hb-dim { color: #b45309; opacity: 0.75; }

/* 窄屏: 岗位负荷改单列，比率卡已由 el-col 断点处理 */
@media (max-width: 768px) {
  .hb-pos-grid { grid-template-columns: 1fr; }
  .hb-value { font-size: 17px; }
}

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
