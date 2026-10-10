<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 范围：本页 Tab1「登录态」区块（2026-09-13 故障修复时补齐说明；其它 Tab 为存量未改造）。 │
  │ 目的：运维/管理员在**采集取不到数据**时，5 分钟内回答「是登录态失效还是显示服务坏了」。│
  │       使用频率：登录态过期时（约每周 1 次）或采集报错时。                              │
  │ 逻辑：① 先给「平台 × 登录态文件年龄」总表 —— 一眼判断要不要重登；                      │
  │       ② 再给「采集源 × 操作」表 —— 表上直接发起扫码，不跳页（原先是唯一断点：          │
  │          提示「需重新扫码」却没有扫码入口，只能 SSH 上服务器跑脚本）；                  │
  │       ③ 显示服务可用性做成**警示条**而非静默开关：它不是登录态问题，是部署问题，       │
  │          必须让人看见「这条链路现在根本起不来浏览器」。                                │
  │ 口径：登录态健康度 = 登录态文件内嵌 _meta 的真实登录时间/login_at(旧文件无 _meta 时回退    │
  │       mtime)，超 7 天仅**提示**不阻断（各平台 token 实际有效期不同，无法准确判定）。       │
  │       登录态文件路径口径见 docs/arch/A-20260910-001-共享文档页面内登录态管理.md §三；        │
  │       显示服务口径：$DISPLAY 的 X socket 是否真在跑 + 能否按需拉起 Xvfb。              │
  │ 取舍：❌ 不做「一键重登全部平台」—— 同平台并发登录会互相覆盖 state，必须人工逐个扫；    │
  │         ❌ 不展示登录态文件内容/绝对路径（等同账号凭据，只回 cookie 数量）；            │
  │         ❌ 不在 GET /states 时就拉起 Xvfb —— 一个查询接口不该有起 daemon 的副作用，     │
  │            只在真正点「扫码登录」时才自愈拉起（接口返回「可自愈」而非「已就绪」）。      │
  │ 工单：#49（本区块修复）· 设计：docs/arch/A-20260910-001-共享文档页面内登录态管理.md §四 │
  │       运维：docs/ops/数据采集-运维手册.md §八                                          │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="access-center">
    <div class="flex-between mb-16">
      <div>
        <div class="page-title" style="margin: 0">接入中心</div>
        <div class="page-sub">对外数据接入的统一入口 · 登录态 / 外部 Token / 推送配置包</div>
      </div>
      <el-button size="small" :icon="Refresh" @click="refreshAll">刷新</el-button>
    </div>

    <el-tabs v-model="tab" class="access-tabs">
      <!-- ═══ Tab1 登录态 ═══ -->
      <el-tab-pane label="① 登录态" name="states">
        <el-alert
          v-if="!states.display_available"
          type="warning"
          :closable="false"
          show-icon
          class="mb-16"
          title="服务器缺少虚拟显示服务：本容器未安装 Xvfb，无法在页面内启动扫码登录。"
        >
          <template #default>
            请用命令行登录（下方各源「命令行」按钮可复制完整步骤）。
            数据采集本身不受影响，只是无法在页面内扫码。
          </template>
        </el-alert>
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>采集源登录态健康度</span>
              <!-- 页面层设计说明(第三层落地): 说明「看什么 + 不做什么」 -->
              <el-popover placement="bottom-end" :width="420" trigger="click">
                <template #reference>
                  <el-icon class="info-icon" title="设计说明"><InfoFilled /></el-icon>
                </template>
                <div class="rationale">
                  <p><b>替谁：</b>运维 / 管理员。判断「采集取不到数是登录态过期，还是服务器显示服务坏了」。</p>
                  <p><b>怎么判：</b>登录态超 7 天仅提示不阻断（各平台 token 有效期不同，判不准）；关键词是
                    <b>文件 mtime</b>，不是「已登录/未登录」的断言。</p>
                  <p><b>取舍（不做）：</b></p>
                  <ul>
                    <li>不做「一键重登全部平台」—— 同平台并发登录会互相覆盖登录态文件，必须人工逐个扫。</li>
                    <li>不回显登录态文件内容或绝对路径（等同账号凭据），只给 cookie 数量与文件年龄。</li>
                    <li>不在打开本页时就拉起 Xvfb —— 查询接口不该有起后台进程的副作用，
                      只在点「扫码登录」时才自动准备显示服务。</li>
                  </ul>
                  <p class="dim">详见 <code>docs/ops/数据采集-运维手册.md</code> §八</p>
                </div>
              </el-popover>
            </div>
          </template>
          <el-table :data="states.items" size="small" border>
            <el-table-column label="平台" width="110">
              <template #default="{ row }">
                <el-tag :type="row.platform === 'tencent' ? 'primary' : 'warning'" size="small">
                  {{ platformName(row.platform) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="登录态文件" prop="state_file" min-width="150" />
            <el-table-column label="登录账号" width="130">
              <!-- #149: 真实登录账号(uid 脱敏), 无 _meta 时显示 '—/未知' -->
              <template #default="{ row }">{{ row.uid && row.uid !== 'undefined' ? row.uid : '—' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <el-tag v-if="!row.exists" type="danger" size="small" effect="plain">未登录</el-tag>
                <el-tag v-else-if="row.stale" type="warning" size="small" effect="plain">建议重登</el-tag>
                <el-tag v-else type="success" size="small" effect="plain">正常</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="登录时间" width="150">
              <!-- #149: 优先真实登录时间(内嵌 _meta); 旧文件无 _meta 时回退文件 mtime -->
              <template #default="{ row }">
                {{ fmtTime(row.login_at || row.mtime) }}
              </template>
            </el-table-column>
            <el-table-column label="已过天数" width="100">
              <template #default="{ row }">{{ row.age_days ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="提示" min-width="180">
              <template #default="{ row }">
                <span :class="row.stale ? 'warn-text' : 'dim'">{{ row.advice || '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="mt-16">
          <template #header>
            <div class="card-header">
              <span>采集源（可对某源发起扫码登录）</span>
              <el-button link type="primary" size="small" @click="openLoginLogs">登录历史</el-button>
              <el-button link type="primary" size="small" @click="loadSources">刷新</el-button>
            </div>
          </template>
          <el-table :data="sources" size="small" border empty-text="暂无采集源（可在 数据采集 → 共享文档采集 中新增）">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column label="平台" width="90">
              <template #default="{ row }">
                <el-tag :type="row.platform === 'tencent' ? 'primary' : 'warning'" size="small">
                  {{ platformName(row.platform) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="doc_name" label="文档名" min-width="150" show-overflow-tooltip />
            <el-table-column prop="doc_url" label="共享链接" min-width="200" show-overflow-tooltip />
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button
                  link
                  type="primary"
                  size="small"
                  :disabled="!states.display_available"
                  @click="startLogin(row)"
                >
                  扫码登录
                </el-button>
                <el-button link type="info" size="small" @click="copyCliCommand(row)">命令行</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ═══ Tab2 外部 Token ═══ -->
      <el-tab-pane label="② 外部 Token" name="tokens">
        <ExternalTokenPanel />
      </el-tab-pane>

      <!-- ═══ Tab3 推送配置包 ═══ -->
      <el-tab-pane label="③ 推送配置包" name="packages">
        <el-alert type="info" :closable="false" show-icon class="mb-16">
          <template #title>
            选好场景、填上共享文档地址，点「生成并复制」即可得到一段完整 Markdown ——
            含接口地址、鉴权方式、字段规范与提示词要求，<b>一次粘贴给 WorkBuddy 即可</b>。
          </template>
        </el-alert>

        <el-row :gutter="16">
          <el-col :span="9">
            <el-card shadow="never">
              <template #header><span>生成参数</span></template>
              <el-form label-width="100px" label-position="right">
                <el-form-item label="接入场景" required>
                  <el-select v-model="pkg.profileId" placeholder="选择场景" style="width: 100%" @change="onProfileChange">
                    <el-option v-for="p in profiles" :key="p.id" :label="p.name" :value="p.id" />
                  </el-select>
                </el-form-item>
                <el-form-item
                  v-for="v in currentVariables"
                  :key="v.key"
                  :label="v.label"
                  :required="v.required"
                >
                  <el-input
                    v-model="pkg.values[v.key]"
                    :placeholder="placeholderFor(v.key)"
                    :maxlength="v.key === 'doc_url' ? 512 : 128"
                  />
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" :loading="pkgLoading" :icon="Document" @click="renderAndCopy">
                    生成并复制
                  </el-button>
                  <el-button :disabled="!pkgMarkdown" @click="pkgPreviewVisible = true">预览</el-button>
                </el-form-item>
              </el-form>
            </el-card>

            <el-card shadow="never" class="mt-16">
              <template #header>
                <div class="card-header">
                  <span>模板维护（管理员）</span>
                </div>
              </template>
              <div class="dim mb-8">模板可编辑，改错了可一键恢复默认。</div>
              <el-button size="small" :icon="Edit" @click="openEdit">编辑当前模板</el-button>
              <el-button size="small" :icon="RefreshLeft" :disabled="!pkg.profileId" @click="resetCurrent">
                恢复默认
              </el-button>
            </el-card>
          </el-col>

          <el-col :span="15">
            <el-card shadow="never">
              <template #header>
                <div class="card-header">
                  <span>配置包预览</span>
                  <el-button
                    v-if="pkgMarkdown"
                    type="primary"
                    link
                    size="small"
                    :icon="DocumentCopy"
                    @click="copyWithToast(pkgMarkdown, '配置包')"
                  >
                    复制
                  </el-button>
                </div>
              </template>
              <el-input
                v-if="pkgMarkdown"
                v-model="pkgMarkdown"
                type="textarea"
                :rows="28"
                readonly
                class="pkg-textarea"
              />
              <el-empty v-else description="填写左侧参数后点「生成并复制」" />
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- Tab4「AI 与模型」已于 2026-09-29 迁出至「AI 与分析」组:
           模型接入(.env 只读)→ LlmProviders.vue 底部「兜底配置」区块;
           提示词四层总览 → AiFocusConfigs.vue 顶部。接入中心回归纯数据对接。 -->
    </el-tabs>

    <!-- ═══ 扫码登录弹窗 (#45) ═══ -->
    <el-dialog
      v-model="loginVisible"
      :title="`${loginSource?.doc_name || loginSource?.doc_url || ''} — 扫码登录`"
      width="620px"
      :close-on-click-modal="false"
      @closed="onLoginDialogClosed"
    >
      <el-alert type="info" :closable="false" show-icon class="mb-12">
        <template #title>
          请在下方窗口中完成扫码登录；登录成功后点「我已扫码完成」保存登录态。
          同一平台同时只能有一个登录会话。
        </template>
      </el-alert>

      <div class="login-shot">
        <img v-if="loginScreenshot" :src="loginScreenshot" alt="登录页截图" class="shot-img" />
        <div v-else class="shot-empty">正在启动浏览器并加载登录页…</div>
      </div>

      <div class="login-status">
        <el-tag :type="statusType(loginStatus)" size="small">{{ statusText(loginStatus) }}</el-tag>
        <span class="dim ml-8">{{ loginCurrentUrl }}</span>
        <span v-if="loginExpiresIn" class="dim ml-8">剩余 {{ loginExpiresIn }} 秒</span>
      </div>

      <template #footer>
        <el-button @click="cancelLogin">取消</el-button>
        <el-button type="primary" :loading="confirming" @click="confirmLogin">我已扫码完成</el-button>
      </template>
    </el-dialog>

    <!-- ═══ 登录历史弹窗 (#149) ═══ -->
    <el-dialog v-model="loginLogsVisible" title="登录历史（最近 90 天）" width="760px">
      <div class="dim mb-12">
        记录页面内扫码登录的每次结果；「成功」表示登录态已校验通过并保存。
      </div>
      <el-table :data="loginLogs" size="small" border max-height="420">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="平台" width="100">
          <template #default="{ row }">{{ platformName(row.platform) }}</template>
        </el-table-column>
        <el-table-column label="账号" width="130">
          <template #default="{ row }">{{ row.uid || '—' }}</template>
        </el-table-column>
        <el-table-column label="来源" width="80">
          <template #default="{ row }">
            <span>{{ row.source_id ? `源#${row.source_id}` : 'CLI' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag :type="row.result === 'success' ? 'success' : 'danger'" size="small">
              {{ row.result === 'success' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="cookies_count" label="Cookies" width="80" />
        <el-table-column label="失败原因" min-width="180">
          <template #default="{ row }">
            <span class="dim">{{ row.error || '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="loginLogsEmpty" class="dim" style="text-align:center;padding:24px 0;">暂无登录历史记录</div>
      <el-pagination
        v-if="loginLogsTotal > loginLogsPageSize"
        small
        layout="prev, pager, next, total"
        :total="loginLogsTotal"
        :page-size="loginLogsPageSize"
        :current-page="loginLogsPage"
        @current-change="loadLoginLogs"
      />
    </el-dialog>

    <!-- 模板编辑弹窗 -->
    <el-dialog v-model="editVisible" title="编辑接入配置包模板" width="860px" :close-on-click-modal="false">
      <el-form label-width="90px" label-position="right">
        <el-form-item label="显示名">
          <el-input v-model="editForm.name" maxlength="64" />
        </el-form-item>
        <el-form-item label="模板内容">
          <el-input v-model="editForm.content_md" type="textarea" :rows="22" class="pkg-textarea" />
          <div class="sub-tip">
            可用变量：
            <code v-for="v in varHints" :key="v" class="var-chip">{{ v }}</code>
            ；其它未列入的占位符会原样保留。
          </div>
        </el-form-item>
        <!-- #47: 历史版本区段(只在有上一版时显示; 无则提示「从未更新过」让用户知道按钮不是坏了) -->
        <el-form-item label="历史版本">
          <div v-if="editForm.has_previous">
            <el-input
              v-model="editForm.previous_content_md"
              type="textarea"
              :rows="6"
              readonly
              class="pkg-textarea"
            />
            <div class="sub-tip">
              上一版 content_md 快照(只读)。点「回滚到上一版」会把当前编辑区内容替换为该版本;
              <b>当前内容会作为新的「上一版」保留</b>, 可再次点「回滚到上一版」恢复(可逆)。
            </div>
            <el-button
              type="warning"
              size="small"
              :icon="RefreshLeft"
              :loading="rolling"
              class="mt-8"
              @click="rollbackTemplate"
            >
              回滚到上一版
            </el-button>
          </div>
          <div v-else class="sub-tip">无历史版本(从未更新过 content_md, 或已无可更早的版本可继续回滚)。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveTemplate">保存</el-button>
      </template>
    </el-dialog>

    <!-- 配置包大图预览 -->
    <el-dialog v-model="pkgPreviewVisible" title="配置包全文" width="820px">
      <el-input v-model="pkgMarkdown" type="textarea" :rows="26" readonly class="pkg-textarea" />
      <template #footer>
        <el-button type="primary" :icon="DocumentCopy" @click="copyWithToast(pkgMarkdown, '配置包')">
          复制配置包
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { Refresh, Document, DocumentCopy, Edit, RefreshLeft, InfoFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import ExternalTokenPanel from '@/components/ExternalTokenPanel.vue'
import { copyWithToast, copyText } from '@/utils/clipboard'
import {
  listSharedDocSources,
  getSharedDocStates,
  startSharedDocLogin,
  getSharedDocLoginStatus,
  confirmSharedDocLogin,
  cancelSharedDocLogin,
  getSharedDocCliFallback,
  listSharedDocLoginLogs,
} from '@/api/sharedDoc'
import {
  listProfiles,
  renderProfile,
  updateProfile,
  resetProfile,
  rollbackProfile,
} from '@/api/integrationProfile'
import { ElMessageBox } from 'element-plus'
import { usePolling, POLL_INTERVALS } from '@/composables/usePolling'

// ── Tab ──
const tab = ref('states')

// ── Tab1 登录态 ──
const states = reactive({ items: [], stale_threshold_days: 7, display_available: true })
const sources = ref([])

const platformName = (p) => ({ tencent: '腾讯文档', kdocs: '金山文档' }[p] || p)
const fmtTime = (v) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '')

async function loadStates() {
  try {
    const res = await getSharedDocStates()
    Object.assign(states, res.data || {})
  } catch (e) { /* 拦截器已提示 */ }
}

async function loadSources() {
  try {
    const res = await listSharedDocSources()
    sources.value = res.data || []
  } catch (e) { /* 拦截器已提示 */ }
}

// 登录会话
const loginVisible = ref(false)
const loginSource = ref(null)
const loginSessionId = ref('')
const loginScreenshot = ref('')
const loginStatus = ref('pending')
const loginCurrentUrl = ref('')
const loginExpiresIn = ref(0)
const confirming = ref(false)
// 统一轮询层: 登录会话状态(页面隐藏自动暂停, key 全局单例)
const LOGIN_POLL_KEY = 'access-center:login'
const { startPolling: startGlobalPoll, stopPolling: stopGlobalPoll } = usePolling()

const statusText = (s) => ({
  pending: '等待扫码', scanned: '已扫码，待确认', authorized: '已检测到登录',
  expired: '会话已过期', closed: '会话已关闭',
}[s] || s)
const statusType = (s) => ({
  pending: 'info', scanned: 'warning', authorized: 'success',
  expired: 'danger', closed: 'info',
}[s] || 'info')

async function startLogin(row) {
  loginSource.value = row
  loginVisible.value = true
  loginScreenshot.value = ''
  loginStatus.value = 'pending'
  try {
    const res = await startSharedDocLogin(row.id)
    const d = res.data || {}
    loginSessionId.value = d.session_id
    loginScreenshot.value = d.screenshot || ''
    loginCurrentUrl.value = d.current_url || ''
    loginExpiresIn.value = d.expires_in || 0
    startPolling()
  } catch (e) {
    const detail = e?.response?.data?.detail || '启动登录失败'
    ElMessage.error(detail)
    loginVisible.value = false
  }
}

function startPolling() {
  // 2 秒轮询会话状态; 截图不重复拉取(避免带宽浪费), 仅状态与 URL
  startGlobalPoll(LOGIN_POLL_KEY, async () => {
    if (!loginSessionId.value) return
    try {
      const res = await getSharedDocLoginStatus(loginSessionId.value)
      const d = res.data || {}
      loginStatus.value = d.status
      loginCurrentUrl.value = d.current_url || ''
      loginExpiresIn.value = d.expires_in || 0
      if (d.status === 'expired' || d.status === 'closed') stopPolling()
    } catch (e) {
      stopPolling()
    }
  }, POLL_INTERVALS.LOGIN)
}

function stopPolling() {
  stopGlobalPoll(LOGIN_POLL_KEY)
}

async function confirmLogin() {
  if (!loginSessionId.value) return
  confirming.value = true
  try {
    const res = await confirmSharedDocLogin(loginSessionId.value)
    const d = res.data || {}
    if (d.ok) {
      ElMessage.success(`登录态已保存（cookies ${d.cookies} 条）`)
      loginVisible.value = false
      await loadStates()
    } else {
      // 后端轻量校验未通过 —— 不覆盖旧登录态, 明确告知用户
      ElMessage.warning(d.message || '未检测到登录态，请完成扫码后重试')
    }
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存登录态失败')
  } finally {
    confirming.value = false
  }
}

async function cancelLogin() {
  if (loginSessionId.value) {
    try { await cancelSharedDocLogin(loginSessionId.value) } catch (e) { /* 幂等 */ }
  }
  loginVisible.value = false
}

function onLoginDialogClosed() {
  stopPolling()
  if (loginSessionId.value) {
    // 关闭弹窗即释放会话(避免 chromium 进程泄漏)
    cancelSharedDocLogin(loginSessionId.value).catch(() => {})
    loginSessionId.value = ''
  }
}

// ── 登录历史 (#149) ──
// 目的: 让运维知道「上次哪个账号何时登录成功」, 判断是否临近需重登。
// 记录写入点在确认扫码时(成功/失败均留痕, 后端保留 90 天)。
const loginLogsVisible = ref(false)
const loginLogs = ref([])
const loginLogsTotal = ref(0)
const loginLogsPage = ref(1)
const loginLogsPageSize = 20
const loginLogsEmpty = ref(false)

async function openLoginLogs() {
  loginLogsVisible.value = true
  await loadLoginLogs(1)
}

async function loadLoginLogs(page = 1) {
  loginLogsPage.value = page
  try {
    const res = await listSharedDocLoginLogs({
      page,
      page_size: loginLogsPageSize,
    })
    const d = res.data || {}
    loginLogs.value = d.items || []
    loginLogsTotal.value = d.total || 0
    loginLogsEmpty.value = loginLogs.value.length === 0
  } catch (e) { /* 拦截器已提示 */ }
}

// 命令行降级: 命令文本由后端 GET /cli-fallback 下发(单一事实来源)。
// 历史坑(2026-09-13): 前端曾硬编码 `cd /path/to/backend && ./.venv/bin/python ../scripts/...`,
// 该路径在生产容器里根本不存在(容器无 scripts/ 无 .venv) → 复制了也跑不通。
let _cliCmdCache = null
async function copyCliCommand(row) {
  const script = row.platform === 'tencent' ? 'tencent_doc_login.py' : 'kdocs_login.py'
  try {
    if (!_cliCmdCache) {
      const res = await getSharedDocCliFallback()
      _cliCmdCache = res.data?.commands || {}
    }
    // 后端模板里的 <文档地址> 占位替换为该源真实地址
    const tpl = _cliCmdCache[row.platform] || ''
    const cmd = tpl
      ? tpl.replace('<文档地址>', row.doc_url)
      : fallbackCliText(row.platform, row.doc_url)
    await copyWithToast(cmd, `${script} 登录命令`)
  } catch (e) {
    // 接口不可用(如旧版本后端) → 退化为本地文案, 保证按钮仍可用
    await copyWithToast(fallbackCliText(row.platform, row.doc_url), '命令行登录命令')
  }
}

// 兜底文案(仅在 /cli-fallback 不可用时使用)
function fallbackCliText(platform, docUrl) {
  const script = platform === 'tencent' ? 'tencent_doc_login.py' : 'kdocs_login.py'
  const stateFile = platform === 'tencent' ? 'tencent_doc.json' : 'kdocs.json'
  return (
    `# ① 本地(macOS)生成登录态: cd backend && ./.venv/bin/python ../scripts/${script} --url "${docUrl}"\n` +
    `# ② 上传: scp backend/data/browser_states/${stateFile} tanzk@<服务器IP>:/tmp/ && ` +
    `ssh tanzk@<服务器IP> "sudo mv /tmp/${stateFile} /opt/csctalon/browser_states/"`
  )
}

// ── Tab3 配置包 ──
const profiles = ref([])
const pkg = reactive({ profileId: null, values: {} })
const pkgMarkdown = ref('')
const pkgLoading = ref(false)
const pkgPreviewVisible = ref(false)

// 变量提示文案在脚本里构造(避免 Vue 模板中 {{ }} 嵌套造成的解析错误)
const varHints = ['base_url', 'doc_url', 'doc_name', 'token', 'target_type', 'sync_interval']
  .map((k) => `{{${k}}}`)

const currentProfile = computed(() => profiles.value.find((p) => p.id === pkg.profileId) || null)
const currentVariables = computed(() => currentProfile.value?.variables || [])

const PLACEHOLDERS = {
  doc_url: 'https://docs.qq.com/sheet/...',
  doc_name: '如：315投诉登记表',
  sync_interval: '如：20',
  target_type: 'complaint 或 manual_refund',
}

function placeholderFor(key) {
  if (PLACEHOLDERS[key]) return PLACEHOLDERS[key]
  if (key === 'base_url') return window.location.origin
  if (key === 'token') return '（留空即可，系统不会回填明文）'
  return ''
}

function onProfileChange() {
  pkg.values = {}
  pkgMarkdown.value = ''
  // 预填默认值: 系统地址 + 同步间隔(减少手工输入)
  const vars = currentVariables.value || []
  if (vars.some((v) => v.key === 'base_url')) pkg.values.base_url = window.location.origin
  if (vars.some((v) => v.key === 'sync_interval')) pkg.values.sync_interval = 20
}

async function renderAndCopy() {
  if (!pkg.profileId) { ElMessage.warning('请先选择接入场景'); return }
  const missing = (currentVariables.value || [])
    .filter((v) => v.required && !pkg.values[v.key])
    .map((v) => v.label)
  if (missing.length) {
    ElMessage.warning(`请填写：${missing.join('、')}`)
    return
  }
  pkgLoading.value = true
  try {
    const payload = { base_url: window.location.origin, ...pkg.values }
    // 数值字段转 int(后端 schema 为 int, 传字符串会 422)
    if (payload.sync_interval) payload.sync_interval = Number(payload.sync_interval)
    const res = await renderProfile(pkg.profileId, payload)
    pkgMarkdown.value = res.data?.markdown || ''
    if (res.data?.unresolved?.length) {
      ElMessage.warning(`以下变量未填值：${res.data.unresolved.join('、')}`)
    }
    await copyWithToast(pkgMarkdown.value, '配置包')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '生成配置包失败')
  } finally {
    pkgLoading.value = false
  }
}

async function loadProfiles() {
  try {
    const res = await listProfiles()
    profiles.value = (res.data || []).filter((p) => p.enabled !== false)
    if (profiles.value.length && !pkg.profileId) {
      pkg.profileId = profiles.value[0].id
      onProfileChange()
    }
  } catch (e) { /* 拦截器已提示 */ }
}

// 模板编辑
const editVisible = ref(false)
const saving = ref(false)
const rolling = ref(false)
// #47: previous_content_md / has_previous 由后端返回, 用于「回滚到上一版」按钮显隐与展示
const editForm = reactive({ id: null, name: '', content_md: '', previous_content_md: null, has_previous: false })

function openEdit() {
  if (!currentProfile.value) { ElMessage.warning('请先选择场景'); return }
  editForm.id = currentProfile.value.id
  editForm.name = currentProfile.value.name
  editForm.content_md = currentProfile.value.content_md
  editForm.previous_content_md = currentProfile.value.previous_content_md ?? null
  editForm.has_previous = currentProfile.value.has_previous === true
  editVisible.value = true
}

async function saveTemplate() {
  saving.value = true
  try {
    await updateProfile(editForm.id, { name: editForm.name, content_md: editForm.content_md })
    ElMessage.success('已保存')
    editVisible.value = false
    await loadProfiles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function rollbackTemplate() {
  if (!editForm.id || !editForm.has_previous) return
  // 二次确认: 防误点(回滚本身是写操作, 且会再次刷新 previous —— 不解释清楚会让人以为「丢了版本」)
  try {
    await ElMessageBox.confirm(
      '将把当前编辑区内容替换为上一版快照, 当前内容会作为新的「上一版」保留(可再次回滚恢复)。',
      '回滚到上一版？',
      { confirmButtonText: '回滚', cancelButtonText: '取消', type: 'warning' },
    )
  } catch (e) {
    return // 用户取消
  }
  rolling.value = true
  try {
    const res = await rollbackProfile(editForm.id, { confirm: true })
    const d = res.data || {}
    editForm.content_md = d.content_md || ''
    editForm.previous_content_md = d.previous_content_md ?? null
    editForm.has_previous = d.has_previous === true
    ElMessage.success('已回滚到上一版(可再次点「回滚到上一版」恢复)')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '回滚失败')
  } finally {
    rolling.value = false
  }
}

async function resetCurrent() {
  if (!pkg.profileId) return
  try {
    await resetProfile(pkg.profileId)
    ElMessage.success('已恢复默认模板')
    await loadProfiles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '恢复失败')
  }
}

// ── 全局 ──
async function refreshAll() {
  await Promise.all([loadStates(), loadSources()])
  ElMessage.success('已刷新')
}

onMounted(() => {
  loadStates()
  loadSources()
  loadProfiles()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style scoped>
.access-center {
  max-width: 1280px;
}
.flex-between {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-title { font-size: 18px; font-weight: 600; }
.page-sub { font-size: 12px; color: #909399; margin-top: 2px; }
.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
.mb-8 { margin-bottom: 8px; }
.mb-16 { margin-bottom: 16px; }
.mt-12 { margin-top: 12px; }
.mt-16 { margin-top: 16px; }
.ml-8 { margin-left: 8px; }
.dim { color: var(--el-text-color-secondary); font-size: 12px; }
.warn-text { color: var(--el-color-warning); font-size: 12px; }
/* 页面层设计说明气泡(第三层落地): 图标 + 正文排版 */
.info-icon {
  color: var(--el-text-color-secondary);
  cursor: pointer;
  font-size: 15px;
}
.rationale {
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}
.rationale p { margin: 0 0 6px; }
.rationale ul { margin: 0 0 6px; padding-left: 18px; }
.rationale li { margin-bottom: 3px; }
.sub-tip {
  color: #909399;
  font-size: 12px;
  line-height: 1.8;
  margin-top: 4px;
}
.var-chip {
  margin-right: 6px;
  background: #f5f7fa;
  padding: 1px 5px;
  border-radius: 3px;
}
.login-shot {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #f5f7fa;
  min-height: 300px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.shot-img { width: 100%; display: block; }
.shot-empty { color: #909399; font-size: 13px; }
.login-status {
  margin-top: 10px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.pkg-textarea :deep(textarea) {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
}
</style>
