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
  │       积压明细筛选含设备维度（设备类型/套餐/商户号/激活时间段，走 devices 表，#273）。   │
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
          <!-- #273: 设备维度筛选(口径=devices 表, 与退款明细同套参数) -->
          <el-select v-model="backlogFilters.device_type" size="small" placeholder="设备类型" clearable filterable style="width:150px" @change="backlogPage = 1; loadBacklog()">
            <el-option v-for="t in backlogDimOptions.device_types || []" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
          <el-select v-model="backlogFilters.combo" size="small" placeholder="套餐" clearable filterable style="width:160px" @change="backlogPage = 1; loadBacklog()">
            <el-option v-for="c in backlogDimOptions.combos || []" :key="c" :label="c" :value="c" />
          </el-select>
          <el-select v-model="backlogFilters.merchant" size="small" placeholder="商户号" clearable filterable style="width:160px" @change="backlogPage = 1; loadBacklog()">
            <el-option v-for="m in backlogDimOptions.merchants || []" :key="m" :label="m" :value="m" />
          </el-select>
          <el-date-picker v-model="backlogFilters.activeRange" type="daterange" size="small" range-separator="~" start-placeholder="激活起" end-placeholder="激活止" value-format="YYYY-MM-DD" style="width:210px" @change="backlogPage = 1; loadBacklog()" />
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
import { useRoute } from 'vue-router'
import { Search, Refresh, Download, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import isoWeek from 'dayjs/plugin/isoWeek'
import BaseChart from '@/components/BaseChart.vue'
import ComplaintTimeBar from '@/components/dashboard/ComplaintTimeBar.vue'
import ComplaintConclusionBar from '@/components/dashboard/ComplaintConclusionBar.vue'
import ComplaintChannelPanel from '@/components/dashboard/ComplaintChannelPanel.vue'
import { complaintAnalysis, complaintOverview, complaintSummary, complaintDetail, complaintSupplementary, complaintBacklog, complaintBacklogExport, complaintSubjectAnalysis, createAudit } from '@/api/complaint'
import { useAudit } from '@/composables/useAudit'

import { useComplaintAnalysis } from './useComplaintAnalysis'

const {
  openDeviceAudit,
  openPersonAudit,
  route,
  loaded,
  loading,
  topTab,
  analysisTab,
  channelTab,
  activeTab,
  rangePreset,
  timeGrain,
  compareMode,
  customRange,
  range,
  onRangeChange,
  overviewData,
  overviewLoading,
  data,
  subjectDrawerVisible,
  subjectDetailLoading,
  currentSubject,
  subjectDetailList,
  isMobile,
  _mq,
  _onMediaChange,
  subjectTrendGrain,
  topSubjectTrendGrain,
  backlogData,
  backlogLoading,
  backlogExporting,
  backlogPage,
  backlogPageSize,
  backlogTotal,
  backlogFilters,
  auditVisible,
  auditSubmitting,
  auditForm,
  attributions,
  subjectData,
  subjectLoading,
  subjectFilter,
  b,
  backlogItems,
  oldestAging,
  bySourceCount,
  sourceBarColor,
  sourceRatioPct,
  backlogSnapshotItems,
  backlogSourceAgingOption,
  doingPct,
  backlogAgingOption,
  reasonColors,
  reasonTagType,
  backlogReasonOption,
  subj,
  subjectRanking,
  extRatioColor,
  subjectSeverityOption,
  escalationChainOption,
  subjectTrendOption,
  topSubjectColors,
  topSubjectTrendOption,
  s,
  complaintWaterOption,
  platformSummary,
  merchantEntitySummary,
  params,
  overviewParams,
  loadOverview,
  summaryData,
  summarySrc,
  channelLoading,
  channelRows,
  loadSummary,
  channelSrcData,
  effectiveGrain,
  CHANNEL_DETAIL_SOURCE,
  loadChannelDetail,
  conclusions,
  onConclusionDrill,
  ovKpi,
  kpiDeltaText,
  kpiDeltaType,
  fmtKpi,
  ovStackOption,
  ovCardTrendOption,
  ovWaterOption,
  ovRefundOption,
  ovAgingOption,
  ovHourOption,
  peakHint,
  ovRepeat,
  ovRepeatRows,
  repeatThreshold,
  ovEscalatedCount,
  sourcePieColors,
  platformColors,
  merchantEntityColors,
  sourceTagType,
  formatNum,
  formatTime,
  aggregateSeries,
  aggregateMultiSeries,
  loadData,
  loadSubjectAnalysis,
  onSubjectCompareChange,
  subjRankCount,
  openSubjectDetail,
  sortTimeStr,
  loadBacklog,
  handleBacklogExport,
  SOURCE_TO_AUDIT,
  openAudit,
  submitAudit,
} = useComplaintAnalysis()
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