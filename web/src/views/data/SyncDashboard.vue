<template>
  <div class="sync-layout">
    <!-- ==================== 左栏(4 个手风琴面板: 执行 / 设置 / 状态 / 历史) ==================== -->
    <div class="sync-left">
      <el-collapse v-model="activePanel" accordion>

        <!-- ① 面板1: 执行操作 (默认展开,所有人可见) -->
        <el-collapse-item title="执行操作 (定时 / 手动同步)" name="run">
          <div class="panel-inner">
            <!-- 定时同步控制 -->
            <el-card class="mb-16" shadow="never">
              <template #header>
                <div class="flex justify-between items-center">
                  <span>定时同步(BMS + SCRM 共用调度器)</span>
                  <div class="flex gap-8 items-center">
                    <el-tag v-if="scheduleStatus.enabled" type="success">运行中</el-tag>
                    <el-tag v-else type="info">已停止</el-tag>
                    <el-switch
                      v-model="scheduleEnabled"
                      :loading="scheduleToggling"
                      active-text="启用"
                      inactive-text="停止"
                      @change="toggleSchedule"
                    />
                  </div>
                </div>
              </template>
              <el-descriptions :column="3" border size="small">
                <el-descriptions-item label="定时计划">
                  <!-- #51: 展示槽位节拍(时段+间隔), 替代"整点 tag"列表 -->
                  <el-tag size="small" type="primary" effect="plain">
                    {{ scheduleStatus.start_hour }}:00 ~ {{ scheduleStatus.end_hour }}:00
                  </el-tag>
                  <span class="ml-8">每 {{ scheduleStatus.interval_minutes }} 分钟</span>
                  <span class="ml-8 dim">({{ scheduleStatus.slot_count }} 次/天)</span>
                </el-descriptions-item>
                <el-descriptions-item label="下次执行">{{ formatTime(scheduleStatus.next_run) || '-' }}</el-descriptions-item>
                <el-descriptions-item label="凭证状态">
                  <span class="credential-group">
                    <el-tag
                      v-if="hasBmsCredentials"
                      type="success"
                      size="small"
                      effect="plain"
                    >BMS 已配</el-tag>
                    <el-tag v-else type="danger" size="small" effect="plain">BMS 未配</el-tag>
                    <el-tag
                      v-if="hasScrmCredentials"
                      type="success"
                      size="small"
                      effect="plain"
                      class="ml-4"
                    >SCRM 已配</el-tag>
                    <el-tag v-else type="danger" size="small" effect="plain" class="ml-4">SCRM 未配</el-tag>
                  </span>
                </el-descriptions-item>
              </el-descriptions>

              <!-- #51: 高频节拍配置(时段+间隔, 管理员可改, 改后即时生效) -->
              <el-divider content-position="left">高频采集节拍</el-divider>
              <ScheduleConfigPanel ref="scheduleCfgRef" class="mb-12" />

              <el-alert
                v-if="(!hasBmsCredentials || !hasScrmCredentials) && !isAdmin"
                type="warning"
                :closable="false"
                show-icon
                class="mt-16"
              >
                {{ !hasBmsCredentials ? 'BMS 默认源未全部配置。' : '' }}
                {{ !hasScrmCredentials ? 'SCRM 凭证未配置。' : '' }}
                请联系管理员配置。
              </el-alert>
              <el-alert
                v-if="(!hasBmsCredentials || !hasScrmCredentials) && isAdmin"
                type="warning"
                :closable="false"
                show-icon
                class="mt-16"
              >
                {{ !hasBmsCredentials ? 'BMS 默认源未全部配置。' : '' }}
                {{ !hasScrmCredentials ? 'SCRM 凭证未配置。' : '' }}
                请展开下方「<b>抓取模式 & 凭证设置</b>」面板配置。
              </el-alert>
            </el-card>

            <!-- 手动同步(统一入口,纯配置,不含任何实时进度显示) -->
            <el-card shadow="never">
              <template #header>
                <div class="flex justify-between items-center">
                  <span>手动同步(单一入口)</span>
                  <el-button
                    v-if="isAdmin"
                    text
                    type="primary"
                    @click="expandPanel('settings')"
                  >
                    <el-icon><Setting /></el-icon>
                    抓取模式 & 凭证设置
                  </el-button>
                </div>
              </template>
              <el-form ref="formRef" :model="form" label-width="110px" :rules="rules">
                <el-form-item label="BMS 数据源" prop="bmsTypes">
                  <el-checkbox-group v-model="form.bmsTypes">
                    <el-checkbox
                      v-for="ds in bmsSources"
                      :key="ds.data_type"
                      :label="ds.data_type"
                      :disabled="busyRunning"
                    >
                      {{ ds.name }}
                      <span v-if="ds.data_type === 'refund_record_report'" class="src-alias">（商户号现金流 E 路）</span>
                      <el-tag v-if="!ds.has_credentials" type="danger" size="small" class="ml-4">未配凭证</el-tag>
                      <el-tag v-else-if="ds.requires_captcha && !captchaStates[ds.data_type]?.has_storage_state"
                              type="warning" size="small" class="ml-4">需验证码登录</el-tag>
                      <el-tag v-else-if="ds.requires_captcha && captchaStates[ds.data_type]?.has_storage_state"
                              type="success" size="small" class="ml-4">登录态已保存</el-tag>
                      <el-tag v-else type="success" size="small" class="ml-4">已配凭证</el-tag>
                    </el-checkbox>
                  </el-checkbox-group>
                </el-form-item>
                <el-form-item label="SCRM 数据源" prop="scrmTypes">
                  <el-checkbox-group v-model="form.scrmTypes">
                    <el-checkbox
                      v-for="ds in scrmStatusRows"
                      :key="ds.data_type"
                      :label="ds.data_type"
                      :disabled="busyRunning"
                    >
                      {{ ds.name || dsName(ds.data_type) }}
                      <el-tag v-if="!ds.has_credentials" type="danger" size="small" class="ml-4">未配凭证</el-tag>
                      <el-tag v-else type="success" size="small" class="ml-4">已配凭证</el-tag>
                    </el-checkbox>
                  </el-checkbox-group>
                </el-form-item>
                <el-form-item label="时间范围" prop="timeRange">
                  <el-radio-group v-model="form.timeRange" :disabled="busyRunning" @change="onTimeRangeChange">
                    <el-radio-button label="incremental">增量接续(推荐)</el-radio-button>
                    <el-radio-button label="today">今天</el-radio-button>
                    <el-radio-button label="yesterday">昨天</el-radio-button>
                    <el-radio-button label="week">过去一周</el-radio-button>
                    <el-radio-button label="custom">自定义</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <el-form-item v-if="form.timeRange === 'incremental'" label="即将抓取范围">
                  <div class="range-list">
                    <template v-for="r in preflightBmsRanges" :key="'bms-' + r.data_type">
                      <div v-if="form.bmsTypes.includes(r.data_type)" class="range-item">
                        <span class="range-chip range-chip-bms">BMS</span>
                        <span class="range-ds">{{ dsName(r.data_type) }}</span>
                        <span class="range-desc">{{ r.start_time || '今日 00:00' }} → {{ r.end_time || '现在' }}</span>
                      </div>
                    </template>
                    <template v-for="r in scrmStatusRows" :key="'scrm-' + r.data_type">
                      <div v-if="form.scrmTypes.includes(r.data_type)" class="range-item">
                        <span class="range-chip range-chip-scrm">SCRM</span>
                        <span class="range-ds">{{ dsName(r.data_type) }}</span>
                        <span v-if="r.status === 'never'" class="range-desc">（首次）30 天前 → 现在</span>
                        <span v-else class="range-desc">上次 {{ formatTime(r.last_start) }} 后 (重叠2h) → 现在</span>
                      </div>
                    </template>
                  </div>
                  <div v-if="cooldownRemaining > 0" class="cooldown-tip mt-8">
                    <el-icon><Clock /></el-icon>
                    距离上次成功完成还在冷却窗口内,还剩
                    <b>{{ Math.ceil(cooldownRemaining / 60) }}分{{ cooldownRemaining % 60 }}秒</b>。
                  </div>
                </el-form-item>
                <el-form-item v-if="form.timeRange === 'custom'" label="自定义时间">
                  <el-date-picker
                    v-model="form.customRange"
                    type="datetimerange"
                    range-separator="至"
                    start-placeholder="开始时间"
                    end-placeholder="结束时间"
                    format="YYYY-MM-DD HH:mm:ss"
                    value-format="YYYY-MM-DDTHH:mm:ss"
                    style="width: 100%"
                    :disabled="busyRunning"
                  />
                </el-form-item>
                <el-form-item>
                  <el-alert type="info" :closable="false" show-icon>
                    <p class="alert-text">
                      勾选的 BMS + SCRM 源会按顺序执行; BMS 每源间隔 ~65s; SCRM 走 OpenAPI。
                      「增量接续」按上次成功截止时间补边;任务在后台执行,可关闭页面。
                      所有实时操作显示在<b>右侧面板</b>(不会弹来弹去)。
                    </p>
                  </el-alert>
                </el-form-item>
                <el-form-item>
                  <el-button
                    type="primary"
                    :loading="submitLoading"
                    :disabled="busyRunning || totalSelectedCount === 0 || submitDisabledByCool"
                    @click="handleSyncAll"
                  >
                    <el-icon><Refresh /></el-icon>
                    <span v-if="busyRunning">任务执行中…(实时状态看右侧)</span>
                    <span v-else-if="submitDisabledByCool">冷却中,还剩{{ Math.ceil(cooldownRemaining/60) }}分</span>
                    <span v-else>开始同步(共 {{ totalSelectedCount }} 个 · BMS {{ form.bmsTypes.length }} + SCRM {{ form.scrmTypes.length }} · {{ timeRangeLabel }})</span>
                  </el-button>
                  <el-button
                    v-if="form.timeRange === 'incremental' && cooldownRemaining > 0 && !busyRunning"
                    type="warning"
                    plain
                    :loading="submitLoading"
                    @click="handleSyncAll(true)"
                  >
                    强制同步(跳过冷却)
                  </el-button>
                  <el-button v-if="hasAnyCaptchaSources" :loading="testing" @click="handleTest">测试浏览器</el-button>
                </el-form-item>
              </el-form>
            </el-card>
          </div>
        </el-collapse-item>

        <!-- ② 面板2: 抓取模式 & 凭证设置 (仅管理员可见, 默认折叠) -->
        <el-collapse-item
          v-if="isAdmin"
          title="抓取模式 & 凭证设置 (管理员) — 按平台分组 / 组级统一凭证 / 单源覆盖 / 测试登录"
          name="settings"
        >
          <div class="panel-inner">
            <SyncSettings embedded />
          </div>
        </el-collapse-item>

        <!-- ③ 面板3: 数据源同步状态 (默认折叠) -->
        <el-collapse-item title="数据源同步状态 (BMS + SCRM 合并)" name="status">
          <div class="panel-inner">
            <div class="flex justify-between items-center mb-8">
              <el-radio-group v-model="statusGroupFilter" size="small">
                <el-radio-button label="all">全部</el-radio-button>
                <el-radio-button label="bms">仅 BMS</el-radio-button>
                <el-radio-button label="scrm">仅 SCRM</el-radio-button>
              </el-radio-group>
            </div>
            <el-table :data="mergedStatusRows" border size="small">
              <el-table-column label="来源" width="70">
                <template #default="{ row }">
                  <el-tag size="small" :class="row._src === 'scrm' ? 'tag-scrm' : 'tag-bms'">
                    {{ row._src === 'scrm' ? 'SCRM' : 'BMS' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="数据源" width="200" />
              <el-table-column prop="data_type" label="代码" width="180" />
              <el-table-column label="凭证" width="80">
                <template #default="{ row }">
                  <span v-if="row._src === 'bms'">
                    <el-tag v-if="row.has_credentials" type="success" size="small">已配置</el-tag>
                    <el-tag v-else type="danger" size="small">未配置</el-tag>
                  </span>
                  <span v-else>
                    <el-tag v-if="hasScrmCredentials" type="success" size="small">已配置</el-tag>
                    <el-tag v-else type="danger" size="small">未配置</el-tag>
                  </span>
                </template>
              </el-table-column>
              <el-table-column v-if="hasAnyCaptchaSources" label="验证码登录" width="120">
                <template #default="{ row }">
                  <template v-if="row._src === 'bms' && row.requires_captcha">
                    <el-tag
                      v-if="captchaStates[row.data_type]?.has_storage_state"
                      type="success"
                      size="small"
                      effect="light"
                    >已登录</el-tag>
                    <el-tag v-else type="warning" size="small" effect="light">未登录</el-tag>
                  </template>
                  <span v-else class="dim">—</span>
                </template>
              </el-table-column>
              <el-table-column label="定时同步" width="80">
                <template #default="{ row }">
                  <el-tag v-if="row.default_sync" type="primary" size="small">纳入</el-tag>
                  <el-tag v-else type="info" size="small">不纳入</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="最近同步" width="170">
                <template #default="{ row }">
                  {{ formatTime(row._src === 'bms' ? row.last_sync : row.last_end) || '-' }}
                </template>
              </el-table-column>
              <el-table-column label="状态" width="70">
                <template #default="{ row }">
                  <el-tag
                    v-if="row._src === 'bms' ? row.last_status === 'success' : row.status === 'success'"
                    type="success"
                    size="small"
                  >成功</el-tag>
                  <el-tag
                    v-else-if="row._src === 'bms' ? row.last_status === 'failed' : row.status === 'failed'"
                    type="danger"
                    size="small"
                  >失败</el-tag>
                  <el-tag
                    v-else-if="row._src === 'bms' ? row.last_status === 'running' : row.status === 'running'"
                    type="warning"
                    size="small"
                  >进行中</el-tag>
                  <el-text v-else type="info" size="small">未同步</el-text>
                </template>
              </el-table-column>
              <el-table-column label="行/对账" width="130">
                <template #default="{ row }">
                  <span v-if="row._src === 'bms'">行:{{ row.last_rows ?? '-' }}</span>
                  <span v-else>
                    拉:{{ row.stats?.fetched_rows ?? '-' }}
                    <el-text
                      v-if="row.stats?.reconcile_delta != null"
                      :type="row.stats.reconcile_delta === 0 ? 'success' : 'warning'"
                      size="small"
                    >Δ={{ row.stats.reconcile_delta }}</el-text>
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="已同步至" width="220" show-overflow-tooltip>
                <template #default="{ row }">
                  <span v-if="row._src === 'bms' && row.last_query_end">{{ formatTime(row.last_query_end) }}</span>
                  <span v-else-if="row._src === 'scrm' && row.last_end">{{ formatTime(row.last_end) }}</span>
                  <span v-else class="dim">今日 00:00 起</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-collapse-item>

        <!-- ④ 面板4: 同步历史日志 (默认折叠) -->
        <el-collapse-item title="同步历史日志 (BMS + SCRM 合并)" name="history">
          <div class="panel-inner">
            <div class="flex justify-between items-center flex-wrap gap-8 mb-8">
              <div class="flex gap-8">
                <el-select
                  v-model="logFilterSource"
                  placeholder="来源"
                  size="small"
                  style="width: 110px"
                  @change="loadMergedLogs"
                >
                  <el-option label="全部" value="" />
                  <el-option label="BMS" value="bms" />
                  <el-option label="SCRM" value="scrm" />
                </el-select>
                <el-select
                  v-model="logFilterDataType"
                  placeholder="数据源"
                  size="small"
                  style="width: 180px"
                  filterable
                  clearable
                  @change="loadMergedLogs"
                >
                  <el-option
                    v-for="opt in mergedDataTypeOptions"
                    :key="opt.value"
                    :label="opt.label"
                    :value="opt.value"
                  />
                </el-select>
              </div>
            </div>
            <el-table :data="mergedSyncLogs" border size="small" max-height="460">
              <el-table-column label="来源" width="70">
                <template #default="{ row }">
                  <el-tag size="small" :class="row._src === 'scrm' ? 'tag-scrm' : 'tag-bms'">
                    {{ row._src === 'scrm' ? 'SCRM' : 'BMS' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="时间" width="160">
                <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="数据源" width="160">
                <template #default="{ row }">{{ dsName(row.data_type) }}</template>
              </el-table-column>
              <el-table-column label="触发" width="70">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.trigger_type === 'scheduled' ? 'warning' : 'info'">
                    {{ row.trigger_type === 'scheduled' ? '定时' : '手动' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="triggered_by" label="触发者" width="80" />
              <el-table-column label="状态" width="70">
                <template #default="{ row }">
                  <el-tag
                    :type="row.status === 'success' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'"
                    size="small"
                  >
                    {{ row.status === 'success' ? '成功' : row.status === 'failed' ? '失败' : '进行中' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="拉取" width="60">
                <template #default="{ row }">{{ row.fetched_rows ?? row.total_rows ?? 0 }}</template>
              </el-table-column>
              <el-table-column label="新增" width="60">
                <template #default="{ row }">{{ row.inserted_rows ?? 0 }}</template>
              </el-table-column>
              <el-table-column label="更新" width="60">
                <template #default="{ row }">{{ row.updated_rows ?? 0 }}</template>
              </el-table-column>
              <el-table-column label="失败" width="60">
                <template #default="{ row }">{{ row.failed_rows ?? 0 }}</template>
              </el-table-column>
              <el-table-column label="对账Δ" width="80">
                <template #default="{ row }">
                  <el-text
                    v-if="row.reconcile_delta != null"
                    :type="row.reconcile_delta === 0 ? 'success' : 'warning'"
                    size="small"
                  >{{ row.reconcile_delta }}</el-text>
                  <span v-else class="dim">—</span>
                </template>
              </el-table-column>
              <el-table-column label="时间范围" show-overflow-tooltip>
                <template #default="{ row }">
                  {{ formatTime(row.start_time) }} ~ {{ formatTime(row.end_time) }}
                </template>
              </el-table-column>
              <el-table-column prop="error" label="错误" show-overflow-tooltip>
                <template #default="{ row }">
                  <el-text v-if="row.error" type="danger" size="small">{{ row.error }}</el-text>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-collapse-item>

      </el-collapse>
    </div>

    <!-- ==================== 右栏(固定 sticky,简化为「彩色日志 + 顶部1行状态 + 底部1行汇总」,绝不弹来弹去) ==================== -->
    <aside class="sync-right">
      <div class="sync-right-sticky">
        <el-card shadow="hover" class="right-card">
          <template #header>
            <div class="sync-header-row">
              <!-- 左侧:标题 + 任务ID -->
              <div class="sync-header-title">
                <el-icon size="16" color="#409eff"><DataAnalysis /></el-icon>
                <span class="right-title"><b>同步日志</b></span>
                <span v-if="runningTask" class="task-id dim">#{{ runningTask.task_id }}</span>
              </div>

              <!-- 右侧:状态 + 进度条 + 文字 单行紧凑展示 -->
              <div v-if="runningTask" class="sync-header-progress">
                <el-tag
                  size="small"
                  effect="dark"
                  :type="runningTask.status === 'success' ? 'success' : runningTask.status === 'failed' ? 'danger' : 'warning'"
                  class="status-tag"
                >{{ runningStatusLabel(runningTask.status) }}</el-tag>
                <el-progress
                  class="inline-progress"
                  :percentage="runningTask.percent || 0"
                  :stroke-width="8"
                  :show-text="false"
                  :status="runningTask.status === 'success' ? 'success' : runningTask.status === 'failed' ? 'exception' : undefined"
                />
                <span class="progress-text">
                  <b>{{ runningTask.done_sources }}/{{ runningTask.total_sources }}</b>
                  <span v-if="runningTask.current_source"> · {{ dsName(runningTask.current_source) }}</span>
                  <span v-if="runningTask.current_phase"> · {{ runningTask.current_phase }}</span>
                </span>
              </div>
              <span v-else class="dim">等待任务…所有操作会按顺序追加在这里</span>
            </div>
          </template>

          <!-- ① 日志工具栏(精简:自动滚动 + 清空 + 回底部) -->
          <div class="right-log-toolbar mb-10">
            <el-checkbox v-model="autoScrollEnabled" size="small">
              自动滚动
            </el-checkbox>
            <el-button
              size="small"
              plain
              text
              :disabled="!runningTask || !runningTask.recent_logs || !runningTask.recent_logs.length"
              @click="clearLogView"
            >
              清空视图
            </el-button>
            <el-button
              v-if="!isNearBottom && runningTask && runningTask.recent_logs && runningTask.recent_logs.length"
              size="small"
              type="primary"
              plain
              class="ml-auto"
              @click="scrollToBottom(true)"
            >
              ⬇ 回到底部
            </el-button>
          </div>

          <!-- ② 彩色日志流(永久展开,按顺序一直输出,不折叠) -->
          <div
            ref="logStreamRef"
            class="right-log-stream"
            @scroll="onLogScroll"
          >
            <div
              v-for="(log, i) in (logViewLogs || [])"
              :key="i"
              :class="['log-line', 'log-' + (log.phase || 'info')]"
            >
              <span class="log-t">[{{ log.t }}]</span>
              <span
                class="log-tag"
                :class="isScrm(log.dt) ? 'log-tag-scrm' : 'log-tag-bms'"
              >{{ isScrm(log.dt) ? 'SCRM' : 'BMS' }}</span>
              <span class="log-dt">{{ dsName(log.dt) }}</span>
              <span class="log-msg">{{ log.msg }}</span>
            </div>
            <div
              v-if="!logViewLogs || logViewLogs.length === 0"
              class="log-empty"
            >
              {{ (runningTask ? '等待后端推送日志…(按顺序一直追加在这里)' : '等待提交任务…所有实时操作会按顺序追加在这里') }}
            </div>
          </div>

          <!-- ③ 底部单行结果汇总(不弹一堆tag,只用一行文字) -->
          <div v-if="syncResults && syncResults.length" class="right-result-summary mt-12">
            <span class="right-result-title">本次结果:</span>
            <span class="right-result-line">
              <span
                v-for="(r, i) in syncResults"
                :key="r.data_type"
                :class="['log-inline', r.success ? 'log-inline-ok' : 'log-inline-err']"
              >{{ i > 0 ? ' · ' : '' }}{{ dsName(r.data_type) }} · {{ r.success ? '成功' : '失败' }}{{ r.success ? ('(' + (r.inserted_rows ?? r.inserted ?? 0) + '/' + (r.updated_rows ?? r.updated ?? 0) + ')') : (' [' + (r.error || '').slice(0, 24) + ']') }}</span>
            </span>
          </div>
        </el-card>
      </div>
    </aside>

    <!-- ========= 编辑对话框 ========= -->
    <el-dialog v-model="dialogVisible" :title="`编辑同步源 - ${editForm.name || editForm.data_type}`" width="560px">
      <el-form :model="editForm" label-width="110px">
        <el-form-item label="显示名">
          <el-input v-model="editForm.name" />
        </el-form-item>
        <el-form-item label="基础地址">
          <el-input
            v-model="editForm.base_url"
            :placeholder="isScrmSource(editForm) ? '如 https://your-scrm.example.com' : '如 https://support.cloud-cat.cn/bms'"
          />
        </el-form-item>
        <el-form-item label="页面路径">
          <el-input v-model="editForm.page_path" placeholder="如 /index.html#/device/device-combo" />
        </el-form-item>
        <el-form-item label="时间标签">
          <el-input
            v-model="editForm.time_labels_text"
            placeholder="多个时间筛选框时按标签精确填,逗号分隔(如: 起始完成时间,结束完成时间);单个日期范围留空"
          />
        </el-form-item>
        <el-form-item v-if="isScrmSource(editForm)" label="抓取模式">
          <el-select v-model="editForm.fetch_mode" style="width: 100%" @change="onFetchModeChange">
            <el-option label="OpenAPI (默认,稳定)" value="openapi" />
            <el-option label="Playwright 网页(数学验证码兜底)" value="playwright" />
            <el-option label="双模式(OpenAPI优先,失败自动回退网页)" value="dual" />
          </el-select>
          <div class="form-hint mt-8">
            切换到 Playwright/双模式 后,需保存配置并点击「登录」完成网页验证码一次性登录,
            账号密码也会被解释为网页登录凭证。
          </div>
        </el-form-item>
        <el-form-item label="默认同步">
          <el-switch v-model="editForm.default_sync" active-text="纳入定时/默认勾选" inactive-text="不纳入" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="editForm.enabled" active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item :label="scrmLabel[0]">
          <el-input v-model="editForm.username" :placeholder="scrmUsernamePlaceholder" />
        </el-form-item>
        <el-form-item :label="scrmLabel[1]">
          <el-input v-model="editForm.password" type="password" show-password :placeholder="scrmPasswordPlaceholder" />
        </el-form-item>
        <!-- Playwright / Dual 模式下需要独立再配置一对「网页登录账号密码」(SCRM 专属,和 OpenAPI 凭证分离) -->
        <template v-if="isScrmSource(editForm) && editForm.fetch_mode === 'dual'">
          <el-divider content-position="left">Playwright 网页登录凭证(双模式下独立配置)</el-divider>
          <el-alert type="warning" :closable="false" show-icon class="mb-8" size="small">
            下方是 SCRM 后台管理页面实际的「登录账号/密码」(手机号/邮箱 + 登录密码),<strong>不要填 ClientID/ClientSecret</strong>。
            留空表示不修改之前保存过的值。
          </el-alert>
          <el-form-item label="SCRM 网页账号">
            <el-input
              v-model="editForm.web_username"
              :placeholder="webUsernamePlaceholder"
            />
          </el-form-item>
          <el-form-item label="SCRM 网页密码">
            <el-input
              v-model="editForm.web_password"
              type="password"
              show-password
              :placeholder="webPasswordPlaceholder"
            />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- ========= 验证码登录对话框 ========= -->
    <el-dialog
      v-model="captchaDialogVisible"
      :title="`${captchaRow?.name || captchaRow?.data_type} - ${isMathCaptcha ? '数学验证码' : '验证码'}登录`"
      width="480px"
      :close-on-click-modal="false"
      @closed="resetCaptchaForm"
    >
      <el-alert :type="isMathCaptcha ? 'warning' : 'info'" :closable="false" show-icon class="mb-16" size="small">
        <template v-if="isMathCaptcha">
          SCRM 登录使用<strong>数学计算式</strong>验证码。下方图片显示的是如「7*0=?」的算式,
          请在输入框中填入<strong>计算结果数字</strong>(如 0、63)。<br />
          若 OCR 自动识别出了答案,已经帮你填好了,请核对后直接点「提交并登录」。
        </template>
        <template v-else>
          请读取图片中的验证码后填入下方输入框,点击提交后会自动填账号密码并登录。
          登录成功后会自动保存登录态,后续同步无需再次输入验证码。
        </template>
      </el-alert>
      <div v-if="captchaLoading" class="captcha-loading">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在启动浏览器并加载验证码...(最多等待 90 秒)</span>
      </div>
      <div v-else class="captcha-box">
        <div class="captcha-img-wrap">
          <img
            v-if="captchaImage"
            :src="captchaImage"
            alt="验证码"
            class="captcha-img"
            title="点击图片重试(注意:后端会话会重新生成)"
            @click="refreshCaptcha"
          />
          <div v-else class="captcha-empty">验证码加载失败</div>
        </div>
        <el-button link type="primary" size="small" :loading="captchaLoading" @click="refreshCaptcha">
          看不清,换一张
        </el-button>
      </div>
      <el-form :model="captchaForm" label-width="80px" class="mt-16">
        <el-form-item :label="isMathCaptcha ? '算式答案' : '验证码'">
          <el-input
            v-model="captchaForm.captcha"
            :placeholder="isMathCaptcha ? '请输入计算结果(整数),OCR已自动填充' : '请输入图片中的验证码'"
            :maxlength="isMathCaptcha ? 16 : 8"
            clearable
            @keyup.enter="submitCaptcha"
          />
        </el-form-item>
        <el-form-item v-if="captchaHint" label="提示">
          <span class="hint-text" v-html="captchaHintHtml"></span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="captchaDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="captchaSubmitting"
          :disabled="!captchaForm.sessionId || !captchaForm.captcha"
          @click="submitCaptcha"
        >
          提交并登录
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, computed, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Setting, Clock, DataAnalysis, Loading } from '@element-plus/icons-vue'
import SyncSettings from '../system/SyncSettings.vue'
import {
  listSyncDataSources,
  taskPreflight,
  taskSubmit,
  listSyncLogs,
  getScheduleStatus,
  startSchedule,
  stopSchedule,
  testBrowser,
  taskStatus,
  captchaLoginCheck,
  captchaLoginStart,
  captchaLoginSubmit,
  captchaLoginLogout,
  getScrmSyncStatus,
  submitScrmSync,
  listScrmSyncLogs,
} from '@/api/fetch'
import ScheduleConfigPanel from '@/components/ScheduleConfigPanel.vue'
import { listSyncSources, updateSyncSource } from '@/api/system'

const route = useRoute()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const SCRM_DATA_TYPES = [
  { data_type: 'scrm_operator', name: 'SCRM · 客服操作员(快照)', default_sync: true },
  { data_type: 'scrm_session_private', name: 'SCRM · 私聊会话(增量)', default_sync: true },
  { data_type: 'scrm_session_group', name: 'SCRM · 群聊会话(增量)', default_sync: false },
  { data_type: 'scrm_session_virtual', name: 'SCRM · 虚拟会话(增量)', default_sync: false },
]

const isScrm = (dt) => !!(dt && dt.startsWith('scrm_'))

/* ===== 手风琴面板控制 ===== */
// 旧 URL 兼容: 如果 route.query.panel === 'settings' 就从「抓取模式 & 凭证设置」展开
const activePanel = ref(['run'])
onMounted(() => {
  const wantPanel = route.query.panel
  if (wantPanel && ['run', 'settings', 'status', 'history'].includes(wantPanel)) {
    activePanel.value = [wantPanel]
  }
})
function expandPanel(name) {
  activePanel.value = [name]
}

/* ===== 通用状态 ===== */
const testing = ref(false)
const scheduleToggling = ref(false)
const bmsSources = ref([])
const scrmStatusRows = ref([])
const syncResults = ref([])
const logFilterSource = ref('')
const logFilterDataType = ref('')
const statusGroupFilter = ref('all')
const mergedSyncLogs = ref([])
const formRef = ref(null)
const typesInited = ref(false)
const submitLoading = ref(false)
const logStreamRef = ref(null)

const captchaStates = reactive({})

const form = reactive({
  bmsTypes: [],
  scrmTypes: [],
  timeRange: 'incremental',
  customRange: null,
})

const rules = { /* 自定义校验会在 validate 时通过 handleSyncAll 里的显式检查兜底 */ }

const cooldownRemaining = ref(0)
const preflightBmsRanges = ref([])
const busyRunning = ref(false)
const currentTaskId = ref(null)
const runningTask = ref(null)

// 日志滚动控制
const autoScrollEnabled = ref(true)
const isNearBottom = ref(true)
const _logViewOffset = ref(0)

const scheduleStatus = ref({
  enabled: false,
  schedule_hours: [0, 8, 12, 16, 20],
  next_run: null,
  current_progress: null,
  has_credentials: { bms: false, scrm: false },
})

/* ===== 管理员: SyncSettings 的状态 (仅 isAdmin 加载) ===== */
const sources = ref([])
const sourcesLoading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const loginStates = reactive({})
const captchaDialogVisible = ref(false)
const captchaRow = ref(null)
const captchaLoading = ref(false)
const captchaSubmitting = ref(false)
const captchaImage = ref('')
const captchaHint = ref('')
const captchaForm = reactive({ sessionId: '', captcha: '' })

// 动态占位: "留空=不修改" 但需要知道后端是不是已经保存过 → 通过标志位动态提示
const credentialsState = reactive({
  username_configured: false,
  password_configured: false,
  web_username_configured: false,
  web_password_configured: false,
})

const editForm = reactive({
  data_type: '',
  name: '',
  base_url: '',
  page_path: '',
  time_labels_text: '',
  default_sync: true,
  enabled: true,
  fetch_mode: 'openapi', // SCRM 模式: openapi / playwright / dual
  username: '',         // SCRM: ClientID 或 OpenAPI 主账号; BMS: BMS 网页登录账号
  password: '',         // SCRM: ClientSecret 或 OpenAPI 主密码; BMS: BMS 网页登录密码
  web_username: '',     // SCRM Playwright/Dual 模式: 网页登录账号(真实手机号/邮箱)
  web_password: '',     // SCRM Playwright/Dual 模式: 网页登录密码
})

// 管理员: 抓取模式配置相关 computed
const hasCaptchaSources = computed(
  () => Array.isArray(sources.value) && sources.value.some(r => r.requires_captcha),
)
const scrmLabel = computed(() => {
  if (!isScrmSource(editForm)) return ['BMS 账号', 'BMS 密码']
  const mode = editForm.fetch_mode || 'openapi'
  // Playwright 模式: 主字段直接就是网页登录凭证（用户最直观）
  if (mode === 'openapi') return ['Client ID', 'Client Secret']
  if (mode === 'playwright') return ['SCRM 网页账号', 'SCRM 网页密码']
  // Dual 模式: 主字段是 OpenAPI,下方再额外补一对 web_ 网页凭证
  return ['OpenAPI Client ID', 'OpenAPI Client Secret']
})
const scrmUsernamePlaceholder = computed(() => {
  if (!isScrmSource(editForm)) {
    return credentialsState.username_configured ? 'BMS 登录账号(已配置,留空=不修改,输入新值覆盖)' : '请输入 BMS 登录账号'
  }
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') {
    return credentialsState.username_configured ? 'SCRM OpenAPI Client ID(已配置,留空=不修改,输入新值覆盖)' : 'SCRM OpenAPI Client ID'
  }
  if (mode === 'playwright') {
    return credentialsState.username_configured ? 'SCRM 网页登录账号(已配置,留空=不修改,输入新值覆盖)' : 'SCRM 网页登录账号(用户名/手机号)'
  }
  // Dual
  return credentialsState.username_configured ? 'OpenAPI Client ID(已配置,留空=不修改,输入新值覆盖)' : 'OpenAPI Client ID 或网页登录账号(留空表示不修改)'
})
const scrmPasswordPlaceholder = computed(() => {
  if (!isScrmSource(editForm)) {
    return credentialsState.password_configured ? 'BMS 登录密码(已配置,留空=不修改,输入新值覆盖)' : '请输入 BMS 登录密码'
  }
  const mode = editForm.fetch_mode || 'openapi'
  if (mode === 'openapi') {
    return credentialsState.password_configured ? 'SCRM OpenAPI Client Secret(已配置,留空=不修改,输入新值覆盖)' : 'SCRM OpenAPI Client Secret'
  }
  if (mode === 'playwright') {
    return credentialsState.password_configured ? 'SCRM 网页登录密码(已配置,留空=不修改,输入新值覆盖)' : 'SCRM 网页登录密码'
  }
  // Dual
  return credentialsState.password_configured ? 'OpenAPI Client Secret(已配置,留空=不修改,输入新值覆盖)' : 'OpenAPI Client Secret 或网页登录密码(留空表示不修改)'
})
const webUsernamePlaceholder = computed(() => {
  return credentialsState.web_username_configured
    ? 'SCRM 网页登录账号(已配置,留空=不修改,输入新值覆盖)'
    : '请输入 SCRM 后台真实登录账号(手机号/邮箱),不要填 ClientID'
})
const webPasswordPlaceholder = computed(() => {
  return credentialsState.web_password_configured
    ? 'SCRM 网页登录密码(已配置,留空=不修改,输入新值覆盖)'
    : '请输入 SCRM 后台登录密码,不要填 ClientSecret'
})
const isMathCaptcha = computed(
  () => !!(captchaRow.value && isScrmSource(captchaRow.value) && captchaRow.value?.captcha_type === 'math'),
)
const captchaHintHtml = computed(() => {
  const t = captchaHint.value || ''
  if (!t) return ''
  const escaped = t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped.replace(/'(\d+)'/g, ' <b style="color:#409eff;font-weight:600">\'$1\'</b> ')
})

// 工具函数
function isScrmSource(row) {
  return !!(row && typeof row.data_type === 'string' && row.data_type.startsWith('scrm_'))
}
function onFetchModeChange() { /* 切换模式不做打断提示,只在 form-hint 里说明 */ }

const hasBmsCredentials = computed(() => {
  const hc = scheduleStatus.value.has_credentials
  if (typeof hc === 'boolean') return hc
  if (!hc) return false
  if (typeof hc.bms === 'boolean') return hc.bms
  return !!hc
})

const hasScrmCredentials = computed(() => {
  const hc = scheduleStatus.value.has_credentials
  if (typeof hc === 'boolean') return hc
  if (!hc) return false
  if (typeof hc.scrm === 'boolean') return hc.scrm
  return scrmStatusRows.value.length > 0
})

const scheduleEnabled = computed({
  get: () => !!scheduleStatus.value.enabled,
  set: (v) => { scheduleStatus.value.enabled = !!v },
})

const hasAnyCaptchaSources = computed(() =>
  bmsSources.value.some(d => d.requires_captcha)
)

let pollTimer = null
let preflightTimer = null
let _pollErrorStreak = 0
const POLL_ERROR_MAX = 5
let _preflightErrorStreak = 0
const PREFLIGHT_ERROR_MAX = 3
// 连续多少次 poll 都返回 task=null 才真的清空 runningTask
// (防止 gunicorn 多 worker 下 SyncTaskManager 内存不共享导致 UI 反复弹跳"等待任务" / "日志流"两种画面)
let _taskNullStreak = 0
const TASK_NULL_MAX = 5

onMounted(async () => {
  const jobs = [
    loadBmsSources(),
    loadScrmStatus(),
    loadScheduleStatus(),
    runPreflight(),
    loadMergedLogs(),
  ]
  // 管理员才拉取 SyncSettings.sources (全量配置)
  if (isAdmin.value) jobs.push(loadSources())
  await Promise.all(jobs)

  pollTimer = setInterval(pollProgress, 2000)
  preflightTimer = setInterval(() => runPreflight({ silentError: true }), 30000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (preflightTimer) clearInterval(preflightTimer)
})

watch(() => [...form.bmsTypes, ...form.scrmTypes].sort().join(','), () => runPreflight())
watch(() => form.timeRange, () => runPreflight())

const totalSelectedCount = computed(() => form.bmsTypes.length + form.scrmTypes.length)

const timeRangeLabel = computed(() => ({
  incremental: '增量接续',
  today: '今天',
  yesterday: '昨天',
  week: '过去一周',
  custom: '自定义时间',
}[form.timeRange] || form.timeRange))

const submitDisabledByCool = computed(() => {
  if (!['incremental', 'today'].includes(form.timeRange)) return false
  return cooldownRemaining.value > 0
})

// 日志显示
const logViewLogs = computed(() => {
  const all = runningTask.value && runningTask.value.recent_logs ? runningTask.value.recent_logs : []
  if (_logViewOffset.value >= all.length) return []
  return all.slice(_logViewOffset.value)
})

const mergedDataTypeOptions = computed(() => {
  const opts = []
  for (const d of bmsSources.value) {
    opts.push({ value: d.data_type, label: `BMS · ${d.name}` })
  }
  for (const d of SCRM_DATA_TYPES) {
    opts.push({ value: d.data_type, label: `SCRM · ${d.name}` })
  }
  return opts
})

const mergedStatusRows = computed(() => {
  const rows = []
  if (statusGroupFilter.value === 'all' || statusGroupFilter.value === 'bms') {
    for (const r of bmsSources.value) rows.push({ ...r, _src: 'bms' })
  }
  if (statusGroupFilter.value === 'all' || statusGroupFilter.value === 'scrm') {
    for (const r of scrmStatusRows.value) rows.push({ ...r, _src: 'scrm' })
  }
  return rows
})

function runningStatusLabel(s) {
  switch (s) {
    case 'success': return '已完成'
    case 'failed': return '已失败'
    case 'partial': return '部分完成'
    default: return '运行中'
  }
}

function triggerLabel(tt, who) {
  if (tt === 'scheduled') return '定时任务'
  return `手动任务（${who || '未知'}）`
}

function dsName(dt) {
  if (!dt) return '-'
  if (dt === '__post_sync__') return '后处理'
  if (isScrm(dt)) {
    const d = SCRM_DATA_TYPES.find(s => s.data_type === dt)
    if (d) return d.name
    const r = scrmStatusRows.value.find(s => s.data_type === dt)
    return r ? (r.name || dt) : dt
  }
  const d = bmsSources.value.find(s => s.data_type === dt)
  return d ? d.name : dt
}

async function loadBmsSources() {
  try {
    const res = await listSyncDataSources()
    bmsSources.value = res.data
    if (!typesInited.value) {
      form.bmsTypes = res.data.filter(d => d.default_sync).map(d => d.data_type)
    }
    await loadCaptchaStates()
  } catch (e) {
    ElMessage.error(`加载 BMS 数据源失败: ${e.message}`)
  }
}

async function loadScrmStatus() {
  try {
    const res = await getScrmSyncStatus()
    scrmStatusRows.value = (res.data || []).map(r => ({ ...r, name: r.name || dsName(r.data_type) }))
    if (!typesInited.value) {
      // 使用后端返回的 default_sync 配置（优先），fallback 到前端常量
      form.scrmTypes = scrmStatusRows.value
        .filter(r => r.default_sync !== undefined ? r.default_sync : SCRM_DATA_TYPES.find(d => d.data_type === r.data_type)?.default_sync)
        .map(r => r.data_type)
      typesInited.value = true
    }
  } catch {
    scrmStatusRows.value = []
  }
}

async function loadCaptchaStates() {
  const targets = (bmsSources.value || []).filter(d => d.requires_captcha)
  if (!targets.length) return
  await Promise.all(targets.map(async d => {
    try {
      const r = await captchaLoginCheck(d.data_type)
      const x = r.data || {}
      captchaStates[d.data_type] = {
        has_storage_state: !!x.has_storage_state,
        state_mtime: x.state_mtime || null,
      }
    } catch {
      captchaStates[d.data_type] = { has_storage_state: false, state_mtime: null }
    }
  }))
}

async function loadScheduleStatus() {
  try {
    const res = await getScheduleStatus()
    scheduleStatus.value = res.data
  } catch { /* ignore */ }
}

async function loadMergedLogs() {
  try {
    const onlyBms = logFilterSource.value === 'bms'
    const onlyScrm = logFilterSource.value === 'scrm'
    const wantDt = logFilterDataType.value
    const dtIsScrm = wantDt && isScrm(wantDt)
    const dtIsBms = wantDt && !dtIsScrm
    const needsBms = !onlyScrm && !(wantDt && dtIsScrm)
    const needsScrm = !onlyBms && !(wantDt && dtIsBms)

    const jobs = []
    if (needsBms) {
      const p = {}
      if (wantDt && dtIsBms) p.data_type = wantDt
      jobs.push(listSyncLogs(p).then(r => (r.data || []).map(x => ({ ...x, _src: 'bms' }))))
    } else jobs.push(Promise.resolve([]))
    if (needsScrm) {
      const p = {}
      if (wantDt && dtIsScrm) p.data_type = wantDt
      jobs.push(listScrmSyncLogs(p).then(r => (r.data || []).map(x => ({ ...x, _src: 'scrm' }))))
    } else jobs.push(Promise.resolve([]))

    const [bms, scrm] = await Promise.all(jobs)
    const all = [...(bms || []), ...(scrm || [])]
    all.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
    mergedSyncLogs.value = all
  } catch {
    mergedSyncLogs.value = []
  }
}

async function runPreflight(opts = {}) {
  try {
    const allTypes = [...form.bmsTypes, ...form.scrmTypes]
    const res = await taskPreflight(allTypes, opts)
    const d = res.data
    busyRunning.value = !!d.busy
    cooldownRemaining.value = d.cooldown_remaining || 0
    preflightBmsRanges.value = d.time_ranges || []
    if (d.busy && d.current_task_id && !currentTaskId.value) {
      currentTaskId.value = d.current_task_id
    }
    _preflightErrorStreak = 0
  } catch (e) {
    if (opts.silentError) {
      _preflightErrorStreak += 1
      if (_preflightErrorStreak >= PREFLIGHT_ERROR_MAX && preflightTimer) {
        clearInterval(preflightTimer)
        preflightTimer = null
        ElMessage.warning(`预检连续 ${PREFLIGHT_ERROR_MAX} 次失败,已停止后台刷新`)
      }
    }
  }
}

// ============ 日志滚动逻辑 ============
function computeNearBottom() {
  const el = logStreamRef.value
  if (!el) return true
  return (el.scrollHeight - el.scrollTop - el.clientHeight) <= 24
}

function onLogScroll() {
  isNearBottom.value = computeNearBottom()
  if (!isNearBottom.value) autoScrollEnabled.value = false
  else autoScrollEnabled.value = true
}

async function scrollToBottom(force = false) {
  await nextTick()
  const el = logStreamRef.value
  if (!el) return
  if (force || (autoScrollEnabled.value && isNearBottom.value)) {
    el.scrollTop = el.scrollHeight
  }
}

function clearLogView() {
  const all = runningTask.value && runningTask.value.recent_logs ? runningTask.value.recent_logs : []
  _logViewOffset.value = all.length
}

async function pollProgress() {
  try {
    const res = await taskStatus(currentTaskId.value)
    const d = res.data
    _pollErrorStreak = 0
    busyRunning.value = !!d.busy
    cooldownRemaining.value = d.cooldown_remaining || 0
    if (d.task) {
      // 只要拿到一次有效 task, 立刻清零 null 连续计数,防止错误清空
      _taskNullStreak = 0

      const isTerminal = ['success', 'failed', 'partial'].includes(d.task.status)
      const trackingSpecific = !!currentTaskId.value
      const taskIdChanged = !runningTask.value || runningTask.value.task_id !== d.task.task_id

      if (taskIdChanged) {
        // 真的切到了新任务,才重置日志偏移和结果
        _logViewOffset.value = 0
        syncResults.value = []
        // 新任务 → 整体赋值新对象引用,彻底重置
        runningTask.value = d.task
      } else {
        // ⚠️ 同一任务的轮询:绝对不要 runningTask.value = d.task !!!
        //    每次整对象赋值会让 Vue 响应式认为 recent_logs 数组整体变化,触发 DOM 全量销毁重建,
        //    视觉上就是"日志全部清空 → 又重新一条条刷出来"的弹跳感,用户极其烦躁。
        //    改成:只增量 push 新增日志,其余字段(meta/status/current_source 等)覆盖。
        const oldLogs = (runningTask.value && runningTask.value.recent_logs) || []
        const newLogs = (d.task && d.task.recent_logs) || []
        // 校验: 如果 newLogs 的前 N 条和 oldLogs 完全对应,就取 oldLogs.length 之后的新增部分
        let appendStart = 0
        if (newLogs.length >= oldLogs.length) {
          let samePrefix = true
          for (let i = 0; i < oldLogs.length; i++) {
            const o = oldLogs[i] || {}
            const n = newLogs[i] || {}
            if (o.t !== n.t || o.msg !== n.msg || o.dt !== n.dt) {
              samePrefix = false
              break
            }
          }
          if (samePrefix) {
            appendStart = oldLogs.length
          }
        }
        if (appendStart > 0 && appendStart < newLogs.length) {
          // 增量模式: 把新增的 push 进现有数组,保持数组引用稳定 → Vue 只重绘末尾新增行
          for (let i = appendStart; i < newLogs.length; i++) {
            oldLogs.push(newLogs[i])
          }
          // 非 recent_logs 字段(状态、进度、current_source等)仍更新
          const clone = runningTask.value
          for (const k of Object.keys(d.task)) {
            if (k === 'recent_logs') continue
            clone[k] = d.task[k]
          }
          runningTask.value = clone
        } else {
          // 兜底: 前缀不一致(可能后端重连、重试等),整体赋值但不触发 _logViewOffset 重置
          runningTask.value = d.task
        }
      }

      if (isTerminal && trackingSpecific && d.task.status === 'success') {
        // ⚠️ 修复 BMS→SCRM 切换导致 currentTaskId 被旧定时器错误清空的竞态 bug
        //    保存本次 task_id 的闭包,只在"定时器触发时 currentTaskId 仍然等于这个 BMS task_id"的情况下
        //    才执行清空。如果中间 BMS→SCRM 已经提交了新的 SCRM task_id 并覆盖了 currentTaskId,
        //    那么旧定时器就不该动 currentTaskId,否则会触发日志"清空 → 又刷出来 → 又清空"的弹跳。
        const thisTaskId = d.task.task_id
        setTimeout(() => {
          if (currentTaskId.value === thisTaskId) {
            currentTaskId.value = null
          }
        }, 2500)
      }

      if (isTerminal && d.task.results) {
        const statusChanged =
          !syncResults.value.length ||
          JSON.stringify(syncResults.value) !== JSON.stringify(d.task.results)
        if (statusChanged) {
          syncResults.value = d.task.results
          await Promise.all([loadBmsSources(), loadScrmStatus(), loadMergedLogs()])
        }
      }
      await scrollToBottom(false)
    } else {
      // ⚠️ 不要立刻清空 runningTask!
      // gunicorn 多 worker 下 SyncTaskManager 是各 worker 独立内存单例,轮询打到不同 worker 会偶发 task=null
      // 只有连续 TASK_NULL_MAX 次(默认 5 次 = 10 秒)都返回 null,才确认真的没有任务了,清空视图
      _taskNullStreak += 1
      if (_taskNullStreak >= TASK_NULL_MAX) {
        runningTask.value = null
      }
    }
    if (!pollProgress._t) pollProgress._t = 0
    pollProgress._t += 1
    if (pollProgress._t % 5 === 0) {
      loadScheduleStatus()
      if (pollProgress._t % 15 === 0) {
        loadBmsSources()
        loadScrmStatus()
        if (isAdmin.value) loadSources()
      }
    }
  } catch (e) {
    _pollErrorStreak += 1
    if (_pollErrorStreak >= POLL_ERROR_MAX && pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
      ElMessage.warning(`状态查询连续 ${POLL_ERROR_MAX} 次失败,已停止自动刷新`)
    }
  }
}

function onTimeRangeChange() {
  if (form.timeRange !== 'custom') form.customRange = null
  runPreflight()
}

async function toggleSchedule(val) {
  scheduleToggling.value = true
  try {
    if (val) await startSchedule()
    else await stopSchedule()
    ElMessage.success(val ? '定时同步已启动(BMS + SCRM)' : '定时同步已停止')
    await loadScheduleStatus()
  } catch (e) {
    ElMessage.error(e.message || '操作失败')
    scheduleStatus.value.enabled = !val
  } finally {
    scheduleToggling.value = false
  }
}

async function handleSyncAll(force = false) {
  if (totalSelectedCount.value === 0) {
    ElMessage.warning('请至少勾选一个 BMS 或 SCRM 数据源')
    return
  }
  if (form.timeRange === 'custom' && (!form.customRange || form.customRange.length !== 2)) {
    ElMessage.warning('请选择自定义时间范围')
    return
  }
  const allNames = [
    ...form.bmsTypes.map(dsName),
    ...form.scrmTypes.map(dsName),
  ].join('、')
  let rangeText = timeRangeLabel.value
  if (form.timeRange === 'incremental') {
    rangeText = '各源按上次截止时间接续增量(SCRM 含 2h 重叠补边)'
  }
  try {
    await ElMessageBox.confirm(
      `将同步 ${totalSelectedCount.value} 个数据源:\n${allNames}\n时间范围: ${rangeText}\n任务在后台执行,可关闭页面。是否继续?`,
      '确认同步',
      { confirmButtonText: '开始同步', cancelButtonText: '取消', type: 'info' },
    )
  } catch {
    return
  }
  submitLoading.value = true
  syncResults.value = []
  _logViewOffset.value = 0
  try {
    const commonPayload = {
      time_range: form.timeRange,
      force: !!force,
      ignore_cooldown: !!force,
    }
    if (form.timeRange === 'custom' && form.customRange) {
      commonPayload.custom_start = form.customRange[0]
      commonPayload.custom_end = form.customRange[1]
    }
    let res
    if (form.bmsTypes.length && form.scrmTypes.length === 0) {
      res = await taskSubmit({ ...commonPayload, data_types: form.bmsTypes })
    } else if (form.scrmTypes.length && form.bmsTypes.length === 0) {
      res = await submitScrmSync({ ...commonPayload, sync_types: form.scrmTypes })
    } else {
      // 两边都勾了: 先 BMS, 完成后自动触发 SCRM
      res = await taskSubmit({ ...commonPayload, data_types: form.bmsTypes })
      ElMessage.info(`已提交 BMS 任务(${res.data.task_id}),BMS 结束后自动启动 SCRM…`)
      const bmsTaskId = res.data.task_id
      const autoStartScrm = setInterval(async () => {
        try {
          const s = await taskStatus(bmsTaskId)
          const t = s.data.task
          if (t && ['success', 'failed', 'partial'].includes(t.status)) {
            clearInterval(autoStartScrm)
            const sRes = await submitScrmSync({ ...commonPayload, sync_types: form.scrmTypes })
            currentTaskId.value = sRes.data.task_id
            _logViewOffset.value = 0
            ElMessage.success(`BMS 任务结束,已自动提交 SCRM 任务(${sRes.data.task_id})`)
          }
        } catch { /* ignore */ }
      }, 3000)
    }
    if (res) {
      currentTaskId.value = res.data.task_id
      ElMessage.success(res.data.message || `任务已提交(${res.data.task_id}),后台执行中…(实时状态在右侧)`)
    }
    await pollProgress()
  } catch (e) {
    ElMessage.error(`提交失败: ${e.message}`)
  } finally {
    submitLoading.value = false
  }
}

async function handleTest() {
  testing.value = true
  try {
    const res = await testBrowser()
    if (res.data) ElMessage.success('浏览器测试成功')
    else ElMessage.error('浏览器测试失败')
  } catch (e) {
    ElMessage.error(`测试失败: ${e.message}`)
  } finally {
    testing.value = false
  }
}

/* ===== 管理员: SyncSettings 函数 ===== */
async function loadSources() {
  sourcesLoading.value = true
  try {
    const res = await listSyncSources()
    sources.value = res.data
    await Promise.all(
      (sources.value || [])
        .filter(r => r.requires_captcha)
        .map(r => loadLoginState(r.data_type)),
    )
  } catch (e) {
    ElMessage.error(`加载同步源配置失败: ${e.message}`)
  } finally {
    sourcesLoading.value = false
  }
}

async function loadLoginState(dataType) {
  try {
    if (!loginStates[dataType]) loginStates[dataType] = { busy: false }
    loginStates[dataType].busy = true
    const res = await captchaLoginCheck(dataType)
    const d = res.data
    loginStates[dataType].has_storage_state = !!d?.has_storage_state
    loginStates[dataType].state_mtime = d?.state_mtime || null
    loginStates[dataType].state_path = d?.state_path || null
  } catch {
    loginStates[dataType] = loginStates[dataType] || { has_storage_state: false, busy: false }
  } finally {
    loginStates[dataType].busy = false
  }
}

function openEdit(row) {
  editForm.data_type = row.data_type
  editForm.name = row.name
  editForm.base_url = row.base_url
  editForm.page_path = row.page_path
  editForm.time_labels_text = (row.time_labels || []).join(',')
  editForm.default_sync = row.default_sync
  editForm.enabled = row.enabled
  editForm.fetch_mode = isScrmSource(row) ? (row.fetch_mode || 'openapi') : 'playwright'
  // 「留空=不修改」的安全模型: 编辑态绝不要把密文密码回填到 input
  // 避免用户点保存 → 前端"以为自己保存了密文" → 后端再加密一次变垃圾
  // 但要通过 credentialsState 让 placeholder 里明显告诉用户"其实后端已经保存过"
  credentialsState.username_configured = !!(row.username || row.has_credentials)
  credentialsState.password_configured = !!(row.password || row.has_credentials)
  credentialsState.web_username_configured = !!row.options?.web_username_configured
  credentialsState.web_password_configured = !!row.options?.web_password_configured
  editForm.username = ''
  editForm.password = ''
  editForm.web_username = ''
  editForm.web_password = ''
  dialogVisible.value = true
}

async function handleSave() {
  saving.value = true
  try {
    const payload = {
      name: editForm.name,
      base_url: editForm.base_url,
      page_path: editForm.page_path,
      time_labels: editForm.time_labels_text
        ? editForm.time_labels_text.split(',').map(s => s.trim()).filter(Boolean)
        : [],
      default_sync: editForm.default_sync,
      enabled: editForm.enabled,
    }
    // SCRM 才提交 fetch_mode
    if (isScrmSource(editForm) && editForm.fetch_mode) {
      payload.fetch_mode = editForm.fetch_mode
    }
    // 主账号密码: 仅当非空时传(后端:空表示不改)
    if (editForm.username) payload.username = editForm.username
    if (editForm.password) payload.password = editForm.password
    // SCRM Dual 模式: web_username / web_password → 合并进 options patch
    //   (后端会自动把 options.patch 的 key 合并进 DB options 列,加密后保存)
    if (isScrmSource(editForm) && editForm.fetch_mode === 'dual') {
      const optsPatch = {}
      if (editForm.web_username) optsPatch.web_username = editForm.web_username
      if (editForm.web_password) optsPatch.web_password = editForm.web_password
      if (Object.keys(optsPatch).length > 0) payload.options = optsPatch
    }

    await updateSyncSource(editForm.data_type, payload)
    ElMessage.success('保存成功')
    dialogVisible.value = false
    await Promise.all([loadSources(), loadBmsSources(), loadScrmStatus()])
  } catch (e) {
    ElMessage.error(`保存失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}

function openCaptchaLogin(row) {
  if (!row?.has_credentials) {
    ElMessage.warning('请先配置账号密码,再进行验证码登录')
    openEdit(row)
    return
  }
  captchaRow.value = row
  captchaDialogVisible.value = true
  resetCaptchaForm()
  startCaptchaLogin()
}

function resetCaptchaForm() {
  captchaForm.sessionId = ''
  captchaForm.captcha = ''
  captchaImage.value = ''
  captchaHint.value = ''
  captchaLoading.value = false
  captchaSubmitting.value = false
}

async function startCaptchaLogin() {
  if (!captchaRow.value) return
  captchaLoading.value = true
  captchaImage.value = ''
  captchaHint.value = ''
  try {
    const res = await captchaLoginStart(captchaRow.value.data_type)
    const d = res.data
    captchaForm.sessionId = d?.session_id || ''
    captchaImage.value = d?.captcha_image || ''
    captchaHint.value = d?.hint || ''

    if (d?.auto_login_success) {
      ElMessage.success(d?.auto_login_message || d?.message || '后端已自动识别并登录成功')
      captchaDialogVisible.value = false
      if (captchaRow.value) {
        await loadLoginState(captchaRow.value.data_type)
        await loadCaptchaStates()
      }
      return
    }
    const captchaType = d?.captcha_type || (isScrmSource(captchaRow.value) ? 'math' : 'text')
    const recognized = d?.ocr_recognized_text
    if (captchaType === 'math' && recognized && !captchaForm.captcha) {
      captchaForm.captcha = recognized
      const extra = `识别出的答案已自动填入: '${recognized}' —— 请核对图片是否一致再提交。`
      captchaHint.value = captchaHint.value
        ? captchaHint.value + ' | ' + extra
        : extra
    }
  } catch (e) {
    ElMessage.error(`获取验证码失败: ${e.message}`)
  } finally {
    captchaLoading.value = false
  }
}

function refreshCaptcha() {
  startCaptchaLogin()
}

async function submitCaptcha() {
  if (!captchaForm.sessionId) {
    ElMessage.warning('会话已失效,请重新获取验证码')
    return
  }
  if (!captchaForm.captcha.trim()) {
    ElMessage.warning(isMathCaptcha.value ? '请输入数学计算的答案' : '请输入验证码')
    return
  }
  captchaSubmitting.value = true
  try {
    const res = await captchaLoginSubmit(captchaForm.sessionId, captchaForm.captcha.trim())
    const d = res.data
    if (d?.success) {
      ElMessage.success(d?.message || '登录成功')
      captchaDialogVisible.value = false
      if (captchaRow.value) {
        await loadLoginState(captchaRow.value.data_type)
        await loadCaptchaStates()
      }
    } else {
      ElMessageBox.alert(
        d?.message || '登录失败,可能是验证码错误或账号密码错误',
        '登录失败',
        { type: 'warning', confirmButtonText: '确定' },
      )
      if (captchaDialogVisible.value) setTimeout(startCaptchaLogin, 400)
    }
  } catch (e) {
    ElMessage.error(`提交失败: ${e.message}`)
  } finally {
    captchaSubmitting.value = false
  }
}

async function handleLogout(row) {
  try {
    await ElMessageBox.confirm(
      `确认清除「${row.name || row.data_type}」的登录态?清除后下次同步需重新输入验证码。`,
      '退出登录',
      { type: 'warning', confirmButtonText: '确认清除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    if (loginStates[row.data_type]) loginStates[row.data_type].busy = true
    await captchaLoginLogout(row.data_type)
    await Promise.all([loadLoginState(row.data_type), loadCaptchaStates()])
    ElMessage.success('登录态已清除')
  } catch (e) {
    ElMessage.error(`退出失败: ${e.message}`)
  } finally {
    if (loginStates[row.data_type]) loginStates[row.data_type].busy = false
  }
}

function formatTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}
</script>

<style scoped>
/* ========== 整体左右双列布局 ========== */
.sync-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.sync-left {
  flex: 1 1 0;
  min-width: 0;
}
.sync-left :deep(.el-collapse-item__header) {
  font-weight: 600;
  padding-left: 4px;
  font-size: 14px;
}
.sync-left :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
}
.panel-inner {
  padding: 16px 12px 8px 4px;
}
.sync-left :deep(.el-radio-group) {
  flex-wrap: wrap;
  row-gap: 4px;
}
.sync-right {
  flex: 0 0 540px;
  max-width: 540px;
}
@media (max-width: 1400px) {
  .sync-right { flex: 0 0 480px; max-width: 480px; }
}
@media (max-width: 1200px) {
  .sync-layout { flex-direction: column; }
  .sync-right { flex: 0 0 auto; max-width: 100%; width: 100%; }
}
@media (max-width: 768px) {
  /* 列布局下取消 sticky,避免右栏日志遮挡上方左栏内容 */
  .sync-right-sticky { position: static; }
  .right-log-stream {
    max-height: 320px;
    min-height: 200px;
  }
  .sync-header-row { flex-wrap: wrap; }
  .sync-header-progress { flex: 1 1 100%; }
  .panel-inner { padding: 12px 4px 8px; }
  /* el-descriptions 三列 → 单列堆叠,避免窄屏挤压 */
  .sync-left :deep(.el-descriptions__table),
  .sync-left :deep(.el-descriptions__table tbody) { display: block; }
  .sync-left :deep(.el-descriptions__table tr) { display: flex; flex-wrap: wrap; }
  .sync-left :deep(.el-descriptions__cell) { flex: 1 1 100%; box-sizing: border-box; }
}

/* 右栏 sticky */
.sync-right-sticky {
  position: sticky;
  top: 16px;
  z-index: 10;
}
.right-card {
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 88px);
}
.right-card :deep(.el-card__body) {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 14px 16px;
  overflow-y: auto;
}
.right-title { font-size: 14px; }

/* 顶部单行进度条: 紧凑, 不挤占垂直空间 */
.sync-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}
.sync-header-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.sync-header-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
}
.sync-header-progress .status-tag { flex-shrink: 0; }
.sync-header-progress .inline-progress {
  flex: 0 0 90px;
  margin: 0;
}
.sync-header-progress .inline-progress :deep(.el-progress-bar__outer) {
  background-color: #f0f2f5;
  border-radius: 4px;
}
.sync-header-progress .progress-text {
  font-size: 12px;
  color: #606266;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
}
.sync-header-progress .progress-text b { color: #303133; font-weight: 600; }
@media (max-width: 1400px) {
  .sync-header-progress .inline-progress { flex-basis: 70px; }
  .sync-header-progress .progress-text { font-size: 11px; }
}

/* 结果汇总:底部单行文字,不再弹一堆 tag */
.right-result-summary {
  border-top: 1px dashed #ebeef5;
  padding-top: 10px;
  font-size: 12px;
  line-height: 1.7;
}
.right-result-title {
  font-weight: 600;
  color: #303133;
  margin-right: 6px;
}
.right-result-line {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: #606266;
}
.log-inline-ok { color: #67c23a; }
.log-inline-err { color: #f56c6c; }
.right-log-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0 10px 0;
  border-bottom: 1px dashed #ebeef5;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.right-log-toolbar .ml-auto { margin-left: auto; }

/* ④ 日志流 */
.right-log-stream {
  flex: 1 1 auto;
  min-height: 300px;
  max-height: calc(100vh - 420px);
  overflow-y: auto;
  overflow-x: hidden;
  background: #1e1e1e;
  color: #e0e0e0;
  padding: 10px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.75;
  border-radius: 4px;
}

.log-line { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.log-t { color: #9cdcfe; margin-right: 6px; }
.log-tag {
  display: inline-block;
  padding: 0 4px;
  border-radius: 2px;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
  margin-right: 6px;
}
.log-tag-bms { background: #409eff; }
.log-tag-scrm { background: #67c23a; }
.log-dt { color: #4ec9b0; margin-right: 8px; }
.log-line.log-failed .log-msg { color: #f48771; }
.log-line.log-imported .log-msg { color: #ce9178; }
.log-empty { color: #858585; font-style: italic; padding: 16px 0; text-align: center; }

/* ⑤ 结果汇总 */
.right-result-summary {
  border-top: 1px dashed #ebeef5;
  padding-top: 10px;
}
.right-result-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
}
.right-result-tags {
  display: flex;
  flex-wrap: wrap;
  max-height: 160px;
  overflow-y: auto;
}

/* 通用 utility */
.alert-text { margin: 0; font-size: 13px; line-height: 1.5; }
.form-hint { color: #909399; font-size: 12px; line-height: 1.5; }
.src-alias { color: #909399; font-size: 12px; margin-left: 4px; font-weight: 400; }
.mr-4 { margin-right: 4px; }
.ml-4 { margin-left: 4px; }
.mt-8 { margin-top: 8px; }
.mt-10 { margin-top: 10px; }
.mt-12 { margin-top: 12px; }
.mt-16 { margin-top: 16px; }
.mb-4 { margin-bottom: 4px; }
.mb-8 { margin-bottom: 8px; }
.mb-10 { margin-bottom: 10px; }
.mb-12 { margin-bottom: 12px; }
.mb-16 { margin-bottom: 16px; }
.gap-6 { gap: 6px; }
.gap-8 { gap: 8px; }
.gap-12 { gap: 12px; }
.gap-16 { gap: 16px; }
.flex-wrap { flex-wrap: wrap; }
.flex { display: flex; }
.ml-auto { margin-left: auto; }
.justify-between { justify-content: space-between; }
.items-center { align-items: center; }
.items-baseline { align-items: baseline; }
.font-medium { font-weight: 500; }
.task-meta { color: #303133; font-size: 13px; }
.link { color: var(--el-color-primary); text-decoration: underline; }
.credential-group { white-space: nowrap; }
.range-list { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.range-item { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.range-chip {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  min-width: 38px;
  text-align: center;
}
.range-chip-bms { background: #409eff; }
.range-chip-scrm { background: #67c23a; }
.range-ds { min-width: 180px; color: #303133; font-weight: 500; }
.range-desc {
  color: #606266;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.cooldown-tip {
  color: #e6a23c;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.task-id {
  color: #909399;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
}
.dim { color: #909399; }
.muted { color: #909399; font-size: 12px; }
.captcha-login-cell {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.captcha-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  padding: 24px 0;
  color: #909399;
  font-size: 13px;
}
.captcha-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.captcha-img-wrap {
  width: 100%;
  display: flex;
  justify-content: center;
  background: #f5f7fa;
  border: 1px dashed #dcdfe6;
  border-radius: 6px;
  padding: 12px;
  min-height: 90px;
}
.captcha-img {
  max-width: 100%;
  max-height: 90px;
  cursor: pointer;
  border-radius: 4px;
}
.captcha-empty {
  color: #c0c4cc;
  font-size: 13px;
  line-height: 66px;
}
.hint-text {
  color: #909399;
  font-size: 12px;
}
.tag-bms {
  --el-tag-bg-color: #ecf5ff;
  --el-tag-border-color: #d9ecff;
  --el-tag-text-color: #409eff;
}
.tag-scrm {
  --el-tag-bg-color: #f0f9eb;
  --el-tag-border-color: #e1f3d8;
  --el-tag-text-color: #67c23a;
}
</style>
