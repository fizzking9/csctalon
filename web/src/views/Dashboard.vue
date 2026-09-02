<template>
  <div class="page-container dashboard">
    <!-- 顶部问候 -->
    <div class="greeting">
      <div>
        <div class="page-title" style="margin: 0">{{ greeting }},{{ authStore.displayName }}</div>
        <div class="page-sub">{{ todayText }}</div>
      </div>
      <el-tag :type="roleTagType" effect="plain">{{ roleLabel }}</el-tag>
    </div>

    <!-- ========================================================== -->
    <!-- 1. 系统管理员工作台: 系统配置 + 数据源 + 操作日志 + 全局待办 -->
    <!-- ========================================================== -->
    <template v-if="authStore.isAdmin">
      <!-- 快捷入口: 系统管理为主 -->
      <el-card class="mb-16" shadow="never">
        <template #header>
          <span style="font-weight:600">系统管理快捷入口</span>
        </template>
        <div class="shortcut-grid">
          <div class="shortcut-item shortcut-hero is-primary" @click="$router.push('/dashboards/today')">
            <el-icon :size="40"><DataLine /></el-icon>
            <div class="si-info">
              <div class="si-title">运营看板</div>
              <div class="si-desc">日 / 周 / 月 / 季度 · 全员工作一览</div>
            </div>
            <el-icon class="si-arrow"><ArrowRight /></el-icon>
          </div>

          <el-divider direction="vertical" style="height: 96px" />

          <div class="shortcut-item-group">
            <div class="si-subtitle">系统配置</div>
            <div class="shortcut-row">
              <div class="shortcut-chip" @click="$router.push('/system/users')">
                <el-icon color="#409eff"><UserFilled /></el-icon><span>用户管理</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/system/persons')">
                <el-icon color="#67c23a"><User /></el-icon><span>人员管理</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/data/ingest')">
                <el-icon color="#e6a23c"><Upload /></el-icon><span>数据采集</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/system/classification-rules')">
                <el-icon color="#9b59b6"><Filter /></el-icon><span>分类规则</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/system/data-quality')">
                <el-icon color="#16a085"><CircleCheck /></el-icon><span>数据质量</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/system/operation-logs')">
                <el-icon color="#909399"><Tickets /></el-icon><span>操作日志</span>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <el-row :gutter="16">
        <el-col :xs="24" :md="14">
          <!-- 待办事项 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>待办事项 <el-tag size="small" type="warning" round style="margin-left:6px">{{ todoCount }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/todos')">全部待办 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTodos" class="mini-list">
              <li v-for="t in todoList" :key="t.id" class="mini-list-item" @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="todoPrioTag(t.priority)" effect="plain" class="mr-8">{{ t.priority || '中' }}</el-tag>
                <span class="mli-title">{{ t.title }}</span>
                <span class="mli-time">{{ formatDay(t.due_date || t.created_at) }}</span>
                <el-tag size="small" :type="todoStatusTag(t.status)" effect="plain">{{ t.status || '待处理' }}</el-tag>
              </li>
              <li v-if="!loadingTodos && !todoList.length" class="mli-empty">暂无待办 ✅</li>
            </ul>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="10">
          <!-- 最近系统操作 -->
          <el-card shadow="never">
            <template #header>
              <div class="flex-between" style="margin: -8px 0">
                <span>最近系统操作</span>
                <el-button link type="primary" @click="$router.push('/system/operation-logs')">全部日志 ›</el-button>
              </div>
            </template>
            <div v-loading="loadingOps" class="op-list">
              <div v-for="op in recentOps" :key="op.id" class="op-item">
                <div class="op-top">
                  <el-tag size="small" :type="opTagType(op.action)">{{ op.action }}</el-tag>
                  <span class="op-time">{{ formatTime(op.created_at) }}</span>
                </div>
                <div class="op-meta">{{ op.username || '(未登录)' }} · {{ op.method }} {{ op.path }}</div>
              </div>
              <div v-if="!loadingOps && !recentOps.length" class="text-muted" style="text-align: center; padding: 16px">
                暂无操作记录
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- ========================================================== -->
    <!-- 2. 主管工作台: 决策看板 + 管理闭环 + 全局待办 + 专项任务   -->
    <!-- ========================================================== -->
    <template v-else-if="authStore.isSupervisor">
      <!-- 快捷入口: 决策看板 + 管理闭环 -->
      <el-card class="mb-16" shadow="never">
        <template #header>
          <span style="font-weight:600">快捷入口</span>
        </template>
        <div class="shortcut-grid">
          <div class="shortcut-item shortcut-hero is-primary" @click="$router.push('/dashboards/today')">
            <el-icon :size="40"><DataLine /></el-icon>
            <div class="si-info">
              <div class="si-title">运营看板</div>
              <div class="si-desc">日 / 周 / 月 / 季度 · 全员工作一览</div>
            </div>
            <el-icon class="si-arrow"><ArrowRight /></el-icon>
          </div>

          <el-divider direction="vertical" style="height: 96px" />

          <div class="shortcut-item-group">
            <div class="si-subtitle">专项分析 · 专项深入分析透视</div>
            <div class="shortcut-row">
              <div class="shortcut-chip" @click="$router.push('/dashboards/refund')">
                <el-icon color="#e6a23c"><Wallet /></el-icon><span>退款分析</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/complaint-analysis')">
                <el-icon color="#f56c6c"><WarningFilled /></el-icon><span>投诉分析</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/reception')">
                <el-icon color="#409eff"><Service /></el-icon><span>接待分析</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/refund')">
                <el-icon color="#9b59b6"><Money /></el-icon><span>提现时效</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/performance')">
                <el-icon color="#67c23a"><UserFilled /></el-icon><span>绩效分析</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/dashboards/special')">
                <el-icon color="#909399"><Aim /></el-icon><span>专项跟进</span>
              </div>
            </div>
          </div>

          <el-divider direction="vertical" style="height: 96px" />

          <div class="shortcut-item-group">
            <div class="si-subtitle">管理闭环 · 日常流程</div>
            <div class="shortcut-row">
              <div class="shortcut-chip" @click="$router.push('/todos')">
                <el-icon color="#e6a23c"><Bell /></el-icon><span>待办事项</span>
                <el-badge v-if="todoCount" :value="todoCount" class="sc-badge" />
              </div>
              <div class="shortcut-chip" @click="$router.push('/tasks')">
                <el-icon color="#409eff"><List /></el-icon><span>专项任务</span>
                <el-badge v-if="taskCount" :value="taskCount" class="sc-badge" />
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/reports')">
                <el-icon color="#34495e"><Document /></el-icon><span>报告索引</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/weekly')">
                <el-icon color="#16a085"><Calendar /></el-icon><span>周报看板</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/measures')">
                <el-icon color="#d35400"><Flag /></el-icon><span>管理措施</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/policies')">
                <el-icon color="#8e44ad"><Document /></el-icon><span>政策措施</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/alerts')">
                <el-icon color="#f56c6c"><AlarmClock /></el-icon><span>预警中心</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/report/center')">
                <el-icon color="#2c3e50"><Printer /></el-icon><span>报表中心</span>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <el-row :gutter="16">
        <el-col :xs="24" :md="14">
          <!-- 待办事项 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>待办事项 <el-tag size="small" type="warning" round style="margin-left:6px">{{ todoCount }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/todos')">全部待办 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTodos" class="mini-list">
              <li v-for="t in todoList" :key="t.id" class="mini-list-item" @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="todoPrioTag(t.priority)" effect="plain" class="mr-8">{{ t.priority || '中' }}</el-tag>
                <span class="mli-title">{{ t.title }}</span>
                <span class="mli-time">{{ formatDay(t.due_date || t.created_at) }}</span>
                <el-tag size="small" :type="todoStatusTag(t.status)" effect="plain">{{ t.status || '待处理' }}</el-tag>
              </li>
              <li v-if="!loadingTodos && !todoList.length" class="mli-empty">暂无待办 ✅</li>
            </ul>
          </el-card>

          <!-- 专项任务 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>专项任务 <el-tag size="small" type="primary" round style="margin-left:6px">{{ taskCount }} 个进行中</el-tag></span>
                <el-button link type="primary" @click="$router.push('/tasks')">全部任务 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTasks" class="mini-list">
              <li v-for="tk in taskList" :key="tk.id" class="mini-list-item" @click="$router.push(`/tasks/${tk.id}`)">
                <el-icon color="#409eff" style="flex-shrink:0"><List /></el-icon>
                <span class="mli-title">{{ tk.title }}</span>
                <div v-if="tk.progress != null" class="mli-progress">
                  <el-progress :percentage="Number(tk.progress) || 0" :stroke-width="6" :show-text="false" style="width: 120px" />
                  <span class="mli-progress-text">{{ tk.progress }}%</span>
                </div>
                <span class="mli-time">{{ formatDay(tk.end_date || tk.start_date || tk.created_at) }}</span>
                <el-tag size="small" :type="taskStatusTag(tk.status)" effect="dark">{{ tk.status || '进行中' }}</el-tag>
              </li>
              <li v-if="!loadingTasks && !taskList.length" class="mli-empty">暂无专项任务</li>
            </ul>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="10">
          <!-- 未读策略 -->
          <el-card v-if="unreadStrategies.length || loadingStrategies" class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>未读策略规范 <el-tag v-if="unreadStrategies.length" size="small" type="warning" round>{{ unreadStrategies.length }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/strategy/publish')">全部策略 ›</el-button>
              </div>
            </template>
            <div v-loading="loadingStrategies" class="strategy-list">
              <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
                <span class="unread-dot" />
                <span class="strategy-title">{{ s.title }}</span>
                <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
              </div>
            </div>
          </el-card>

          <!-- 管理闭环快捷 -->
          <el-card shadow="never">
            <template #header>管理闭环</template>
            <div class="preset-grid">
              <div class="preset-item" @click="$router.push('/management/incidents')">
                <el-icon color="#f56c6c"><WarningFilled /></el-icon>
                <span class="preset-name">影响事件</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
              <div class="preset-item" @click="$router.push('/management/schedule')">
                <el-icon color="#409eff"><Calendar /></el-icon>
                <span class="preset-name">排班管理</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
              <div class="preset-item" @click="$router.push('/management/upstream-tickets')">
                <el-icon color="#9b59b6"><Connection /></el-icon>
                <span class="preset-name">跨部门协作</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
              <div class="preset-item" @click="$router.push('/management/templates')">
                <el-icon color="#16a085"><Files /></el-icon>
                <span class="preset-name">看板模板</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- ========================================================== -->
    <!-- 3. 专项组长工作台: 专项任务 + 组员管理 + 知识库 + 排班      -->
    <!-- ========================================================== -->
    <template v-else-if="authStore.isSpecialtyLead">
      <el-card class="mb-16" shadow="never">
        <template #header>
          <span style="font-weight:600">专项管理快捷入口</span>
        </template>
        <div class="shortcut-grid">
          <div class="shortcut-item shortcut-hero is-primary" @click="$router.push('/dashboards/today')">
            <el-icon :size="40"><DataLine /></el-icon>
            <div class="si-info">
              <div class="si-title">运营看板</div>
              <div class="si-desc">本组工作一览 · 组员效率</div>
            </div>
            <el-icon class="si-arrow"><ArrowRight /></el-icon>
          </div>

          <el-divider direction="vertical" style="height: 96px" />

          <div class="shortcut-item-group">
            <div class="si-subtitle">专项管理</div>
            <div class="shortcut-row">
              <div class="shortcut-chip" @click="$router.push('/tasks')">
                <el-icon color="#409eff"><List /></el-icon><span>专项任务</span>
                <el-badge v-if="taskCount" :value="taskCount" class="sc-badge" />
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/kb-articles')">
                <el-icon color="#16a085"><Reading /></el-icon><span>知识库管理</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/schedule')">
                <el-icon color="#e6a23c"><Calendar /></el-icon><span>排班管理</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/upstream-tickets')">
                <el-icon color="#9b59b6"><Connection /></el-icon><span>跨部门协作</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/device-tags')">
                <el-icon color="#909399"><CollectionTag /></el-icon><span>设备专项标签</span>
              </div>
              <div class="shortcut-chip" @click="$router.push('/management/policies')">
                <el-icon color="#8e44ad"><Document /></el-icon><span>政策措施</span>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <el-row :gutter="16">
        <el-col :xs="24" :md="14">
          <!-- 待办事项 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>待办事项 <el-tag size="small" type="warning" round style="margin-left:6px">{{ todoCount }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/todos')">全部待办 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTodos" class="mini-list">
              <li v-for="t in todoList" :key="t.id" class="mini-list-item" @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="todoPrioTag(t.priority)" effect="plain" class="mr-8">{{ t.priority || '中' }}</el-tag>
                <span class="mli-title">{{ t.title }}</span>
                <span class="mli-time">{{ formatDay(t.due_date || t.created_at) }}</span>
                <el-tag size="small" :type="todoStatusTag(t.status)" effect="plain">{{ t.status || '待处理' }}</el-tag>
              </li>
              <li v-if="!loadingTodos && !todoList.length" class="mli-empty">暂无待办 ✅</li>
            </ul>
          </el-card>

          <!-- 专项任务 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>专项任务 <el-tag size="small" type="primary" round style="margin-left:6px">{{ taskCount }} 个进行中</el-tag></span>
                <el-button link type="primary" @click="$router.push('/tasks')">全部任务 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTasks" class="mini-list">
              <li v-for="tk in taskList" :key="tk.id" class="mini-list-item" @click="$router.push(`/tasks/${tk.id}`)">
                <el-icon color="#409eff" style="flex-shrink:0"><List /></el-icon>
                <span class="mli-title">{{ tk.title }}</span>
                <div v-if="tk.progress != null" class="mli-progress">
                  <el-progress :percentage="Number(tk.progress) || 0" :stroke-width="6" :show-text="false" style="width: 120px" />
                  <span class="mli-progress-text">{{ tk.progress }}%</span>
                </div>
                <span class="mli-time">{{ formatDay(tk.end_date || tk.start_date || tk.created_at) }}</span>
                <el-tag size="small" :type="taskStatusTag(tk.status)" effect="dark">{{ tk.status || '进行中' }}</el-tag>
              </li>
              <li v-if="!loadingTasks && !taskList.length" class="mli-empty">暂无专项任务</li>
            </ul>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="10">
          <!-- 未读策略 -->
          <el-card v-if="unreadStrategies.length || loadingStrategies" class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>未读策略规范 <el-tag v-if="unreadStrategies.length" size="small" type="warning" round>{{ unreadStrategies.length }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/strategy/publish')">全部策略 ›</el-button>
              </div>
            </template>
            <div v-loading="loadingStrategies" class="strategy-list">
              <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
                <span class="unread-dot" />
                <span class="strategy-title">{{ s.title }}</span>
                <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
              </div>
            </div>
          </el-card>

          <!-- 知识库快捷 -->
          <el-card shadow="never">
            <template #header>知识库 / 话术库</template>
            <div class="preset-grid">
              <div class="preset-item" @click="$router.push('/management/kb-articles')">
                <el-icon color="#16a085"><Reading /></el-icon>
                <span class="preset-name">条目管理</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
              <div class="preset-item" @click="$router.push('/strategy/publish')">
                <el-icon color="#9b59b6"><Document /></el-icon>
                <span class="preset-name">策略发布</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
              <div class="preset-item" @click="$router.push('/management/incidents')">
                <el-icon color="#f56c6c"><WarningFilled /></el-icon>
                <span class="preset-name">影响事件</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
              <div class="preset-item" @click="$router.push('/management/alerts')">
                <el-icon color="#e6a23c"><AlarmClock /></el-icon>
                <span class="preset-name">预警中心</span>
                <el-icon class="preset-arrow"><ArrowRight /></el-icon>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- ========================================================== -->
    <!-- 4. 专员工作台: 个人待办 + 专项任务 + 策略 + 知识库查阅      -->
    <!-- ========================================================== -->
    <template v-else-if="authStore.isSpecialist">
      <el-row :gutter="16">
        <el-col :xs="24" :md="15">
          <!-- 未读策略 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin: -8px 0">
                <span>策略规范 <el-tag v-if="unreadStrategies.length" size="small" type="warning" round>{{ unreadStrategies.length }} 未读</el-tag></span>
                <el-button link type="primary" @click="$router.push('/strategy/publish')">查看全部 ›</el-button>
              </div>
            </template>
            <div v-if="unreadStrategies.length || loadingStrategies" v-loading="loadingStrategies" class="strategy-list">
              <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
                <span class="unread-dot" />
                <span class="strategy-title">{{ s.title }}</span>
                <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
              </div>
            </div>
            <el-result v-else icon="success" title="策略已全部阅读" sub-title="当前没有未读的策略与规范">
              <template #extra>
                <el-button type="primary" @click="$router.push('/strategy/publish')">前往策略发布页</el-button>
              </template>
            </el-result>
          </el-card>

          <!-- 个人待办 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>我的待办 <el-tag size="small" type="warning" round style="margin-left:6px">{{ todoCount }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/todos')">全部待办 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTodos" class="mini-list">
              <li v-for="t in todoList" :key="t.id" class="mini-list-item" @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="todoPrioTag(t.priority)" effect="plain">{{ t.priority || '中' }}</el-tag>
                <span class="mli-title">{{ t.title }}</span>
                <span class="mli-time">{{ formatDay(t.due_date || t.created_at) }}</span>
              </li>
              <li v-if="!loadingTodos && !todoList.length" class="mli-empty">暂无待办 👍</li>
            </ul>
          </el-card>

          <!-- 专项任务 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>我的专项任务 <el-tag size="small" type="primary" round style="margin-left:6px">{{ taskCount }} 个</el-tag></span>
                <el-button link type="primary" @click="$router.push('/tasks')">全部任务 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTasks" class="mini-list">
              <li v-for="tk in taskList" :key="tk.id" class="mini-list-item" @click="$router.push(`/tasks/${tk.id}`)">
                <el-icon color="#409eff" style="flex-shrink:0"><List /></el-icon>
                <span class="mli-title">{{ tk.title }}</span>
                <div v-if="tk.progress != null" class="mli-progress">
                  <el-progress :percentage="Number(tk.progress) || 0" :stroke-width="6" :show-text="false" style="width: 100px" />
                  <span class="mli-progress-text">{{ tk.progress }}%</span>
                </div>
                <el-tag size="small" :type="taskStatusTag(tk.status)" effect="dark">{{ tk.status || '进行中' }}</el-tag>
              </li>
              <li v-if="!loadingTasks && !taskList.length" class="mli-empty">暂无专项任务</li>
            </ul>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="9">
          <!-- 快捷入口 -->
          <el-card class="mb-16" shadow="never">
            <template #header>快捷入口</template>
            <div class="agent-shortcuts">
              <div class="shortcut-tile" @click="$router.push('/dashboards/today')">
                <el-icon :size="32" color="#409eff"><DataLine /></el-icon>
                <span>运营看板</span>
              </div>
              <div class="shortcut-tile" @click="$router.push('/strategy/publish')">
                <el-icon :size="32" color="#9b59b6"><Document /></el-icon>
                <span>策略发布</span>
              </div>
              <div class="shortcut-tile" @click="$router.push('/todos')">
                <el-icon :size="32" color="#e6a23c"><Bell /></el-icon>
                <span>事项跟进</span>
                <el-badge v-if="todoCount" :value="todoCount" class="shortcut-badge" />
              </div>
              <div class="shortcut-tile" @click="$router.push('/tasks')">
                <el-icon :size="32" color="#16a085"><List /></el-icon>
                <span>专项任务</span>
                <el-badge v-if="taskCount" :value="taskCount" class="shortcut-badge" />
              </div>
              <div class="shortcut-tile" @click="$router.push('/management/kb-articles')">
                <el-icon :size="32" color="#34495e"><Reading /></el-icon>
                <span>知识库</span>
              </div>
              <div class="shortcut-tile" @click="$router.push('/management/invoices')">
                <el-icon :size="32" color="#d35400"><Tickets /></el-icon>
                <span>发票登记</span>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- ========================================================== -->
    <!-- 5. 普通客服工作台: 策略 + 个人待办 + 最简快捷入口          -->
    <!-- ========================================================== -->
    <template v-else>
      <el-row :gutter="16">
        <el-col :xs="24" :md="15">
          <!-- 未读策略 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin: -8px 0">
                <span>策略规范 <el-tag v-if="unreadStrategies.length" size="small" type="warning" round>{{ unreadStrategies.length }} 未读</el-tag></span>
                <el-button link type="primary" @click="$router.push('/strategy/publish')">查看全部 ›</el-button>
              </div>
            </template>
            <div v-if="unreadStrategies.length || loadingStrategies" v-loading="loadingStrategies" class="strategy-list">
              <div v-for="s in unreadStrategies" :key="s.id" class="strategy-item" @click="$router.push('/strategy/publish')">
                <span class="unread-dot" />
                <span class="strategy-title">{{ s.title }}</span>
                <el-tag size="small" effect="plain">{{ s.category || '通用' }}</el-tag>
              </div>
            </div>
            <el-result v-else icon="success" title="策略已全部阅读" sub-title="当前没有未读的策略与规范">
              <template #extra>
                <el-button type="primary" @click="$router.push('/strategy/publish')">前往策略发布页</el-button>
              </template>
            </el-result>
          </el-card>

          <!-- 个人待办 -->
          <el-card class="mb-16" shadow="never">
            <template #header>
              <div class="flex-between" style="margin:-8px 0">
                <span>我的待办 <el-tag size="small" type="warning" round style="margin-left:6px">{{ todoCount }} 条</el-tag></span>
                <el-button link type="primary" @click="$router.push('/todos')">全部待办 ›</el-button>
              </div>
            </template>
            <ul v-loading="loadingTodos" class="mini-list">
              <li v-for="t in todoList" :key="t.id" class="mini-list-item" @click="$router.push(`/todos/${t.id}`)">
                <el-tag size="small" :type="todoPrioTag(t.priority)" effect="plain">{{ t.priority || '中' }}</el-tag>
                <span class="mli-title">{{ t.title }}</span>
                <span class="mli-time">{{ formatDay(t.due_date || t.created_at) }}</span>
              </li>
              <li v-if="!loadingTodos && !todoList.length" class="mli-empty">暂无待办 👍</li>
            </ul>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="9">
          <!-- 快捷入口 -->
          <el-card class="mb-16" shadow="never">
            <template #header>快捷入口</template>
            <div class="agent-shortcuts">
              <div class="shortcut-tile" @click="$router.push('/dashboards/today')">
                <el-icon :size="32" color="#409eff"><DataLine /></el-icon>
                <span>运营看板</span>
              </div>
              <div class="shortcut-tile" @click="$router.push('/strategy/publish')">
                <el-icon :size="32" color="#9b59b6"><Document /></el-icon>
                <span>策略发布</span>
              </div>
              <div class="shortcut-tile" @click="$router.push('/todos')">
                <el-icon :size="32" color="#e6a23c"><Bell /></el-icon>
                <span>事项跟进</span>
                <el-badge v-if="todoCount" :value="todoCount" class="shortcut-badge" />
              </div>
              <div class="shortcut-tile" @click="$router.push('/management/kb-articles')">
                <el-icon :size="32" color="#34495e"><Reading /></el-icon>
                <span>知识库</span>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { listTodos } from '@/api/todo'
import { listStrategy } from '@/api/strategy'
import { listTasks } from '@/api/task'
import { listOperationLogs } from '@/api/operationLog'
import dayjs from 'dayjs'
import {
  ArrowRight, Bell, List, Calendar, Flag, EditPen, Printer,
  Wallet, WarningFilled, Service, Money, UserFilled, Aim, Document, DataLine,
  Upload, User, Filter, CircleCheck, Tickets, Reading, CollectionTag,
  Connection, Files, AlarmClock,
} from '@element-plus/icons-vue'

const authStore = useAuthStore()

const todoCount = ref(0)
const todoList = ref([])
const loadingTodos = ref(false)
const taskCount = ref(0)
const taskList = ref([])
const loadingTasks = ref(false)
const unreadStrategies = ref([])
const loadingStrategies = ref(false)
const recentOps = ref([])
const loadingOps = ref(false)

const greeting = computed(() => {
  const h = dayjs().hour()
  if (h < 6) return '凌晨好'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const todayText = computed(() => {
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][dayjs().day()]
  return dayjs().format('YYYY年MM月DD日') + ' ' + wd + ' · ' + roleLabel.value + '工作台'
})
const roleLabel = computed(() => ({
  admin: '系统管理员', supervisor: '主管', specialty_lead: '专项组长', specialist: '专员', agent: '客服',
}[authStore.role] || ''))
const roleTagType = computed(() => ({
  admin: 'danger', supervisor: 'warning', specialty_lead: 'primary', specialist: 'success', agent: 'info',
}[authStore.role] || 'info'))

const formatTime = (t) => (t ? dayjs(t).format('MM-DD HH:mm') : '-')
const formatDay = (t) => (t ? dayjs(t).format('YYYY-MM-DD') : '-')

function opTagType(a) {
  if (a.includes('失败') || a.includes('拒绝') || a.includes('删除')) return 'danger'
  if (a.includes('发布') || a.includes('创建')) return 'success'
  if (a.includes('更新') || a.includes('跟进')) return 'warning'
  if (a.includes('登录')) return 'info'
  return 'info'
}
function todoPrioTag(p) {
  const k = String(p || '中')
  if (k.includes('高') || k === 'H') return 'danger'
  if (k.includes('低') || k === 'L') return 'info'
  return 'warning'
}
function todoStatusTag(s) {
  const k = String(s || '待处理')
  if (k.includes('已完成') || k.includes('done')) return 'success'
  if (k.includes('处理中') || k.includes('进行')) return 'primary'
  if (k.includes('关闭') || k.includes('取消')) return 'info'
  return 'warning'
}
function taskStatusTag(s) {
  const k = String(s || '进行中')
  if (k.includes('已完成') || k.includes('完成')) return 'success'
  if (k.includes('进行') || k.includes('执行')) return 'primary'
  if (k.includes('暂停') || k.includes('风险')) return 'warning'
  if (k.includes('关闭') || k.includes('取消')) return 'info'
  return 'danger'
}

async function loadTodos() {
  loadingTodos.value = true
  try {
    const res = await listTodos({ page: 1, page_size: 10, sort: 'priority_desc' })
    todoCount.value = Number(res.data?.total) || 0
    todoList.value = (res.data?.items || []).slice(0, 10)
  } catch {
    todoCount.value = 0; todoList.value = []
  } finally {
    loadingTodos.value = false
  }
}
async function loadTasks() {
  loadingTasks.value = true
  try {
    const res = await listTasks({ page: 1, page_size: 10 })
    taskCount.value = Number(res.data?.total) || 0
    taskList.value = (res.data?.items || []).slice(0, 10)
  } catch {
    taskCount.value = 0; taskList.value = []
  } finally {
    loadingTasks.value = false
  }
}
async function loadStrategies() {
  loadingStrategies.value = true
  try {
    const res = await listStrategy({ status: 'published', page: 1, page_size: 50 })
    unreadStrategies.value = (res.data?.items || []).filter((s) => !s.has_read).slice(0, 5)
  } catch {
    unreadStrategies.value = []
  } finally {
    loadingStrategies.value = false
  }
}
async function loadRecentOps() {
  if (!authStore.isAdmin) return
  loadingOps.value = true
  try {
    const res = await listOperationLogs({ page: 1, page_size: 6 })
    recentOps.value = res.data?.items || []
  } catch {
    recentOps.value = []
  } finally {
    loadingOps.value = false
  }
}

onMounted(async () => {
  await Promise.all([
    loadTodos(), loadTasks(), loadStrategies(),
    authStore.isAdmin && loadRecentOps(),
  ].filter(Boolean))
})
</script>

<style scoped>
.dashboard { padding-bottom: 24px; }
.greeting {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-sub {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}

/* 快捷入口网格 */
.shortcut-grid {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.shortcut-item.shortcut-hero {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  background: linear-gradient(135deg, #ecf5ff 0%, #f0f9eb 100%);
  border: 1px solid #d9ecff;
}
.shortcut-item.shortcut-hero:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(64, 158, 255, 0.15);
}
.si-info { display: flex; flex-direction: column; gap: 2px; }
.si-title { font-size: 16px; font-weight: 600; color: #303133; }
.si-desc { font-size: 12px; color: #909399; }
.si-arrow { color: #c0c4cc; }
.shortcut-item-group { display: flex; flex-direction: column; }
.si-subtitle { font-size: 12px; color: #909399; margin-bottom: 8px; }
.shortcut-row { display: flex; gap: 12px; flex-wrap: wrap; }
.shortcut-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid #ebeef5;
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
  background: #fff;
}
.shortcut-chip:hover {
  border-color: #409eff;
  background: #ecf5ff;
  transform: translateY(-1px);
}
.shortcut-chip span { font-size: 13px; color: #303133; }
.sc-badge { margin-left: 4px; }

/* 待办/任务列表 */
.mini-list { list-style: none; padding: 0; margin: 0; }
.mini-list-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 0;
  border-bottom: 1px solid #f5f5f5;
  cursor: pointer;
  transition: background 0.15s;
}
.mini-list-item:last-child { border-bottom: none; }
.mini-list-item:hover { background: #f5f7fa; }
.mli-title {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mli-time { font-size: 12px; color: #909399; }
.mli-progress { display: flex; align-items: center; gap: 6px; }
.mli-progress-text { font-size: 12px; color: #606266; min-width: 32px; }
.mli-empty {
  text-align: center;
  color: #909399;
  padding: 24px 0;
  font-size: 13px;
}
.mr-8 { margin-right: 8px; }

/* 策略列表 */
.strategy-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.strategy-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
}
.strategy-item:hover { background: #f5f7fa; }
.unread-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e6a23c;
  flex-shrink: 0;
}
.strategy-title {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 操作日志 */
.op-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.op-item {
  padding: 6px 0;
  border-bottom: 1px solid #f5f5f5;
}
.op-item:last-child { border-bottom: none; }
.op-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2px;
}
.op-time { font-size: 12px; color: #909399; }
.op-meta {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'SF Mono', Menlo, monospace;
}

/* 客服快捷入口磁贴 */
.agent-shortcuts {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.shortcut-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 28px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
  min-width: 120px;
}
.shortcut-tile:hover {
  border-color: #409eff;
  background: #ecf5ff;
  transform: translateY(-2px);
}
.shortcut-tile span { font-size: 14px; color: #303133; }
.shortcut-badge {
  position: absolute;
  top: 8px;
  right: 8px;
}

/* 预设网格 */
.preset-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}
.preset-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.preset-item:hover {
  border-color: #409eff;
  background: #ecf5ff;
}
.preset-name {
  flex: 1;
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preset-arrow { color: #c0c4cc; }

.text-muted { color: #c0c4cc; }
.mb-16 { margin-bottom: 16px; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
</style>
