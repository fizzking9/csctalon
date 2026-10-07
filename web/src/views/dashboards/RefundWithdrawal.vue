<!--
  ┌─────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：替退款条线主管回答「退到钱包的钱沉淀了多少（要不要催提）？三路退款各自多慢（该找谁）？」      │
  │       —— 沉淀是可控指标（催提可动），时效是归因指标（分路定位责任方），沉淀前置。                  │
  │ 逻辑：先「库存」（钱包沉淀 2 卡）→ 再「流速」（时效 3 卡 + ≤1 天占比 + 出款总量）→                  │
  │       趋势与水位两图看走向 → 沉淀清单逐笔催提（清单是沉淀卡的真身明细）。                          │
  │ 口径：来源 GET /analytics/refund-withdrawal/overview · /daily-split · /sleeping 及                 │
  │       /analytics/cash/wallet-vs-withdraw（服务：backend/app/services/analytics/refund_chain.py）。│
  │       三路窗口均按 D4 实际出款时刻落筛选区间：A钱包=D2入钱包→D4用户提现；                           │
  │       B直退=D1发起→D4接口回调（回调时间戳晚于发起超改单污染阈值 MAX_B_DAYS=3 天则回落按发起计）；   │
  │       D手动=登记 register_time→实际退款 op_time（排除已治理重复/忽略记录）。                        │
  │       ⚠️ 钱包沉淀 2 卡为全局口径（不随窗口变化），与沉淀清单（窗口内转入）数字含义不同。            │
  │ 取舍：① 不做「线下/未填写」退款路 —— 非实际现金出款，后端 _classify_method 已剔除；                │
  │       ② 不做 C 闪猫路时效 —— C 无时效字段，只在决策概览/对账复核出现；                              │
  │       ③ ≤1 天提现占比用「日均值换算」估算（≈100/加权均天数），不做逐笔精确占比 ——                  │
  │          现有接口不返回逐笔天数分布，精确口径需后端新增字段；                                       │
  │       ④ 两张趋势图不做图内点击下钻 —— 趋势是切面而非构成，构成明细由 KPI 卡承接。                  │
  │ 工单：#205                                                                                         │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="timeliness">
    <!-- 筛选: 日期范围 + 趋势粒度 -->
    <section class="block-card mb-16">
      <div class="filter-bar">
        <span class="block-title" style="margin:0">提现时效
          <!-- ★ #205 页面层可见设计说明（界面设计说明规范 §二·方案A）：含「取舍」段 -->
          <el-tooltip effect="dark" placement="bottom-start" raw-content
            content="<b>这个区块为什么这样设计</b><br/><b>给谁</b>：退款条线主管（按需查阅）<br/><b>回答什么</b>：钱包沉淀了多少（催提决策）· 三路退款各自多慢（归因到责任方）<br/><b>组织逻辑</b>：先库存（沉淀）后流速（时效），沉淀是可控指标前置<br/><b>口径</b>：三路窗口均按 D4 实际出款时刻落筛选区间（非发起时刻）；A钱包=入钱包→用户提现，B直退=发起→接口回调，D手动=登记→实际退款；金额单位为元<br/>详见 backend/app/services/analytics/refund_chain.py<br/><br/><b>⚠️ 取舍（不做什么及原因）</b><br/>① 不做线下/未填写退款路 —— 非实际现金出款，后端已剔除<br/>② 不做 C 闪猫路时效 —— C 无时效字段<br/>③ ≤1 天占比为日均值换算的估算值，非逐笔统计<br/>④ 趋势图不做图内点击下钻 —— 构成明细由 KPI 卡承接">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-2px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <el-date-picker
          v-model="filters.range"
          type="daterange"
          range-separator="至"
          start-placeholder="开始"
          end-placeholder="结束"
          value-format="YYYY-MM-DD"
          :shortcuts="dateShortcuts"
          size="small"
          @change="loadAll"
        />
        <el-radio-group v-model="grain" size="small" @change="renderTrend">
          <el-radio-button value="day">按日</el-radio-button>
          <el-radio-button value="week">按周</el-radio-button>
          <el-radio-button value="month">按月</el-radio-button>
        </el-radio-group>
        <el-button type="primary" size="small" :icon="Search" :loading="loading" @click="loadAll">分析</el-button>
      </div>
    </section>

    <!-- KPI 条
         ★ #205 看板卡片双强制（规范 §二/§三）：每卡 ① 有 ⓘ 说明（来源/口径/时点/空的含义）② 可下钻。
           沉淀 2 卡 → 本页沉淀清单（同页 §3.2③）；时效/占比/出款 5 卡 → 决策概览「六、退款明细」
           （带当前窗口，路径筛选无法透传的降级说明见 drillToOverviewDetail 注释）。 -->
    <section class="block-card mb-16">
      <div class="block-head"><span class="block-title">钱包沉淀与退款时效
        <!-- ★ #205 组级数据说明：三路窗口口径 + 「空」的含义（规范 §二） -->
        <el-tooltip effect="dark" placement="top" raw-content
          content="<b>来源</b>：/analytics/refund-withdrawal/overview · /daily-split（服务 refund_chain.py）<br/><b>口径</b>：三路有时效退款 A钱包/B直退/D手动，窗口按 <b>D4 实际出款时刻</b>落筛选区间（非发起时刻）；金额单位为元<br/><b>时点</b>：点「分析」重算；后端缓存 60s<br/><b>「空」的含义</b>：时效卡显示 0.0 = 窗口内该路无出款（非真实 0 天）<br/><b>下钻</b>：点任一卡片 → 沉淀清单（同页）或决策概览退款明细（带当前窗口）<br/><b>⚠️ 注意</b>：钱包沉淀/沉淀金额两卡为<b>全局口径</b>，不随筛选窗口变化（见各卡 ⓘ）">
          <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
        </el-tooltip>
      </span><span class="block-hint">三路有时效的退款: 直接线上退款 · 钱包提现 · 手动商户号操作 · 沉淀高=用户不提现(资金滞留风险)</span></div>
      <el-row :gutter="12">
        <el-col :xs="12" :sm="8" :md="4">
          <!-- ★ #205 钱包沉淀卡: 全局沉淀口径(overview.wallet_sleep, 不随窗口变化)。
               下钻 → 本页沉淀清单(§3.2③)。⚠️ 降级说明: 清单按筛选窗口内「转入」逐笔计算,
               与本卡的全局口径数字不一定相等(本卡=全部历史转入未提现), 已在两处ⓘ写明。 -->
          <div class="kpi-card kpi-drillable" style="background:#fef2f2"
               title="点击查看窗口内「沉淀清单」逐笔" @click="scrollToSleeping">
            <div class="kpi-label" style="color:#dc2626">钱包沉淀
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund-withdrawal/overview 的 wallet_sleep<br/><b>口径</b>：全量「转入提现账户」退款工单中，至今未匹配到「用户提现」的笔数占比（按退款单号、兜底设备SN+金额匹配，排除失败/冻结提现）<br/><b>时点</b>：点「分析」重算；⚠️ 全局口径，<b>不随筛选窗口变化</b><br/><b>「空」的含义</b>：0 = 转入均已提现，或暂无转入工单<br/><b>下钻</b>：点卡片 → 下方「沉淀清单」逐笔（清单按窗口内转入计算，与本卡数字含义不同）">
                <span class="kpi-info" @click.stop>
                  <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
                </span>
              </el-tooltip>
            </div>
            <div class="kpi-value">{{ sleep.count }}<span class="unit">笔</span></div>
            <div class="kpi-sub">占转入 {{ sleep.pct }}%</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <!-- ★ #205 沉淀金额卡: 同 wallet_sleep.still_in_wallet_amount(全局口径, 元)。下钻 → 本页沉淀清单 -->
          <div class="kpi-card kpi-drillable" style="background:#fef2f2"
               title="点击查看窗口内「沉淀清单」逐笔" @click="scrollToSleeping">
            <div class="kpi-label" style="color:#dc2626">沉淀金额
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund-withdrawal/overview 的 wallet_sleep.still_in_wallet_amount<br/><b>口径</b>：同「钱包沉淀」卡——未匹配到用户提现的转入工单金额合计（元）<br/><b>时点</b>：点「分析」重算；⚠️ 全局口径，<b>不随筛选窗口变化</b><br/><b>「空」的含义</b>：¥0 = 无沉淀资金（全部已提现）或暂无转入<br/><b>下钻</b>：点卡片 → 下方「沉淀清单」逐笔">
                <span class="kpi-info" @click.stop>
                  <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
                </span>
              </el-tooltip>
            </div>
            <div class="kpi-value">¥{{ formatMoney(sleep.amount) }}</div>
            <div class="kpi-sub">钱仍躺在提现钱包</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <!-- ★ #205 钱包提现时效卡: paths.A_wallet.avg_days_2_4(D2入钱包→D4提现)。下钻 → 决策概览退款明细 -->
          <div class="kpi-card kpi-drillable" style="background:#fff7ed"
               title="点击查看决策概览「退款明细」（带当前窗口）" @click="drillToOverviewDetail">
            <div class="kpi-label" style="color:#ea580c">钱包提现时效(均)
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund-withdrawal/overview 的 paths.A_wallet.avg_days_2_4 / median_days_2_4<br/><b>口径</b>：入钱包（D2 订单退费转入）→ 用户提现（D4）的平均天数；仅统计匹配到入钱包时刻的 A 路出款<br/><b>窗口</b>：按 D4 实际出款时刻落筛选区间<br/><b>时点</b>：点「分析」重算；缓存 60s<br/><b>「空」的含义</b>：0.0 = 窗口内无 A 路出款（或均无可计算的入钱包时刻），非真实 0 天<br/><b>下钻</b>：点卡片 → 决策概览「六、退款明细」（带当前窗口，可再按路径=钱包提现筛选）">
                <span class="kpi-info" @click.stop>
                  <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
                </span>
              </el-tooltip>
            </div>
            <div class="kpi-value">{{ fmtD(timeliness.avg) }}<span class="unit">天</span></div>
            <div class="kpi-sub">入钱包→提现, 中位 {{ fmtD(timeliness.median) }} 天</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <!-- ★ #205 直接退款时效卡: paths.B_direct.avg_days(D1发起→D4回调)。下钻 → 决策概览退款明细 -->
          <div class="kpi-card kpi-drillable" style="background:#eff6ff"
               title="点击查看决策概览「退款明细」（带当前窗口）" @click="drillToOverviewDetail">
            <div class="kpi-label" style="color:#2563eb">直接退款时效(均)
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund-withdrawal/overview 的 paths.B_direct.avg_days<br/><b>口径</b>：发起退款（D1）→ 实际到账（D4 ≈ 接口回调成功时间）的平均天数；无独立到账字段，以 updated_at_raw 近似——若晚于发起超改单污染阈值（后端 MAX_B_DAYS=3 天）则回落按发起时刻计<br/><b>窗口</b>：按 D4 落筛选区间<br/><b>「空」的含义</b>：0.0 = 窗口内无 B 路直退<br/><b>下钻</b>：点卡片 → 决策概览「六、退款明细」（带当前窗口，可再按路径=线上直退筛选）">
                <span class="kpi-info" @click.stop>
                  <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
                </span>
              </el-tooltip>
            </div>
            <div class="kpi-value">{{ fmtD(timeliness.directAvg) }}<span class="unit">天</span></div>
            <div class="kpi-sub">线上直退, 发起→实际到账</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <!-- ★ #205 手动退款时效卡: cashout_split.manual_refund.avg_days(登记→操作)。下钻 → 决策概览退款明细 -->
          <div class="kpi-card kpi-drillable" style="background:#f5f3ff"
               title="点击查看决策概览「退款明细」（带当前窗口）" @click="drillToOverviewDetail">
            <div class="kpi-label" style="color:#7c3aed">手动退款时效(均)
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund-withdrawal/overview 的 cashout_split.manual_refund.avg_days（manual_refunds 台账）<br/><b>口径</b>：登记时间（register_time）→ 实际退款操作时间（op_time）的平均天数；仅 is_refunded='是'，并排除已治理重复（dup_residual）与人工忽略（ignored）记录<br/><b>窗口</b>：按 op_time（实际出款时刻）落筛选区间<br/><b>「空」的含义</b>：0.0 = 窗口内无 D 路手动退款<br/><b>下钻</b>：点卡片 → 决策概览「六、退款明细」（带当前窗口，可再按路径=手动退款筛选）">
                <span class="kpi-info" @click.stop>
                  <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
                </span>
              </el-tooltip>
            </div>
            <div class="kpi-value">{{ fmtD(timeliness.manualAvg) }}<span class="unit">天</span></div>
            <div class="kpi-sub">商户号手动操作, 登记→实际退款</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <!-- ★ #205 ≤1天提现占比卡: 前端由 daily-split 各日 wallet_avg_days_2_4 按笔数加权换算(≈100/均值), 属估算口径。
               下钻 → 决策概览退款明细(同窗口, 构成=A 路出款)。 -->
          <div class="kpi-card kpi-drillable" style="background:#f0fdf4"
               title="点击查看决策概览「退款明细」（带当前窗口）" @click="drillToOverviewDetail">
            <div class="kpi-label" style="color:#16a34a">≤1天提现占比
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund-withdrawal/daily-split 的 wallet_avg_days_2_4（<b>前端换算的估算值</b>：各日平均拖延按笔数加权后 ≈100/均值）<br/><b>口径</b>：窗口内钱包提现「当天到账即提现」占比的估算——<b>非逐笔统计</b>，精确口径需后端返回逐笔天数分布<br/><b>「空」的含义</b>：0% = 窗口内无 A 路钱包出款数据（100% = 加权平均 ≤1 天）<br/><b>下钻</b>：点卡片 → 决策概览「六、退款明细」（带当前窗口，可再按路径=钱包提现筛选）">
                <span class="kpi-info" @click.stop>
                  <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
                </span>
              </el-tooltip>
            </div>
            <div class="kpi-value">{{ timeliness.pct1 }}<span class="unit">%</span></div>
            <div class="kpi-sub">入钱包后当天提现</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="4">
          <!-- ★ #205 区间出款笔数卡: daily-split 三路合计(按 D4 出款日聚合)。下钻 → 决策概览退款明细 -->
          <div class="kpi-card kpi-drillable" style="background:#f8fafc"
               title="点击查看决策概览「退款明细」（带当前窗口）" @click="drillToOverviewDetail">
            <div class="kpi-label" style="color:#475569">区间出款笔数
              <el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund-withdrawal/daily-split 各日 total_count/total_amount 汇总<br/><b>口径</b>：窗口内三路实际出款合计（A钱包提现 + B直退 + D手动），按 D4 实际出款日聚合；金额单位为元<br/><b>「空」的含义</b>：0 笔 / ¥0 = 窗口内无任何实际出款<br/><b>下钻</b>：点卡片 → 决策概览「六、退款明细」（带当前窗口）">
                <span class="kpi-info" @click.stop>
                  <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:12px"><QuestionFilled /></el-icon>
                </span>
              </el-tooltip>
            </div>
            <div class="kpi-value">{{ timeliness.count }}<span class="unit">笔</span></div>
            <div class="kpi-sub">三路合计 ¥{{ formatMoney(timeliness.amount) }}</div>
          </div>
        </el-col>
      </el-row>
    </section>

    <!-- 图1: 三路出款趋势
         ★ #205 ⓘ 数据说明 + ⚠️ 不下钻声明（规范 §3.4）: 图为「按 D4 出款日」的每日切面趋势，
           构成明细无法按"某个柱"再切（明细接口按窗口/路径筛选，无单日出款明细独立视图），
           构成查询由上方各 KPI 卡承接 → 图内不做点击下钻。 -->
    <section class="block-card mb-16">
      <div class="block-head">
        <span class="block-title">三路实际出款趋势
          <el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund-withdrawal/daily-split<br/><b>口径</b>：按 D4 实际出款日逐天拆分三路（柱=直退/钱包提现/手动退款堆叠笔数）；线=各日钱包平均拖延天数（入钱包→用户提现，日/周/月粒度切换时按笔数加权平均）<br/><b>「空」的含义</b>：显示「所选区间无出款数据」= 窗口内无任何实际出款<br/><b>⚠️ 不下钻</b>：图为每日切面趋势，无单日出款明细视图，构成明细由上方 KPI 卡承接">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="block-hint">柱=每日实际出款笔数(直退/钱包提现/手动退款堆叠) · 线=钱包平均拖延天数(入钱包→用户提现)</span>
      </div>
      <BaseChart v-if="trendOption.series?.length" :option="trendOption" height="300px" />
      <el-empty v-else description="所选区间无出款数据" :image-size="60" />
    </section>

    <!-- 图2: 沉淀与清理
         ★ #205 ⓘ 数据说明 + ⚠️ 不下钻声明（规范 §3.4）: 数据来自现金流服务 wallet-vs-withdraw,
           与本页 KPI（退款链路服务）不同源; 其天然下钻去向是「现金流分析」页, 但该页不读取
           URL 窗口参数, 跳转无法携带时间窗口（规范 §3.3 禁止裸跳）→ 图内不做点击下钻,
           沉淀构成由「钱包沉淀」卡 → 本页沉淀清单承接。 -->
    <section class="block-card mb-16">
      <div class="block-head">
        <span class="block-title">钱包资金流入 vs 提现流出
          <el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/cash/wallet-vs-withdraw（现金流服务，按 op_time Asia/Shanghai 逐日聚合）<br/><b>口径</b>：柱=「订单退费转入」入钱包金额 / 「用户提现」实际流出金额（提现排除失败/冻结备注）；线=当日净水位（转入−提现）<br/><b>「空」的含义</b>：显示「暂无钱包水位数据」= 窗口内无转入也无提现流水<br/><b>⚠️ 不下钻</b>：现金流分析页不接收 URL 窗口参数，跳转无法带上下文；沉淀构成由「钱包沉淀」卡 → 本页沉淀清单承接">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="block-hint">柱=转入(订单退费转入)/提现 · 线=当日净水位(转入−转出)</span>
      </div>
      <BaseChart v-if="waterOption.series?.length" :option="waterOption" height="300px" />
      <el-empty v-else description="暂无钱包水位数据" :image-size="60" />
    </section>

    <!-- 沉淀清单
         ★ #205 ⓘ 数据说明: 清单是「钱包沉淀」卡在同窗口下的逐笔真身(口径差异见ⓘ)。
           下钻: SN 点击 → 设备审计(既有交互, 保留)。 -->
    <section ref="sleepSectionRef" class="block-card">
      <div class="block-head">
        <span class="block-title">沉淀清单（转入钱包未提现）
          <el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund-withdrawal/sleeping<br/><b>口径</b>：筛选窗口内「转入提现账户」且至今未匹配到「用户提现」的退款单逐笔（匹配=退款单号命中提现备注，兜底设备SN+金额且提现时间≥发起时间；排除失败/冻结提现）<br/><b>时点</b>：按转入时间（created_at_raw）落窗口——⚠️ 与「钱包沉淀」卡的全局口径不同，数字不必相等<br/><b>「空」的含义</b>：0 条 = 窗口内转入均已提现，或窗口内无转入<br/><b>下钻</b>：点击设备 SN → 设备审计页（既有交互）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip>
        </span>
        <span class="block-hint">共 {{ sleepTotal }} 条 · 点击 SN 可查看设备审计 · 拖延越久越需催提</span>
      </div>
      <el-table :data="sleepList" border stripe size="small" max-height="420">
        <el-table-column prop="refund_no" label="退款单号" width="160" fixed show-overflow-tooltip />
        <el-table-column prop="device_sn" label="设备SN" width="140" fixed>
          <template #default="{ row }">
            <span v-if="row.device_sn" class="sn-chip" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</span>
            <span v-else style="color:#9ca3af">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="refund_amount" label="金额(¥)" width="110" align="right" sortable>
          <template #default="{ row }">{{ formatMoney(row.refund_amount) }}</template>
        </el-table-column>
        <el-table-column prop="operator" label="操作人" width="100" show-overflow-tooltip />
        <el-table-column prop="d1_refund_created" label="转入时间" width="160" show-overflow-tooltip />
        <el-table-column prop="waiting_days" label="已滞留(天)" width="100" align="right" sortable>
          <template #default="{ row }">
            <el-tag size="small" :type="row.waiting_days > 7 ? 'danger' : row.waiting_days > 3 ? 'warning' : 'info'">
              {{ row.waiting_days ?? '—' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div class="pag-wrap">
        <el-pagination
          v-model:current-page="sleepPage"
          v-model:page-size="sleepPageSize"
          :total="sleepTotal"
          :page-sizes="[30, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          size="small"
          @current-change="loadSleeping"
          @size-change="sleepPage = 1; loadSleeping()"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Search, QuestionFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import BaseChart from '@/components/BaseChart.vue'
import { refundWithdrawal as rwApi, cash } from '@/api/analytics'
import { useAudit } from '@/composables/useAudit'
import { formatMoney } from '@/utils/format'

const router = useRouter()
const { openDeviceAudit } = useAudit()

// ★ #205 下钻: 时效/占比/出款类 KPI 卡 → 决策概览 Tab 的「六、退款明细」区块
//   （真实逐笔明细, API /analytics/refund-withdrawal/detail, 含路径/涉诉/操作人/原因筛选）。
//   时间窗口逐字透传: 本组件 filters.range → query start/end（RefundDashboard 挂载时还原为自定义窗口并自动装载）。
//   ⚠️ 降级说明（规范 §3.3）: 明细的「路径」筛选(A/B/D)无法经 URL 透传 —— DecisionOverview 未提供 path
//   入参, 为不改其对外行为, 降级为「全部路径」落地; 用户可在明细区块的路径下拉一键再选
//   （线上直退=B / 钱包提现=A / 手动退款=D）。落地后明细区块在页面下方(区块6), 不做自动滚动。
function drillToOverviewDetail() {
  router.push({
    path: '/dashboards/refund',
    query: { tab: 'overview', start: filters.range?.[0] || '', end: filters.range?.[1] || '' },
  })
}

// ★ #205 下钻(同页 §3.2③): 沉淀 2 卡 → 本页沉淀清单区块。
//   ⚠️ 口径差异已在注释与两卡 ⓘ 写明: 卡=全局沉淀(全部历史转入未提现), 清单=筛选窗口内转入逐笔。
const sleepSectionRef = ref(null)
function scrollToSleeping() {
  sleepSectionRef.value?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

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

const today = new Date()
const last30 = new Date(today.getTime() - 29 * 86400000)
const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`

const filters = reactive({ range: [fmt(last30), fmt(today)] })
const grain = ref('day')
const loading = ref(false)

const dateShortcuts = [
  { text: '最近7天', value: () => { const e = new Date(); const s = new Date(e - 6*864e5); return [s, e] } },
  { text: '最近30天', value: () => { const e = new Date(); const s = new Date(e - 29*864e5); return [s, e] } },
  { text: '最近90天', value: () => { const e = new Date(); const s = new Date(e - 89*864e5); return [s, e] } },
]

const overview = ref({ wallet_sleep: {}, paths: {} })
const dailySplit = ref([])
const walletWater = ref([])
const sleepList = ref([])
const sleepTotal = ref(0)
const sleepPage = ref(1)
const sleepPageSize = ref(50)

function fmtD(v) {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toFixed(1)
}

const sleep = computed(() => {
  const ws = overview.value?.wallet_sleep || {}
  return { count: ws.still_in_wallet_count || 0, pct: ws.still_in_wallet_pct ?? 0, amount: ws.still_in_wallet_amount || 0 }
})

const timeliness = computed(() => {
  const a = overview.value?.paths?.A_wallet || {}
  const b = overview.value?.paths?.B_direct || {}
  const m = overview.value?.cashout_split?.manual_refund || {}
  const d24 = dailySplit.value || []
  const totalCount = d24.reduce((s, r) => s + (r.total_count || 0), 0)
  const totalAmount = d24.reduce((s, r) => s + (r.total_amount || 0), 0)
  // ≤1天提现占比: 从日粒度数据按 wallet_count 加权估算
  let le1 = 0
  const weights = []
  d24.forEach(r => {
    if (r.wallet_avg_days_2_4 != null && r.wallet_count) {
      weights.push({ w: r.wallet_count, v: r.wallet_avg_days_2_4 })
    }
  })
  if (weights.length) {
    const totalW = weights.reduce((s, x) => s + x.w, 0)
    const avg = weights.reduce((s, x) => s + x.v * x.w, 0) / totalW
    // avg=0 也视为当天提现(占比100%), 避免 1/0 → Infinity 显示异常
    le1 = avg > 1 ? Math.max(0, Math.round(100 / avg)) : 100
  }
  return {
    avg: a.avg_days_2_4 ?? 0, median: a.median_days_2_4 ?? 0,
    directAvg: b.avg_days ?? 0, manualAvg: m.avg_days ?? 0,
    pct1: le1,
    count: totalCount, amount: totalAmount,
  }
})

// ---- 图1: 三路实际出款趋势 (按日/周/月聚合) ----
const trendOption = computed(() => {
  const agg = aggregateTrend()
  if (!agg.labels.length) return {}
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: ['直退笔数', '钱包提现笔数', '手动退款笔数', '平均拖延(天)'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: agg.labels, axisLabel: { fontSize: 11, rotate: agg.labels.length > 12 ? 30 : 0 } },
    yAxis: [
      { type: 'value', name: '笔数', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '天数', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      { name: '直退笔数', type: 'bar', stack: 'out', data: agg.directCounts, barMaxWidth: 22, itemStyle: { color: '#93c5fd', borderRadius: [2, 2, 0, 0] } },
      { name: '钱包提现笔数', type: 'bar', stack: 'out', data: agg.walletCounts, barMaxWidth: 22, itemStyle: { color: '#fbbf24' } },
      { name: '手动退款笔数', type: 'bar', stack: 'out', data: agg.manualCounts, barMaxWidth: 22, itemStyle: { color: '#c4b5fd', borderRadius: [4, 4, 0, 0] } },
      { name: '平均拖延(天)', type: 'line', yAxisIndex: 1, data: agg.avgDays, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#f59e0b' }, lineStyle: { width: 2.5, color: '#f59e0b' } },
    ],
  }
})

function aggregateTrend() {
  const rows = dailySplit.value || []
  if (!rows.length) return { labels: [], directCounts: [], walletCounts: [], manualCounts: [], avgDays: [] }
  if (grain.value === 'day') {
    return {
      labels: rows.map(r => r.date.slice(5)),
      directCounts: rows.map(r => r.direct_count || 0),
      walletCounts: rows.map(r => r.wallet_count || 0),
      manualCounts: rows.map(r => r.manual_count || 0),
      avgDays: rows.map(r => (r.wallet_avg_days_2_4 == null ? null : Number(r.wallet_avg_days_2_4).toFixed(1))),
    }
  }
  // 按周/月聚合: 加权平均拖延天数
  const groups = new Map()
  rows.forEach(r => {
    const d = new Date(r.date)
    let key, label
    if (grain.value === 'week') {
      const iso = getISOWeek(d)
      key = `${d.getFullYear()}-W${iso}`
      label = `${d.getMonth()+1}/${d.getDate()}周`
    } else {
      key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`
      label = `${d.getFullYear()}-${d.getMonth()+1}`
    }
    if (!groups.has(key)) { groups.set(key, { sum: 0, n: 0, dc: 0, wc: 0, mc: 0, label }) }
    const g = groups.get(key)
    g.dc += r.direct_count || 0
    g.wc += r.wallet_count || 0
    g.mc += r.manual_count || 0
    if (r.wallet_avg_days_2_4 != null) { g.sum += r.wallet_avg_days_2_4 * (r.wallet_count || 0); g.n += (r.wallet_count || 0) }
  })
  const sorted = [...groups.entries()].sort((a, b) => a[0] < b[0] ? -1 : 1)
  return {
    labels: sorted.map(([, g]) => g.label),
    directCounts: sorted.map(([, g]) => g.dc),
    walletCounts: sorted.map(([, g]) => g.wc),
    manualCounts: sorted.map(([, g]) => g.mc),
    avgDays: sorted.map(([, g]) => (g.n ? (g.sum / g.n).toFixed(1) : null)),
  }
}

function getISOWeek(date) {
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
  const dayNum = d.getUTCDay() || 7
  d.setUTCDate(d.getUTCDate() + 4 - dayNum)
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1))
  return Math.ceil((((d - yearStart) / 86400000) + 1) / 7)
}

// ---- 图2: 沉淀与清理 ----
const waterOption = computed(() => {
  const rows = walletWater.value || []
  if (!rows.length) return {}
  const dates = rows.map(r => String(r.date).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['转入', '提现', '净水位'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11, rotate: dates.length > 14 ? 30 : 0 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '净水位', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      { name: '转入', type: 'bar', data: rows.map(r => r.wallet_in), itemStyle: { color: '#10b981' }, barMaxWidth: 26 },
      { name: '提现', type: 'bar', data: rows.map(r => r.withdraw), itemStyle: { color: '#f87171' }, barMaxWidth: 26 },
      { name: '净水位', type: 'line', yAxisIndex: 1, data: rows.map(r => (r.wallet_in - r.withdraw)), smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
    ],
  }
})

function renderTrend() {
  // grain 变化直接由 trendOption computed 重新计算
}

function rangeParams() {
  const params = {}
  if (filters.range && filters.range.length === 2) {
    params.start = filters.range[0] + 'T00:00:00'
    params.end = filters.range[1] + 'T23:59:59'
  }
  return params
}

async function loadSleeping() {
  const params = rangeParams()
  params.page = sleepPage.value
  params.page_size = sleepPageSize.value
  try {
    const res = await rwApi.sleeping(params)
    sleepTotal.value = res?.data?.total || 0
    sleepList.value = res?.data?.list || []
  } catch (e) {
    sleepList.value = []
  }
}

async function loadAll() {
  loading.value = true
  try {
    const params = rangeParams()
    const [o, ds, wv] = await Promise.all([
      rwApi.overview(params),
      rwApi.dailySplit(params),
      cash.walletVsWithdraw(params),
    ])
    overview.value = o?.data || {}
    dailySplit.value = ds?.data || []
    walletWater.value = wv?.data || []
    sleepPage.value = 1
    await loadSleeping()
  } catch (e) {
    ElMessage.error('加载失败: ' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.timeliness { width: 100%; }
.block-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.block-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 600; color: #111827; }
.block-hint { font-size: 12px; color: #6b7280; }
.mb-16 { margin-bottom: 16px; }
.filter-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.kpi-card { border-radius: 10px; padding: 12px 14px 10px; border: 1px solid rgba(148,163,184,0.15); display: flex; flex-direction: column; gap: 3px; min-height: 84px; }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { margin-top: 2px; font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; }
.unit { font-size: 12px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
/* ★ #205 可下钻卡片交互反馈（看板卡片规范 §3.5）: cursor:pointer + hover 反馈 */
.kpi-drillable { cursor: pointer; transition: box-shadow .15s ease, transform .15s ease; }
.kpi-drillable:hover { box-shadow: 0 2px 10px rgba(0,0,0,0.12); transform: translateY(-1px); }
.kpi-info { display: inline-flex; margin-left: 2px; }
.sn-chip { color: #2563eb; cursor: pointer; font-weight: 600; text-decoration: underline; text-decoration-style: dotted; }
.sn-chip:hover { color: #1d4ed8; }
.pag-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .block-card { padding: 12px; }
  .block-title { font-size: 14px; }
  .block-hint { font-size: 11px; }
  .filter-bar { gap: 6px; }
  .kpi-card { padding: 8px 10px; min-height: 68px; }
  .kpi-label { font-size: 11px; }
  .kpi-value { font-size: 18px; }
  .kpi-sub { font-size: 10px; }
  :deep(.el-table) { min-width: 620px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
  :deep(.el-card) { overflow: hidden; }
}
</style>
