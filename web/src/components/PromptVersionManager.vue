<!--
  提示词版本管理组件（2026-09-23 全量版本化）

  通用组件：governance（研判 judge_system/judge_user）与 onboarding（起草 draft_system/draft_user）
  共用同一套「版本账本 → 新建草稿 → 激活 / 回滚」交互，仅 API 前缀与 code 集合不同。
  与评估中心 #178 区块同模式，但这两类不走金丝雀冒烟（非开放问答侧，冒烟价值低）。

  口径：生效版本 = is_active=true 的行（每 code 至多一个）；新建只入账本不激活（草稿制）；
  回滚 = 激活历史版本（不删行，账本完整）；每版必须填「解决什么」（change_reason）。
-->
<template>
  <div>
    <div class="pvm-toolbar">
      <el-button size="small" type="primary" :loading="loading" @click="load">刷新版本</el-button>
      <el-button size="small" @click="openNew">新建版本</el-button>
      <el-tooltip placement="bottom-end" content="新建只入账本（草稿），人工激活后才生效；不满意可随时回滚到任意历史版本">
        <el-button size="small" :icon="InfoFilled" circle />
      </el-tooltip>
    </div>

    <el-tabs v-model="activeCode" @tab-change="load">
      <el-tab-pane v-for="c in codes" :key="c.code" :name="c.code">
        <template #label>
          <span>{{ c.label }}</span>
          <el-tag v-if="activeVersion(c.code)" size="small" type="success" style="margin-left: 6px">
            v{{ activeVersion(c.code).version }} 生效
          </el-tag>
          <el-tag v-else size="small" type="info" style="margin-left: 6px">代码常量兜底</el-tag>
        </template>
        <el-table v-loading="loading" :data="versionsByCode(c.code)" size="small" empty-text="暂无版本（当前使用代码内置默认提示词）">
          <el-table-column label="版本" width="80">
            <template #default="{ row }">v{{ row.version }}</template>
          </el-table-column>
          <el-table-column prop="name_cn" label="名称" min-width="150" show-overflow-tooltip />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                {{ row.is_active ? '生效中' : '草稿/历史' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="激活人 / 时间" width="150">
            <template #default="{ row }">
              <span v-if="row.activated_at">{{ row.activated_by ?? '-' }} · {{ fmtTime(row.activated_at) }}</span>
              <span v-else class="muted">未激活</span>
            </template>
          </el-table-column>
          <el-table-column label="解决什么 / 参考" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <div v-if="row.change_reason" class="ver-reason">🗒 {{ row.change_reason }}</div>
              <div v-if="row.references" class="ver-reason muted">📎 {{ row.references }}</div>
              <span v-if="!row.change_reason && !row.references" class="muted">无（历史版本，升级前存量）</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="showContent(row)">内容</el-button>
              <el-button v-if="!row.is_active" size="small" type="primary"
                         :loading="actionId === row.id" @click="onActivate(row)">激活</el-button>
              <el-button v-else size="small" type="warning" :disabled="!prevVersion(row.code)"
                         :loading="actionId === prevVersion(row.code)?.id"
                         @click="onRollback(row)">回滚上一版</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 新建版本弹窗 -->
    <el-dialog v-model="newDialog" title="新建提示词版本（草稿，不自动激活）" width="680px">
      <div class="drawer-sub mb-12">
        先改后升：默认预填当前生效版本内容；「内容」留空则复制生效版本为底。新版本入账本（version+1），
        人工点「激活」后才生效。代码常量兜底且无生效版本时，留空会被拒绝。
      </div>
      <el-form label-width="90px">
        <el-form-item label="提示词">
          <el-select v-model="form.code" size="small" style="width: 100%">
            <el-option v-for="c in codes" :key="c.code" :label="c.label" :value="c.code" />
          </el-select>
        </el-form-item>
        <el-form-item label="显示名">
          <el-input v-model="form.name_cn" size="small" placeholder="可选，默认沿用当前生效版名称" />
        </el-form-item>
        <el-form-item label="解决什么" required>
          <el-input v-model="form.change_reason" type="textarea" :rows="2"
                    maxlength="300" show-word-limit
                    placeholder="必填：本版解决什么问题 / 为什么改（版本账本永久备案）" />
        </el-form-item>
        <el-form-item label="影响内容">
          <el-input v-model="form.references" type="textarea" :rows="2"
                    maxlength="500" show-word-limit
                    placeholder="可选：影响哪些输出/行为，参考依据（评估建议/评价/意见工单/文档链接）" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input v-model="form.content" type="textarea" :rows="10"
                    placeholder="提示词正文（可含 {占位符}）…" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="newDialog = false">取消</el-button>
        <el-button size="small" type="primary" :loading="creating" @click="submit">保存为草稿</el-button>
      </template>
    </el-dialog>

    <!-- 版本内容弹窗 -->
    <el-dialog v-model="contentDialog" :title="contentTitle" width="720px">
      <pre class="ver-content">{{ contentText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { InfoFilled } from '@element-plus/icons-vue'
import {
  listPromptVersions, createPromptVersion,
  activatePromptVersion, rollbackPromptVersion,
} from '@/api/promptVersion'

const props = defineProps({
  // 'governance' | 'onboarding'
  kind: { type: String, required: true },
  // [{ code: 'judge_system', label: '研判·系统提示' }, ...]
  codes: { type: Array, required: true },
})

const loading = ref(false)
const actionId = ref(null)
const activeCode = ref(props.codes[0]?.code)
const verMap = ref({})

const newDialog = ref(false)
const creating = ref(false)
const form = ref({ code: activeCode.value, name_cn: '', change_reason: '', references: '', content: '' })

const contentDialog = ref(false)
const contentTitle = ref('')
const contentText = ref('')

function fmtTime(v) {
  if (!v) return ''
  return String(v).replace('T', ' ').slice(0, 16)
}

function versionsByCode(code) {
  return verMap.value[code] || []
}
function activeVersion(code) {
  return versionsByCode(code).find(v => v.is_active) || null
}
// 生效版本的上一个历史版本（一键回滚目标）；无历史版本返回 null
function prevVersion(code) {
  const active = activeVersion(code)
  if (!active) return null
  const older = versionsByCode(code).filter(v => v.version < active.version)
  if (!older.length) return null
  return [...older].sort((a, b) => b.version - a.version)[0]
}

async function load() {
  loading.value = true
  try {
    const results = await Promise.all(
      props.codes.map(c => listPromptVersions(props.kind, { code: c.code }))
    )
    const next = {}
    props.codes.forEach((c, i) => { next[c.code] = results[i].data.items || [] })
    verMap.value = next
  } finally {
    loading.value = false
  }
}

function openNew() {
  const active = activeVersion(activeCode.value)
  form.value = {
    code: activeCode.value,
    name_cn: '',
    change_reason: '',
    references: '',
    content: active?.content ?? '',
  }
  newDialog.value = true
}

async function submit() {
  if (!form.value.change_reason?.trim()) {
    ElMessage.warning('请填写「解决什么」（为何改，版本账本永久备案）')
    return
  }
  creating.value = true
  try {
    const res = await createPromptVersion(props.kind, {
      code: form.value.code,
      content: form.value.content?.trim() || undefined,
      name_cn: form.value.name_cn?.trim() || undefined,
      change_reason: form.value.change_reason?.trim() || undefined,
      references: form.value.references?.trim() || undefined,
    })
    newDialog.value = false
    activeCode.value = form.value.code
    ElMessage.success(res.message || `已新增版本 v${res.data.version}（草稿）`)
    await load()
  } finally {
    creating.value = false
  }
}

async function onActivate(row) {
  try {
    await ElMessageBox.confirm(
      `确认激活 v${row.version}？激活后同提示词的旧生效版本立即停用，相关服务即时改用新版。`,
      '激活版本', { type: 'warning', confirmButtonText: '激活', cancelButtonText: '取消' }
    )
  } catch { return }
  actionId.value = row.id
  try {
    const res = await activatePromptVersion(props.kind, row.id)
    ElMessage.success(res.message || `版本 v${row.version} 已生效`)
    await load()
  } finally {
    actionId.value = null
  }
}

async function onRollback(row) {
  const target = prevVersion(row.code)
  if (!target) return
  try {
    await ElMessageBox.confirm(
      `确认回滚？生效指针将从 v${row.version} 切回 v${target.version}（当前版本行保留在账本，随时可再切回）。`,
      '回滚到上一版', { type: 'warning', confirmButtonText: '回滚', cancelButtonText: '取消' }
    )
  } catch { return }
  actionId.value = target.id
  try {
    const res = await rollbackPromptVersion(props.kind, target.id)
    ElMessage.success(res.message || `已回滚到版本 v${target.version}`)
    await load()
  } finally {
    actionId.value = null
  }
}

function showContent(row) {
  contentTitle.value = `${row.name_cn} · v${row.version}${row.is_active ? '（生效中）' : ''}`
  contentText.value = row.content || '（空）'
  contentDialog.value = true
}

onMounted(load)

defineExpose({ reload: load })
</script>

<style scoped>
.pvm-toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.muted { color: #909399; }
.drawer-sub { font-size: 12px; color: #909399; }
.mb-12 { margin-bottom: 12px; }
.ver-content { white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.6; background: #f5f7fa; padding: 10px; border-radius: 4px; max-height: 60vh; overflow: auto; margin: 0; }
.ver-reason { font-size: 12px; line-height: 1.5; margin-bottom: 2px; }
</style>
