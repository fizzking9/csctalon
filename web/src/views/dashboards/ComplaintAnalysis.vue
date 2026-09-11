<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：客服主管 / 运营经理在**晨会前 5 分钟**判断「投诉是否失控、该先处理谁」。         │
  │       使用频率：日看概览、周看趋势、月做复盘。                                        │
  │ 逻辑：① 总量是**后果**指标，单看会骗人（客户放弃/渠道堵塞都会让量下降）→ 故概览        │
  │         除总量外同时给「登记 vs 处理」对称水位与「涉诉金额」，把量翻译成代价；          │
  │       ② 可控指标前置：升级率（内部/商户号投诉后升级到外部）是唯一 100% 可控的指标，     │
  │         「内部没处理好」的直接证据；                                                   │
  │       ③ 结论条先行：先告诉使用者"该干什么"，再看图验证；                               │
  │       ④ 时间控件全页唯一（改造前 5 个图表各有粒度开关 → 同屏可能 3 种粒度）。            │
  │ 口径：三源 = complaint_scope 分桶（内部/商户号/外部）；**(外)卡商完全独立，不入总量**   │
  │       （用户 2026-09-11 #53 决策）。时间过滤「登记 OR 完成」与 /summary 对齐。          │
  │       详见 docs/standards/投诉渠道口径与对账说明.md ·                                     │
  │       docs/standards/数据时间字段使用规范.md                                                │
  │ 取舍：❌ 不堆图 —— 概览的旧 12+ 图表已下移到渠道页；每个图表必须**指向一个动作**。        │
  │       ❌ 不做日粒度报警（日波动噪声大，按周/月对齐）。                                  │
  │       ❌ 不做比率指标（数据完整度不足，接口预留 ratios=null，见 #68）。                  │
  │       ❌ 不做措施时间轴（measures 数据未采集完整，见 #69）。                            │
  │ 工单：#67 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md                │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">投诉分析</div>
        <div class="page-sub">三源统一口径 · 决策概览 · 渠道明细 · 执行跟踪</div>
      </div>
      <!-- 统一时间条(#67 P1): 全页唯一时间控件，替换原 4 按钮 + 5 个图表粒度开关 -->
      <ComplaintTimeBar
        v-model:preset="rangePreset"
        v-model:grain="timeGrain"
        v-model:compare="compareMode"
        v-model:custom-range="customRange"
        @update:range="onRangeChange"
      />
    </div>

    <!-- 未加载时的提示 -->
    <section v-if="!loaded" class="empty-hint">
      <el-empty :image-size="80" description="">
        <template #description>
          <div class="empty-tip">
            <div class="empty-main">正在加载数据…</div>
            <div class="empty-sub">按需加载 · 概览由单一接口一次性返回</div>
          </div>
        </template>
      </el-empty>
    </section>

    <template v-if="loaded && data">
      <!-- ============ TAB: 决策概览(2026-09-11 #67 P1 瘦身重排) ============
           ★ 决策概览只回答三件事: ①失控了吗 ②为什么 ③该先处理谁
             旧版在此塞了 12+ 个图表(需下拉四屏)，分析图表已下移到渠道页。
             每个区块都必须指向一个动作，否则不应出现在概览。
           ★ 命中卡商独立口径(#53): 总量堆积只含内/商/外，(外)卡商单列一块。 -->
      <section v-if="activeTab === 'overview'" v-loading="overviewLoading" class="block-card">
        <!-- ① 结论条: 先告诉使用者"该干什么"，再看图验证 -->
        <ComplaintConclusionBar :items="conclusions" @drill="onConclusionDrill" />

        <!-- ② KPI 5 卡(带环比) —— 概览不堆卡；每张的口径由后端 kpi_meta 提供 -->
        <el-row :gutter="12" class="mb-16" style="margin-top:12px">
          <el-col v-for="k in (overviewData?.kpi || [])" :key="k.key" :xs="12" :sm="8" :md="4.8" style="flex:1 0 18%">
            <div class="kpi-card" :class="{ 'kpi-clickable': k.key === 'backlog' }"
                 @click="k.key === 'backlog' ? (topTab = 'execute') : null">
              <div class="kpi-label" style="color:#374151">
                {{ k.label }}
                <el-tooltip v-if="overviewData?.kpi_meta?.[k.key]" effect="dark" placement="top"
                            :content="overviewData.kpi_meta[k.key]">
                  <el-icon style="margin-left:3px;color:#b0b4bb;font-size:12px;vertical-align:-1px"><QuestionFilled /></el-icon>
                </el-tooltip>
              </div>
              <div class="kpi-value">{{ fmtKpi(k) }}<span v-if="k.unit === '条'" class="unit">条</span><span v-else-if="k.unit === '%'" class="unit">%</span></div>
              <div class="kpi-sub">
                <el-tag v-if="k.delta_pct != null || k.delta_pp != null" size="small" effect="plain" :type="kpiDeltaType(k)">
                  {{ kpiDeltaText(k) }}
                </el-tag>
                <span v-else class="dim">—</span>
                <span style="margin-left:6px" class="dim">环比</span>
                <span v-if="k.value != null && k.key === 'total'" style="margin-left:6px">
                  · 内/商/外 三源
                </span>
              </div>
            </div>
          </el-col>
        </el-row>

        <!-- ③ 四个趋势块 + 积压明细(概览的全部图表，一屏看完) -->
        <el-row :gutter="12" class="mb-16">
          <!-- ① 总量(分项堆积)趋势 -->
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">
                总量趋势（分项堆积）
                <span class="dim-text"> · 内/商/外 三源堆叠；(外)卡商独立，不计入</span>
              </div>
              <BaseChart v-if="ovStackOption" :option="ovStackOption" height="240px" />
              <el-empty v-else description="暂无数据" :image-size="36" />
            </div>
          </el-col>
          <!-- ② 登记 vs 处理(对称水位) -->
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">
                登记 vs 处理
                <span class="dim-text"> · 出水⊂进水，累计净水位 = 当前积压</span>
              </div>
              <BaseChart v-if="ovWaterOption" :option="ovWaterOption" height="240px" />
              <el-empty v-else description="暂无数据" :image-size="36" />
            </div>
          </el-col>
        </el-row>

        <el-row :gutter="12" class="mb-16">
          <!-- ③ 涉诉金额趋势 -->
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">
                涉诉金额趋势
                <span class="dim-text"> · 投诉涉及 SN 关联的线上退款（把量翻译成代价）</span>
              </div>
              <BaseChart v-if="ovRefundOption" :option="ovRefundOption" height="230px" />
              <el-empty v-else description="窗口内无涉诉退款" :image-size="36" />
            </div>
          </el-col>
          <!-- (外)卡商独立趋势 —— #53 不并入总量 -->
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="chart-title">
                (外)卡商登记趋势
                <span class="dim-text"> · 独立统计体系，不纳入投诉总量</span>
              </div>
              <BaseChart v-if="ovCardTrendOption" :option="ovCardTrendOption" height="230px" />
              <el-empty v-else description="窗口内无卡商投诉" :image-size="36" />
            </div>
          </el-col>
        </el-row>

        <!-- ④ 总积压 + 明细 -->
        <div class="block-head" style="margin-top:4px">
          <span class="block-title" style="font-size:14px">总积压</span>
          <span class="block-hint">截至窗口末仍未完成 · 超期阈值 &gt;7 天 · 不含(外)卡商</span>
          <div style="flex:1" />
          <el-button size="small" type="primary" plain @click="topTab = 'execute'">去处理 →</el-button>
        </div>
        <el-row :gutter="12" class="mb-16">
          <el-col :xs="24" :md="7">
            <div class="chart-card">
              <div class="snapshot-kpi-row">
                <div class="snapshot-kpi" style="color:#dc2626">
                  <div class="snapshot-kpi-v">{{ formatNum(overviewData?.backlog?.total) }}</div>
                  <div class="snapshot-kpi-l">积压总量</div>
                </div>
                <div class="snapshot-kpi" style="color:#ea580c">
                  <div class="snapshot-kpi-v">{{ formatNum(overviewData?.backlog?.overdue) }}</div>
                  <div class="snapshot-kpi-l">超期(&gt;7天)</div>
                </div>
                <div class="snapshot-kpi" style="color:#7c3aed">
                  <div class="snapshot-kpi-v">{{ formatNum(overviewData?.backlog?.unassigned) }}</div>
                  <div class="snapshot-kpi-l">待分派</div>
                </div>
                <div class="snapshot-kpi" style="color:#2563eb">
                  <div class="snapshot-kpi-v">{{ overviewData?.backlog?.max_aging ?? '—' }}</div>
                  <div class="snapshot-kpi-l">最旧(天)</div>
                </div>
              </div>
              <div class="snapshot-source-bars">
                <div v-for="src in (overviewData?.backlog?.by_source || [])" :key="src.name" class="snapshot-source-row">
                  <span class="snapshot-source-name">{{ src.name }}</span>
                  <div class="snapshot-source-track">
                    <div class="snapshot-source-fill"
                         :style="{ width: (overviewData.backlog.total ? Math.round(src.count / overviewData.backlog.total * 100) : 0) + '%', background: sourceBarColor(src.name) }" />
                  </div>
                  <span class="snapshot-source-cnt">{{ formatNum(src.count) }}</span>
                </div>
              </div>
            </div>
          </el-col>
          <el-col :xs="24" :md="8">
            <div class="chart-card">
              <div class="chart-title">积压龄期分布（积压多久了）</div>
              <BaseChart v-if="ovAgingOption" :option="ovAgingOption" height="180px" />
              <el-empty v-else description="无积压" :image-size="36" />
            </div>
          </el-col>
          <el-col :xs="24" :md="9">
            <div class="chart-card">
              <div class="chart-title">最旧积压 Top10（最旧优先）</div>
              <div v-if="backlogSnapshotItems.length" class="snapshot-list">
                <div v-for="(it, i) in backlogSnapshotItems" :key="i" class="snapshot-row">
                  <span class="snapshot-rank">{{ i + 1 }}</span>
                  <el-tag :type="sourceTagType(it.source)" size="small" effect="plain">{{ it.source }}</el-tag>
                  <span class="snapshot-no">{{ it.ticket_no }}</span>
                  <el-link v-if="it.device_sn" type="primary" underline="never" class="snapshot-sn" @click="openDeviceAudit(it.device_sn)">{{ it.device_sn }}</el-link>
                  <el-tag v-if="it.aging_days != null" size="small" :type="it.aging_days >= 8 ? 'danger' : it.aging_days >= 4 ? 'warning' : 'info'">{{ it.aging_days }}天</el-tag>
                </div>
              </div>
              <el-empty v-else description="无积压" :image-size="36" />
            </div>
          </el-col>
        </el-row>

        <!-- ══════════════ A5: 投诉登记时段热力（#72） ══════════════
             设计说明（Design Rationale）
             目的：**排班决策** —— 让班组长看清"什么时候来投诉最多"，据此安排人手。
             逻辑：投诉登记量随时段呈明显节律（实测双峰：09–12 时、19–21 时；
                   02–05 时谷底）。只看总量会漏掉节律 → 高峰期必然积压。
                   峰谷由后端按 P75/P25 **分位**判定（非固定阈值，自适应业务量级）。
             口径：按「登记时间」的北京时间小时×星期聚合（排班关心"何时有人来"，
                   不是"投诉属于哪天"）；不含 (外)卡商（#53 独立体系）。
                   详见 docs/standards/数据时间字段使用规范.md
             取舍：❌ 不做"建议排班人数" —— 需要人均处理产能数据（接待+投诉混合），
                   数据未齐；当前只给"量级分布"这一事实，由班组长自行判断。
                   ❌ 不做按周/月切换 —— 时段节律是**日周期**，跨周聚合会把节律抹平。
             工单：#72 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §四点五 -->
        <el-row v-if="ovHourOption || ovRepeatRows.length" :gutter="12" class="mb-16">
          <el-col :xs="24" :md="12">
            <div class="chart-card">
              <div class="flex-between">
                <div class="chart-title">
                  投诉登记时段热力
                  <span class="dim-text"> · 小时 × 星期（北京时间）· 服务排班</span>
                </div>
                <!-- ★ #73: 显式声明范围 —— 与上方 KPI 总量口径不同，必须写明避免误会 -->
                <el-tag size="small" type="info" effect="plain">仅工单侧 {{ formatNum(overviewData?.hour_heatmap?.total) }} 条</el-tag>
                <el-popover placement="bottom-end" :width="400" trigger="click">
                  <template #reference>
                    <el-icon style="color:#909399;cursor:help;font-size:14px"><QuestionFilled /></el-icon>
                  </template>
                  <div class="dr-body">
                    <div class="dr-title">时段热力 · 设计说明</div>
                    <div class="dr-sec"><b>给谁用</b><div>班组长 / 客服主管，用于排班与人力调配。</div></div>
                    <div class="dr-sec"><b>为什么用「登记时间」</b>
                      <div>排班关心的是"什么时候有人来投诉"（接待压力），而不是"投诉属于哪一天"。</div></div>
                    <div class="dr-sec"><b>峰谷怎么判</b>
                      <div>按 P75 / P25 分位（自适应业务量级，而非固定阈值）。<b>高峰</b>=需确保有人值守；<b>谷底</b>=可减配。</div></div>
                    <div class="dr-sec"><b>我们不做什么</b>
                      <div>· 不做"建议排班人数"：需人均处理产能数据，当前未齐 —— 只给量级事实，由班组长判断。<br>
                      · 不做周/月切换：时段节律是日周期，跨周聚合会抹平节律。</div></div>
                    <div class="dr-sec"><b>口径</b>
                      <div>北京时间（Asia/Shanghai）· <b>仅工单侧登记</b>（不含 315 补充表 —— 该表只有投诉日期、无投诉时刻）</div></div>
                    <div class="dr-sec"><b>为什么不含 315</b>
                      <div>315 补充表的日期字段<b>全为 00:00</b>（无小时），其入库时间是<b>采集批次时刻</b>（06 时集中入库）。
                        若拿来当「客户几点投诉」，会造出假的凌晨高峰 → <b>排班结论方向性错误</b>。
                        故本图只覆盖工单侧，并在此显式标注范围。</div></div>
                  </div>
                </el-popover>
              </div>
              <BaseChart v-if="ovHourOption" :option="ovHourOption" height="230px" />
              <el-empty v-else description="窗口内无登记数据" :image-size="36" />
              <div v-if="peakHint" class="peak-hint">{{ peakHint }}</div>
            </div>
          </el-col>

          <!-- ══════════════ A2: 反复投诉（#72） ══════════════
               设计说明（Design Rationale）
               目的：识别**「第一次没解决好」的设备**，比投诉总量更接近真相。
               逻辑：总量会被一次性事件稀释，反复投诉不会 ——
                     同一设备多次投诉 = 处理质量问题的直接证据；
                     其中"又反复、又已升级到外部"的设备是**最高优先级**跟进对象。
               口径：★★ 计数单位是「投诉事件（SN + 北京日期去重）」，**不是原始记录数**。
                     实测差异 3 倍（237 台 vs 78 台）—— 同一天多渠道登记属同一笔投诉，
                     不算反复（#55 口径）。用记录数会把"一天多渠道"误判为反复，方向性错误。
                     不含 (外)卡商（#53）。
               取舍：❌ 不做"自动判定是否同一问题" —— 需问题分类语义，当前 remark 质量不足；
                     ❌ 不做阈值自定义 UI —— 阈值由后端常量统一（避免每人一套口径）。
               工单：#72 · 设计：docs/arch/_pending/A-20260911-001-投诉分析整体重构.md §四点五 -->
          <el-col v-if="ovRepeatRows.length" :xs="24" :md="12">
            <div class="chart-card">
              <div class="flex-between">
                <div class="chart-title">
                  反复投诉
                  <span class="dim-text"> · 同一设备 ≥{{ repeatThreshold }} 次投诉事件</span>
                </div>
                <!-- ★ #73: 范围声明（同 A5） -->
                <el-tag size="small" type="info" effect="plain">仅工单侧</el-tag>
                <el-popover placement="bottom-end" :width="400" trigger="click">
                  <template #reference>
                    <el-icon style="color:#909399;cursor:help;font-size:14px"><QuestionFilled /></el-icon>
                  </template>
                  <div class="dr-body">
                    <div class="dr-title">反复投诉 · 设计说明</div>
                    <div class="dr-sec"><b>给谁用</b><div>客服主管，用于找出"处理没到位"的设备并定向跟进。</div></div>
                    <div class="dr-sec"><b>为什么要看这个</b>
                      <div>投诉<b>总量</b>会被一次性事件稀释；<b>反复投诉</b>不会 —— 同一设备多次投诉是"第一次没解决好"的直接证据。</div></div>
                    <div class="dr-sec"><b>★ 口径（关键）</b>
                      <div>计数单位是<b>投诉事件</b>（同一设备 + 同一北京日期算 1 次），<b>不是原始记录数</b>。
                        同一天多渠道登记的同一笔投诉只算 1 次（否则会把"一天多渠道"误判为反复，方向性错误）。</div></div>
                    <div class="dr-sec"><b>红色「已升级」是什么</b>
                      <div>该设备既有内部/商户号投诉、又有外部投诉 → 既反复、又已升级，<b>最高优先级</b>。</div></div>
                    <div class="dr-sec"><b>我们不做什么</b>
                      <div>· 不自动判定"是否为同一问题"：需问题分类语义，当前 remark 质量不足。<br>
                      · 不做阈值自定义：阈值由后端统一，避免每人一套口径。</div></div>
                    <div class="dr-sec"><b>范围（与 KPI 不同）</b>
                      <div><b>仅工单侧</b>（不含 315 补充表）。因此"事件总数"会小于上方 KPI 总量 —— 这是<b>口径差异</b>，非漏统。</div></div>
                  </div>
                </el-popover>
              </div>
              <div class="repeat-summary">
                <span class="repeat-kpi" style="color:#dc2626">{{ formatNum(ovRepeat.repeat_units) }}</span>
                <span class="dim">台设备反复投诉（占 {{ ovRepeat.rate ?? '—' }}%）· 贡献 {{ formatNum(ovRepeat.repeat_events) }} 次事件</span>
                <el-tag v-if="ovEscalatedCount" size="small" type="danger" effect="plain" style="margin-left:6px">
                  {{ ovEscalatedCount }} 台已升级外部
                </el-tag>
              </div>
              <div class="repeat-list">
                <div v-for="(it, i) in ovRepeatRows" :key="it.device_sn" class="repeat-row">
                  <span class="snapshot-rank">{{ i + 1 }}</span>
                  <el-link type="primary" underline="never" class="snapshot-sn"
                           @click="openDeviceAudit(it.device_sn)">{{ it.device_sn }}</el-link>
                  <el-tag size="small" :type="it.events >= 5 ? 'danger' : 'warning'" effect="plain">{{ it.events }} 次</el-tag>
                  <el-tag v-if="it.escalated" size="small" type="danger">已升级</el-tag>
                  <span class="dim-text repeat-days">{{ it.first_day }} ~ {{ it.last_day }}</span>
                </div>
              </div>
            </div>
          </el-col>
        </el-row>

        <!-- ★ 比率指标占位: 数据完整度不足, 本期不渲染(见 #68 专项研究)
             此处保留注释而非代码 —— 数据成熟后由 #68 决定是否加回 -->
      </section>

      <!-- ============ 渠道明细(2026-09-11 #67 P2 同构化) ============
           ★ 四个渠道由**同一组件**渲染(ComplaintChannelPanel)：
             改造前 外部/商户号 各写一套、内部与卡商缺失 →
             口径与配色不一致、新增渠道要复制整页、修 bug 要改四处。
           ★ 数据源: `/complaints/summary` 的 internal/merchant/external/card 四个分源对象
             (含 total/registered/handled/by_category/by_handler/trend)。
           ★ 明细: `/complaints/detail?source_type=`
           ★ 卡商渠道带「独立统计」标记 —— 不纳入投诉总量(#53)。 -->
      <section v-if="analysisTab === 'channel'" class="block-card">
        <ComplaintChannelPanel
          :key="channelTab"
          :source="channelTab"
          :src-data="channelSrcData"
          :rows="channelRows"
          :loading="channelLoading"
          :grain="effectiveGrain"
          @refresh="loadChannelDetail"
          @device="openDeviceAudit"
          @person="openPersonAudit"
        />
      </section>

      <!-- ★ 2026-09-11 (#67 P2) 已删除三个历史区块：
             「外部投诉重点」「商户号投诉」「受理分析」
             原因：三者与新的「渠道明细」同构面板**功能重复**（同指标两处各画一遍）。
             按界面设计规范「禁止重复展示」原则，保留渠道面板作为唯一真身。
             详见 docs/standards/界面设计说明规范.md §四 -->

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
          <el-table v-loading="backlogLoading" :data="backlogItems" border stripe size="small" max-height="420">
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
                <el-link v-if="row.device_sn" type="primary" underline="never" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="problem" label="投诉内容" min-width="180" show-overflow-tooltip />
            <el-table-column label="处理人" width="100">
              <template #default="{ row }">
                <el-link v-if="row.handler !== '未分配'" type="primary" underline="never" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
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
              size="small"
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
                <el-radio-button value="通过" />
                <el-radio-button value="有问题" />
                <el-radio-button value="需改进" />
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


      <!-- ============================================================
           ★ 2026-09-11 (#67 P3) 数据治理区块已外迁:
             「人工审核(#55)」与「采集质量(#60)」→ 系统管理 → 投诉采集质量
             原因: 治理工具的使用者是数据治理/运维，不是决策者；
                   混在分析页会让决策者在 8 个 tab 里找不到主线。
             新入口: /system/complaint-quality
           ============================================================ -->

      <!-- ============ TAB: 被投诉主体(2026-09-11 #67 P2 加筛选) ============
           ★ 用户需求: "主体 也是能提供不同的分项 筛选着来"
             理由: 主体榜若只有一种排序，无法支持不同管理动作 ——
               治「升级」→ 看 both(两源都有)
               治「成本」→ 看涉诉金额
               治「新冒头」→ 看环比增幅
           ★ 口径: 主体仅覆盖有主体字段的两源(商户号 registrant / 外部 315 主体)；
             内部投诉无主体字段不纳入主体统计。详见
             docs/standards/投诉渠道口径与对账说明.md -->
      <section v-if="activeTab === 'subject'" class="block-card">
        <div class="block-head">
          <span class="block-title">被投诉主体分析</span>
          <span class="block-hint">商户号投诉=工单被投诉商户主体 · 外部投诉=315公司主体/投诉店铺 · 内部投诉无主体字段不纳入 · 升级链反映「内部处理不好→商户号→外部」</span>
          <div style="flex:1" />
          <el-popover placement="bottom-end" :width="420" trigger="click">
            <template #reference>
              <el-icon style="color:#909399;cursor:help;font-size:15px"><QuestionFilled /></el-icon>
            </template>
            <div style="font-size:12px;line-height:1.6;color:#4b5563">
              <div style="font-weight:700;font-size:13px;color:#1f2937;margin-bottom:8px">主体分析 · 设计说明</div>
              <div style="margin-bottom:8px"><b>给谁用</b><div>客服主管 / 运营经理，用于定位「哪个被投诉主体需要干预」。</div></div>
              <div style="margin-bottom:8px"><b>为什么可筛选</b>
                <div>固定排序只能回答一个问题；不同管理动作需要不同视角 —— 治升级看「两源都有」，治成本看「涉诉金额」，治新冒头看「环比增幅」。</div></div>
              <div style="margin-bottom:8px"><b>为什么不包含内部投诉</b>
                <div>内部投诉工单无主体字段（registrant 是内部登记人），无法归属到被投诉主体。</div></div>
              <div style="margin-bottom:8px"><b>为什么不做什么</b>
                <div>· 不做主体合并（如把「云客猫」与「云客猫科技」归并为一家）—— 会掩盖真实差异，需业务先给归属规则。<br>
                · 不做主体级时间軸（措施回看）—— 依赖 measures 数据，见工单 #69。</div></div>
              <div><b>口径</b><div>主体归一规则见后端 <code>_norm_subject_entity</code>；渠道口径见 docs/standards/投诉渠道口径与对账说明.md</div></div>
            </div>
          </el-popover>
        </div>

        <!-- ★ 筛选条(#67 P2): 来源 × 排序 × TopN × 对比 -->
        <div class="filter-bar mb-16">
          <span class="filter-label">来源</span>
          <el-radio-group v-model="subjectFilter.source_type" size="small" @change="loadSubjectAnalysis">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="merchant">仅商户号</el-radio-button>
            <el-radio-button value="external">仅外部</el-radio-button>
            <el-radio-button value="both">升级主体</el-radio-button>
          </el-radio-group>
          <span class="filter-label">排序</span>
          <el-select v-model="subjectFilter.sort_by" size="small" style="width:130px" @change="loadSubjectAnalysis">
            <el-option label="投诉总量" value="total" />
            <el-option label="外部占比" value="external_ratio" />
            <el-option label="涉诉金额" value="refund_amount" />
            <el-option label="环比增幅" value="delta" />
          </el-select>
          <span class="filter-label">显示</span>
          <el-select v-model="subjectFilter.top_n" size="small" style="width:100px" @change="loadSubjectAnalysis">
            <el-option label="Top 10" :value="10" />
            <el-option label="Top 20" :value="20" />
            <el-option label="Top 50" :value="50" />
            <el-option label="全部" :value="0" />
          </el-select>
          <el-checkbox v-model="subjectFilter.comparePrev" size="small" @change="onSubjectCompareChange">环比</el-checkbox>
          <el-tag v-if="subjRankCount" size="small" effect="plain" type="info">
            命中 {{ formatNum(subjRankCount) }} 个主体
          </el-tag>
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
                  <el-radio-button value="day">按日</el-radio-button>
                  <el-radio-button value="week">按周</el-radio-button>
                  <el-radio-button value="month">按月</el-radio-button>
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
                  <el-radio-button value="day">按日</el-radio-button>
                  <el-radio-button value="week">按周</el-radio-button>
                  <el-radio-button value="month">按月</el-radio-button>
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
                <el-link type="primary" underline="never" class="subject-link" @click="openSubjectDetail(row.name)">
                  <span class="subject-name">{{ row.name }}</span>
                </el-link>
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
            <!-- ★ #67 P2: 环比列(勾选「环比」时才有值) -->
            <el-table-column v-if="subjectFilter.comparePrev" label="环比" width="110" align="right" sortable
                             :sort-method="(a, b) => (a.delta_pct ?? -1e9) - (b.delta_pct ?? -1e9)">
              <template #default="{ row }">
                <template v-if="row.delta_pct != null">
                  <span :style="{ color: row.delta_pct > 0 ? '#dc2626' : '#16a34a', fontWeight: 600 }">
                    {{ row.delta_pct > 0 ? '↑' : '↓' }} {{ Math.abs(row.delta_pct) }}%
                  </span>
                  <div class="dim-text" style="font-size:10px">对比期 {{ row.prev_total }}</div>
                </template>
                <span v-else class="dim" title="对比期无数据，不可比">—</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- F-P2-4: 主体下钻抽屉(点 Top 榜主体行 → 看该主体投诉明细) -->
        <el-drawer v-model="subjectDrawerVisible" :title="`被投诉主体明细 · ${currentSubject || ''}`" size="60%">
          <div class="sub-tip mb-16" style="color:#909399;font-size:12px">
            窗口内该主体的商户号 + 外部(315)投诉明细 · 点 SN/处理人可进一步审计
          </div>
          <el-table v-loading="subjectDetailLoading" :data="subjectDetailList" border stripe size="small" max-height="520">
            <el-table-column label="来源" width="92" align="center">
              <template #default="{ row }">
                <el-tag :type="sourceTagType(row.source)" size="small" effect="plain">{{ row.source }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="120" sortable :sort-method="(a,b)=>sortTimeStr(a.time,b.time)">
              <template #default="{ row }">{{ formatTime(row.time) }}</template>
            </el-table-column>
            <el-table-column label="分类" width="165" show-overflow-tooltip>
              <template #default="{ row }">
                <!-- ★ #65: 软纠正标注 —— 工单原登记「工信部投诉」,
                     但 SN 在 315 属监所/市监局类 → 已按「外部投诉」统计。 -->
                <el-tooltip
                  v-if="row.soft_corrected"
                  content="BMS 原登记为「工信部投诉」。因该 SN 在 315 文档属监所/市监局类，已按「外部投诉」统计（原数据未改）。"
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
            <el-table-column label="设备SN" width="140" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link v-if="row.device_sn" type="primary" underline="never" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</el-link>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column label="投诉内容" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">{{ row.problem || row.suppl_problem || '—' }}</template>
            </el-table-column>
            <el-table-column label="处理人" width="100" show-overflow-tooltip>
              <template #default="{ row }">
                <el-link v-if="row.handler && row.handler !== '未分配'" type="primary" underline="never" @click="openPersonAudit(row.handler)">{{ row.handler }}</el-link>
                <span v-else class="dim">未分配</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="row.status === '已完成' ? 'success' : row.status === '处理中' ? 'warning' : row.status === '待跟进' ? 'info' : 'danger'" size="small">{{ row.status || '—' }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!subjectDetailLoading && !subjectDetailList.length" description="该主体暂无明细(内部投诉无主体字段不纳入)" :image-size="50" />
        </el-drawer>
      </section>
    </template>
    <!-- 两级导航(#67 P1)
         一级 = 任务域(分析 / 执行跟踪)
         二级 = 分析内的视角(决策概览 / 渠道明细 / 主体分析)
         三级 = 渠道明细的子页(内部/商户号/外部/卡商 同构面板)
         ★ 为什么分两级而不是 8 个平铺 tab：
           "看趋势做判断"(分析) 与 "盯人干活"(执行跟踪) 是两种不同任务，
           平铺会让决策者在 8 个 tab 里迷路。 -->
    <div v-if="loaded" class="nav-wrap">
      <el-radio-group v-model="topTab" size="default">
        <el-radio-button value="analysis">分析</el-radio-button>
        <el-radio-button value="execute">
          执行跟踪
          <el-badge v-if="(b.total ?? 0) > 0" :value="b.total" type="danger" style="margin-left:6px" />
        </el-radio-button>
      </el-radio-group>

      <template v-if="topTab === 'analysis'">
        <span class="nav-sep" />
        <el-radio-group v-model="analysisTab" size="small">
          <el-radio-button value="overview">决策概览</el-radio-button>
          <el-radio-button value="channel">渠道明细</el-radio-button>
          <el-radio-button value="subject">主体分析</el-radio-button>
        </el-radio-group>
      </template>

      <template v-if="topTab === 'analysis' && analysisTab === 'channel'">
        <span class="nav-sep" />
        <el-radio-group v-model="channelTab" size="small">
          <el-radio-button value="internal">内部投诉</el-radio-button>
          <el-radio-button value="merchant">商户号投诉</el-radio-button>
          <el-radio-button value="external">外部投诉</el-radio-button>
          <el-radio-button value="card">(外)卡商</el-radio-button>
        </el-radio-group>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onBeforeUnmount, watch } from 'vue'
import { Search, Refresh, Download, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
dayjs.extend(isoWeek)
import BaseChart from '@/components/BaseChart.vue'
import ComplaintTimeBar from '@/components/dashboard/ComplaintTimeBar.vue'
import ComplaintConclusionBar from '@/components/dashboard/ComplaintConclusionBar.vue'
import ComplaintChannelPanel from '@/components/dashboard/ComplaintChannelPanel.vue'
import { complaintAnalysis, complaintOverview, complaintSummary, complaintDetail, complaintSupplementary, complaintBacklog, complaintBacklogExport, complaintSubjectAnalysis, createAudit } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'

const { openDeviceAudit, openPersonAudit } = useAudit()

const loaded = ref(false)
const loading = ref(false)

// ── 两级导航状态(#67 P1) ──
const topTab = ref('analysis')      // analysis(看趋势做判断) | execute(盯人干活)
const analysisTab = ref('overview') // overview | channel | subject
const channelTab = ref('external')  // internal | merchant | external | card

/** 由新导航**派生**旧 activeTab —— 保持既有 section 的 v-if 不动，降低回归风险。
 *  ⚠️ 这是过渡期写法：P2 渠道四页同构后，v-if 会由 channelTab 直接驱动。 */
const activeTab = computed(() => {
  if (topTab.value === 'execute') return 'backlog'
  if (analysisTab.value === 'subject') return 'subject'
  if (analysisTab.value === 'channel') {
    return channelTab.value === 'card' ? 'card' : channelTab.value
  }
  return 'overview'
})

// ── 统一时间条(#67 P1): 全页唯一时间状态 ──
const rangePreset = ref('last30')
const timeGrain = ref('auto')
const compareMode = ref('prev')
const customRange = ref([])
const range = ref({ start: '', end: '' })

function onRangeChange(r) {
  range.value = { ...r }
  loadData()
}

// ── 决策概览数据(#67 P1: 单请求出一屏) ──
const overviewData = ref(null)
const overviewLoading = ref(false)

const data = ref(null)

// F-P2-4: 被投诉主体下钻抽屉 state
const subjectDrawerVisible = ref(false)
const subjectDetailLoading = ref(false)
const currentSubject = ref('')
const subjectDetailList = ref([])

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
const subjectTrendGrain = ref('day')
const topSubjectTrendGrain = ref('day')
// F-P1-2: 明细 登记/处理 计数(标题展示)

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

// ── ★ 2026-09-11 (#67 P3): 数据治理 state 已随页面外迁 ──
//   「人工审核(#55)」与「采集质量(#60)」→ 系统管理 → 投诉采集质量
//   新入口: /system/complaint-quality (见 web/src/views/management/ComplaintDataQuality.vue)
// ── 被投诉主体分析(subject tab) ──
const subjectData = ref(null)
const subjectLoading = ref(false)
// ★ #67 P2: 主体筛选状态(默认与改造前一致: 全部 / 按总量 / Top20 / 不对比)
//   "Top20" 是后端不传 top_n 时的旧行为 —— 前端显式传 20 保持等价。
const subjectFilter = reactive({
  source_type: 'all',
  sort_by: 'total',
  top_n: 20,
  comparePrev: false,
})

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
  reg_handled: {
    total: { registered: 0, handled: 0 },
    '内部投诉': { registered: 0, handled: 0 },
    '商户号投诉': { registered: 0, handled: 0 },
    '外部投诉': { registered: 0, handled: 0 },
  },
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

// ★★ 2026-09-11 (#53): (外)卡商 **独立统计** ★★
//   用户: "卡商的投诉 不纳入总量的管理, 整体独立统计分析…全部单算"。
//   后端 `card` 对象与 `reg_handled` **平行**(非其一部分), 故单独取值。

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



const platformSummary = computed(() => {
  const p = s.value.platform_distribution || []
  return p.slice(0, 3).map(x => x.name).join(' / ') || '暂无'
})

const merchantEntitySummary = computed(() => {
  const p = s.value.merchant_entity_distribution || []
  return p.slice(0, 2).map(x => x.name).join(' / ') || '暂无'
})

// ── 请求参数(统一由时间条驱动, #67 P1 移除了页面内散落的粒度开关) ──
const params = computed(() => {
  const p = {}
  if (range.value.start) p.start = range.value.start
  if (range.value.end) p.end = range.value.end
  if (timeGrain.value && timeGrain.value !== 'auto') p.grain = timeGrain.value
  return p
})

// ── 决策概览数据加载(#67 P1) ──
const overviewParams = computed(() => ({
  ...params.value,
  compare: compareMode.value,
}))

async function loadOverview() {
  overviewLoading.value = true
  try {
    const res = await complaintOverview(overviewParams.value)
    overviewData.value = res.data || null
  } catch (e) {
    overviewData.value = null
    ElMessage.error('概览加载失败：' + (e?.message || e))
  } finally {
    overviewLoading.value = false
  }
}

// ── 渠道同构面板(#67 P2) ──
//   四个渠道共用一份数据契约：`/summary` 的 internal/merchant/external/card 分源对象。
//   ⚠️ 卡商(card) 是**独立体系**(#53)，不参与投诉总量，但它与其它三源**同结构**返回，
//      因此可以被同一个面板渲染 —— 这正是"同构"的价值。
const summaryData = ref(null)
const summarySrc = computed(() => summaryData.value || {})
const channelLoading = ref(false)
const channelRows = ref([])

/** 加载 /summary —— 渠道面板的分类/处理人/趋势数据源 */
async function loadSummary() {
  try {
    const res = await complaintSummary(params.value)
    summaryData.value = res.data || null
  } catch {
    summaryData.value = null
  }
}
/** 该渠道的分源数据对象 */
const channelSrcData = computed(() => {
  const d = summarySrc.value
  return d?.[channelTab.value] || {}
})
/** 趋势粒度：与顶部时间条的"自动"保持一致(≤31天=日 / 32~120天=周 / >120天=月) */
const effectiveGrain = computed(() => {
  if (timeGrain.value && timeGrain.value !== 'auto') return timeGrain.value
  const s = range.value.start; const e = range.value.end
  if (!s || !e) return 'day'
  const days = dayjs(e).diff(dayjs(s), 'day') + 1
  if (days <= 31) return 'day'
  if (days <= 120) return 'week'
  return 'month'
})

/** 渠道 → 明细接口的 source_type 映射
 *  ⚠️ 卡商在明细接口里的 source_type 是 `(外)卡商`(带括号)，与内部 tab key 不同 */
const CHANNEL_DETAIL_SOURCE = {
  internal: '内部投诉',
  merchant: '商户号投诉',
  external: '外部投诉',
  card: '(外)卡商',
}

async function loadChannelDetail() {
  channelLoading.value = true
  try {
    const p = { ...params.value, source_type: CHANNEL_DETAIL_SOURCE[channelTab.value] || '外部投诉', limit: 500 }
    const res = await complaintDetail(p)
    channelRows.value = res.data?.items || res.data?.records || []
  } catch (e) {
    channelRows.value = []
    ElMessage.error('明细加载失败：' + (e?.message || e))
  } finally {
    channelLoading.value = false
  }
}

// ── 概览: 结论条(点击下钻到对应页) ──
const conclusions = computed(() => overviewData.value?.conclusions || [])

function onConclusionDrill(drill) {
  const tab = drill?.tab
  if (!tab || tab === 'overview') return
  if (tab === 'backlog') { topTab.value = 'execute'; return }
  if (tab === 'subject') { topTab.value = 'analysis'; analysisTab.value = 'subject'; loadSubjectAnalysis(); return }
  if (['internal', 'merchant', 'external', 'card'].includes(tab)) {
    topTab.value = 'analysis'; analysisTab.value = 'channel'; channelTab.value = tab
    if (tab === 'external') loadExternalDetail()
    if (tab === 'merchant') loadMerchantDetail()
    return
  }
}

// ── 概览: KPI 卡(5 张 + 环比) ──
const ovKpi = computed(() => {
  const m = {}
  for (const k of (overviewData.value?.kpi || [])) m[k.key] = k
  return m
})

function kpiDeltaText(k) {
  if (!k) return ''
  if (k.delta_pp != null) {
    return `${k.delta_pp > 0 ? '↑' : '↓'} ${Math.abs(k.delta_pp)}pp`
  }
  if (k.delta_pct != null) {
    return `${k.delta_pct > 0 ? '↑' : '↓'} ${Math.abs(k.delta_pct)}%`
  }
  return ''
}

function kpiDeltaType(k) {
  if (!k) return 'info'
  const v = k.delta_pp != null ? k.delta_pp : k.delta_pct
  if (v == null) return 'info'
  return v > 0 ? 'danger' : 'success'
}

function fmtKpi(k) {
  if (!k || k.value == null) return '—'
  if (k.unit === '元') return `¥${Number(k.value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (k.unit === '%') return `${k.value}`
  return Number(k.value).toLocaleString('zh-CN')
}

// ── 概览: 总量堆积趋势(内/商/外 三源堆叠; 卡商独立不并入) ──
const ovStackOption = computed(() => {
  const rows = overviewData.value?.trend_stacked || []
  if (!rows.length) return null
  const srcs = ['内部投诉', '商户号投诉', '外部投诉']
  const colors = { '内部投诉': '#2563eb', '商户号投诉': '#7c3aed', '外部投诉': '#dc2626' }
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 20, bottom: 40, top: 12 },
    xAxis: {
      type: 'category',
      data: rows.map(r => r.label),
      axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 },
    },
    yAxis: { type: 'value', name: '条' },
    series: srcs.map(s => ({
      name: s, type: 'bar', stack: 'total',
      data: rows.map(r => r[s] || 0),
      itemStyle: { color: colors[s] }, barMaxWidth: 26,
    })),
  }
})

// ── 概览: (外)卡商独立趋势(不并入上方堆叠) ──
const ovCardTrendOption = computed(() => {
  const rows = overviewData.value?.card_trend || []
  if (!rows.length) return null
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 16, bottom: 28, top: 12 },
    xAxis: { type: 'category', data: rows.map(r => r.label), axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 } },
    yAxis: { type: 'value', name: '条' },
    series: [{
      type: 'line', smooth: true, symbol: 'circle', symbolSize: 5,
      data: rows.map(r => r.registered || 0),
      itemStyle: { color: '#059669' }, areaStyle: { opacity: 0.15 },
    }],
  }
})

// ── 概览: 登记 vs 处理(对称水位) ──
const ovWaterOption = computed(() => {
  const rows = overviewData.value?.trend_reg_handled || []
  if (!rows.length) return null
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 52, bottom: 40, top: 12 },
    xAxis: { type: 'category', data: rows.map(r => r.label), axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 } },
    yAxis: [
      { type: 'value', name: '条' },
      { type: 'value', name: '净水位', splitLine: { show: false } },
    ],
    series: [
      { name: '登记', type: 'bar', data: rows.map(r => r.registered || 0), itemStyle: { color: '#f87171' }, barMaxWidth: 18 },
      { name: '处理', type: 'bar', data: rows.map(r => r.handled || 0), itemStyle: { color: '#10b981' }, barMaxWidth: 18 },
      {
        name: '累计净水位', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 5,
        data: rows.map(r => r.cum_net || 0),
        itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' },
      },
    ],
  }
})

// ── 概览: 涉诉金额趋势(新增) ──
const ovRefundOption = computed(() => {
  const rows = overviewData.value?.trend_refund || []
  if (!rows.length) return null
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (ps) => {
        const i = ps[0]?.dataIndex ?? 0
        const r = rows[i] || {}
        return `${r.label}<br/>笔数: ${r.count || 0}<br/>金额: ¥${Number(r.amount || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      },
    },
    legend: { bottom: 0, left: 'center', itemWidth: 12, itemHeight: 12, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 52, bottom: 40, top: 12 },
    xAxis: { type: 'category', data: rows.map(r => r.label), axisLabel: { fontSize: 10, rotate: rows.length > 12 ? 45 : 0 } },
    yAxis: [
      { type: 'value', name: '笔' },
      { type: 'value', name: '金额(元)', splitLine: { show: false } },
    ],
    series: [
      { name: '退款笔数', type: 'bar', data: rows.map(r => r.count || 0), itemStyle: { color: '#fbbf24' }, barMaxWidth: 18 },
      {
        name: '退款金额', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'circle', symbolSize: 5,
        data: rows.map(r => Math.round((r.amount || 0) * 100) / 100),
        itemStyle: { color: '#ea580c' }, lineStyle: { width: 2 },
      },
    ],
  }
})

// ── 概览: 积压龄期分布 ──
const ovAgingOption = computed(() => {
  const rows = overviewData.value?.backlog?.by_aging || []
  if (!rows.length || rows.every(r => !r.count)) return null
  const colors = ['#2563eb', '#3b82f6', '#f59e0b', '#dc2626']
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 44, right: 20, bottom: 28, top: 12 },
    xAxis: { type: 'category', data: rows.map(r => r.name), axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '条' },
    series: [{
      type: 'bar', barMaxWidth: 34,
      data: rows.map((r, i) => ({ value: r.count, itemStyle: { color: colors[i % colors.length] } })),
      label: { show: true, position: 'top', fontSize: 11 },
    }],
  }
})

// ── A5 · 投诉登记时段热力图（#72） ──
//   x = 小时(0-23)，y = 星期(周一~周日)；色深 = 登记量
//   ★ 用 heatmap 而非折线：时段节律是"二维"的（小时 × 星期），
//     折线只能表一维，会丢掉"周末/工作日差异"这一半信息。
const ovHourOption = computed(() => {
  const h = overviewData.value?.hour_heatmap
  if (!h || !h.total) return null
  const hmap = {}
  for (const c of h.by_hour || []) hmap[c.hour] = c.count
  const data = []   // echarts heatmap: [x(小时), y(星期索引), value]
  let max = 1
  for (const c of h.cells || []) {
    if (c.count > max) max = c.count
  }
  for (const c of h.cells || []) {
    data.push([c.hour, c.weekday - 1, c.count])  // weekday 1..7 → 0..6
  }
  const WD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
  return {
    tooltip: {
      position: 'top',
      formatter: (p) => `${WD[p.value[1]]} ${String(p.value[0]).padStart(2, '0')}时<br/>登记 ${p.value[2]} 条`,
    },
    grid: { left: 44, right: 16, top: 10, bottom: 46 },
    xAxis: {
      type: 'category', data: Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0')),
      splitArea: { show: true }, axisLabel: { fontSize: 9, interval: 1 },
      name: '小时', nameLocation: 'middle', nameGap: 26, nameTextStyle: { fontSize: 10 },
    },
    yAxis: {
      type: 'category', data: WD, splitArea: { show: true }, axisLabel: { fontSize: 10 },
    },
    visualMap: {
      min: 0, max, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
      itemWidth: 10, itemHeight: 60, textStyle: { fontSize: 9 },
      inRange: { color: ['#f1f5f9', '#bfdbfe', '#60a5fa', '#2563eb', '#1e3a8a'] },
    },
    series: [{
      type: 'heatmap', data, label: { show: false },
      emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.3)' } },
    }],
  }
})

/** 峰谷提示文案（把"热力图"翻译成"结论" —— 否则使用者要自己看色块推断） */
const peakHint = computed(() => {
  const h = overviewData.value?.hour_heatmap
  if (!h || !h.total) return ''
  const fmt = (xs) => (xs || []).map(x => `${String(x).padStart(2, '0')}时`).join(' ')
  const parts = []
  if (h.peak_hours?.length) parts.push(`高峰（需值守）：${fmt(h.peak_hours)}`)
  if (h.valley_hours?.length) parts.push(`谷底（可减配）：${fmt(h.valley_hours)}`)
  return parts.join(' · ')
})

// ── A2 · 反复投诉（#72） ──
//   ★ 后端已按「事件口径」算好（SN + 北京日期去重），前端只渲染，不做二次计数。
const ovRepeat = computed(() => overviewData.value?.repeat || {})
const ovRepeatRows = computed(() => (ovRepeat.value.items || []).slice(0, 6))
const repeatThreshold = computed(() => ovRepeat.value.threshold ?? 3)
const ovEscalatedCount = computed(() => (ovRepeat.value.items || []).filter(i => i.escalated).length)

// ── 来源分布饼图(含兜底: source_distribution 为空时用 summary 三源数) ──
const sourcePieColors = ['#2563eb', '#7c3aed', '#dc2626']




// ── 外部投诉平台分布饼图 ──
const platformColors = ['#dc2626', '#ea580c', '#ca8a04', '#65a30d', '#0891b2', '#7c3aed', '#db2777', '#4f46e5']




// ── 商户号被投诉主体趋势(堆叠面积, 商户号内细分; 支持按日/周/月汇聚) ──
const merchantEntityColors = ['#7c3aed', '#ea580c', '#0891b2', '#ca8a04', '#dc2626', '#2563eb', '#16a34a', '#db2777', '#4f46e5']



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
    // ★ 概览改用专用接口单请求出一屏(#67 P1):
    //   原概览需并发 8 个请求且各接口时间口径不一致 → 同屏数字打架。
    loadOverview()
    // 渠道面板的数据源(分类/处理人/趋势)
    loadSummary()
    loadChannelDetail()
    // 同时加载明细
    loadBacklog()
    loadSubjectAnalysis()
  } finally {
    loading.value = false
  }
}

async function loadSubjectAnalysis() {
  subjectLoading.value = true
  try {
    // ★ #67 P2: 主体筛选参数(来源/排序/TopN/环比)
    //   comparePrev 勾选时才传 compare=prev —— 环比需算对比期，不必每次默认开启。
    //   ⚠️ 环比需要「时间窗口」才能算对比期 —— 主体页默认无窗口时
    //     自动补一个近 30 天窗口，否则 delta 恒为 None(实测踩到: 列全是 —)。
    const base = { ...params.value }
    if (subjectFilter.comparePrev && !base.start && !base.end) {
      const today = dayjs()
      base.start = today.subtract(29, 'day').format('YYYY-MM-DD')
      base.end = today.format('YYYY-MM-DD')
    }
    const p = {
      ...base,
      source_type: subjectFilter.source_type,
      sort_by: subjectFilter.sort_by,
      top_n: subjectFilter.top_n,
      compare: subjectFilter.comparePrev ? 'prev' : 'none',
    }
    const res = await complaintSubjectAnalysis(p)
    subjectData.value = res.data || {}
  } finally {
    subjectLoading.value = false
  }
}

/** 环比开关变化 —— 立即重拉(排序若选 delta 也依赖它) */
function onSubjectCompareChange() {
  loadSubjectAnalysis()
}
/** 筛选后命中的主体总数(供前端显示 "命中 N 个主体") */
const subjRankCount = computed(() => subjectData.value?.filters?.ranked_total ?? 0)

// F-P2-4: 主体下钻 - 打开抽屉并拉该主体明细(detail 支持 subject 归一化过滤)
async function openSubjectDetail(name) {
  if (!name) return
  currentSubject.value = name
  subjectDrawerVisible.value = true
  subjectDetailLoading.value = true
  subjectDetailList.value = []
  try {
    const p = { ...params.value, limit: 500, include_supplementary: true, subject: name }
    const res = await complaintDetail(p)
    // 兼容 detail 返回 items; 未命中 source 参数时全三源(内部投诉无主体不返回)
    subjectDetailList.value = res.data?.items || []
  } finally {
    subjectDetailLoading.value = false
  }
}

// 时间字符串排序(ISO/MM-DD HH:mm), 空值排后
function sortTimeStr(a, b) {
  const va = a || ''
  const vb = b || ''
  if (va === vb) return 0
  if (!va) return 1
  if (!vb) return -1
  return va < vb ? -1 : 1
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
  // ★ 初始区间(#67 P1): 默认近 30 天(含今日)。
  //   ⚠️ 必须在此初始化 —— 时间条是受控组件，不自己持有区间；
  //      缺了它 range 恒为空 → 概览/环比/明细全部拿不到窗口(实测踩到)。
  const today = dayjs()
  range.value = {
    start: today.subtract(29, 'day').format('YYYY-MM-DD'),
    end: today.format('YYYY-MM-DD'),
  }
  loadData()
})

// ★ 切换渠道 → 只重新拉该渠道明细(概览/summary 已按区间一次性加载，不必重取)。
//   去掉原来「每切一个 tab 触发全页重载」的行为，减少无效请求。
watch(channelTab, () => {
  if (analysisTab.value === 'channel') loadChannelDetail()
})
// 从概览下钻到渠道时，也要拉明细(此时 channelTab 可能未变化)
watch(analysisTab, () => {
  if (analysisTab.value === 'channel') loadChannelDetail()
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
.filter-label { font-size: 12px; color: #6b7280; }

.kpi-card { border-radius: 10px; padding: 12px 14px; min-height: 68px; display: flex; flex-direction: column; gap: 4px; border: 1px solid rgba(148,163,184,0.15); }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 11px; color: #6b7280; }

.block-card { margin-bottom: 16px; }
.block-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.block-title { font-size: 16px; font-weight: 600; color: #1f2937; }
.block-hint { font-size: 12px; color: #9ca3af; }
.nav-wrap { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; padding: 8px 12px; background: #fafbfc; border: 1px solid #ebeef5; border-radius: 8px; }
.nav-sep { width: 1px; height: 18px; background: #dcdfe6; }
.nav-wrap :deep(.el-radio-button__inner) { border-radius: 0; }
.nav-wrap :deep(.el-radio-group:first-child .el-radio-button:first-child .el-radio-button__inner) { border-radius: 4px 0 0 4px; }
.nav-wrap :deep(.el-radio-group:first-child .el-radio-button:last-child .el-radio-button__inner) { border-radius: 0 4px 4px 0; }
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
/* ── A5/A2 (#72) 新增区块样式 ── */
.peak-hint { font-size: 11px; color: #6b7280; margin-top: 6px; line-height: 1.6; }
.repeat-summary { display: flex; align-items: baseline; gap: 6px; font-size: 12px; margin-bottom: 8px; flex-wrap: wrap; }
.repeat-kpi { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
.repeat-list { max-height: 168px; overflow-y: auto; }
.repeat-row { display: flex; align-items: center; gap: 6px; padding: 5px 2px; border-bottom: 1px dashed #f1f3f5; }
.repeat-days { font-size: 10px; }
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
  .nav-wrap { padding: 6px 8px; gap: 6px; }
  .nav-sep { display: none; }
}
</style>