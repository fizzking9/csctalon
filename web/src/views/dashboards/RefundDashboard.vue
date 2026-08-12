<template>
  <div class="page-container">
    <div class="flex-between mb-16">
      <div class="flex-between" style="gap: 12px">
        <div class="page-title" style="margin: 0">{{ dashboard?.name || '加载中...' }}</div>
        <el-tag v-if="dashboard" size="small">{{ dashboard.category }}</el-tag>
        <el-tag v-if="dashboard?.is_preset" type="warning" size="small">预设</el-tag>
      </div>
      <div v-if="dashboard" class="filter-bar">
        <el-radio-group v-model="tabMode" size="small" @change="onTabChange">
          <el-radio-button label="today">概览</el-radio-button>
          <el-radio-button label="personnel">人员分析</el-radio-button>
          <el-radio-button label="device">设备分析</el-radio-button>
          <el-radio-button label="type">类型趋势</el-radio-button>
          <el-radio-button label="overview">综合看板</el-radio-button>
        </el-radio-group>
        <el-radio-group v-model="rangeMode" size="small" @change="onRangeChange">
          <el-radio-button label="7">近7天</el-radio-button>
          <el-radio-button label="30">近30天</el-radio-button>
          <el-radio-button label="90">近90天</el-radio-button>
          <el-radio-button label="default">默认</el-radio-button>
          <el-radio-button label="custom">自定义</el-radio-button>
        </el-radio-group>
        <el-date-picker
          v-if="rangeMode === 'custom'"
          v-model="customRange"
          type="daterange"
          size="small"
          value-format="YYYY-MM-DD"
          start-placeholder="开始"
          end-placeholder="结束"
          style="width: 240px"
          @change="onRangeChange"
        />
        <el-radio-group v-if="tabMode==='overview'" v-model="grainMode" size="small" @change="onGrainChange">
          <el-radio-button label="day">按日</el-radio-button>
          <el-radio-button label="week">按周</el-radio-button>
          <el-radio-button label="month">按月</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" :loading="batchLoading" @click="loadAll">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="dashboard?.description && tabMode==='overview'"
      :title="dashboard.description"
      type="info"
      :closable="false"
      class="mb-16"
    />
    <el-alert
      v-if="tabMode==='today'"
      type="success" :closable="false"
      title="退款看板 · 今日视图 (2026-08-04 新口径)"
      description="分母=退款专项组成员 + problem_type∈(不想用了/充值错误/设备故障/服务原因) 真退款类SN; 失败=T+7窗口内进入退款工单/退款单; 今日成功率因审批未走完会随时间更新,24点后趋于稳定."
      class="mb-16"
    />

    <div v-if="errorMsg" class="error-msg">{{ errorMsg }}</div>

    <!-- ============================================================== -->
    <!-- TAB 1: 今日视图 - 用户新增需求: 按天的今日监控 (日粒度+小时粒度)    -->
    <!-- ============================================================== -->
    <template v-if="tabMode==='today'">
      <!-- 今日 KPI条 (11张 KPI卡 保持和综合挽回一致) -->
      <section class="section-card mb-24">
        <div class="flex-between mb-12">
          <div>
            <div class="block-title">今日退款挽回 · KPI 核心</div>
            <div class="block-sub">新口径:退款专项组接待真退款类问题 4类 · T+7 判定失败</div>
          </div>
          <el-tag size="small" effect="dark" type="danger">{{ todayDateStr }}</el-tag>
        </div>
        <el-row :gutter="12" class="mb-12">
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#eff6ff">
              <div class="kpi-mini-label" style="color:#2563eb">总接待SN(分母)</div>
              <div class="kpi-mini-sub">退款专员真退款类去重</div>
              <div class="kpi-mini-value" style="color:#2563eb">
                {{ formatNum(recoveryData.kpi.total_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#f0fdf4">
              <div class="kpi-mini-label" style="color:#16a34a">挽留成功</div>
              <div class="kpi-mini-sub">未进入退款流程</div>
              <div class="kpi-mini-value" style="color:#16a34a">
                {{ formatNum(recoveryData.kpi.success_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#fef2f2">
              <div class="kpi-mini-label" style="color:#dc2626">挽留失败</div>
              <div class="kpi-mini-sub">最终完成退款</div>
              <div class="kpi-mini-value" style="color:#dc2626">
                {{ formatNum(recoveryData.kpi.failed_sn) }}<span class="kpi-mini-unit">个</span>
              </div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" :style="rateBg(recoveryData.kpi.success_rate)">
              <div class="kpi-mini-label" :style="rateColor(recoveryData.kpi.success_rate)">挽回成功率</div>
              <div class="kpi-mini-sub">成功SN / 总接待SN</div>
              <div class="kpi-mini-value" :style="rateColor(recoveryData.kpi.success_rate)">
                {{ Number(recoveryData.kpi.success_rate||0).toFixed(1) }}<span class="kpi-mini-unit">%</span>
              </div>
            </div>
          </el-col>
          <!-- 4 分类单量 -->
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#f3f4f6">
              <div class="kpi-mini-label" style="color:#6b7280">放弃单数</div>
              <div class="kpi-mini-sub">系统清退·无需退款等</div>
              <div class="kpi-mini-value" style="color:#6b7280">{{ formatNum(recoveryData.kpi.abandon_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.abandon_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#fef2f2">
              <div class="kpi-mini-label" style="color:#dc2626">失败单数</div>
              <div class="kpi-mini-sub">拦截失败·真实退款成功</div>
              <div class="kpi-mini-value" style="color:#dc2626">{{ formatNum(recoveryData.kpi.fail_cnt) }}<span class="kpi-mini-unit">单</span></div>
              <div class="kpi-mini-money">¥{{ formatMoney(Number(recoveryData.kpi.fail_amount||0)) }}</div>
            </div>
          </el-col>
          <el-col :xs="12" :sm="6" :md="3">
            <div class="kpi-mini-card" style="background:#f0fdf4">
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

      <!-- 今日 按小时 趋势 24 col + 近7天 日趋势 24 col (两张大图) -->
      <section class="section-card mb-24">
        <div class="block-title mb-12">按时间粒度监控</div>
        <el-row :gutter="16">
          <el-col :xs="24" :lg="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">今日 按小时 · 挽回趋势 (00:00 ~ 当前)</span>
                  <el-tag size="small" effect="plain" type="success">日视图 · 小时粒度</el-tag>
                </div>
              </template>
              <BaseChart v-if="todayHourlyOption && todayHourlyOption.series && todayHourlyOption.series.length" :option="todayHourlyOption" height="340px" />
              <el-empty v-else description="今日暂无小时级趋势数据" />
            </el-card>
          </el-col>
          <el-col :xs="24" :lg="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">近 {{ recentDaysDaily }} 天 · 按日 挽回趋势</span>
                  <el-tag size="small" effect="plain" type="warning">日视图 · 日粒度</el-tag>
                </div>
              </template>
              <BaseChart v-if="dailyTrendOption && dailyTrendOption.series && dailyTrendOption.series.length" :option="dailyTrendOption" height="340px" />
              <el-empty v-else description="暂无日趋势数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>

      <!-- Tab1 强化: Top5 高风险设备 + 金额分桶 -->
      <section class="section-card mb-24">
        <div class="block-title mb-12">高风险设备 · 金额分布</div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="14" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">Top5 高风险设备 (按退款金额倒序)</span>
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
                  <span style="font-weight:600">失败设备 · 金额分桶</span>
                  <el-tag size="small" effect="plain" type="info">按退款金额区间</el-tag>
                </div>
              </template>
              <BaseChart v-if="amountBucketOption && amountBucketOption.series && amountBucketOption.series.length" :option="amountBucketOption" height="320px" />
              <el-empty v-else description="暂无金额分桶数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>

      <!-- 今日视图下半屏: 失败原因TOP(7类柱图) + 退款专员接待排名 (各12+12) -->
      <section class="section-card">
        <div class="block-title mb-12">归因 · 绩效 (今日视角)</div>
        <el-row :gutter="16">
          <el-col :xs="24" :md="12" class="mb-16">
            <el-card shadow="never">
              <template #header>
                <div style="display:flex; align-items:center; justify-content:space-between">
                  <span style="font-weight:600">挽留失败 SN 原因分布(归一化 7 大类)</span>
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
                  <span style="font-weight:600">退款专员 接待/挽回/操作 明细</span>
                  <el-tag size="small" effect="plain" type="primary">按 接待SN 倒序</el-tag>
                </div>
              </template>
              <el-table :data="recoveryData.staff_detail || []" border stripe size="small" max-height="360">
                <el-table-column label="姓名" width="100" fixed><template #default="{row}"><span style="font-weight:600">{{row.name}}</span></template></el-table-column>
                <el-table-column label="组" width="96"><template #default="{row}"><el-tag size="small" effect="plain" :type="grpTagType(row.group)">{{row.group||'未分组'}}</el-tag></template></el-table-column>
                <el-table-column prop="total_sn" label="接待SN" width="82" align="right" sortable><template #default="{row}">{{formatNum(row.total_sn)}}个</template></el-table-column>
                <el-table-column prop="success_sn" label="成功" width="64" align="right"><template #default="{row}"><span style="color:#16a34a">{{formatNum(row.success_sn)}}</span></template></el-table-column>
                <el-table-column prop="failed_sn" label="失败" width="64" align="right"><template #default="{row}"><span style="color:#dc2626">{{formatNum(row.failed_sn)}}</span></template></el-table-column>
                <el-table-column prop="success_rate" label="成功率" width="84" align="right" sortable :sort-method="(a,b)=>Number(a.success_rate??-1)-Number(b.success_rate??-1)">
                  <template #default="{row}">
                    <template v-if="row.success_rate==null"><span style="color:#9ca3af">—</span></template>
                    <template v-else>
                      <el-tag size="small" :effect="Number(row.success_rate||0)>=38?'dark':'plain'" :type="Number(row.success_rate||0)>=38?'success':(Number(row.success_rate||0)>=30?'warning':'danger')">
                        {{Number(row.success_rate||0).toFixed(1)}}%
                      </el-tag>
                    </template>
                  </template>
                </el-table-column>
                <el-table-column prop="refund_op_cnt" label="退款操作SN" width="100" align="right" sortable><template #default="{row}"><span :style="{color:Number(row.refund_op_cnt||0)>0?'#1d4ed8':'#9ca3af',fontWeight:Number(row.refund_op_cnt||0)>0?600:400}">{{formatNum(row.refund_op_cnt)}}个</span></template></el-table-column>
                <el-table-column prop="refund_op_amount" label="退款操作金额" width="116" align="right" sortable><template #default="{row}"><span :style="{color:Number(row.refund_op_amount||0)>0?'#b91c1c':'#9ca3af',fontWeight:Number(row.refund_op_amount||0)>0?600:400}">¥{{formatMoney(row.refund_op_amount)}}</span></template></el-table-column>
              </el-table>
              <el-empty v-if="!recoveryData.staff_detail?.length" description="暂无人员数据" />
            </el-card>
          </el-col>
        </el-row>
      </section>
    </template>

    <!-- ============================================================== -->
    <!-- TAB: 人员分析 - 人员对比表+趋势+热力图+设备清单下钻             -->
    <!-- ============================================================== -->
    <template v-else-if="tabMode==='personnel'">
      <PersonnelAnalysis :start="effectiveStart" :end="effectiveEnd" />
    </template>

    <!-- ============================================================== -->
    <!-- TAB: 设备分析 - Top风险设备+时差分布+设备详情下钻               -->
    <!-- ============================================================== -->
    <template v-else-if="tabMode==='device'">
      <DeviceAnalysis :start="effectiveStart" :end="effectiveEnd" />
    </template>

    <!-- ============================================================== -->
    <!-- TAB: 类型×趋势 - 原因/人员类型趋势+热力图+组间对比             -->
    <!-- ============================================================== -->
    <template v-else-if="tabMode==='type'">
      <TypeTrend :start="effectiveStart" :end="effectiveEnd" />
    </template>

    <!-- ============================================================== -->
    <!-- TAB 5: 综合看板 - 视图驱动 (挽回7视图 + 现金流6视图 = 13)        -->
    <!-- ============================================================== -->
    <template v-else>
      <!-- ============ 区块1: 退款挽回专项 (视图驱动,7个视图) ============ -->
      <section v-if="recoveryItems.length" class="section-card mb-24">
        <div class="flex-between mb-12">
          <div>
            <div class="block-title">一、退款挽回专项 (2026-08-04 新口径 · 视图驱动)</div>
            <div class="block-sub">
              分母=退款专项组成员接待 problem_category=退款 AND problem_type∈(不想用了/充值错误/设备故障/服务原因) 去重SN ·
              失败=分母SN∩T+7窗口内最终退款(退款单∪退款工单) · 成功率=成功/分母 · 已移除原「组长/主管接待强制失败」规则
            </div>
          </div>
          <el-tooltip effect="light" placement="top" :show-arrow="false" popper-class="score-pop" teleported>
            <template #content>
              <div style="max-width:480px; line-height:1.9; font-size:12px; color:#4b5563;">
                <div style="font-weight:600; color:#1f2937; margin-bottom:6px">【退款挽回 新口径 2026-08-11】</div>
                <div>① 总接待SN(分母): 所有人员(专职退款挽回专员+组长/主管+其他岗位)在周期内, 接待 problem_category='退款' 的去重 device_sn (所有退款理由均计入, 包含网络问题/其他等; 不限制problem_type)</div>
                <div>② <b>专职判定:</b> 退款挽回专员 = Person.specialty='退款挽回' (与分组无关, 同一组如朱海洋组内既有套餐专项也有退款挽回专项, 按人员维表specialty判定)</div>
                <div>③ <b>非专职挽回:</b> 任何人员(含组长/主管)接待退款类SN但最终未产生退款行为(refunds表退款/退款类工单) → 也算挽回成功</div>
                <div>④ 挽留失败(分子): 分母SN中, 在 [最早接待时间, 最早接待时间+7天] T+7 宽限窗口内 出现在 refunds线上退款单(非驳回/无需退款) 或 service_tickets(service_category=退款,非终止)中的 SN → 防止接待后才走完退款审批的SN被误判为成功</div>
                <div>⑤ 挽留成功: 分母SN - 失败SN → 被客服沟通拦截下来, 最终没走到退款流程</div>
                <div>⑥ 成功率 = 挽留成功 / 总接待SN</div>
                <div style="margin-top:6px;padding-top:6px;border-top:1px dashed #e5e7eb">
                  <b>移除项:</b> 原规则「组长/主管接待SN强制失败」用户已确认移除(2026-08-04).<br/>
                  <b>分组≠专项:</b> 分组(group_name)是行政/管理维度, 专项(specialty)是业务内容维度, 两者无必然关联, 一律按Person.specialty判定本职/转接.<br/>
                  <b>费占比:</b> 单量口径=成功单数/(成功单数+实际退款单数(失败+退充)) · 参考目标 37%+<br/>
                  <b>今日视图:</b> 顶部Tab切到「今日视图」查看 KPI + 小时趋势 + 近7日日趋势
                </div>
              </div>
            </template>
            <el-tag size="small" effect="plain" type="primary">查看口径</el-tag>
          </el-tooltip>
        </div>
        <el-row :gutter="16">
          <el-col
            v-for="item in recoveryItems"
            :key="item.view_id"
            :span="item.span"
            class="mb-16"
          >
            <ViewItemCard
              :view-id="item.view_id"
              :title="item.title || ''"
              :start="effectiveStart"
              :end="effectiveEnd"
              :prefetched-result="itemResult(item.view_id).result"
              :prefetched-error="itemResult(item.view_id).error"
              :prefetched-view="itemView(item.view_id)"
              :batch-loading="batchLoading"
            />
          </el-col>
        </el-row>
      </section>

      <!-- ============ 区块2: 现金流 / 退款基础 ============ -->
      <section v-if="cashflowItems.length" class="section-card">
        <div class="block-title">二、现金流与退款基础分析</div>
        <el-row :gutter="16">
          <el-col
            v-for="item in cashflowItems"
            :key="item.view_id"
            :span="item.span"
            class="mb-16"
          >
            <ViewItemCard
              :view-id="item.view_id"
              :title="item.title || ''"
              :start="effectiveStart"
              :end="effectiveEnd"
              :prefetched-result="itemResult(item.view_id).result"
              :prefetched-error="itemResult(item.view_id).error"
              :prefetched-view="itemView(item.view_id)"
              :batch-loading="batchLoading"
            />
          </el-col>
        </el-row>
      </section>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import ViewItemCard from '@/components/ViewItemCard.vue'
import BaseChart from '@/components/BaseChart.vue'
import PersonnelAnalysis from './refund/PersonnelAnalysis.vue'
import DeviceAnalysis from './refund/DeviceAnalysis.vue'
import TypeTrend from './refund/TypeTrend.vue'
import { getDashboard, listDashboards, executeDashboard } from '@/api/dashboard'
import { today } from '@/api/analytics'
import { formatMoney } from '@/utils/format'

// ---- 三元样式兜底 helpers (代替模板内嵌套三元,解决 Vue 编译 134:46 parse 报错) ----
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

const route = useRoute()

const dashboard = ref(null)
const tabMode = ref('overview')   // 'overview'=综合看板 / 'today'=今日视图
const rangeMode = ref('30')
const customRange = ref([])
const grainMode = ref('day')
const batchLoading = ref(false)
const batchResults = ref({})
const errorMsg = ref('')

const todayDateStr = computed(() => dayjs().format('YYYY-MM-DD (dddd)'))
const recentDaysDaily = ref(7)

// ---- 退款挽回数据 & 图表配置 ----
function emptyKpi() {
  return {
    total_sn: 0, success_sn: 0, failed_sn: 0, success_rate: 0,
    abandon_cnt: 0, fail_cnt: 0, success_cnt: 0, recharge_cnt: 0, target_total_cnt: 0,
    abandon_amount: 0, fail_amount: 0, success_amount: 0, recharge_amount: 0,
    target_total_amount: 0, actual_refund_amount: 0, fee_ratio: 0,
    _calc_note: {},
  }
}
const recoveryData = ref({
  kpi: emptyKpi(), reason_dist: [], staff_detail: [], weekly_trend: [], daily_reason: [], daily_trend: [], hourly_today: [],
  top_risk_devices: [], amount_buckets: [],
})
function formatNum(v) {
  if (v == null || v === '') return '0'
  const n = Number(v)
  if (Number.isNaN(n)) return '0'
  return n.toLocaleString('zh-CN')
}

// --- 看板视图分段 (2026-08-12: 移除催单块, 按 view_code 前缀划分) ---
// 退款挽回视图块 = view_code 以 preset_refund_recovery 开头
const recoveryItems = computed(() => (dashboard.value?.items || []).filter((it) => it.view_code?.startsWith('preset_refund_recovery') && (it.visible !== false)))
// 现金流模块 = 其余非 recovery 视图
const cashflowItems = computed(() => (dashboard.value?.items || []).filter((it) => !it.view_code?.startsWith('preset_refund_recovery') && (it.visible !== false)))

// --- 时间范围 ---
// 今日视图: start=今天, end=今天 (固定 不随 rangeMode); 综合视图: 按 rangeMode
const effectiveStart = computed(() => {
  if (tabMode.value === 'today') return dayjs().format('YYYY-MM-DD')
  if (rangeMode.value === 'custom') return customRange.value?.[0] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().subtract(Number(rangeMode.value), 'day').format('YYYY-MM-DD')
})
const effectiveEnd = computed(() => {
  if (tabMode.value === 'today') return dayjs().format('YYYY-MM-DD')
  if (rangeMode.value === 'custom') return customRange.value?.[1] || ''
  if (rangeMode.value === 'default' || !rangeMode.value) return ''
  return dayjs().format('YYYY-MM-DD')
})

function itemResult(viewId) {
  const r = batchResults.value[viewId]
  return r || { result: null, error: null }
}
function itemView(viewId) {
  const r = batchResults.value[viewId]
  if (!r || r.view_name === null || r.view_name === undefined) return undefined
  return { name: r.view_name, ratios: r.ratios, is_preset: r.is_preset }
}

function onTabChange() {
  // 新 Tab (personnel/device/type) 由子组件自行加载数据, 仅 today/overview 需要父组件加载
  if (tabMode.value === 'today' || tabMode.value === 'overview') {
    loadAll()
  }
}
function onRangeChange() {}
function onGrainChange() { loadAll() }

// --------- 图表 options (今日视图 + 原综合用 reasonBarOption) ---------
const todayHourlyOption = computed(() => {
  const hl = recoveryData.value?.hourly_today || []
  if (!hl || !hl.length) return {}
  const x = hl.map(r => r.hour)
  const total = hl.map(r => Number(r.total_sn) || 0)
  const succ = hl.map(r => Number(r.success_sn) || 0)
  const fail = hl.map(r => Number(r.failed_sn) || 0)
  const rates = hl.map(r => {
    const t = Number(r.total_sn) || 0
    return t ? round2(Number(r.success_sn) / t * 100) : 0
  })
  return _buildDoubleYOption({
    title: '',
    x, barSeries: [
      { name: '总接待SN', data: total, color: '#bfdbfe' },
      { name: '挽留成功SN', data: succ, color: '#34d399' },
      { name: '挽留失败SN', data: fail, color: '#fca5a5' },
    ],
    lineSeries: [
      { name: '成功率%', data: rates, color: '#047857', lineWidth: 3 },
    ],
    y2Min: 0, y2Max: 100, y2Suffix: '%',
    legend: ['总接待SN', '挽留成功SN', '挽留失败SN', '成功率%'],
    heightPx: 340,
  })
})

const dailyTrendOption = computed(() => {
  const dl = recoveryData.value?.daily_trend || []
  if (!dl || !dl.length) return {}
  // 近 recentDaysDaily(默认7) 天用于今日视图, 不超过实际总天数
  const sliceN = Math.min(recentDaysDaily.value, dl.length)
  const arr = dl.slice(-sliceN)
  const x = arr.map(r => r.date ? String(r.date).slice(5) : '')  // MM-DD
  const total = arr.map(r => Number(r.total_sn) || 0)
  const succ = arr.map(r => Number(r.success_sn) || 0)
  const fail = arr.map(r => Number(r.failed_sn) || 0)
  const rates = arr.map(r => round2(Number(r.success_rate) || 0))
  return _buildDoubleYOption({
    title: '',
    x, barSeries: [
      { name: '总接待SN', data: total, color: '#93c5fd' },
      { name: '挽留成功SN', data: succ, color: '#4ade80' },
      { name: '挽留失败SN', data: fail, color: '#f87171' },
    ],
    lineSeries: [
      { name: '成功率%', data: rates, color: '#059669', lineWidth: 3 },
    ],
    y2Min: 0, y2Max: 100, y2Suffix: '%',
    legend: ['总接待SN', '挽留成功SN', '挽留失败SN', '成功率%'],
    heightPx: 340,
  })
})

const reasonBarOption = computed(() => {
  const list = recoveryData.value?.reason_dist || []
  if (!list.length) return {}
  const labels = list.map(x => x.reason)
  const cnts = list.map(x => Number(x.order_cnt) || 0)
  const ratios = list.map(x => round2(Number(x.ratio) || 0))
  const pal = ['#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#6366f1', '#ec4899', '#0ea5e9']
  const barData = cnts.map((v, i) => ({
    value: v,
    itemStyle: { color: pal[i % pal.length], borderRadius: [4, 4, 0, 0] },
  }))
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
      { name: '失败SN数', type: 'bar', data: barData, barMaxWidth: 34,
        label: { show: true, position: 'top', fontSize: 11,
          formatter: (p) => `${p.value} (${ratios[p.dataIndex]}%)` } },
      { name: '占比%', type: 'line', yAxisIndex: 1, data: ratios, smooth: true, symbol: 'circle', symbolSize: 7,
        itemStyle: { color: '#7c3aed' }, lineStyle: { width: 3, color: '#7c3aed' } },
    ],
  }
})

// ---- echarts 通用构建辅助 (双Y轴: 左柱 右线) ----
function _buildDoubleYOption({ title, x, barSeries = [], lineSeries = [], y2Min = null, y2Max = null, y2Suffix = '', legend = [], heightPx = 340 }) {
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
      name: b.name, type: 'bar', data: b.data, barMaxWidth: 16,
      itemStyle: { color: b.color || '#93c5fd', borderRadius: [4, 4, 0, 0] },
    })
  }
  for (const l of lineSeries) {
    series.push({
      name: l.name, type: 'line', yAxisIndex: 1, data: l.data, smooth: true,
      symbol: 'circle', symbolSize: 7,
      itemStyle: { color: l.color || '#15803d' },
      lineStyle: { width: l.lineWidth || 3, color: l.color || '#15803d' },
    })
  }
  return {
    title: title ? { text: title, left: 'center', top: 6, textStyle: { fontSize: 13, fontWeight: 600, color: '#111827' } } : undefined,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: legend || [], top: title ? 30 : 0, type: 'scroll' },
    grid: { left: 56, right: 72, bottom: 44, top: (title ? 60 : 44) },
    xAxis: { type: 'category', data: x, boundaryGap: true, axisLabel: { fontSize: 11 } },
    yAxis: [y1, y2],
    series,
    animationDuration: 400,
  }
}
function round2(v) {
  const n = Number(v || 0)
  return Math.round(n * 10) / 10
}

const GRP_COLOR_MAP = {
  '朱海洋组': 'success', '董梦玲组': 'warning', '吴晓龙组': 'primary', '叶珍凤组': 'danger', '投诉专项组': 'info',
}
function grpTagType(g) {
  if (!g || g === '未分组') return 'info'
  return GRP_COLOR_MAP[g] || 'info'
}

// ---- Tab1 强化: Top5 高风险设备 + 金额分桶 ----
const REASON_TAG_MAP = {
  '不想用了': 'info', '充值错误': 'warning', '设备故障': 'danger',
  '服务原因': 'primary', '其他': 'info',
}
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

const amountBucketOption = computed(() => {
  const list = recoveryData.value?.amount_buckets || []
  if (!list.length) return {}
  const palette = ['#93c5fd', '#fbbf24', '#fb923c', '#dc2626']
  const data = list.map((b, i) => ({
    name: b.bucket,
    value: b.sn_cnt,
    amount: b.amount,
    ratio: b.ratio,
    itemStyle: { color: palette[i % palette.length] },
  }))
  const totalCnt = list.reduce((s, x) => s + Number(x.sn_cnt || 0), 0)
  return {
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        const d = p.data || {}
        const amt = Number(d.amount || 0)
        return `<b>${p.name}</b><br/>设备数: <b>${p.value}</b> 个 (${Number(d.ratio||0).toFixed(1)}%)<br/>金额合计: ¥${formatMoney(amt)}`
      },
    },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: ['42%', '68%'],
      center: ['50%', '46%'],
      avoidLabelOverlap: true,
      label: {
        show: true,
        formatter: (p) => `${p.name}\n${p.value}个`,
        fontSize: 11,
      },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 600 },
        itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.15)' },
      },
      data,
    }],
    graphic: totalCnt ? {
      type: 'text', left: 'center', top: '42%',
      style: { text: `共${totalCnt}\n失败设备`, fontSize: 13, fontWeight: 600, fill: '#374151', textAlign: 'center' },
    } : undefined,
  }
})

async function resolveDashboardId() {
  const code = 'preset_dashboard_refund'
  const res = await listDashboards({ page: 1, page_size: 100 })
  const d = res.data.items.find((x) => x.code === code)
  return d?.id || null
}

async function loadDashboard() {
  errorMsg.value = ''
  dashboard.value = null
  batchResults.value = {}
  try {
    const id = await resolveDashboardId()
    if (!id) {
      errorMsg.value = '退款看板不存在或无权访问'
      return
    }
    const res = await getDashboard(id)
    dashboard.value = res.data
    if (dashboard.value.time_range_default?.days) {
      rangeMode.value = String(dashboard.value.time_range_default.days)
    } else {
      rangeMode.value = 'default'
    }
    await loadAll()
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载看板失败'
  }
}

async function loadAll() {
  if (!dashboard.value) return
  batchLoading.value = true
  try {
    const params = {}
    if (effectiveStart.value) params.start = effectiveStart.value
    if (effectiveEnd.value) params.end = effectiveEnd.value
    if (grainMode.value !== 'day' && tabMode.value === 'overview') params.time_grain = grainMode.value
    // 并发:
    // · 综合看板(Tab overview) → executeDashboard 拉取 18 视图批量结果
    // · 今日视图(Tab today)   → 不跑批量视图节省后端,只拉 refundRecovery 核心数据(KPI/hourly/daily/staff/reason)
    const tasks = []
    if (tabMode.value === 'overview') tasks.push(executeDashboard(dashboard.value.id, params))
    else tasks.push(Promise.resolve({ data: { items: [] } }))
    tasks.push(today.refundRecovery(params))
    const [dashRes, recoverRes] = await Promise.all(tasks)
    const map = {}
    for (const it of dashRes.data.items || []) {
      map[it.view_id] = it
    }
    batchResults.value = map
    recoveryData.value = Object.assign(
      { kpi: emptyKpi(), reason_dist: [], staff_detail: [], weekly_trend: [], daily_reason: [], daily_trend: [], hourly_today: [],
        top_risk_devices: [], amount_buckets: [] },
      recoverRes.data || {}
    )
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || '加载失败'
  } finally {
    batchLoading.value = false
  }
}

watch([effectiveStart, effectiveEnd], () => {
  if (dashboard.value) loadAll()
})
watch(
  () => [route.name, route.params.id, route.params.code],
  () => { loadDashboard() }
)

onMounted(loadDashboard)
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.error-msg {
  color: #f56c6c;
  padding: 12px;
  background: #fef0f0;
  border-radius: 4px;
}
.block-title {
  font-size: 17px;
  font-weight: 700;
  color: #111827;
  letter-spacing: 0.3px;
  margin-bottom: 4px;
}
.block-sub {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 16px;
}
.section-card { }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.mb-24 { margin-bottom: 24px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }

/* 退款挽回 KPI 卡样式 (和 Today.vue 保持一致) */
.kpi-mini-card {
  border-radius: 10px;
  padding: 12px 14px 10px;
  border: 1px solid rgba(148,163,184,0.15);
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-height: 88px;
  transition: transform .15s ease, box-shadow .15s ease;
}
.kpi-mini-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px -8px rgba(15,23,42,0.18);
}
.kpi-mini-label { font-size: 12px; font-weight: 600; letter-spacing: 0.2px; line-height: 1.3; }
.kpi-mini-sub { font-size: 10.5px; color: #6b7280; line-height: 1.3; opacity: 0.9; }
.kpi-mini-value { margin-top: 2px; font-size: 22px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; letter-spacing: -0.3px; }
.kpi-mini-unit { font-size: 13px; font-weight: 500; margin-left: 2px; opacity: 0.85; }
.kpi-mini-money { margin-top: 1px; font-size: 11.5px; font-weight: 500; color: #374151; font-variant-numeric: tabular-nums; opacity: 0.92; }
</style>
