<!--
  ┌──────────────────────── 设计说明（Design Rationale） ─────────────────────────┐
  │ 目的：退款业务负责人在日/周复盘时回答「钱出去多少 · 环比是否异常 · 钱卡在哪 ·   │
  │       退款是否被投诉驱动 · 谁在退/为什么退」，并据此决定下一步治理动作。        │
  │ 逻辑：自动结论条先行（先给判断再给数）；资金全景→钱包水位→登记水位→涉诉归因     │
  │       →原因/人员→单笔明细，由总量到归因再到个案，每块对应一个独立决策问题。     │
  │ 口径：四路经营 A线上直退/B钱包提现/C闪猫退款/D手动退款（成功口径+质量治理排除， │
  │       按实际扣款时间优先归日）+ 对账基准E商户号实际现金流（不与四路累加）；      │
  │       涉诉判据沿用投诉渠道口径（外部>商户号>内部 + 备注标识）。                 │
  │       明细筛选含设备维度（设备类型/套餐/商户号/激活时间段，走 devices 表，#273）。│
  │       详见 docs/standards/投诉渠道口径与对账说明.md ·                          │
  │       backend/app/services/analytics/refund.py refund_decision_overview。      │
  │ 取舍：① 不做 C 闪猫单笔明细下钻——明细台账接口未覆盖闪猫路，强行给会缺一路误导； │
  │       ② 不做图内点击下钻——明细接口按日筛选，无小时/单桶粒度，跳过去数字对不上； │
  │       ③ 不另立涉诉判据——沿用投诉渠道口径文档，避免双口径打架。                 │
  │ 工单：#205（看板卡片双强制补齐：ⓘ 数据说明 + 可下钻）                          │
  └───────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div v-loading="loading" class="decision-overview">
    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>
    <template v-if="data">
      <!-- ===== 区块0: 管理摘要(结论条 + KPI) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">管理摘要
            <!-- #205 页面层设计说明（界面设计说明规范 §二：使用者可见，含「取舍」段） -->
            <el-tooltip effect="dark" placement="bottom-start" raw-content
              content="<b>这个页面为什么这样设计</b><br/><b>给谁</b>：退款业务负责人，日/周复盘时使用。<br/><b>回答什么</b>：钱出去多少 · 环比是否异常 · 钱卡在哪 · 退款是否被投诉驱动 · 谁在退/为什么退。<br/><b>组织逻辑</b>：自动结论先行 → 资金全景 → 钱包水位 → 登记水位 → 涉诉归因 → 原因/人员 → 单笔明细，由总量到归因再到个案。<br/><b>口径</b>：四路经营（A线上直退/B钱包提现/C闪猫退款/D手动退款，成功口径+质量治理排除，按实际扣款时间归日）+ 对账基准E（不累加）；涉诉判据沿用投诉渠道口径文档 docs/standards/投诉渠道口径与对账说明.md。<br/><br/><b>⚠️ 我们不做什么（及原因）</b><br/>① <b>不做</b>闪猫(C)单笔明细下钻 —— 明细台账接口未覆盖闪猫路，强行给会缺一路误导；<br/>② <b>不做</b>图内点击下钻 —— 明细接口按日筛选，无更细粒度，跳过去数字对不上（见各区块ⓘ）；<br/>③ <b>不另立</b>涉诉判据 —— 沿用口径文档，避免双口径打架。<br/><b>关联</b>：工单 #205">
              <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
            </el-tooltip>
          </span>
          <span class="block-hint">自动结论 · 随筛选区间更新</span>
        </div>
        <el-row :gutter="12" class="mb-12">
          <el-col :xs="12" :sm="8" :md="4">
            <!-- #205 卡片说明+下钻: 点卡 → 区块一「资金流出全景」按日四路构成表 -->
            <div class="kpi-card kpi-drill" style="background:#eff6ff" title="点击查看按日四路构成" @click="drillRoutes">
              <div class="kpi-label" style="color:#2563eb">现金流出总额<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.cash_out_total<br/><b>口径</b>：四路经营合计 = A线上直退(refunds 线上+已退款, 排除重复/忽略质量行) + B钱包提现(用户提现, 排除冻结/失败备注) + C闪猫退款(已退款) + D手动退款(is_refunded=是)，各路按<b>实际扣款时间</b>优先归日(上海时区)<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；后端缓存 60s<br/><b>「空」的含义</b>：0 = 窗口内四路均无成功退款流水<br/><b>下钻</b>：点卡片 → 「一、资金流出全景」按日四路构成表">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value">¥{{ formatMoney(summary.cash_out_total) }}</div>
              <div class="kpi-sub">{{ summary.cash_out_count }} 笔 · 四路经营合计</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <!-- ★ #309 窗口充值对照卡: 给「现金流出」一个「流入」对照 ——
                 退款 ¥X 是充值的多大比例, 回答「退款压力是否可控」。
                 下钻 → 数据查看·充值流量(带窗口, 按充值时间落窗)。 -->
            <div class="kpi-card kpi-drill" style="background:#ecfdf5" title="点击查看充值流量明细" @click="drillRecharge">
              <div class="kpi-label" style="color:#059669">窗口充值<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.recharge_amount / recharge_count / refund_recharge_ratio<br/><b>口径</b>：窗口内 device_combos 实际充值金额合计(按充值时间 created_at_raw 落窗)，<b>已剔除工厂质检专用套餐流水</b>(套餐名含「质检」，与今日看板充值卡、分品类运营看板 #293 同判据)，只计真实付费充入<br/><b>退款占充值</b>：现金流出总额(四路经营合计) ÷ 窗口充值 × 100%；分母为 0 时显示「—」<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；后端缓存 60s<br/><b>「空」的含义</b>：0 = 窗口内无真实充值流水(或全部为质检流水)<br/><b>下钻</b>：点卡片 → 数据查看·充值流量(带当前窗口，按充值时间；该页为原始流水含质检行，剔质检合计以本卡为准)">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value">¥{{ formatMoney(summary.recharge_amount) }}</div>
              <div class="kpi-sub">{{ summary.recharge_count ?? 0 }} 笔 · 退款占充值 {{ summary.refund_recharge_ratio != null ? summary.refund_recharge_ratio + '%' : '—' }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <!-- #205 ⚠️ 不做下钻（规范 §3.4）: 纯环比派生值 =（本期四路合计−上一等长窗口）/上一窗口，
                 无独立明细构成；构成即「现金流出总额」卡（已可下钻）。故只给ⓘ说明、不做点击跳转。 -->
            <div class="kpi-card" style="background:#f0fdf4">
              <div class="kpi-label" style="color:#16a34a">较上一周期<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.chain_pct<br/><b>口径</b>：（本期四路合计 − 上一等长窗口四路合计）/ 上一窗口 × 100%；与「现金流出总额」同源同判据<br/><b>时点</b>：跟随顶部时间条；上一窗口无数据时显示「—」<br/><b>「空」的含义</b>：「—」= 上一等长窗口四路合计为 0（如窗口外无数据/新业务），无法计算环比<br/><b>⚠️ 不下钻</b>：纯环比派生值，无独立明细；构成请看「现金流出总额」卡（可下钻）">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value" :style="{ color: chainColor }">{{ chainPctText }}</div>
              <div class="kpi-sub">同长度窗口对比</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <!-- #205 卡片说明+下钻: 点卡 → 区块六「退款明细」并自动选中涉诉筛选 -->
            <div class="kpi-card kpi-drill" style="background:#fef2f2" title="点击查看涉诉退款明细" @click="drillComplaintDetail">
              <div class="kpi-label" style="color:#dc2626">涉诉退款占比<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.complaint_ratio<br/><b>口径</b>：涉诉金额 / 线上直退金额 × 100%（仅线上已退款单）；涉诉判据 = SN 关联工单服务项关键词（<b>外部</b>: 工信部/12315/315平台/12345/政务平台等 &gt; <b>商户号</b>: 微信 &gt; <b>内部</b>: 商品投诉/服务投诉）或 审核备注/备注含「涉投诉/客诉/投诉/315/争议/催退」→ 备注涉诉<br/>详见 docs/standards/投诉渠道口径与对账说明.md<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：0% = 窗口内无涉诉退款，或无线上直退单（分母为 0）<br/><b>下钻</b>：点卡片 → 「七、退款明细」并自动选中涉诉筛选">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value">{{ summary.complaint_ratio }}<span class="unit">%</span></div>
              <div class="kpi-sub">涉诉 ¥{{ formatMoney(summary.complaint_amount) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <!-- #205 卡片说明+下钻: 点卡 → 区块二「钱包水位监控」按日转入/转出/扣费构成表 -->
            <div class="kpi-card kpi-drill" style="background:#fff7ed" title="点击查看按日水位构成" @click="drillWallet">
              <div class="kpi-label" style="color:#ea580c">钱包净水位<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.wallet_net<br/><b>口径</b>：窗口内 转入(op_type=订单退费转入) − 转出(用户提现) − 系统扣费；已排除「提现申请-冻结」等失败/冻结备注（冻结非真实出账）<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：0 = 无钱包流水或进出恰好平衡；净水位为窗口内净流量，非账户余额<br/><b>下钻</b>：点卡片 → 「二、钱包水位监控」按日转入/转出/扣费构成表">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value" :style="{ color: netColor }">{{ summary.wallet_net >= 0 ? '+' : '' }}{{ formatMoney(summary.wallet_net) }}</div>
              <div class="kpi-sub">转入 − 转出（含系统扣费）</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <!-- #205 卡片说明+下钻: 点卡 → 区块六「退款明细」并自动选中路径=线上直退 -->
            <div class="kpi-card kpi-drill" style="background:#f5f3ff" title="点击查看线上直退明细" @click="drillOnlineDetail">
              <div class="kpi-label" style="color:#7c3aed">线上直退<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.online_count / online_amount<br/><b>口径</b>：refunds refund_method=线上 且 status=已退款（排除重复/忽略质量行），按实际扣款时间优先归日；为四路经营中的 A 路<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：0 笔 = 窗口内无线上已退款单<br/><b>下钻</b>：点卡片 → 「七、退款明细」并自动选中路径=线上直退">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value">{{ summary.online_count }}<span class="unit">笔</span></div>
              <div class="kpi-sub">¥{{ formatMoney(summary.online_amount) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <!-- #205 卡片说明+下钻: 点卡 → 区块一表内「商户号实际(E)」列；E 单笔明细本页不展示，指向导出 Excel -->
            <div class="kpi-card kpi-drill" style="background:#fefce8" title="点击查看 E 路按日构成" @click="drillRoutes">
              <div class="kpi-label" style="color:#ca8a04">商户号实际现金流<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.actual_cashflow<br/><b>口径</b>：refund_record_reports（商户号退款记录报表）退款成功（退款成功/成功/SUCCESS），为<b>对账基准 E</b>，独立展示、不与四路累加；数据截止日见副行<br/><b>时点</b>：跟随顶部时间条；E 依赖持续采集/导入<br/><b>「空」的含义</b>：0 = 窗口内 E 无成功退款，或窗口晚于 E 数据截止日（采集未覆盖）<br/><b>下钻</b>：点卡片 → 「一、资金流出全景」表内「商户号实际(E)」按日列；E 单笔明细本页不展示，可用右上角「导出」Excel 的 E sheet 查看">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value">¥{{ formatMoney(summary.actual_cashflow) }}</div>
              <div class="kpi-sub">{{ summary.actual_cashflow_count }} 笔 · 对账基准 E<template v-if="summary.e_data_until">（数据截止 {{ summary.e_data_until }}）</template></div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="8" :md="4">
            <!-- #205 卡片说明+下钻: 点卡 → 区块一表内「对账偏差」按日列；逐笔比对指向导出 Excel -->
            <div class="kpi-card kpi-drill" style="background:#f0fdfa" title="点击查看按日对账偏差" @click="drillRoutes">
              <div class="kpi-label" style="color:#0f766e">对账偏差<el-tooltip effect="dark" placement="top" raw-content
                content="<b>来源</b>：/analytics/refund/decision-overview → summary.reconcile_diff<br/><b>口径</b>：经营四路合计 − 商户号实际(E)；正 = 经营大于实际（四路多计或 E 缺数据），负 = 反之；该项<b>不累加</b>进任何总额<br/><b>时点</b>：跟随顶部时间条；窗口晚于 E 数据截止日时仅供参考（见上方提示条）<br/><b>「空」的含义</b>：0 = 两口径恰好一致；≠0 不一定是错账，常见原因是 E 采集滞后<br/><b>下钻</b>：点卡片 → 「一、资金流出全景」表内「对账偏差」按日列；逐笔比对可用右上角「导出」Excel 的四路与 E sheet">
                <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
              </el-tooltip></div>
              <div class="kpi-value" :style="{ color: (summary.reconcile_diff ?? 0) >= 0 ? '#dc2626' : '#16a34a' }">{{ (summary.reconcile_diff ?? 0) >= 0 ? '+' : '' }}{{ formatMoney(summary.reconcile_diff) }}</div>
              <div class="kpi-sub">经营四路 − 实际E（不累加）</div>
            </div>
          </el-col>
        </el-row>
        <!-- #311 方案C: C 路闪猫退款独立提示行(业务相对独立, 已从下方激活/维度分析拆出) -->
        <div v-if="cRouteSummary.count" class="c-route-hint">
          <el-icon style="color:#9333ea;vertical-align:-1px"><InfoFilled /></el-icon>
          <span>其中 C 路闪猫退款 <b>{{ cRouteSummary.count }}</b> 笔 · ¥{{ formatMoney(cRouteSummary.amount) }}
            （业务相对独立, 已从下方「激活时长」「设备维度」分析拆出单列, 不混入三路 A+B+D 汇总）</span>
        </div>
        <el-alert
          v-if="summary.conclusions && summary.conclusions.length"
          type="info" :closable="false" show-icon
        >
          <div v-for="(c, i) in summary.conclusions" :key="i" class="conclusion-line">{{ c }}</div>
        </el-alert>
      </section>

      <!-- ===== 区块六: 设备维度汇总（位置上移到摘要下方, #291+#293 改造, 2026-10-10）=====
        设计说明(Design Rationale):
        · 目的: 替运营/客服主管回答「退款集中在哪类设备型号/套餐/店铺/激活时长」——
          设备型号/套餐/店铺指向具体供应与销售环节; 激活时长区分新机质量(0-15天) vs
          长尾老化(>365天)。位置在「管理摘要」之下, 让用户看完总额(KPI)后第一眼
          看到结构集中度, 再往下看按日趋势与原因归因(决策顺序: 总量→结构→趋势→原因)。
        · 逻辑(为何这样组织): 前三维度(型号/套餐/店铺)用饼图+Top5+其他聚合——
          占比构成语义清晰, 长尾聚合避免>8扇区不可读; 激活时长保条形按时长排序——
          保留"新→老"顺序语义(饼图会丢失这一关键洞察, 新机高退=质量/预期差, 老机
          高退=老化/续费话术)。
        · 口径: 经营四路 A+B+C+D 全量(#291 2026-10-10 用户拍板, 原仅 A 路线上直退——
          只算 A 会漏掉钱包提现/闪猫/手动退款的钱, 结构集中度失真), 与「一、资金流出
          全景」同口径同窗口(各路按实际出款归日, 已排除重复/忽略与冻结类流水), 各桶
          合计可与资金流出全景四路合计对账; 每桶附 routes 分路明细(tooltip 展示);
          设备属性 = devices 表(#271/#273 同源, #293 探针补全驱动覆盖);
          激活时长 = 出款日 − bms_orders 最早激活日(#295 五桶: 0-15/16-30/31-180/
          181-365/>365/(无激活记录), 切点=上界: 30/180/365 归其左侧桶);
          激活时长图复用 sale_days_split(同一事实不两处汇总), 桶定义与投诉侧
          同名同序(_core.ACTIVE_AGE_BUCKETS 单一来源); 点击桶 1:1 映射到明细
          active_age 参数(明细按 D1 发起日锚定且不含 C 路 → 下钻为近似映射)。
        · tooltip 统一: {分项名} · 占比% · 金额¥ · 单数笔 · 笔单价¥(金额/单数);
          笔单价识别"单笔损失大"维度(笔数少但金额大=高单价设备出问题)。
          #296(2026-10-11): 激活时长条形按单数, tooltip 必须并列「单数占比+金额占比」,
          禁止只写一个无口径的「占比」(实测两口径可差 4pct, 会误读)。
        · 排序: 前三饼图按金额倒序(钱集中度), 扇区 value=金额; 激活时长按桶固定
          顺序(0-15→16-30→...→(无激活记录), 即按时长由短到长)。
        · 取舍: ① 不做型号×套餐交叉图(组合爆炸, 明细筛选可自由组合);
          ② 前三饼图 Top5+其他聚合(占比构成 vs 长尾取舍, 避免>8扇区不可读);
          ③ 激活时长不另做饼图(有序分桶, 顺序是核心信息, 饼图丢顺序);
          ④ 未填/(未填SN)/(未建档)桶显式保留(不美化覆盖缺口);
          ⑤ 型号桶反查不到类型码时不设筛选(避免假下钻), 其余桶点击带筛选跳明细。
        · 工单: #291(维度汇总, 2026-10-09 拍板) + #293(探针补全驱动覆盖, 2026-10-10 改造)
          + #295(激活时长改五桶并与投诉侧统一, 2026-10-11)。
      -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">六、设备维度汇总<el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund/decision-overview → device_dims / sale_days_split<br/><b>口径</b>：经营四路 A+B+C+D 全量（#291 2026-10-10 拍板，与「一、资金流出全景」同口径同窗口，各路按实际出款归日，已排除重复/忽略与冻结类流水，各桶合计可与四路合计对账；tooltip 展示每桶分路构成）；设备型号/套餐/店铺 = devices 表属性（#271/#273 同源，#293 探针补全驱动覆盖）；激活时长 = 出款日 − bms_orders 最早激活日（#295 五桶，与投诉侧同名同序：0-15/16-30/31-180/181-365/&gt;365/(无激活记录)；切点=上界：30/180/365 归其左侧桶，激活晚于出款的负值自然落首桶）；<b>激活时长条形按单数</b>，tooltip 同时给出单数占比与金额占比两个口径（#296，两口径不相等属正常：新机/老机笔单价不同）<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：图为空 = 窗口内四路均无成功退款；(未填)/(未填SN)/(未建档) 桶 = 台账/设备库覆盖缺口，显式保留不隐藏<br/><b>下钻</b>：点击任一桶 → 「七、退款明细」自动带对应筛选（只设筛选不自动滚动，消息提示引导；激活桶与明细 active_age 参数 1:1 映射；明细按 D1 发起日锚定且不含 C 路，下钻为近似映射）；设备型号桶需反查到类型码才可下钻；「其他」为 Top5 长尾聚合桶，不可下钻<br/><b>取舍</b>：前三饼图 Top5+其他聚合（占比构成 vs 长尾取舍）；激活时长保条形（有序分桶，饼图丢顺序）；不做型号×套餐交叉图（明细可组合）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip></span>
          <span class="block-hint">四路 A/B/C/D · 判断: 退款集中在哪类设备型号/套餐/店铺/激活时长 → 行动: 新机高退查质量与预期差, 老机高退查老化与续费话术 · 点击桶带筛选下钻明细</span>
        </div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="8" class="mb-12">
            <div class="sub-title">设备型号（Top5+其他, 按金额占比）</div>
            <BaseChart v-if="deviceTypeOption.series" :option="deviceTypeOption" height="260px" @chartClick="(p) => drillDeviceDim('type', p.name)" />
            <el-empty v-else description="暂无数据" :image-size="60" />
          </el-col>
          <el-col :xs="24" :md="8" class="mb-12">
            <div class="sub-title">套餐（Top5+其他, 按金额占比）</div>
            <BaseChart v-if="comboOption.series" :option="comboOption" height="260px" @chartClick="(p) => drillDeviceDim('combo', p.name)" />
            <el-empty v-else description="暂无数据" :image-size="60" />
          </el-col>
          <el-col :xs="24" :md="8" class="mb-12">
            <div class="sub-title">店铺（Top5+其他, 按金额占比）</div>
            <BaseChart v-if="merchantOption.series" :option="merchantOption" height="260px" @chartClick="(p) => drillDeviceDim('merchant', p.name)" />
            <el-empty v-else description="暂无数据" :image-size="60" />
          </el-col>
        </el-row>
        <el-row>
          <el-col :xs="24" :md="12">
            <div class="sub-title">激活时长（#295 五桶口径 · 条形按单数, 按时长排序）</div>
            <BaseChart v-if="activeAgeOption.series" :option="activeAgeOption" height="240px" @chartClick="(p) => drillDeviceDim('age', p.name)" />
            <el-empty v-else description="暂无数据" :image-size="60" />
          </el-col>
        </el-row>
      </section>

      <!-- ===== 区块1: 资金流出全景(钱出去多少) ===== -->
      <section id="sec-routes" class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">一、资金流出全景<el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund/decision-overview → routes_daily<br/><b>口径</b>：堆叠柱 = A/B/C/D 四路按日金额（实际扣款时间优先归日，已排除重复/忽略与冻结类流水）；曲线 = 商户号实际 E（对账基准，独立右轴，<b>不与四路累加</b>）<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：某日无成功退款则该日不出现；整图为空 = 窗口内四路与 E 均无流水<br/><b>下钻</b>：下方日表即按日构成；单笔明细 → 「七、退款明细」，全量比对用「导出」Excel">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip></span>
          <span class="block-hint">四路经营 A/B/C/D（堆叠）= 经营估算 · E 商户号实际现金流（曲线，对账基准，不与四路累加）</span>
        </div>
        <!-- ❌ E 同步滞后 Alert 已移除(2026-10-10): "请补充采集/导入"是运维指引,
             业务看板不承载运维逻辑; 数据截止日仍在「商户号实际现金流」KPI 卡副行作为解读标注。
             同步问题请去「数据采集」页或「对账核查」tab(那本就是核查页)。 -->
        <BaseChart v-if="routeOption.series?.length" :option="routeOption" height="300px" />
        <el-empty v-else description="暂无资金流出数据" :image-size="60" />
        <el-table :data="routes.table" border stripe size="small" class="mt-12" max-height="260">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="A 线上直退" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['A 线上直退_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['A 线上直退_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="B 钱包提现" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['B 钱包提现_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['B 钱包提现_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="C 闪猫退款" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['C 闪猫退款_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['C 闪猫退款_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="D 手动退款" align="right">
            <template #default="{ row }">
              <span class="num">{{ row['D 手动退款_cnt'] }}笔</span>
              <span class="amt">¥{{ formatMoney(row['D 手动退款_amt']) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="四路合计" align="right" width="150">
            <template #default="{ row }">
              <span class="num strong">{{ row.total_cnt }}笔</span>
              <span class="amt strong">¥{{ formatMoney(row.total_amt) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="商户号实际(E)" align="right" width="150">
            <template #default="{ row }">
              <span class="num">{{ row.actual_cnt }}笔</span>
              <span class="amt" style="color:#ca8a04">¥{{ formatMoney(row.actual_amt) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="对账偏差" align="right" fixed="right" width="150">
            <template #default="{ row }">
              <span class="amt" :style="{ color: (row.diff_amt ?? 0) >= 0 ? '#dc2626' : '#16a34a' }">{{ (row.diff_amt ?? 0) >= 0 ? '+' : '' }}{{ formatMoney(row.diff_amt) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块2: 钱包水位(钱卡在哪) ===== -->
      <section id="sec-wallet" class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">二、钱包水位监控<el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund/decision-overview → wallet_water<br/><b>口径</b>：转入 = op_type「订单退费转入」· 转出 =「用户提现」· 扣费 =「系统扣费」（均已排除「提现申请-冻结」等失败/冻结备注）；净水位 = 转入 − 转出 − 扣费，累计 = 窗口内逐日累加<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：某日无钱包流水则不出现；整块为空 = 窗口内无任何钱包转入/转出<br/><b>下钻</b>：下方日表即按日构成">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip></span>
          <span class="block-hint">判断: 净水位过高=沉淀+待提现负债(挤兑/投诉风险) · 过低需区分转入不足 vs 过账快(结合涉诉比率判把关) → 行动: 排查提现顺畅度/查转入端/审视退款政策</span>
        </div>
        <BaseChart v-if="waterOption.series?.length" :option="waterOption" height="300px" />
        <el-empty v-else description="暂无钱包水位数据" :image-size="60" />
        <el-table :data="waterTable" border stripe size="small" class="mt-12" max-height="240">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="转入(订单退费转入)" align="right">
            <template #default="{ row }"><span class="num">{{ row.tin_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.tin_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="转出(用户提现)" align="right">
            <template #default="{ row }"><span class="num">{{ row.wd_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.wd_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="系统扣费" align="right">
            <template #default="{ row }"><span class="num">{{ row.fee_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.fee_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="当日净水位" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.net >= 0 ? 'pos' : 'neg'">{{ row.net >= 0 ? '+' : '' }}{{ formatMoney(row.net) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="累计净水位" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.cum >= 0 ? 'pos' : 'neg'">{{ row.cum >= 0 ? '+' : '' }}{{ formatMoney(row.cum) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块3: 手动退款登记水位(登记 vs 已退 = 蓄水) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">三、手动退款登记水位<el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund/decision-overview → manual_water<br/><b>口径</b>：登记 = manual_refunds 全部登记按 register_time 归日；已退 = is_refunded「是」按 op_time（缺失回退 register_time）归日；蓄水 = 登记 − 已退，累计 = 窗口内逐日累加<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：某日既无登记也无退款完成则不出现；整块为空 = 窗口内无手动退款登记流水<br/><b>下钻</b>：下方日表即按日构成；「七、退款明细」路径筛选=手动退款可看单笔（仅已退部分）">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip></span>
          <span class="block-hint">登记(商户号手机退款处理登记) − 已退款完成 = 蓄水 · 手动操作为主,存在登记后未退的积压</span>
        </div>
        <div v-if="manualWaterTable.length" class="water-summary mb-12">
          登记蓄水总额 <b :class="manualWaterTotal >= 0 ? 'pos' : 'neg'">{{ manualWaterTotal >= 0 ? '+' : '' }}¥{{ formatMoney(manualWaterTotal) }}</b>
          （登记 ¥{{ formatMoney(manualRegTotal) }} − 已退 ¥{{ formatMoney(manualDoneTotal) }}）
        </div>
        <BaseChart v-if="manualWaterOption.series?.length" :option="manualWaterOption" height="300px" />
        <el-empty v-else description="暂无手动退款登记数据" :image-size="60" />
        <el-table :data="manualWaterTable" border stripe size="small" class="mt-12" max-height="240">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="登记(登记日期)" align="right">
            <template #default="{ row }"><span class="num">{{ row.reg_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.reg_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="已退款完成(操作时间)" align="right">
            <template #default="{ row }"><span class="num">{{ row.done_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.done_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="当日蓄水" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.net >= 0 ? 'pos' : 'neg'">{{ row.net >= 0 ? '+' : '' }}{{ formatMoney(row.net) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="累计蓄水" align="right" width="150">
            <template #default="{ row }">
              <span :class="row.cum >= 0 ? 'pos' : 'neg'">{{ row.cum >= 0 ? '+' : '' }}{{ formatMoney(row.cum) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块4: 涉诉归因(风险敞口) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">四、涉诉 vs 普通退款<el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund/decision-overview → complaint_split<br/><b>口径</b>：仅<b>线上已退款</b>单；每单按 SN 关联工单服务项判涉诉，优先级 <b>外部&gt;商户号&gt;内部</b>（外部: 工信部/12315/315平台/12345/政务平台等 · 商户号: 微信 · 内部: 商品投诉/服务投诉）；无工单标识但审核备注/备注含「涉投诉/客诉/投诉/315/争议/催退」归「备注涉诉」；占比 = 当日涉诉金额/(涉诉+普通)×100<br/>详见 docs/standards/投诉渠道口径与对账说明.md<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：某日无线上已退款单则不出现；整块为空 = 窗口内无线上已退款<br/><b>下钻</b>：下方日表即按日构成；单笔构成 → 点「涉诉退款占比」KPI 卡或用「七、退款明细」涉诉筛选">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip></span>
          <span class="block-hint">线上直退 · 涉诉=SN关联工单(外部&gt;商户号&gt;内部) 或 审核备注投诉标识(涉投诉/客诉/315/争议等) · 判断: 占比高=该退才退(控制到位,同时盯投诉绝对量) / 占比低=警惕宽松退款 → 行动: 转投诉/商品质量专项</span>
        </div>
        <BaseChart v-if="complaintOption.series?.length" :option="complaintOption" height="300px" />
        <el-empty v-else description="暂无涉诉归因数据" :image-size="60" />
        <el-table :data="complaintTable" border stripe size="small" class="mt-12" max-height="240">
          <el-table-column prop="date" label="日期" width="100" fixed />
          <el-table-column label="内部投诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.int_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.int_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="商户号投诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.mer_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.mer_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="外部投诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.ext_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.ext_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="备注涉诉" align="right">
            <template #default="{ row }"><span class="num">{{ row.rmk_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.rmk_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="涉诉合计" align="right">
            <template #default="{ row }"><span class="num strong">{{ row.comp_cnt }}笔</span><span class="amt strong">¥{{ formatMoney(row.comp_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="普通退款" align="right">
            <template #default="{ row }"><span class="num">{{ row.nor_cnt }}笔</span><span class="amt">¥{{ formatMoney(row.nor_amt) }}</span></template>
          </el-table-column>
          <el-table-column label="涉诉占比" align="right" width="100">
            <template #default="{ row }"><el-tag size="small" :type="row.ratio >= 40 ? 'danger' : row.ratio >= 25 ? 'warning' : 'info'">{{ row.ratio }}%</el-tag></template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ===== 区块5: 原因归因 + 人员归因(为什么退/谁在退) ===== -->
      <section class="block-card mb-16">
        <div class="block-head">
          <span class="block-title">五、退款原因 & 操作人员<el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund/decision-overview → reason_operator<br/><b>口径</b>：仅<b>线上已退款</b>单；原因 = 该退款单设备<b>最新完工工单</b>的服务项（未关联工单 →「&lt;未关联工单&gt;」）；人员 = refunds.operator（别名归一为显示名，系统/空 →「&lt;系统&gt;」）；Top 原因按笔数、Top 人员按金额<br/><b>时点</b>：跟随顶部时间条，点「分析」刷新；缓存 60s<br/><b>「空」的含义</b>：图为空 = 窗口内无线上已退款；「&lt;未关联工单&gt;」占比高 = 工单关联缺失（结论条会提示），此时原因分布失真<br/><b>⚠️ 下钻说明</b>：图内点击未实现——明细可在「七、退款明细」用「退款原因」「退款人」筛选查看单笔（与图同源字段），故不另做图内跳转">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip></span>
          <span class="block-hint">线上直退 · 判断: 主因类别与主力人员 → 行动: 针对主因解决上游问题(网络质量/商品问题/话术)以减少退款 · 未关联工单占比高时原因覆盖不足</span>
        </div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="14" class="mb-12">
            <div class="sub-title">退款原因 Top（按笔数）</div>
            <BaseChart v-if="reasonOption.series?.length" :option="reasonOption" height="320px" />
            <el-empty v-else description="暂无原因数据" :image-size="60" />
          </el-col>
          <el-col :xs="24" :md="10" class="mb-12">
            <div class="sub-title">操作人员 Top（按金额）</div>
            <el-table :data="operators" border stripe size="small" max-height="360">
              <el-table-column prop="name" label="人员" min-width="120" fixed />
              <el-table-column prop="count" label="笔数" width="70" align="right" sortable />
              <el-table-column prop="amount" label="金额(¥)" width="110" align="right" sortable>
                <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
              </el-table-column>
              <el-table-column prop="avg_amount" label="均单(¥)" width="100" align="right">
                <template #default="{ row }">{{ formatMoney(row.avg_amount) }}</template>
              </el-table-column>
            </el-table>
          </el-col>
        </el-row>
      </section>

      <!-- ===== 区块7: 明细下钻(可查可追) ===== -->
      <section id="sec-detail" class="block-card">
        <div class="block-head">
          <span class="block-title">七、退款明细<el-tooltip effect="dark" placement="top" raw-content
            content="<b>来源</b>：/analytics/refund-withdrawal/detail（退款明细台账）<br/><b>口径</b>：支持 涉诉/退款路径/退款人/退款原因/设备维度(类型·套餐·商户号·激活时间)/激活时段(#291桶) 筛选（选项见下方筛选器）；风险分级 = 接口 risk_summary（高/中/低）；各项口径以接口返回为准<br/><b>时点</b>：随当前筛选实时查询（无缓存）<br/><b>「空」的含义</b>：列表为空 = 当前筛选组合无匹配行；清空筛选可验证是否为口径/筛选问题<br/><b>下钻</b>：SN 点击 → 设备审计页（新窗口，带当前窗口）；全量数据用右上角「导出」下载 Excel">
            <el-icon style="color:#b0b4bb;cursor:help;vertical-align:-1px;font-size:13px"><QuestionFilled /></el-icon>
          </el-tooltip></span>
          <span class="block-hint">共 {{ detailTotal }} 条 · 点击 SN 可查看设备审计 · 全量数据可点右上角「导出」下载 Excel</span>
        </div>
        <!-- 风险概览 -->
        <div v-if="detailLoaded" class="risk-summary mb-12">
          涉诉风险：
          <span class="risk-high">{{ detailRisk.high }} 笔高风险</span>
          <span class="risk-med">{{ detailRisk.medium }} 笔中风险</span>
          <span class="risk-low">{{ detailRisk.low }} 笔低风险</span>
        </div>
        <!-- 明细筛选 -->
        <div class="detail-filters mb-12">
          <el-select v-model="filter.comp" placeholder="涉诉" size="small" clearable style="width: 120px">
            <el-option label="涉诉" value="any" />
            <el-option label="非涉诉" value="normal" />
            <el-option label="内部投诉" value="internal" />
            <el-option label="商户号投诉" value="merchant" />
            <el-option label="外部投诉" value="external" />
            <el-option label="备注涉诉" value="remark" />
          </el-select>
          <el-select v-model="filter.path" placeholder="退款路径" size="small" clearable style="width: 140px">
            <el-option label="线上直退" value="B" />
            <el-option label="钱包提现" value="A" />
            <el-option label="手动退款" value="D" />
            <el-option label="线上直退+钱包提现" value="AB" />
          </el-select>
          <el-select v-model="filter.operator" placeholder="退款人" size="small" clearable filterable style="width: 150px">
            <el-option v-for="o in facetOperators" :key="o" :label="o" :value="o" />
          </el-select>
          <el-select v-model="filter.reason" placeholder="退款原因" size="small" clearable filterable style="min-width: 180px">
            <el-option v-for="rs in facetReasons" :key="rs" :label="rs" :value="rs" />
          </el-select>
          <!-- #273: 设备维度筛选(口径=devices 表, 与投诉积压同套参数; 命名与区块六一致: 设备型号/套餐/店铺/激活时长) -->
          <el-select v-model="filter.device_type" placeholder="设备型号" size="small" clearable filterable style="width: 150px">
            <el-option v-for="t in facetDeviceTypes" :key="t.value" :label="t.label" :value="t.value" />
          </el-select>
          <el-select v-model="filter.combo" placeholder="套餐" size="small" clearable filterable style="width: 160px">
            <el-option v-for="c in facetCombos" :key="c" :label="c" :value="c" />
          </el-select>
          <el-select v-model="filter.merchant" placeholder="店铺" size="small" clearable filterable style="width: 160px">
            <el-option v-for="m in facetMerchants" :key="m" :label="m" :value="m" />
          </el-select>
          <el-date-picker v-model="filter.activeRange" type="daterange" size="small" range-separator="~" start-placeholder="激活起" end-placeholder="激活止" value-format="YYYY-MM-DD" style="width: 210px" />
          <!-- #295: 激活时长桶(六、设备维度汇总 激活桶下钻入口) -->
          <el-select v-model="filter.activeAge" placeholder="激活时长" size="small" clearable style="width: 130px">
            <el-option label="0-15天" value="le15" />
            <el-option label="16-30天" value="16-30" />
            <el-option label="31-180天" value="31-180" />
            <el-option label="181-365天" value="181-365" />
            <el-option label=">365天" value="gt365" />
            <el-option label="无激活记录" value="none" />
          </el-select>
          <el-button size="small" :icon="RefreshLeft" @click="resetFilters">重置</el-button>
        </div>
        <el-table :data="detailList" border stripe size="small" max-height="420">
          <el-table-column prop="refund_no" label="退款单号" width="150" fixed show-overflow-tooltip />
          <el-table-column prop="device_sn" label="设备SN" width="130" fixed>
            <template #default="{ row }">
              <span class="sn-chip" @click="openDeviceAudit(row.device_sn)">{{ row.device_sn }}</span>
            </template>
          </el-table-column>
          <el-table-column label="退款路径" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="pathTagType(row.path)">{{ pathLabel(row.path) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="涉诉" width="96" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.complaint_type && row.complaint_type !== '普通退款'" size="small" :type="compTagType(row.complaint_type)">{{ row.complaint_type }}</el-tag>
              <span v-else class="comp-normal">普通退款</span>
            </template>
          </el-table-column>
          <el-table-column label="风险" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="riskTagType(row.risk_level)">{{ row.risk_level || '低风险' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="refund_reason" label="退款原因" min-width="140" show-overflow-tooltip />
          <el-table-column label="退款人" width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.operator || '-' }}</template>
          </el-table-column>
          <el-table-column prop="refund_amount" label="金额(¥)" width="110" align="right" sortable>
            <template #default="{ row }">{{ formatMoney(row.refund_amount) }}</template>
          </el-table-column>
          <el-table-column prop="d1_refund_created" label="发起时间" width="160" show-overflow-tooltip />
        </el-table>
        <div class="pag-wrap">
          <el-pagination
            v-model:current-page="detailPage"
            v-model:page-size="detailPageSize"
            :total="detailTotal"
            :page-sizes="[30, 50, 100, 200]"
            layout="total, sizes, prev, pager, next, jumper"
            background
            size="small"
            @current-change="loadDetail"
            @size-change="detailPage = 1; loadDetail()"
          />
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { InfoFilled, RefreshLeft, QuestionFilled } from '@element-plus/icons-vue'
import BaseChart from '@/components/BaseChart.vue'
import { refundDeep, refundWithdrawal } from '@/api/analytics'
import { useAudit } from '@/composables/useAudit'
import { formatMoney } from '@/utils/format'

const router = useRouter()

const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  grain: { type: String, default: 'day' },
  reloadKey: { type: Number, default: 0 },
})

const { openDeviceAudit } = useAudit()

const loading = ref(false)
const errorMsg = ref('')
const data = ref(null)
const detailList = ref([])
const detailPage = ref(1)
const detailPageSize = ref(50)
const detailTotal = ref(0)
const detailLoaded = ref(false)
const detailRisk = ref({ high: 0, medium: 0, low: 0 })
// 明细筛选 & 筛选候选项
// #273: 设备维度(device_type/combo/merchant/activeRange) 与投诉积压同一套后端参数
const filter = ref({ comp: '', path: '', operator: '', reason: '', device_type: '', combo: '', merchant: '', activeRange: null, activeAge: '' })
const facetOperators = ref([])
const facetReasons = ref([])
const facetDeviceTypes = ref([])
const facetCombos = ref([])
const facetMerchants = ref([])

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

const PATH_LABEL = { A: '钱包提现', B: '线上直退', D: '手动退款' }
function pathLabel(p) { return PATH_LABEL[p] || p || '-' }
function pathTagType(p) { return p === 'B' ? 'success' : p === 'A' ? 'warning' : p === 'D' ? 'danger' : 'info' }
function compTagType(c) { return c === '外部投诉' ? 'danger' : c === '商户号投诉' ? 'warning' : c === '内部投诉' ? 'primary' : c === '备注涉诉' ? 'info' : '' }
function riskTagType(r) { return r === '高风险' ? 'danger' : r === '中风险' ? 'warning' : 'success' }

// ---- #205 KPI 卡下钻（看板卡片数据说明与下钻规范 §3.2 优先级③: 跳到同页对应区块）----
// 时间窗口无需透传: 目标区块与本卡同处一页, 共用 props.start/end 的同一份数据。
function scrollToSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
// 现金流出总额 / 商户号实际(E) / 对账偏差 → 区块一「资金流出全景」按日四路构成表
function drillRoutes() { scrollToSection('sec-routes') }
// ★ #309 窗口充值 → 数据查看·充值流量(带窗口, 按充值时间落窗; 跨页下钻须带窗口, 规范 §3.3)
function drillRecharge() {
  router.push({ path: '/data/recharge-traffic', query: { start: props.start, end: props.end } })
}
// 钱包净水位 → 区块二「钱包水位监控」按日转入/转出/扣费构成表
function drillWallet() { scrollToSection('sec-wallet') }
// 涉诉退款占比 → 区块七明细并自动选中「涉诉」筛选(filter 变更由 watch 触发 loadDetail)
function drillComplaintDetail() {
  filter.value = { ...filter.value, comp: 'any' }
  scrollToSection('sec-detail')
}
// 线上直退 → 区块七明细并自动选中「路径=线上直退」筛选
function drillOnlineDetail() {
  filter.value = { ...filter.value, path: 'B' }
  scrollToSection('sec-detail')
}
// ⚠️ 「较上一周期」卡不做下钻: 纯环比派生值, 无独立明细(见该卡模板注释与ⓘ)。
// ⚠️ C 闪猫路不做单笔明细下钻: 明细台账接口未覆盖闪猫路, 强行给会缺一路误导(见文件头取舍①)。

function resetFilters() {
  filter.value = { comp: '', path: '', operator: '', reason: '', device_type: '', combo: '', merchant: '', activeRange: null, activeAge: '' }
  detailPage.value = 1
  loadDetail()
}
watch(filter, () => { detailPage.value = 1; loadDetail() }, { deep: true })

const summary = computed(() => data.value?.summary || {})
const routes = computed(() => data.value?.routes_daily || { dates: [], series: {}, table: [] })
const water = computed(() => data.value?.wallet_water || { dates: [] })
const complaint = computed(() => data.value?.complaint_split || { dates: [] })
const operators = computed(() => data.value?.reason_operator?.operators || [])

const chainPctText = computed(() => {
  const p = summary.value.chain_pct
  if (p == null) return '—'
  return (p >= 0 ? '+' : '') + Number(p).toFixed(1) + '%'
})
const chainColor = computed(() => {
  const p = Number(summary.value.chain_pct || 0)
  return p >= 0 ? '#dc2626' : '#16a34a'
})
const netColor = computed(() => (Number(summary.value.wallet_net || 0) >= 0 ? '#ea580c' : '#16a34a'))

const waterTable = computed(() => {
  const w = water.value
  if (!w.dates?.length) return []
  return w.dates.map((d, i) => ({
    date: d,
    tin_cnt: w.transfer_in_cnt?.[i] ?? 0, tin_amt: w.transfer_in_amt?.[i] ?? 0,
    wd_cnt: w.withdraw_cnt?.[i] ?? 0, wd_amt: w.withdraw_amt?.[i] ?? 0,
    fee_cnt: w.fee_cnt?.[i] ?? 0, fee_amt: w.fee_amt?.[i] ?? 0,
    net: w.net?.[i] ?? 0, cum: w.cum_net?.[i] ?? 0,
  }))
})

// ---- 手动退款登记水位(登记 vs 已退 = 蓄水) ----
const mwater = computed(() => data.value?.manual_water || { dates: [], reg_cnt: [], reg_amt: [], done_cnt: [], done_amt: [], net: [], cum_net: [] })
const manualWaterTable = computed(() => {
  const m = mwater.value
  if (!m.dates?.length) return []
  return m.dates.map((d, i) => ({
    date: d,
    reg_cnt: m.reg_cnt?.[i] ?? 0, reg_amt: m.reg_amt?.[i] ?? 0,
    done_cnt: m.done_cnt?.[i] ?? 0, done_amt: m.done_amt?.[i] ?? 0,
    net: m.net?.[i] ?? 0, cum: m.cum_net?.[i] ?? 0,
  }))
})
const manualWaterTotal = computed(() => data.value?.summary?.manual_water_total ?? 0)
const manualRegTotal = computed(() => mwater.value.reg_amt?.reduce((a, b) => a + (b || 0), 0) || 0)
const manualDoneTotal = computed(() => mwater.value.done_amt?.reduce((a, b) => a + (b || 0), 0) || 0)

const manualWaterOption = computed(() => {
  const m = mwater.value
  if (!m.dates?.length) return {}
  const dates = m.dates.map(d => String(d).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['登记', '已退', '蓄水', '累计蓄水'], top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '蓄水', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      // 登记 vs 已退 = 蓄水关系的进水/出水: 并列柱(不堆叠, 便于直接对比), 而非相加的堆叠
      { name: '登记', type: 'bar', data: m.reg_amt, itemStyle: { color: '#f59e0b' }, barMaxWidth: 26 },
      { name: '已退', type: 'bar', data: m.done_amt, itemStyle: { color: '#34d399' }, barMaxWidth: 26 },
      { name: '蓄水', type: 'line', yAxisIndex: 1, data: m.net, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
      { name: '累计蓄水', type: 'line', yAxisIndex: 1, data: m.cum_net, smooth: true, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' } },
    ],
  }
})

const complaintTable = computed(() => {
  const c = complaint.value
  if (!c.dates?.length) return []
  return c.dates.map((d, i) => ({
    date: d,
    int_cnt: c.internal_cnt?.[i] ?? 0, int_amt: c.internal_amt?.[i] ?? 0,
    mer_cnt: c.merchant_cnt?.[i] ?? 0, mer_amt: c.merchant_amt?.[i] ?? 0,
    ext_cnt: c.external_cnt?.[i] ?? 0, ext_amt: c.external_amt?.[i] ?? 0,
    rmk_cnt: c.remark_cnt?.[i] ?? 0, rmk_amt: c.remark_amt?.[i] ?? 0,
    comp_cnt: c.complaint_cnt?.[i] ?? 0, comp_amt: c.complaint_amt?.[i] ?? 0,
    nor_cnt: c.normal_cnt?.[i] ?? 0, nor_amt: c.normal_amt?.[i] ?? 0,
    ratio: c.ratio?.[i] ?? 0,
  }))
})

// ---- 图表 ----
const ROUTE_COLORS = {
  'A 线上直退': '#2563eb', 'B 钱包提现': '#f59e0b', 'C 闪猫退款': '#10b981', 'D 手动退款': '#a78bfa',
}

const routeOption = computed(() => {
  const r = routes.value
  if (!r.dates?.length) return {}
  const keys = Object.keys(ROUTE_COLORS)
  // E 实际现金流(对账基准) 作为独立曲线 overlay, 不与四路堆叠累加
  const eAmt = (r.table || []).map(row => row.actual_amt ?? 0)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: [...keys, '商户号实际(E)'], top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: r.dates.map(d => String(d).slice(5)), axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '实际(E)', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      ...keys.map(k => ({
        name: k, type: 'bar', stack: 'routes', barMaxWidth: 40,
        data: r.series[k]?.amounts || [],
        itemStyle: { color: ROUTE_COLORS[k] },
      })),
      { name: '商户号实际(E)', type: 'line', yAxisIndex: 1, data: eAmt, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#ca8a04' }, lineStyle: { width: 2.5, color: '#ca8a04' }, z: 10 },
    ],
  }
})

const waterOption = computed(() => {
  const w = water.value
  if (!w.dates?.length) return {}
  const dates = w.dates.map(d => String(d).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['转入', '转出', '净水位', '累计净水位'], top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '净水位', axisLabel: { fontSize: 11 }, splitLine: { show: false } },
    ],
    series: [
      // 转入 vs 转出 = 进水/出水的此消彼长关系: 并列柱(不堆叠, 便于直接对比), 而非相加的堆叠
      { name: '转入', type: 'bar', data: w.transfer_in_amt, itemStyle: { color: '#10b981' }, barMaxWidth: 26 },
      { name: '转出', type: 'bar', data: w.withdraw_amt, itemStyle: { color: '#f87171' }, barMaxWidth: 26 },
      { name: '净水位', type: 'line', yAxisIndex: 1, data: w.net, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' }, data: [{ yAxis: 0 }] } },
      { name: '累计净水位', type: 'line', yAxisIndex: 1, data: w.cum_net, smooth: true, symbol: 'circle', symbolSize: 5,
        itemStyle: { color: '#7c3aed' }, lineStyle: { width: 2, color: '#7c3aed', type: 'dashed' } },
    ],
  }
})

const COMP_COLORS = { '内部投诉': '#f97316', '商户号投诉': '#eab308', '外部投诉': '#dc2626', '备注涉诉': '#8b5cf6', '普通退款': '#9ca3af' }

const complaintOption = computed(() => {
  const c = complaint.value
  if (!c.dates?.length) return {}
  const dates = c.dates.map(d => String(d).slice(5))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: Object.keys(COMP_COLORS).concat(['涉诉占比%']), top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    grid: { left: 56, right: 64, bottom: 44, top: 40 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', name: '金额(元)', axisLabel: { fontSize: 11 }, splitLine: { lineStyle: { type: 'dashed' } } },
      { type: 'value', name: '占比%', min: 0, max: 100, axisLabel: { fontSize: 11, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '内部投诉', type: 'bar', stack: 'c', data: c.internal_amt, itemStyle: { color: COMP_COLORS['内部投诉'] }, barMaxWidth: 40 },
      { name: '商户号投诉', type: 'bar', stack: 'c', data: c.merchant_amt, itemStyle: { color: COMP_COLORS['商户号投诉'] }, barMaxWidth: 40 },
      { name: '外部投诉', type: 'bar', stack: 'c', data: c.external_amt, itemStyle: { color: COMP_COLORS['外部投诉'] }, barMaxWidth: 40 },
      { name: '备注涉诉', type: 'bar', stack: 'c', data: c.remark_amt, itemStyle: { color: COMP_COLORS['备注涉诉'] }, barMaxWidth: 40 },
      { name: '普通退款', type: 'bar', stack: 'c', data: c.normal_amt, itemStyle: { color: COMP_COLORS['普通退款'] }, barMaxWidth: 40 },
      { name: '涉诉占比%', type: 'line', yAxisIndex: 1, data: c.ratio, smooth: true, symbol: 'circle', symbolSize: 6,
        itemStyle: { color: '#2563eb' }, lineStyle: { width: 2.5, color: '#2563eb' } },
    ],
  }
})

const reasonOption = computed(() => {
  const list = data.value?.reason_operator?.reasons || []
  if (!list.length) return {}
  const sorted = [...list].sort((a, b) => b.count - a.count).slice(0, 10)
  const yData = sorted.map(r => r.name)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const i = params[0].dataIndex
        const it = sorted[i] || {}
        return `<b>${it.name}</b><br/>笔数: <b>${it.count}</b><br/>金额: ¥${formatMoney(it.amount)}<br/>占比: ${it.ratio}%`
      } },
    grid: { left: 150, right: 90, top: 16, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    yAxis: { type: 'category', data: yData, axisLabel: { fontSize: 11, width: 130, overflow: 'truncate' } },
    series: [{
      name: '笔数', type: 'bar', data: sorted.map(r => r.count), barMaxWidth: 18,
      itemStyle: { color: '#6366f1', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', fontSize: 11, formatter: (p) => `${p.value}笔` },
    }],
  }
})

// ---- #291 设备维度汇总(区块六): 设备型号/套餐/店铺 饼图Top5+其他 + 激活时长条形(#295 五桶) ----
// 2026-10-10 改造(方案B): 前三饼图(占比构成语义+Top5聚合防碎片) + 激活时长保条形(有序分桶,
// 保留新→老顺序语义); tooltip 全维度统一{分项名/占比/金额/单数/笔单价}; 位置上移到摘要下方。
// 2026-10-10 口径升级: 后端由 A 单路改为四路 A+B+C+D 全量, 每桶附 routes 分路明细 → tooltip 展示。
// 2026-10-11 #295: 激活时长六桶改五桶(0-15/16-30/31-180/181-365/>365/(无激活记录)), 与投诉侧统一。
const deviceDims = computed(() => data.value?.device_dims || {})
// #311 方案C: C 路闪猫退款独立汇总(业务相对独立, 不混入 4 路激活/维度分析)
const cRouteSummary = computed(() => data.value?.c_route_summary || {})

// 四路分路明细行(A/B/C/D, 固定顺序), 供 tooltip 展示每桶的路构成
const ROUTE_ORDER = ['A 线上直退', 'B 钱包提现', 'C 闪猫退款', 'D 手动退款']
function _fmtRouteLines(routes) {
  if (!routes) return ''
  const lines = ROUTE_ORDER.filter(k => routes[k])
    .map(k => `${k}: ¥${formatMoney(routes[k].amount || 0)} · ${routes[k].count || 0}笔`)
  return lines.length ? `<br/>${lines.join('<br/>')}` : ''
}

// 设备型号/套餐/店铺 通用饼图(按金额倒序 Top5+其他聚合; 扇区 value=金额; tooltip 含笔单价+分路)
function _dimPieOption(list) {
  if (!list?.length) return {}
  // 按金额倒序 → Top5 + 其他聚合(占比构成 vs 长尾取舍, 避免>8扇区不可读)
  const sorted = [...list].sort((a, b) => (b.amount || 0) - (a.amount || 0))
  const top5 = sorted.slice(0, 5)
  const rest = sorted.slice(5)
  const restCount = rest.reduce((s, r) => s + (r.count || 0), 0)
  const restAmt = rest.reduce((s, r) => s + (r.amount || 0), 0)
  const totalAmt = sorted.reduce((s, r) => s + (r.amount || 0), 0)
  const data = top5.map(r => ({ name: r.name, value: r.amount || 0, _raw: r }))
  if (rest.length) {
    // 「其他」桶的分路明细 = 长尾各行 routes 累加
    const restRoutes = {}
    rest.forEach(r => Object.entries(r.routes || {}).forEach(([k, v]) => {
      const b = restRoutes[k] || (restRoutes[k] = { count: 0, amount: 0 })
      b.count += v.count || 0
      b.amount += v.amount || 0
    }))
    data.push({ name: '其他', value: restAmt, _raw: { name: '其他', count: restCount, amount: restAmt, routes: restRoutes } })
  }
  return {
    tooltip: { trigger: 'item',
      formatter: (p) => {
        const it = p.data._raw || {}
        const ratio = totalAmt ? (p.value / totalAmt * 100).toFixed(1) : 0
        const cnt = it.count || 0
        const unit = cnt ? (p.value / cnt) : 0
        return `<b>${p.name}</b><br/>占比: ${ratio}%<br/>金额: ¥${formatMoney(p.value)}<br/>单数: ${cnt}笔<br/>笔单价: ¥${formatMoney(unit)}${_fmtRouteLines(it.routes)}`
      } },
    legend: { type: 'scroll', orient: 'vertical', right: 4, top: 'middle', align: 'left',
      itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 11 } },
    series: [{ type: 'pie', radius: ['38%', '68%'], center: ['38%', '50%'],
      avoidLabelOverlap: true, itemStyle: { borderColor: '#fff', borderWidth: 2 },
      label: { show: true, formatter: '{b}\n{d}%', fontSize: 11 },
      labelLine: { length: 8, length2: 8 },
      data }],
  }
}
const deviceTypeOption = computed(() => _dimPieOption(deviceDims.value.by_device_type))
const comboOption = computed(() => _dimPieOption(deviceDims.value.by_combo))
const merchantOption = computed(() => _dimPieOption(deviceDims.value.by_merchant))

// 激活时长: 复用 sale_days_split 真身(#295 五桶, 桶定义后端 _core.ACTIVE_AGE_BUCKETS 单一来源,
// 与投诉侧同名同序, 防同事实两处口径漂移)
// 按时长排序 = 后端返回桶顺序(0-15→16-30→...→(无激活记录), 即由短到长); tooltip 加占比+笔单价
// 点击桶 1:1 映射到明细 active_age 参数(锚点/路径差异致下钻为近似映射, 见区块六设计说明)
const SALE_AGE_PARAM = {
  '0-15天': 'le15', '16-30天': '16-30', '31-180天': '31-180',
  '181-365天': '181-365', '>365天': 'gt365',
  // #311 方案D: (无SN) 单独桶, SN 缺失的退款行不再混入 (无激活记录)
  '(无SN)': 'nosn', '(无激活记录)': 'none',
}
const activeAgeOption = computed(() => {
  const list = data.value?.sale_days_split || []
  if (!list.length) return {}
  // #296: 条形按单数(#291 拍板), tooltip 必须同时标清单数占比与金额占比,
  // 避免「占比」紧邻金额被误读为金额占比(6.1-9.30 实测两口径差 4.2pct)
  const totalCount = list.reduce((s, r) => s + (r.cnt || 0), 0)
  const totalAmount = list.reduce((s, r) => s + (r.amount || 0), 0)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const it = list[params[0].dataIndex] || {}
        const cnt = it.cnt || 0
        const cntRatio = totalCount ? (cnt / totalCount * 100).toFixed(1) : '0.0'
        const amt = it.amount || 0
        const amtRatio = totalAmount ? (amt / totalAmount * 100).toFixed(1) : '0.0'
        const unit = cnt ? (amt / cnt) : 0
        return `<b>${it.bucket}</b><br/>单数占比: ${cntRatio}%（${cnt}笔/${totalCount}笔, 条形按单数）<br/>金额占比: ${amtRatio}%（¥${formatMoney(amt)}）<br/>笔单价: ¥${formatMoney(unit)}${_fmtRouteLines(it.routes)}`
      } },
    grid: { left: 8, right: 56, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    yAxis: { type: 'category', data: list.map(r => r.bucket), inverse: true, axisLabel: { fontSize: 10 } },
    series: [{ name: '笔数', type: 'bar', data: list.map(r => r.cnt), barMaxWidth: 16,
      itemStyle: { color: '#10b981', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', fontSize: 10, formatter: '{c}' } }],
  }
})

// #291 桶点击 → 设筛选到「七、退款明细」
// 设备类型桶名是显示名(type_name), 需从 facets 反查类型码(code); 反查不到 → 不设筛选(避免假下钻)
// 点击语义统一(2026-10-10 修正): 四类桶一律「只设筛选, 不自动滚动」 ——
// 自动 scrollIntoView 会把图表(及 tooltip)推出视口, 体验中断(用户反馈"乱跳");
// 筛选生效由 filter watch 自动触发 loadDetail, 明细见下方「七、退款明细」, 用消息提示引导。
function drillDeviceDim(kind, name) {
  if (!name) return
  // 「其他」是饼图 Top5+其他 聚合出的长尾合成桶, 无对应真实筛选值 ——
  // 直接透传会变成 店铺=其他 / 套餐=其他 精确匹配 0 条(明细清单"空了"的假象), 禁止下钻。
  if (name === '其他') {
    ElMessage.info('「其他」为长尾聚合桶（Top5 之外的合计），不可下钻筛选')
    return
  }
  const patch = {}
  let dimLabel = ''
  if (kind === 'type') {
    const hit = facetDeviceTypes.value.find(t => t.label === name || (name && t.label && t.label.startsWith(name + '(')))
    if (!hit) return
    patch.device_type = hit.value
    dimLabel = '设备型号'
  } else if (kind === 'combo') {
    patch.combo = name
    dimLabel = '套餐'
  } else if (kind === 'merchant') {
    patch.merchant = name
    dimLabel = '店铺'
  } else if (kind === 'age') {
    const v = SALE_AGE_PARAM[name]
    if (!v) return
    patch.activeAge = v
    dimLabel = '激活时长'
  }
  filter.value = { ...filter.value, ...patch }
  ElMessage.success(`已筛选 ${dimLabel}=${name}，明细见下方「七、退款明细」`)
}

async function loadDetail() {
  const params = {}
  if (props.start) params.start = props.start + 'T00:00:00'
  if (props.end) params.end = props.end + 'T23:59:59'
  params.sort = 'd1_desc'
  params.page = detailPage.value
  params.page_size = detailPageSize.value
  if (filter.value.comp) params.comp = filter.value.comp
  if (filter.value.path) params.path = filter.value.path
  if (filter.value.operator) params.operator = filter.value.operator
  if (filter.value.reason) params.reason = filter.value.reason
  // #273: 设备维度筛选
  if (filter.value.device_type) params.device_type = filter.value.device_type
  if (filter.value.combo) params.combo = filter.value.combo
  if (filter.value.merchant) params.merchant = filter.value.merchant
  if (Array.isArray(filter.value.activeRange) && filter.value.activeRange[0]) {
    params.active_start = filter.value.activeRange[0]
    params.active_end = filter.value.activeRange[1]
  }
  // #295: 激活时长桶下钻(le15/16-30/31-180/181-365/gt365/none), 由「六、设备维度汇总」桶点击带入
  if (filter.value.activeAge) params.active_age = filter.value.activeAge
  try {
    const res = await refundWithdrawal.detail(params)
    detailTotal.value = res?.data?.total || 0
    detailList.value = res?.data?.list || []
    detailRisk.value = res?.data?.risk_summary || { high: 0, medium: 0, low: 0 }
    detailLoaded.value = true
  } catch (e) {
    detailList.value = []
    detailLoaded.value = false
  }
}

async function loadFacets() {
  const params = {}
  if (props.start) params.start = props.start
  if (props.end) params.end = props.end
  try {
    const res = await refundWithdrawal.facets(params)
    facetOperators.value = res?.data?.operators || []
    facetReasons.value = res?.data?.reasons || []
    // #273: 设备维度候选项(设备类型/套餐/商户号)
    facetDeviceTypes.value = res?.data?.device_types || []
    facetCombos.value = res?.data?.combos || []
    facetMerchants.value = res?.data?.merchants || []
  } catch (e) {
    /* 筛选候选项加载失败不阻塞明细列表 */
  }
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const params = { grain: props.grain }
    if (props.start) params.start = props.start
    if (props.end) params.end = props.end
    const [ovRes] = await Promise.all([refundDeep.decisionOverview(params)])
    data.value = ovRes?.data || null
    detailPage.value = 1
    loadDetail()
    loadFacets()
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载决策概览失败'
  } finally {
    loading.value = false
  }
}

watch(() => [props.start, props.end, props.grain, props.reloadKey], load)
onMounted(load)
</script>

<style scoped>
.decision-overview { width: 100%; }
.block-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.block-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 600; color: #111827; }
.block-hint { font-size: 12px; color: #6b7280; }
.sub-title { font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 10px; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mt-12 { margin-top: 12px; }
.error-msg { color: #f56c6c; padding: 12px; background: #fef0f0; border-radius: 4px; margin-bottom: 16px; }
.conclusion-line { line-height: 1.8; font-size: 13px; color: #334155; }
/* #311 方案C: C 路闪猫退款独立提示行(KPI 区下方, conclusions 上方) */
.c-route-hint { margin: 6px 0 4px; padding: 6px 10px; background: #fdf4ff; border-radius: 6px;
  font-size: 12px; color: #6b21a8; line-height: 1.5; }
.c-route-hint b { color: #9333ea; }
.kpi-card { border-radius: 10px; padding: 12px 14px 10px; border: 1px solid rgba(148,163,184,0.15); display: flex; flex-direction: column; gap: 3px; min-height: 84px; }
/* #205 可下钻卡片: cursor:pointer + hover 反馈(规范 §3.5 交互一致性) */
.kpi-drill { cursor: pointer; transition: box-shadow 0.15s ease, transform 0.15s ease; }
.kpi-drill:hover { box-shadow: 0 4px 12px -6px rgba(15, 23, 42, 0.25); transform: translateY(-1px); }
.kpi-label { font-size: 12px; font-weight: 600; }
.kpi-value { margin-top: 2px; font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; }
.unit { font-size: 12px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
.num { color: #374151; margin-right: 8px; }
.num.strong { font-weight: 700; }
.amt { color: #9ca3af; font-size: 12px; }
.amt.strong { font-weight: 600; color: #374151; }
.pos { color: #ea580c; font-weight: 600; }
.neg { color: #16a34a; font-weight: 600; }
.water-summary { font-size: 13px; color: #374151; padding: 8px 12px; background: #f8fafc; border-radius: 6px; border: 1px solid #e2e8f0; }
.water-summary b { font-size: 14px; margin-left: 4px; }
.sn-chip { color: #2563eb; cursor: pointer; font-weight: 600; text-decoration: underline; text-decoration-style: dotted; }
.sn-chip:hover { color: #1d4ed8; }
.detail-filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mb-12 { margin-bottom: 12px; }
.risk-summary { display: flex; align-items: center; gap: 16px; font-size: 12px; color: #6b7280; }
.risk-summary .risk-high { color: #dc2626; font-weight: 600; }
.risk-summary .risk-med { color: #d97706; font-weight: 600; }
.risk-summary .risk-low { color: #16a34a; }
.comp-normal { color: #9ca3af; font-size: 12px; }
.pag-wrap { display: flex; justify-content: flex-end; margin-top: 12px; }

/* ===== 移动端优化 (<=768px) ===== */
@media (max-width: 768px) {
  .block-card { padding: 12px; }
  .block-title { font-size: 14px; }
  .block-hint { font-size: 11px; }
  .kpi-card { padding: 8px 10px; min-height: 68px; }
  .kpi-label { font-size: 11px; }
  .kpi-value { font-size: 18px; }
  .kpi-sub { font-size: 10px; }
  .conclusion-line { font-size: 12px; line-height: 1.7; }
  /* 表格在手机上保持可横向滚动 */
  :deep(.el-table) { min-width: 620px; }
  :deep(.el-table__body-wrapper) { overflow-x: auto; }
  :deep(.el-card) { overflow: hidden; }
  .detail-filters { gap: 6px; }
}
</style>
