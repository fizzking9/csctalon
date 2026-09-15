<!--
  ┌──────────────────────────── 设计说明（Design Rationale） ────────────────────────────┐
  │ 目的：采集运维 / 管理员在「数据同步统一入口 → 手动同步」面板里，用 3~5 次点击            │
  │       完成「今天要补采哪几路」的勾选；回答的决策问题是                               │
  │       「这次手动同步，要跑高频那几个，还是把低频大表也带上」。                        │
  │       使用频率：告警后补采 / 排查缺数时（非每日固定动作）。                           │
  │ 逻辑：① 按【档位】分组而非按来源平台分组 —— 因为使用者心里的问题是                     │
  │         「哪些是每小时跑的、哪些是每天 6 点才跑的」，而不是"这些源在哪个域名下"；        │
  │          平台分组（BMS主平台/闪猫/ai-acc）已由「抓取模式&凭证设置」面板承担，           │
  │          此处再按平台切一次属重复展示。                                              │
  │       ② 档位判据【不硬编码源名】，一律读后端 schedule_tier（high/low/off）——            │
  │          否则前端清单一旦与调度器分档漂移，UI 就会骗人（#31 的原始教训：               │
  │          「UI 开关看着是开的，调度器压根不跑」）。                                     │
  │       ③ 批量按钮用【安全动作】而非"操作动作"排序：全选/清空/反选（结构）→              │
  │          只选已配凭证 / 恢复默认（防误跑），后者更贴使用者真实意图，放右侧强调位。        │
  │       ④ 未纳入定时的源（off 档）【折叠且不默认勾选】—— 它们不进定时是有理由的           │
  │          （大表导出 / 需验证码），默认打开会让"手滑全选"变成一次昂贵的大导出。           │
  │ 口径：schedule_tier 唯一真源 = backend/app/services/sync_source_config_service.py      │
  │       ::list_scheduled_bms_sources()（high = all ∩ DEFAULT_SYNC_TYPES；low = all − high）；│
  │       与调度器 bms_scheduler.py 消费的是同一个判据。                                  │
  │       档位语义见 docs/arch/A-20260907-001-BMS定时采集源读库分频.md（LOCKED）§四、       │
  │       docs/arch/A-20260910-006-高频采集节拍可配.md（LOCKED）。                        │
  │       后端未返回档位时按 off 兜底（宁可不勾，不可误勾）。                               │
  │ 取舍：❌ 不做 BMS/SCRM 统一批量提交 —— SCRM 走独立端点 POST /fetch/scrm/sync，          │
  │         合并需新增编排端点，收益（省几次点击）配不上新增一条采集路径的风险（用户         │
  │         2026-09-15 决策：本次只改 BMS）。                                            │
  │       ❌ 不给每源新增 cron / interval 字段 —— 现有 high/low 二分已覆盖定时语义，         │
  │         再加一套 per-source 频率会与调度器分档重复定义，制造新漂移点。                  │
  │       ❌ 不改任何源的档位归属 —— 档位是 DB default_sync 用户态，本组件只做「展示 +       │
  │         批量勾选」，改档位仍走「抓取模式 & 凭证设置」面板，职责不混。                    │
  │       ❌ 不在勾选时静默剔除"未配凭证"的源 —— 静默剔除会让使用者以为跑过了。             │
  │         改为保留勾选 + 红标提示，跑失败由右侧日志如实暴露。                             │
  │ 工单：#97 · 设计：docs/standards/界面设计说明规范.md（本组件含页面层 ⓘ 说明）           │
  └────────────────────────────────────────────────────────────────────────────────────┘
-->
<template>
  <div class="tier-picker">
    <!-- ⓘ 设计说明（页面层，三层落地的第②层） -->
    <div class="tier-picker-head">
      <span class="tier-head-tip">
        <el-popover placement="right-start" :width="440" trigger="click">
          <template #reference>
            <span class="info-dot">
              <el-icon><InfoFilled /></el-icon>
              <span>为什么这样分组</span>
            </span>
          </template>
          <div class="rationale">
            <p><b>目的</b>：让你用 3~5 次点击决定「这次手动同步要跑哪几路」，不用一个个点。</p>
            <p><b>为什么按档位分组</b>：你心里的问题是"哪些是每小时跑的、哪些是每天才跑一次"，
              所以按<b>运行节拍</b>分，而不是按来源域名分（平台分组的视角在「抓取模式 &amp; 凭证设置」面板）。</p>
            <p><b>档位从哪来</b>：直接读定时调度器的分档判据，<b>不在此另行维护一份源名清单</b> ——
              避免出现"界面显示的档位 ≠ 调度器实际跑的档位"。</p>
            <p class="rationale-reject"><b>不做什么</b>：</p>
            <ul>
              <li>不做 BMS + SCRM 合并批量提交（SCRM 走独立接口，本次只改 BMS）；</li>
              <li>不给单个源新增自定义频率（已有高/低频二分，再加一套会与调度器打架）；</li>
              <li>不改任何源的档位归属 —— 这里是「展示 + 批量勾选」，改档位请去「抓取模式 &amp; 凭证设置」；</li>
              <li>不自动跳过"未配凭证"的源 —— 静默剔除会让你误以为已经跑过了。</li>
            </ul>
          </div>
        </el-popover>
      </span>

      <!-- 顶部总控：结构类动作 -->
      <div class="tier-head-actions">
        <span class="tier-selected">已选 <b>{{ modelValue.length }}</b> / {{ selectableSources.length }}</span>
        <el-button size="small" :disabled="disabled" @click="selectAll">全选</el-button>
        <el-button size="small" :disabled="disabled" @click="clearAll">清空</el-button>
        <el-button size="small" :disabled="disabled" @click="invertAll">反选</el-button>
        <el-divider direction="vertical" />
        <el-button size="small" type="primary" plain :disabled="disabled" @click="selectCredentialReady">
          只选已配凭证
        </el-button>
        <el-button size="small" :disabled="disabled" @click="restoreDefault">
          恢复默认勾选
        </el-button>
      </div>
    </div>

    <!-- 高频档 -->
    <div class="tier-group tier-group-high">
      <div class="tier-group-head">
        <el-tag size="small" type="success" effect="dark">高频档</el-tag>
        <span class="tier-group-count">{{ pickedIn(highSources) }} / {{ highSources.length }}</span>
        <span class="tier-group-desc">{{ highTierDesc }}</span>
        <span class="tier-group-btns">
          <el-button link type="primary" size="small" :disabled="disabled || !highSources.length"
                     @click="pickGroup(highSources, true)">本组全选</el-button>
          <el-button link type="primary" size="small" :disabled="disabled || !highSources.length"
                     @click="pickGroup(highSources, false)">本组清空</el-button>
        </span>
      </div>
      <el-checkbox-group :model-value="modelValue" :disabled="disabled"
                         @update:model-value="$emit('update:modelValue', $event)">
        <el-checkbox v-for="ds in highSources" :key="ds.data_type" :value="ds.data_type"
                     :disabled="disabled" class="tier-item">
          <span class="tier-item-name">{{ ds.name }}</span>
          <slot name="badge" :ds="ds" />
        </el-checkbox>
      </el-checkbox-group>
      <div v-if="!highSources.length" class="tier-empty">当前没有处于高频档的源</div>
    </div>

    <!-- 低频档 -->
    <div class="tier-group tier-group-low">
      <div class="tier-group-head">
        <el-tag size="small" type="warning" effect="dark">低频档</el-tag>
        <span class="tier-group-count">{{ pickedIn(lowSources) }} / {{ lowSources.length }}</span>
        <span class="tier-group-desc">{{ lowTierDesc }}</span>
        <span class="tier-group-btns">
          <el-button link type="primary" size="small" :disabled="disabled || !lowSources.length"
                     @click="pickGroup(lowSources, true)">本组全选</el-button>
          <el-button link type="primary" size="small" :disabled="disabled || !lowSources.length"
                     @click="pickGroup(lowSources, false)">本组清空</el-button>
        </span>
      </div>
      <el-alert type="warning" :closable="false" show-icon class="tier-group-warn">
        本组多为大表导出（单源可能跑数分钟），手动全选会明显拉长本次任务耗时。
      </el-alert>
      <el-checkbox-group :model-value="modelValue" :disabled="disabled"
                         @update:model-value="$emit('update:modelValue', $event)">
        <el-checkbox v-for="ds in lowSources" :key="ds.data_type" :value="ds.data_type"
                     :disabled="disabled" class="tier-item">
          <span class="tier-item-name">{{ ds.name }}</span>
          <slot name="badge" :ds="ds" />
        </el-checkbox>
      </el-checkbox-group>
      <div v-if="!lowSources.length" class="tier-empty">当前没有处于低频档的源</div>
    </div>

    <!-- 未纳入定时档（折叠、不默认勾选） -->
    <div v-if="offSources.length" class="tier-group tier-group-off">
      <div class="tier-group-head">
        <el-tag size="small" type="info" effect="plain">未纳入定时</el-tag>
        <span class="tier-group-count">{{ pickedIn(offSources) }} / {{ offSources.length }}</span>
        <span class="tier-group-desc">
          定时采集不会自动跑它们（多为按需补采）。如需临时采一次可展开勾选。
        </span>
        <span class="tier-group-btns">
          <el-button link type="primary" size="small" @click="showOff = !showOff">
            {{ showOff ? '收起' : '展开' }}
          </el-button>
        </span>
      </div>
      <el-checkbox-group v-if="showOff" :model-value="modelValue" :disabled="disabled"
                         @update:model-value="$emit('update:modelValue', $event)">
        <el-checkbox v-for="ds in offSources" :key="ds.data_type" :value="ds.data_type"
                     :disabled="disabled" class="tier-item">
          <span class="tier-item-name">{{ ds.name }}</span>
          <slot name="badge" :ds="ds" />
        </el-checkbox>
      </el-checkbox-group>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { InfoFilled } from '@element-plus/icons-vue'

/**
 * BMS 同步源「按档位分组 + 批量勾选」选择器（工单 #97）
 *
 * 纯受控组件：勾选结果通过 v-model 双向绑定，自身不发起任何网络请求，
 * 也不决定档位 —— 档位完全由后端 schedule_tier 决定（见文件头设计说明）。
 */
const props = defineProps({
  /** 已勾选的 data_type 列表（v-model） */
  modelValue: { type: Array, default: () => [] },
  /** 后端 GET /fetch/data-sources 返回的源清单（含 schedule_tier） */
  sources: { type: Array, default: () => [] },
  /** 运行中/忙时整体禁用 */
  disabled: { type: Boolean, default: false },
  /** 低频档整点（来自 /fetch/schedule/status 的 low_freq_hour，0 = 低频档禁用） */
  lowFreqHour: { type: Number, default: 0 },
})

const emit = defineEmits(['update:modelValue'])

const showOff = ref(false)

/**
 * 档位归一化：后端未返回 schedule_tier（旧版本后端）时按 'off' 兜底。
 * 兜底为 off 而非 high —— 宁可少勾，不可误勾（误勾 = 触发一次昂贵导出）。
 */
function tierOf(ds) {
  const t = ds && ds.schedule_tier
  return t === 'high' || t === 'low' ? t : 'off'
}

const selectableSources = computed(() => props.sources || [])
const highSources = computed(() => selectableSources.value.filter(d => tierOf(d) === 'high'))
const lowSources = computed(() => selectableSources.value.filter(d => tierOf(d) === 'low'))
const offSources = computed(() => selectableSources.value.filter(d => tierOf(d) === 'off'))

/** 高频档节拍描述 —— 时段/间隔是 DB 可配的（#51），故此处只描述语义，不写死"每小时" */
const highTierDesc = computed(
  () => '定时采集的主力：按可配节拍（默认白天每 30 分钟）滚动拉取，另有凌晨补跑与首槽全量兜底',
)

/** 低频档节拍描述 —— 整点从后端读取，不在前端硬编码 6:00 */
const lowTierDesc = computed(() => {
  if (props.lowFreqHour > 0) {
    return `每天 ${String(props.lowFreqHour).padStart(2, '0')}:00 跑一次（抓前一天整天）`
  }
  return '当前低频档已禁用，定时采集不会自动跑这些源'
})

/** 某组已勾选数量 */
function pickedIn(group) {
  const picked = new Set(props.modelValue)
  return group.filter(d => picked.has(d.data_type)).length
}

/** 组内全选 / 清空 */
function pickGroup(group, on) {
  const ids = group.map(d => d.data_type)
  const idSet = new Set(ids)
  const rest = props.modelValue.filter(dt => !idSet.has(dt))
  emit('update:modelValue', on ? [...rest, ...ids] : rest)
}

function selectAll() {
  emit('update:modelValue', dedupe(selectableSources.value.map(d => d.data_type)))
}

function clearAll() {
  emit('update:modelValue', [])
}

/** 反选：在「全部可选源」范围内取补集（含 off 档，保持语义直观） */
function invertAll() {
  const picked = new Set(props.modelValue)
  emit('update:modelValue',
    dedupe(selectableSources.value.map(d => d.data_type).filter(dt => !picked.has(dt))))
}

/**
 * 「只选已配凭证」的可跑判据（比 has_credentials 更严）：
 *   ① 凭证已配；且 ② 若该源需要验证码，登录态也必须已保存 —— 否则勾了也必然失败。
 * 注：登录态来自父级注入的 captchaStates，故此处只做占位判断，
 *     由父级在 badge 插槽里展示实际状态；这里直接读源对象上的 _credentialReady 标记。
 */
function isCredentialReady(ds) {
  if (ds.has_credentials === false) return false
  if (ds.requires_captcha && ds.has_storage_state === false) return false
  return true
}

function selectCredentialReady() {
  emit('update:modelValue', dedupe(selectableSources.value.filter(isCredentialReady).map(d => d.data_type)))
}

/** 恢复默认勾选 = 恢复为「纳入定时的源」（default_sync=true 且 enabled=true） */
function restoreDefault() {
  emit('update:modelValue', dedupe(
    selectableSources.value
      .filter(d => d.default_sync !== false && d.enabled !== false)
      .map(d => d.data_type)))
}

function dedupe(list) {
  return Array.from(new Set(list))
}

defineExpose({ highSources, lowSources, offSources })
</script>

<style scoped>
.tier-picker-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.tier-head-tip {
  flex: 0 0 auto;
}
.info-dot {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #909399;
  font-size: 12px;
  cursor: pointer;
}
.info-dot:hover {
  color: #409eff;
}
.tier-head-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.tier-selected {
  font-size: 12px;
  color: #606266;
  margin-right: 4px;
}
.tier-group {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
  background: #fff;
}
.tier-group-high {
  border-left: 3px solid #67c23a;
}
.tier-group-low {
  border-left: 3px solid #e6a23c;
}
.tier-group-off {
  border-left: 3px solid #c0c4cc;
  background: #fafafa;
}
.tier-group-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.tier-group-count {
  font-size: 12px;
  color: #606266;
}
.tier-group-desc {
  font-size: 12px;
  color: #909399;
}
.tier-group-btns {
  margin-left: auto;
  white-space: nowrap;
}
.tier-group-warn {
  margin-bottom: 6px;
}
.tier-item {
  margin-right: 18px;
}
.tier-item-name {
  font-size: 13px;
}
.tier-empty {
  font-size: 12px;
  color: #c0c4cc;
}
.rationale p {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 1.7;
  color: #303133;
}
.rationale ul {
  margin: 0;
  padding-left: 18px;
}
.rationale li {
  font-size: 12px;
  line-height: 1.7;
  color: #303133;
}
.rationale-reject {
  color: #e6a23c;
}
</style>
