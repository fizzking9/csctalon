<!--
  ┌──────────────────────── 设计说明（Design Rationale） ─────────────────────────┐
  │ 工单：#165 · 设计：docs/design/AI数据治理框架-20260919.md §八                  │
  │ 性质：REQ（具体功能）— 非元需求，不进 AGENTS.md 引用链                        │
  │ 目的（替谁/答何决策）：替 admin（数据治理负责人）回答                           │
  │       「库里到底有没有结构性问题、AI 说是不是真问题、我该不该执行这个动作」——     │
  │       每次体检/研判后看一次，决定「确认还是驳回、执行还是先不动、要不要沉淀规则」。│
  │ 逻辑（为何这样组织）：按治理闭环的**动作顺序**单页走完：                         │
  │       ① 概览（待研判/待处置 是可控闸门，前置）→ ② 体检/研判（异步 202 + 轮询）→ │
  │       ③ findings 列表筛选 → ④ 点行看详情（证据/研判/建议/凭证）→              │
  │       ⑤ 确认 → 执行 / 判定业务允许（沉淀规则）→ ⑥ 规则管理。                   │
  │       详情区把**证据（metrics/样本/复现 SQL）放在结论之前**：让人先能自己核对     │
  │       数字，再看 AI 的判断 —— 不接受「黑盒结论」（设计 §3.1）。                 │
  │ 口径：findings/rules 存 `governance_findings` / `governance_rules`（设计 §3.1/§3.2）；│
  │       探测器 = 确定性扫描（D1 唯一索引重复 / D2 行数突变 / D3 外键孤儿 /             │
  │                 D4 文本异常:换行 或 长度贴列宽上限=截断嫌疑，白名单列见              │
  │                 backend/app/services/text_anomaly_rules.py，导入期同源留痕），        │
  │       判据来源 docs/standards/数据采集合并去重机制说明.md（去重键）、              │
  │       docs/standards/数据时间字段使用规范.md（时区）、                            │
  │       docs/standards/数据源接入与缓存加速规范.md（执行后失效缓存）；              │
  │       执行分级 auto_level / requires_confirm **一律取服务端返回值**，             │
  │       前端不另立映射表（见后端 disposition_levels 唯一来源注释）。               │
  │ 取舍（不做什么及原因）：                                                       │
  │   ❌ 不做「一键治理全部」—— 批量执行不可逆动作等于把红线交给一次误点，           │
  │      每个 L2 动作必须逐条确认（设计 §六 红线）；                                │
  │   ❌ 不做「AI 自动执行」—— 不可逆动作永远需人工确认，与 AI 数据接入助手同一红线； │
  │   ❌ 不做 finding 删除 —— 探测/研判/执行要留痕可审计（system_events），          │
  │      收敛靠重扫 auto_closed，不靠人删；                                        │
  │   ❌ 不做规则「删除」—— 只启用/禁用（规则是判定依据，删掉无法解释历史结论）；     │
  │   ❌ 不做「治理趋势图/健康分」—— 没有可信基线前画趋势线是秀好看，              │
  │      当前要回答的是「这条问题怎么办」，不是「数据健康度涨了没」；                │
  │   ❌ 不在本页提供不可逆动作的规则配置（后端亦拒绝）—— 规则是「命中即自动」，      │
  │      允许 L2 规则 = 把不可逆动作自动执行写成配置（设计 §六 明令禁止）。          │
  └──────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">AI 数据治理</div>
        <div class="page-sub">探测（确定性）→ AI 研判（只出建议）→ 人工确认 → 分级执行（L2 不可逆须确认）</div>
      </div>
      <div class="flex-center gap-8">
        <el-popover placement="bottom-end" :width="520" trigger="click">
          <template #reference>
            <el-button size="small" :icon="InfoFilled" circle />
          </template>
          <div class="rationale">
            <p><b>替谁：</b>admin（数据治理负责人）。每次体检/研判后看一次。</p>
            <p><b>答何决策：</b>库里有没有结构性问题、AI 说是不是真问题、这个动作该不该执行
              （确认 / 驳回 / 先不动 / 沉淀成规则）。</p>
            <p><b>怎么用：</b>① 看概览（待研判/待处置）→ ② 点「体检」扫一遍 → ③ 点「研判」让 AI 给结论 →
              ④ 按探测器/状态筛选 → ⑤ 点行看证据（metrics / 样本 / 复现 SQL）与建议 →
              ⑥ 确认 → 执行（不可逆动作先确认）或「判定业务允许」沉淀规则 → ⑦ 规则区启用/禁用。</p>
            <p><b>数据口径：</b>发现与规则存 <code>governance_findings</code> /
              <code>governance_rules</code>（设计 §3.1/§3.2）；探测器为确定性扫描
              （唯一索引重复 / 行数突变 / 外键孤儿 / 文本异常），判据引用
              <code>docs/standards/数据采集合并去重机制说明.md</code>（去重键）、
              <code>docs/standards/数据时间字段使用规范.md</code>（时区）、
              <code>docs/standards/数据源接入与缓存加速规范.md</code>（执行后失效缓存）。
              执行级别 <code>auto_level</code> / <code>requires_confirm</code> <b>一律取服务端返回值</b>，
              前端不另立级别映射（避免「前端以为可点、后端按红线拦」）。
              「文本异常」判据与**导入期留痕同源**（同一常量与白名单，见
              <code>text_anomaly_rules.py</code>）：白名单列（投诉 problem / handle_detail，
              多行是业务真实形态）不报换行；<code>remark</code> 类列含换行才报，交 AI 研判。</p>
            <p><b>不做什么（及原因）：</b></p>
            <ul>
              <li>❌ <b>不做一键治理全部</b>：批量执行不可逆动作 = 把红线交给一次误点，L2 必须逐条确认；</li>
              <li>❌ <b>不做 AI 自动执行</b>：不可逆动作（删行/改值/建索引）永远需人工确认；</li>
              <li>❌ <b>不删 finding</b>：探测/研判/执行要留痕可审计，收敛靠重扫自动 <code>auto_closed</code>；</li>
              <li>❌ <b>不删规则、只启停</b>：规则是判定依据，删掉无法解释历史结论；</li>
              <li>❌ <b>不做健康分/趋势图</b>：没有可信基线前画趋势是秀好看，本页要回答「这条怎么办」；</li>
              <li>❌ <b>不在此配置不可逆动作规则</b>：规则是「命中即自动」，后端亦拒绝（设计 §六 红线）。</li>
            </ul>
            <p><b>工单：</b>#165（设计 §八 接口契约 + §十 第 9 条 三层落地）</p>
          </div>
        </el-popover>
        <el-button size="small" :loading="submitting" :disabled="!!task" @click="doScan">体检</el-button>
        <el-button size="small" :loading="submitting" :disabled="!!task" @click="doJudge">研判</el-button>
        <el-button size="small" :icon="Refresh" @click="reloadAll">刷新</el-button>
      </div>
    </div>

    <!-- 异步任务：202 + 轮询（同一时刻只跑一个，避免两次体检互相覆盖证据） -->
    <el-alert v-if="task" type="info" :closable="false" show-icon class="mb-16"
      :title="`${task.kind === 'scan' ? '数据体检' : 'AI 研判'}进行中…（每 2 秒轮询一次，最多约 3 分钟）`" />
    <el-alert v-else-if="lastTask" :type="taskAlertType" :closable="true" show-icon class="mb-16"
      :title="`${lastTask.title || '治理任务'}：${lastTask.summary || '已完成'}`"
      :description="lastTaskDetail" />

    <!-- ① 概览四卡（每卡带 ⓘ 口径；能指向动作的卡片可点击下钻） -->
    <el-row :gutter="12" class="mb-8">
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never" class="stat-card clickable" @click="drillStatus('open')">
          <div class="stat-head">
            <span class="card-title">待研判</span>
            <el-tooltip placement="top">
              <template #content>
                <div class="tip">
                  <div>口径：<code>status=open</code>，探测器已算出但尚未研判的发现。</div>
                  <div>空（0）的含义：当前没有新问题待研判。</div>
                  <div>下钻：点卡片 → 下方列表按该状态筛选（带当前筛选条件，不跳空白页）。</div>
                </div>
              </template>
              <el-icon class="dim"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <div class="stat-num">{{ num(summary.by_status?.open) }}<span class="stat-unit">条</span></div>
          <div class="dim">点卡片筛选「待研判」</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never" class="stat-card clickable" @click="drillStatus('judged')">
          <div class="stat-head">
            <span class="card-title">已研判待处置</span>
            <el-tooltip placement="top">
              <template #content>
                <div class="tip">
                  <div>口径：<code>status=judged</code>，AI/规则已给结论但尚未执行或裁定。</div>
                  <div>按结论拆：{{ verdictBreakdown }}</div>
                  <div>空（0）的含义：没有待处置项（不等于数据没问题，可能只是都处理完了）。</div>
                  <div>下钻：点卡片 → 下方列表按该状态筛选。</div>
                </div>
              </template>
              <el-icon class="dim"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <div class="stat-num">{{ num(summary.by_status?.judged) }}<span class="stat-unit">条</span></div>
          <div class="dim">点卡片筛选「已研判」</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never" class="stat-card clickable" @click="drillRules">
          <div class="stat-head">
            <span class="card-title">启用规则 / 命中</span>
            <el-tooltip placement="top">
              <template #content>
                <div class="tip">
                  <div>口径：只统计 <code>enabled=true</code> 的规则；命中总数 = 这些规则
                    <code>hit_count</code> 之和（规则命中的发现直接判定，跳过 AI 调用）。</div>
                  <div>空（0）的含义：0 条规则 = 还没沉淀过规则（不是「没命中」）；命中 0 次 =
                    启用后尚未被任何发现命中。</div>
                  <div>下钻：点卡片 → 展开并定位到下方「治理规则」区。</div>
                </div>
              </template>
              <el-icon class="dim"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <div class="stat-num">{{ num(summary.rules_enabled) }}<span class="stat-unit">条 / 命中 {{ num(summary.rules_hits) }} 次</span></div>
          <div class="dim">点卡片展开「治理规则」</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-head">
            <span class="card-title">近 10 条治理事件</span>
            <el-tooltip placement="top">
              <template #content>
                <div class="tip">
                  <div>口径：<code>system_events(category=ai_governance)</code> 中 action 为
                    体检 / 研判 / 执行的事件，按 id 倒序取 10 条。</div>
                  <div>空（0）的含义：还没跑过体检/研判/执行（功能未被使用，不是故障）。</div>
                  <div>不下钻的原因：事件全量明细在「事务通知」页，本卡只给近 10 条概览，
                    避免同一数据两处重复展示（设计说明「禁止重复展示」）。</div>
                </div>
              </template>
              <el-icon class="dim"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <div class="ev-list">
            <div v-for="ev in recentEvents" :key="ev.id" class="ev-line">
              <span class="dim">{{ fmtShort(ev.created_at) }}</span>
              <el-tag size="small" :type="taskStatusType(ev.status)" effect="plain">{{ taskStatusText(ev.status) }}</el-tag>
              <span class="ev-title" :title="ev.summary || ev.title">{{ ev.title }}</span>
            </div>
            <div v-if="!recentEvents.length" class="dim">暂无治理事件</div>
          </div>
        </el-card>
      </el-col>
    </el-row>
    <!-- 执行分级来自服务端 disposition_levels（前端不另立映射表） -->
    <div class="dim mb-16">执行分级（服务端 <code>disposition_levels</code>）：{{ levelLegend }}</div>

    <!-- ② findings：筛选 + 列表（点行看详情） -->
    <el-card ref="findingsCard" shadow="never" class="mb-16">
      <template #header>
        <div class="flex-between">
          <span class="card-title">探测发现（findings）</span>
          <span class="dim">共 {{ findingsTotal }} 条</span>
        </div>
      </template>
      <div class="filter-row mb-8">
        <el-select v-model="filter.detector" size="small" style="width: 150px" clearable placeholder="探测器" @change="loadFindings(1)">
          <el-option v-for="(label, key) in DETECTOR_TEXT" :key="key" :label="label" :value="key" />
        </el-select>
        <el-select v-model="filter.status" size="small" style="width: 160px" clearable placeholder="状态" @change="loadFindings(1)">
          <el-option v-for="(label, key) in STATUS_TEXT" :key="key" :label="label" :value="key" />
        </el-select>
        <el-select v-model="filter.verdict" size="small" style="width: 130px" clearable placeholder="研判结论" @change="loadFindings(1)">
          <el-option v-for="(label, key) in VERDICT_TEXT" :key="key" :label="label" :value="key" />
        </el-select>
        <el-input v-model="filter.table_name" size="small" style="width: 160px" clearable placeholder="表名（精确匹配）"
          @keyup.enter="loadFindings(1)" @clear="loadFindings(1)" />
        <el-button size="small" @click="loadFindings(1)">查询</el-button>
        <el-button size="small" link @click="resetFilter">重置</el-button>
      </div>
      <el-table v-loading="loadingFindings" :data="findings" size="small" border stripe row-key="id"
        @row-click="openFinding">
        <el-table-column prop="id" label="id" width="66" />
        <el-table-column label="探测器" width="112">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ detectorText(row.detector) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="table_name" label="表" min-width="130" show-overflow-tooltip />
        <el-table-column prop="target" label="目标" min-width="150" show-overflow-tooltip />
        <el-table-column label="严重度" width="86">
          <template #default="{ row }">
            <el-tag size="small" :type="severityType(row.severity_raw)" effect="plain">
              {{ severityText(row.severity_raw) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="104">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)" effect="dark">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="研判结论" width="104">
          <template #default="{ row }">
            <el-tag v-if="row.verdict" size="small" :type="verdictType(row.verdict)" effect="plain">
              {{ verdictText(row.verdict) }}
            </el-tag>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="置信度" width="80">
          <template #default="{ row }">
            <span v-if="row.confidence === null || row.confidence === undefined" class="dim">—</span>
            <span v-else :class="{ 'txt-warn': row.confidence < 0.7 }">{{ row.confidence.toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="探测时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.detected_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-tooltip :disabled="canConfirm(row)" :content="confirmTip(row)" placement="top">
              <span>
                <el-button link type="primary" size="small" :disabled="!canConfirm(row)" @click.stop="doConfirm(row)">确认</el-button>
              </span>
            </el-tooltip>
            <el-tooltip :disabled="!execTip(row)" :content="execTip(row)" placement="top">
              <span>
                <el-button link type="primary" size="small" :disabled="!canExecute(row)" @click.stop="doExecute(row)">执行</el-button>
              </span>
            </el-tooltip>
            <el-tooltip :disabled="canDismiss(row)" content="仅「待研判 / 已研判」的发现可裁定" placement="top">
              <span>
                <el-button link type="warning" size="small" :disabled="!canDismiss(row)" @click.stop="doDismiss(row)">业务允许</el-button>
              </span>
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination class="mt-8" size="small" background layout="total, sizes, prev, pager, next"
        :total="findingsTotal" :current-page="filter.page" :page-size="filter.page_size"
        :page-sizes="[20, 50, 100]" @current-change="loadFindings" @size-change="onSizeChange" />
    </el-card>

    <!-- ③ 规则管理（折叠：低频动作，不占常驻视野） -->
    <el-card ref="rulesCard" shadow="never">
      <template #header>
        <div class="flex-between">
          <span class="card-title">治理规则（命中即直接判定，跳过 AI 调用）</span>
          <div class="flex-center gap-8">
            <el-button link type="primary" size="small" @click="rulesOpen = !rulesOpen">
              {{ rulesOpen ? '收起' : '展开' }}
            </el-button>
            <el-button size="small" type="primary" @click="openRuleDialog">新建规则</el-button>
          </div>
        </div>
      </template>
      <div v-show="rulesOpen">
        <el-table v-loading="loadingRules" :data="rules" size="small" border stripe row-key="id">
          <el-table-column prop="id" label="id" width="66" />
          <el-table-column label="探测器" width="112">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ detectorText(row.detector) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="table_name" label="表" width="140" show-overflow-tooltip />
          <el-table-column prop="target_pattern" label="目标匹配" min-width="140" show-overflow-tooltip />
          <el-table-column label="判定" width="104">
            <template #default="{ row }">
              <el-tag size="small" :type="verdictType(row.verdict)" effect="plain">{{ verdictText(row.verdict) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="动作 / 级别" width="176">
            <template #default="{ row }">
              <div>{{ dispositionText(row.disposition) }}</div>
              <div class="dim">{{ levelText(row.auto_level) }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="hit_count" label="命中" width="72" />
          <el-table-column prop="reason" label="判据" min-width="180" show-overflow-tooltip />
          <el-table-column label="启用" width="80">
            <template #default="{ row }">
              <el-switch :model-value="row.enabled" size="small" @change="(v) => toggleRule(row, v)" />
            </template>
          </el-table-column>
        </el-table>
        <div class="dim mt-8">共 {{ rulesTotal }} 条；规则只支持启用/禁用，不做删除（删掉无法解释历史结论）。</div>
      </div>
    </el-card>

    <!-- ④ 详情抽屉：证据在前、结论在后（先能自己核对，再看 AI 判断） -->
    <el-drawer v-model="drawerOpen" :title="drawerTitle" size="62%">
      <div v-if="detail" class="detail">
        <div class="flex-center gap-8 mb-12" style="flex-wrap: wrap">
          <el-tag :type="statusType(detail.status)" effect="dark">{{ statusText(detail.status) }}</el-tag>
          <el-tag size="small" effect="plain">{{ detectorText(detail.detector) }}</el-tag>
          <el-tag size="small" :type="severityType(detail.severity_raw)" effect="plain">
            严重度 {{ severityText(detail.severity_raw) }}
          </el-tag>
          <span class="strong">{{ detail.table_name }} · {{ detail.target }}</span>
          <el-tag v-if="detail.rule_id" size="small" type="success" effect="plain">规则 #{{ detail.rule_id }} 直接判定</el-tag>
        </div>

        <div class="sec-title">① 基础信息</div>
        <el-descriptions :column="2" border size="small" class="mb-12">
          <el-descriptions-item label="id">{{ detail.id }}</el-descriptions-item>
          <el-descriptions-item label="探测时间">{{ fmtTime(detail.detected_at) }}</el-descriptions-item>
          <el-descriptions-item label="研判时间">{{ fmtTime(detail.judged_at) }}</el-descriptions-item>
          <el-descriptions-item label="确认人 / 时间">
            {{ detail.confirmed_by || '—' }} · {{ fmtTime(detail.confirmed_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="执行时间">{{ fmtTime(detail.executed_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ fmtTime(detail.updated_at) }}</el-descriptions-item>
        </el-descriptions>

        <div class="sec-title">② 量化证据 metrics（先核对数字，再看结论）</div>
        <el-descriptions v-if="metricRows.length" :column="2" border size="small" class="mb-12">
          <el-descriptions-item v-for="kv in metricRows" :key="kv.k" :label="kv.k">{{ kv.v }}</el-descriptions-item>
        </el-descriptions>
        <div v-else class="dim mb-12">无量化证据（探测层未产出 metrics）</div>

        <div class="sec-title">③ 脱敏样本 samples（≤10 条，敏感字段已脱敏）</div>
        <div v-if="detail.detector === 'text_anomaly'" class="dim mb-8">
          样本值中的 <b>⏎</b> 表示换行符（否则看不出问题在哪）；<code>_length</code> 为值长度，
          <code>_kind</code> 为该行命中的判据（newline=含换行 / near_limit=贴列宽上限）
        </div>
        <el-table v-if="sampleColumns.length" :data="detail.samples" size="small" border max-height="260" class="mb-12">
          <el-table-column v-for="c in sampleColumns" :key="c" :prop="c" :label="c" min-width="110" show-overflow-tooltip />
        </el-table>
        <pre v-else-if="detail.samples && detail.samples.length" class="code-block mb-12">{{ prettyJson(detail.samples) }}</pre>
        <div v-else class="dim mb-12">无样本行</div>

        <div class="sec-title">④ 复现 SQL（可在库上直接执行核对上面的数字）</div>
        <div v-if="detail.repro_sql" class="mb-12">
          <el-button size="small" :icon="CopyDocument" @click="copySql(detail.repro_sql)">复制 SQL</el-button>
          <pre class="code-block mt-8">{{ detail.repro_sql }}</pre>
        </div>
        <div v-else class="dim mb-12">无复现 SQL</div>

        <div class="sec-title">⑤ AI 研判</div>
        <div v-if="detail.verdict" class="mb-12">
          <div class="flex-center gap-8 mb-8">
            <el-tag :type="verdictType(detail.verdict)" effect="dark">{{ verdictText(detail.verdict) }}</el-tag>
            <span class="dim">置信度 {{ detail.confidence === null || detail.confidence === undefined ? '—' : detail.confidence }}</span>
            <span v-if="detail.confidence !== null && detail.confidence < 0.7" class="txt-warn">置信度偏低，需人工重点核对</span>
          </div>
          <div class="reason">{{ detail.verdict_reason || '（无判据说明）' }}</div>
        </div>
        <div v-else class="dim mb-12">尚未研判（点右上「研判」触发）</div>

        <div class="sec-title">⑥ 建议动作</div>
        <div v-if="detail.suggestion" class="mb-12">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="action">{{ detail.suggestion.action || '—' }}</el-descriptions-item>
            <el-descriptions-item label="执行级别（服务端）">{{ levelText(detail.auto_level) }}</el-descriptions-item>
            <el-descriptions-item label="risk">{{ detail.suggestion.risk || '—' }}</el-descriptions-item>
            <el-descriptions-item label="requires_human_confirm">
              {{ detail.suggestion.requires_human_confirm === true ? '是' : '否' }}
            </el-descriptions-item>
            <el-descriptions-item label="影响面" :span="2">{{ detail.suggestion.impact || '—' }}</el-descriptions-item>
            <el-descriptions-item label="params" :span="2">
              {{ detail.suggestion.params && Object.keys(detail.suggestion.params).length ? prettyJson(detail.suggestion.params) : '—' }}
            </el-descriptions-item>
          </el-descriptions>
        </div>
        <div v-else class="dim mb-12">无建议动作（未研判或规则未给动作）</div>

        <div class="sec-title">⑦ 执行凭证 execute_result</div>
        <div v-if="detail.execute_result" class="mb-12">
          <el-alert type="warning" :closable="false" show-icon class="mb-8"
            :title="`影响 ${num(detail.execute_result.affected_rows)} 行${detail.execute_result.backup_table ? `；备份表 ${detail.execute_result.backup_table}` : '（无备份表，非删行动作）'}`" />
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item v-for="kv in execRows" :key="kv.k" :label="kv.k">{{ kv.v }}</el-descriptions-item>
          </el-descriptions>
        </div>
        <div v-else class="dim mb-12">尚未执行（无凭证）</div>

        <div class="flex-center gap-8" style="flex-wrap: wrap">
          <el-tooltip :disabled="canConfirm(detail)" :content="confirmTip(detail)" placement="top">
            <span>
              <el-button type="primary" size="small" :disabled="!canConfirm(detail)" @click="doConfirm(detail)">确认</el-button>
            </span>
          </el-tooltip>
          <el-tooltip :disabled="!execTip(detail)" :content="execTip(detail)" placement="top">
            <span>
              <el-button type="success" size="small" :disabled="!canExecute(detail)" @click="doExecute(detail)">执行</el-button>
            </span>
          </el-tooltip>
          <el-tooltip :disabled="canDismiss(detail)" content="仅「待研判 / 已研判」的发现可裁定" placement="top">
            <span>
              <el-button type="warning" size="small" :disabled="!canDismiss(detail)" @click="doDismiss(detail)">判定业务允许（沉淀规则）</el-button>
            </span>
          </el-tooltip>
        </div>
      </div>
      <el-empty v-else description="未选中发现" :image-size="60" />
    </el-drawer>

    <!-- ⑤ 新建规则（不可逆动作不在表单里：后端会拒绝） -->
    <el-dialog v-model="ruleDialogOpen" title="新建治理规则" width="580px">
      <el-form label-width="104px" size="small">
        <el-form-item label="探测器" required>
          <el-select v-model="ruleForm.detector" style="width: 100%">
            <el-option v-for="(label, key) in DETECTOR_TEXT" :key="key" :label="label" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="表" required>
          <el-input v-model="ruleForm.table_name" placeholder="物理表名，支持通配 *" />
        </el-form-item>
        <el-form-item label="目标匹配" required>
          <el-input v-model="ruleForm.target_pattern" placeholder="精确或正则片段，如 uq_wallet_* / *dedup*" />
        </el-form-item>
        <el-form-item label="判定" required>
          <el-select v-model="ruleForm.verdict" style="width: 100%">
            <el-option v-for="(label, key) in VERDICT_TEXT" :key="key" :label="label" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="判据说明" required>
          <el-input v-model="ruleForm.reason" type="textarea" :rows="3"
            placeholder="人读 + 引用规范条款或表说明（禁止编造业务结论）" />
        </el-form-item>
        <el-form-item label="建议动作">
          <el-select v-model="ruleForm.disposition" style="width: 100%">
            <el-option v-for="(label, key) in RULE_DISPOSITION_TEXT" :key="key" :label="label" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="执行级别">
          <el-select v-model="ruleForm.auto_level" style="width: 100%">
            <el-option v-for="(label, key) in RULE_LEVEL_TEXT" :key="key" :label="label" :value="key" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="ruleForm.enabled" />
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon
          title="规则是「命中即自动给建议」：不可逆动作（删行/建索引/改值）不得写成规则，后端会拒绝。" />
      </el-form>
      <template #footer>
        <el-button size="small" @click="ruleDialogOpen = false">取消</el-button>
        <el-button size="small" type="primary" :loading="ruleSubmitting" @click="submitRule">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, InfoFilled, QuestionFilled, Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import {
  confirmFinding,
  createJudge,
  createRule,
  createScan,
  dismissFinding,
  executeFinding,
  getFinding,
  getJudge,
  getScan,
  getSummary,
  listFindings,
  listRules,
  updateRule,
} from '@/api/aiDataGovernance'

// ── 枚举中文标签（仅文案；可用性一律以服务端 auto_level / requires_confirm 为准）──
const DETECTOR_TEXT = {
  unique_index_dup: '唯一索引重复',
  row_count_jump: '行数突变',
  fk_orphan: '外键孤儿',
  text_anomaly: '文本异常',
}
const STATUS_TEXT = {
  open: '待研判',
  judged: '已研判',
  resolved: '已执行',
  ignored: '已裁定业务允许',
  auto_closed: '重扫自动收敛',
}
const STATUS_TYPE = {
  open: 'warning',
  judged: 'primary',
  resolved: 'success',
  ignored: 'info',
  auto_closed: 'info',
}
const VERDICT_TEXT = {
  real_issue: '真问题',
  allowed_by_business: '业务允许',
  uncertain: '不确定',
}
const VERDICT_TYPE = {
  real_issue: 'danger',
  allowed_by_business: 'success',
  uncertain: 'warning',
}
const SEVERITY_TEXT = { info: '提示', warn: '警告', high: '严重' }
const SEVERITY_TYPE = { info: 'info', warn: 'warning', high: 'danger' }
// 执行级别文案（服务端返回的 auto_level 值域，见设计 §六）
const LEVEL_TEXT = {
  L0_readonly: 'L0 只读（自动）',
  L1_reversible: 'L1 可逆（自动）',
  L2_irreversible: 'L2 不可逆（须人工确认）',
}
// 规则表单只给非不可逆动作（后端拒绝 L2 动作写进规则，见设计 §六 红线）
const RULE_DISPOSITION_TEXT = {
  report_only: 'report_only（只报告）',
  mark_ignored: 'mark_ignored（标 ignored，可逆）',
  mark_dup_residual: 'mark_dup_residual（标 dup_residual，可逆）',
}
const RULE_LEVEL_TEXT = {
  L0_readonly: 'L0 只读（自动）',
  L1_reversible: 'L1 可逆（自动）',
}
const TASK_STATUS_TEXT = { running: '进行中', success: '成功', partial: '部分成功', failed: '失败' }
const TASK_STATUS_TYPE = { running: 'info', success: 'success', partial: 'warning', failed: 'danger' }

const summary = ref({})
const findings = ref([])
const findingsTotal = ref(0)
const loadingFindings = ref(false)
const filter = reactive({ detector: '', status: '', verdict: '', table_name: '', page: 1, page_size: 20 })

const rules = ref([])
const rulesTotal = ref(0)
const loadingRules = ref(false)
const rulesOpen = ref(false)

const detail = ref(null)
const drawerOpen = ref(false)

const task = ref(null)
const lastTask = ref(null)
const submitting = ref(false)
let pollTimer = null

const ruleDialogOpen = ref(false)
const ruleSubmitting = ref(false)
const ruleForm = reactive({
  detector: 'unique_index_dup',
  table_name: '',
  target_pattern: '',
  verdict: 'real_issue',
  reason: '',
  disposition: 'report_only',
  auto_level: 'L0_readonly',
  enabled: true,
})

const findingsCard = ref(null)
const rulesCard = ref(null)

// ── 派生展示 ────────────────────────────────────────────────────────
const recentEvents = computed(() => summary.value.recent_events || [])
const levelLegend = computed(() => {
  const m = summary.value.disposition_levels || {}
  const keys = Object.keys(m)
  return keys.length ? keys.map((k) => `${k}=${m[k]}`).join(' · ') : '（服务端未返回分级表）'
})
const verdictBreakdown = computed(() => {
  const m = summary.value.by_verdict || {}
  const keys = Object.keys(m)
  return keys.length ? keys.map((k) => `${verdictText(k)} ${m[k]}`).join(' / ') : '尚无已研判发现'
})
const metricRows = computed(() => kvRows(detail.value?.metrics))
const execRows = computed(() => kvRows(detail.value?.execute_result))
const sampleColumns = computed(() => {
  const rows = detail.value?.samples
  if (!Array.isArray(rows) || !rows.length) return []
  if (typeof rows[0] !== 'object' || rows[0] === null) return []
  const keys = []
  rows.forEach((r) => Object.keys(r || {}).forEach((k) => { if (!keys.includes(k)) keys.push(k) }))
  return keys
})
const drawerTitle = computed(() => (detail.value ? `发现 #${detail.value.id} 详情` : '发现详情'))
const lastTaskDetail = computed(() => {
  const d = lastTask.value?.detail
  if (!d || typeof d !== 'object') return ''
  const parts = Object.entries(d)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
    .map(([k, v]) => `${k}=${v}`)
  return parts.join(' · ')
})
const taskAlertType = computed(() => {
  const s = lastTask.value?.status
  return s === 'failed' ? 'error' : s === 'partial' ? 'warning' : 'success'
})

function num(v) {
  return v === null || v === undefined ? 0 : v
}
// 后端返回带时区偏移的 ISO 串，dayjs 按浏览器本地时区渲染（使用者为 Asia/Shanghai）
function fmtTime(t) {
  if (!t) return '—'
  const d = dayjs(t)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : '—'
}
function fmtShort(t) {
  if (!t) return '—'
  const d = dayjs(t)
  return d.isValid() ? d.format('MM-DD HH:mm') : '—'
}
function prettyJson(v) {
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}
// 任意 JSON 对象 → 键值行（嵌套值转 JSON 文本）
function kvRows(obj) {
  if (!obj || typeof obj !== 'object') return []
  return Object.entries(obj).map(([k, v]) => ({
    k,
    v: v === null || v === undefined ? '—' : typeof v === 'object' ? prettyJson(v) : String(v),
  }))
}
function detectorText(v) { return DETECTOR_TEXT[v] || v || '—' }
function statusText(v) { return STATUS_TEXT[v] || v || '—' }
function statusType(v) { return STATUS_TYPE[v] || 'info' }
function verdictText(v) { return VERDICT_TEXT[v] || v || '—' }
function verdictType(v) { return VERDICT_TYPE[v] || 'info' }
function severityText(v) { return SEVERITY_TEXT[v] || v || '—' }
function severityType(v) { return SEVERITY_TYPE[v] || 'info' }
function levelText(v) { return LEVEL_TEXT[v] || v || '—' }
function dispositionText(v) { return v || '—' }
function taskStatusText(v) { return TASK_STATUS_TEXT[v] || v || '—' }
function taskStatusType(v) { return TASK_STATUS_TYPE[v] || 'info' }

// ── 按钮可用性（严格按服务端字段，前端不定义 L2 映射）────────────────
function canConfirm(row) {
  return row.status === 'judged' && row.requires_confirm === true && !row.confirmed_at
}
function confirmTip(row) {
  if (!row || row.status !== 'judged') return '仅「已研判」的发现可确认'
  if (row.confirmed_at) return `已由 ${row.confirmed_by || '—'} 于 ${fmtTime(row.confirmed_at)} 确认`
  if (row.requires_confirm !== true) return '该动作非不可逆，无需确认'
  return ''
}
function canExecute(row) {
  return row.status === 'judged'
}
function execTip(row) {
  if (!row || row.status !== 'judged') return '仅「已研判」的发现可执行'
  if (row.requires_confirm === true && !row.confirmed_at) return '不可逆动作须先确认'
  return ''
}
function canDismiss(row) {
  return row.status === 'open' || row.status === 'judged'
}

// ── 数据加载 ────────────────────────────────────────────────────────
async function loadSummary() {
  const res = await getSummary()
  summary.value = res.data || {}
}

async function loadFindings(page) {
  if (page) filter.page = page
  loadingFindings.value = true
  try {
    const params = { page: filter.page, page_size: filter.page_size }
    if (filter.detector) params.detector = filter.detector
    if (filter.status) params.status = filter.status
    if (filter.verdict) params.verdict = filter.verdict
    if (filter.table_name.trim()) params.table_name = filter.table_name.trim()
    const res = await listFindings(params)
    findings.value = res.data.items || []
    findingsTotal.value = res.data.total || 0
  } finally {
    loadingFindings.value = false
  }
}

async function loadRules() {
  loadingRules.value = true
  try {
    const res = await listRules({ page: 1, page_size: 100 })
    rules.value = res.data.items || []
    rulesTotal.value = res.data.total || 0
  } finally {
    loadingRules.value = false
  }
}

async function reloadAll() {
  await Promise.all([loadSummary(), loadFindings()])
  if (rulesOpen.value) await loadRules()
}

function resetFilter() {
  filter.detector = ''
  filter.status = ''
  filter.verdict = ''
  filter.table_name = ''
  loadFindings(1)
}

function onSizeChange(size) {
  filter.page_size = size
  loadFindings(1)
}

// 概览卡下钻：把卡片口径变成列表筛选条件（带当前筛选条件，不跳空白页）
function drillStatus(status) {
  filter.status = status
  loadFindings(1)
  findingsCard.value?.$el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function drillRules() {
  rulesOpen.value = true
  loadRules()
  rulesCard.value?.$el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// ── 体检 / 研判：202 + 轮询（同一时刻只跑一个）────────────────────────
async function doScan() {
  submitting.value = true
  try {
    const res = await createScan()
    const id = res.data.scan_id
    lastTask.value = null
    task.value = { kind: 'scan', id }
    pollTask('scan', id)
  } finally {
    submitting.value = false
  }
}

async function doJudge() {
  submitting.value = true
  try {
    const res = await createJudge()
    const id = res.data.judge_id
    lastTask.value = null
    task.value = { kind: 'judge', id }
    pollTask('judge', id)
  } finally {
    submitting.value = false
  }
}

// 轮询任务状态（2s × 最多 90 次）；轮询请求 silentError 抑制报错弹层
function pollTask(kind, id) {
  const isScan = kind === 'scan'
  let n = 0
  pollTimer = setInterval(async () => {
    n += 1
    let data
    try {
      const res = isScan
        ? await getScan(id, { silentError: true })
        : await getJudge(id, { silentError: true })
      data = res.data
    } catch {
      // 任务被清理 / 网络抖动：停止轮询并提示，避免无限刷屏
      clearInterval(pollTimer)
      pollTimer = null
      task.value = null
      ElMessage.error('轮询任务状态失败，请点「刷新」查看结果')
      return
    }
    if (!data.finished && n <= 90) return
    clearInterval(pollTimer)
    pollTimer = null
    task.value = null
    if (!data.finished) {
      // 达到轮询上限仍未结束：任务可能仍在后台跑，别误报「完成」
      ElMessage.warning('轮询超时（约 3 分钟），任务可能仍在后台执行，请稍后点「刷新」查看结果')
      return
    }
    lastTask.value = data
    await reloadAll()
    if (data.status === 'failed') {
      ElMessage.error(`${data.title || '任务'}失败：${data.summary || '未知原因'}`)
    } else if (data.status === 'partial') {
      ElMessage.warning(`${data.title || '任务'}部分成功：${data.summary || ''}`)
    } else {
      ElMessage.success(`${data.title || '任务'}完成：${data.summary || ''}`)
    }
  }, 2000)
}

// ── findings 详情与操作 ─────────────────────────────────────────────
async function openFinding(row) {
  if (!row) return
  await openFindingById(row.id)
  drawerOpen.value = true
}

async function openFindingById(id) {
  const res = await getFinding(id)
  detail.value = res.data
}

async function refreshAfterAction(id) {
  await reloadAll()
  if (detail.value?.id === id) await openFindingById(id)
}

async function doConfirm(row) {
  try {
    const { value } = await ElMessageBox.prompt(
      '确认即承担该动作的后果（L2 不可逆动作确认后即可执行，执行前会强制备份）。可填备注：',
      '确认治理建议',
      { inputPlaceholder: '备注（可留空，如：已与业务确认该重复确为脏数据）', type: 'warning', inputValue: '' },
    )
    const res = await confirmFinding(row.id, { note: (value || '').trim() || null })
    ElMessage.success(`已确认（${res.data.confirmed_by} · ${fmtTime(res.data.confirmed_at)}）`)
    await refreshAfterAction(row.id)
  } catch {
    // 用户取消（ElMessageBox 以 'cancel'/'close' reject）或接口报错（拦截器已提示）
  }
}

async function doExecute(row) {
  // 服务端 requires_confirm === true 即不可逆（L2）→ 二次确认；前端不自行判定级别
  if (row.requires_confirm === true) {
    try {
      await ElMessageBox.confirm(
        `该动作级别为 ${levelText(row.auto_level)}（不可逆）：执行前会强制备份整行，但删行/改值不可自动回滚。确认执行？`,
        '不可逆动作二次确认',
        { type: 'warning', confirmButtonText: '确认执行' },
      )
    } catch {
      return // 用户取消
    }
  }
  const res = await executeFinding(row.id)
  const r = res.data.execute_result || {}
  // 让人看见「删了多少行、备份在哪」
  ElMessage.success(
    `已执行（${levelText(res.data.auto_level)}）：影响 ${num(r.affected_rows)} 行`
    + (r.backup_table ? `，备份表 ${r.backup_table}（可回滚）` : '，无备份表（非删行动作）'),
  )
  await refreshAfterAction(row.id)
}

async function doDismiss(row) {
  let reason
  try {
    const { value } = await ElMessageBox.prompt(
      '判定为「业务允许」后会沉淀一条治理规则，下次同类问题直接命中（不再调 AI）。请填写理由：',
      '判定业务允许（沉淀规则）',
      {
        inputPlaceholder: '如：实时问答没有 task_id 属业务允许（可空列多个 NULL）',
        inputValidator: (v) => (v && v.trim() ? true : '请填写理由（判据要可追溯）'),
        type: 'warning',
      },
    )
    reason = (value || '').trim()
  } catch {
    return // 用户取消
  }
  const res = await dismissFinding(row.id, { reason })
  ElMessage.success(
    res.data.created
      ? `已判定业务允许，并沉淀规则 #${res.data.rule_id}`
      : `已判定业务允许（规则 #${res.data.rule_id} 已存在，未重复沉淀）`,
  )
  rulesOpen.value = true
  await refreshAfterAction(row.id)
  await loadRules()
}

async function copySql(sql) {
  await navigator.clipboard.writeText(sql)
  ElMessage.success('复现 SQL 已复制')
}

// ── 规则管理 ────────────────────────────────────────────────────────
function openRuleDialog() {
  ruleForm.detector = 'unique_index_dup'
  ruleForm.table_name = ''
  ruleForm.target_pattern = ''
  ruleForm.verdict = 'real_issue'
  ruleForm.reason = ''
  ruleForm.disposition = 'report_only'
  ruleForm.auto_level = 'L0_readonly'
  ruleForm.enabled = true
  ruleDialogOpen.value = true
}

async function submitRule() {
  if (!ruleForm.detector || !ruleForm.table_name.trim() || !ruleForm.target_pattern.trim()
    || !ruleForm.verdict || !ruleForm.reason.trim()) {
    ElMessage.warning('探测器 / 表 / 目标匹配 / 判定 / 判据说明 为必填')
    return
  }
  ruleSubmitting.value = true
  try {
    const res = await createRule({
      detector: ruleForm.detector,
      table_name: ruleForm.table_name.trim(),
      target_pattern: ruleForm.target_pattern.trim(),
      verdict: ruleForm.verdict,
      reason: ruleForm.reason.trim(),
      disposition: ruleForm.disposition,
      auto_level: ruleForm.auto_level,
      enabled: ruleForm.enabled,
    })
    ElMessage.success(`规则已创建 #${res.data.id}`)
    ruleDialogOpen.value = false
    await Promise.all([loadRules(), loadSummary()])
  } finally {
    ruleSubmitting.value = false
  }
}

// 启用/禁用（PATCH）；规则不做删除，删掉无法解释历史结论
async function toggleRule(row, enabled) {
  const res = await updateRule(row.id, { enabled })
  Object.assign(row, res.data)
  await loadSummary()
  ElMessage.success(enabled ? '规则已启用（命中即直接判定）' : '规则已禁用（不再命中）')
}

onMounted(async () => {
  await reloadAll()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.flex-center { display: flex; align-items: center; }
.gap-8 { gap: 8px; }
.mt-8 { margin-top: 8px; }
.mb-8 { margin-bottom: 8px; }
.mb-12 { margin-bottom: 12px; }
.page-sub { margin-top: 4px; font-size: 12px; color: #909399; }
.card-title { font-weight: 600; font-size: 13px; }
.strong { font-weight: 600; }
.dim { color: #909399; font-size: 12px; }
.txt-warn { color: #e6a23c; }
.stat-card { min-height: 104px; }
.stat-head { display: flex; align-items: center; justify-content: space-between; }
.stat-num { font-size: 26px; font-weight: 600; margin: 6px 0 2px; }
.stat-unit { font-size: 12px; font-weight: 400; color: #909399; margin-left: 6px; }
/* 可下钻卡片：给出 hover 反馈（点了有反应） */
.clickable { cursor: pointer; }
.clickable:hover { border-color: #409eff; }
.filter-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ev-list { max-height: 76px; overflow: auto; }
.ev-line { display: flex; align-items: center; gap: 6px; font-size: 12px; line-height: 1.8; }
.ev-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sec-title { font-weight: 600; font-size: 13px; margin: 0 0 6px; }
.reason { font-size: 12px; line-height: 1.7; background: #f7f8fa; padding: 8px; border-radius: 4px; }
.code-block {
  margin: 0;
  padding: 8px;
  background: #f7f8fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.tip { max-width: 280px; font-size: 12px; line-height: 1.7; }
.rationale p { margin: 4px 0; font-size: 12px; line-height: 1.6; }
.rationale ul { margin: 4px 0 4px 16px; padding: 0; }
.rationale li { font-size: 12px; line-height: 1.6; }
</style>
