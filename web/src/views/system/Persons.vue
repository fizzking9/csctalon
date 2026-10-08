<template>
  <div class="page-container persons-page">
    <!-- 顶部工具栏 -->
    <div class="flex-between mb-12">
      <div class="page-title" style="margin: 0">人员统一管理</div>
      <div class="flex gap-8" style="flex-wrap: wrap">
        <el-button :icon="Download" @click="handleExport">导出 HR 主表</el-button>
        <el-button :icon="Upload" @click="openImportDialog">导入人力快照</el-button>
        <el-button :icon="Refresh" :loading="refreshing" @click="handleRefresh">提取别名</el-button>
        <el-button :icon="Refresh" :loading="rebuilding" @click="handleRebuild">重建别名</el-button>
        <el-button :icon="Connection" :loading="importingShop" @click="handleBatchImportShop">纳入店铺客服</el-button>
        <el-button :icon="Connection" :loading="importingQw" @click="handleBatchImportQw">纳入企微客服</el-button>
        <el-button :icon="Connection" :loading="importingRnd" @click="handleBatchImportRnd">纳入研发</el-button>
        <el-button :icon="Connection" @click="loadSuggestions">归并建议</el-button>
        <el-button :icon="Grid" @click="openSkillMatrix">技能矩阵</el-button>
      </div>
    </div>

    <!-- 待归并别名(可折叠) -->
    <el-collapse v-if="unmergedAliases.length" v-model="aliasCollapse" class="mb-12 alias-collapse">
      <el-collapse-item name="aliases">
        <template #title>
          <div class="flex-between" style="width: 100%; padding-right: 12px">
            <span>
              待归并别名
              <el-tag size="small" type="warning" style="margin-left: 4px">{{ unmergedAliases.length }}</el-tag>
            </span>
            <span class="flex gap-8" @click.stop>
              <el-button size="small" type="success" :icon="Check" :loading="batchMerging" @click="handleBatchMerge">
                批量按建议归并<span v-if="selectedAliasIds.length">({{ selectedAliasIds.length }})</span><span v-else>(全部有建议)</span>
              </el-button>
              <el-button size="small" type="warning" :disabled="!selectedAliasIds.length" :loading="batchCreating" @click="handleBatchCreateByNormalized">
                按归一名新建({{ selectedAliasIds.length }})
              </el-button>
              <el-button size="small" type="primary" :disabled="!selectedAliasIds.length" @click="openMergeToNew">归并新建({{ selectedAliasIds.length }})</el-button>
              <el-button size="small" :disabled="!selectedAliasIds.length" @click="openMergeToExisting">归并已有</el-button>
            </span>
          </div>
        </template>
        <el-table :data="unmergedAliases" size="small" max-height="320" border @selection-change="onAliasSelect">
          <el-table-column type="selection" width="40" />
          <el-table-column prop="alias" label="别名原值" min-width="140" show-overflow-tooltip />
          <el-table-column label="归一化(匹配名)" width="140">
            <template #default="{ row }">
              <span style="font-weight: 600; color: #409eff">{{ row.alias_normalized }}</span>
            </template>
          </el-table-column>
          <el-table-column label="来源表" width="180">
            <template #default="{ row }">
              <span>{{ row.source_table_label || row.source_table }}</span>
              <div style="font-size: 11px; color: #909399">{{ row.source_table }}</div>
            </template>
          </el-table-column>
          <el-table-column label="来源字段" width="130">
            <template #default="{ row }">
              <span>{{ row.source_field_label || row.source_field }}</span>
            </template>
          </el-table-column>
          <el-table-column label="建议目标" width="220">
            <template #default="{ row }">
              <template v-if="row.initial_match_type === 'unique'">
                <span style="color: #67c23a; font-weight: 600">→ {{ row.suggested_hr_person_name }}</span>
                <el-tag size="small" :type="row.match_source === 'normalized' ? 'success' : 'primary'" style="margin-left: 4px">
                  {{ row.match_source === 'normalized' ? '名匹配' : '首字母' }}
                </el-tag>
                <el-button size="small" type="success" text :icon="Check" :loading="mergingAliasId === row.id" @click="mergeAliasToSuggested(row)">归并</el-button>
              </template>
              <el-tooltip v-else-if="row.initial_match_type === 'ambiguous'" content="歧义(对应多名 HR),需人工判断" placement="top">
                <el-tag type="warning" size="small">歧义</el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>

    <!-- 主区:左右分栏 -->
    <div class="split-layout">
      <!-- 左栏:人员列表 -->
      <el-card class="left-panel" shadow="never">
        <div class="flex-between mb-8" style="flex-wrap: wrap; gap: 6px">
          <div class="flex gap-8" style="flex-wrap: wrap; align-items: center">
            <el-radio-group v-model="filter.status" size="small" @change="onStatusChange">
              <el-radio-button value="confirmed">已确认</el-radio-button>
              <el-radio-button value="unconfirmed">待确认</el-radio-button>
              <el-radio-button value="inactive">已停用</el-radio-button>
            </el-radio-group>
            <el-select v-model="filter.department" placeholder="部门" size="small" clearable style="width: 100px" @change="loadData">
              <el-option label="企微" value="企微" />
              <el-option label="店铺" value="店铺" />
              <el-option label="研发" value="研发" />
            </el-select>
            <el-select v-model="filter.group_id" placeholder="分组" size="small" clearable filterable style="width: 130px" @change="loadData">
              <el-option v-for="g in groupsList" :key="g.id" :label="g.name" :value="g.id" />
            </el-select>
            <el-select v-model="filter.specialty_id" placeholder="专项" size="small" clearable filterable style="width: 130px" @change="loadData">
              <el-option v-for="s in specialtiesList" :key="s.id" :label="s.name" :value="s.id" />
            </el-select>
            <el-select v-model="filter.employment_status" placeholder="在职状态" size="small" clearable style="width: 110px" @change="loadData">
              <el-option label="正式" value="regular" />
              <el-option label="试用期" value="probation" />
              <el-option label="待入职" value="active" />
              <el-option label="调岗" value="transferred" />
              <el-option label="离职" value="resigned" />
              <el-option label="非活跃" value="inactive" />
            </el-select>
            <el-select v-model="filter.skill_tag" placeholder="技能" size="small" clearable filterable style="width: 120px" @change="loadData">
              <el-option v-for="t in skillTagsList" :key="t.skill_tag" :label="`${t.skill_tag}(${t.person_count})`" :value="t.skill_tag" />
            </el-select>
          </div>
          <el-button size="small" type="warning" :icon="Edit" :disabled="!selectedPersonIds.length" @click="openBatchEdit">
            批量编辑<span v-if="selectedPersonIds.length">({{ selectedPersonIds.length }})</span>
          </el-button>
        </div>
        <el-table
          v-loading="loading"
          :data="treeList"
          size="small"
          border
          stripe
          highlight-current-row
          row-key="id"
          :tree-props="{ children: 'children' }"
          default-expand-all
          height="calc(100vh - 220px)"
          @row-click="onRowClick"
          @selection-change="onPersonSelect"
          @sort-change="onSortChange"
          @filter-change="onFilterChange"
        >
          <el-table-column type="selection" width="38" :selectable="isPersonRow" :reserve-selection="false" />
          <el-table-column prop="display_name" label="名称" width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <template v-if="row._nodeType === 'dept'">
                <span style="font-weight:600; color:#303133">{{ row.display_name }}</span>
                <span style="margin-left:6px; color:#909399; font-size:12px">{{ row._personCount }}人</span>
              </template>
              <template v-else-if="row._nodeType === 'group'">
                <span style="color:#606266; font-weight:500">{{ row.display_name || '未分组' }}</span>
                <span style="margin-left:6px; color:#c0c4cc; font-size:12px">{{ row._personCount }}人</span>
              </template>
              <template v-else>
                <span :style="{ color: ['resigned','transferred','inactive'].includes(row.employment_status) ? '#c0c4cc' : '' }">
                  {{ row.display_name }}
                  <el-tag v-if="row.employment_status === 'transferred'" size="small" type="warning" effect="plain" style="margin-left:4px">调岗</el-tag>
                  <el-tag v-else-if="row.employment_status === 'resigned'" size="small" type="danger" effect="plain" style="margin-left:4px">离职</el-tag>
                  <el-tag v-else-if="row.employment_status === 'inactive'" size="small" type="info" effect="plain" style="margin-left:4px">非活跃</el-tag>
                </span>
              </template>
            </template>
          </el-table-column>
          <el-table-column prop="department" label="部门" width="90" column-key="department" :filtered-value="deptFilter" :filters="[{text:'企微',value:'企微'},{text:'店铺',value:'店铺'},{text:'研发',value:'研发'}]">
            <template #default="{ row }">
              <template v-if="row._nodeType === 'dept'">
                <el-tag :type="row.department === '店铺' ? 'warning' : ''" size="small">{{ row.department }}</el-tag>
              </template>
              <template v-else-if="row._nodeType === 'group'">
                <span style="color:#c0c4cc">—</span>
              </template>
              <template v-else>
                <el-tag v-if="row.department" :type="row.department === '店铺' ? 'warning' : ''" size="small">{{ row.department }}</el-tag>
                <span v-else style="color:#c0c4cc">-</span>
              </template>
            </template>
          </el-table-column>
          <el-table-column prop="group_name" label="小组" width="110" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row._nodeType === 'group'" style="color:#909399">{{ row.display_name || '未分组' }}</span>
              <span v-else-if="row._nodeType === 'person' && row.group_name" style="color:#606266">{{ row.group_name }}</span>
              <span v-else style="color:#c0c4cc">-</span>
            </template>
          </el-table-column>
          <el-table-column label="专项" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <template v-if="row._nodeType === 'person'">
                <template v-if="row.current_specialties && row.current_specialties.length">
                  <el-tag 
                    v-for="(spec, idx) in row.current_specialties" 
                    :key="idx"
                    size="small" 
                    :type="spec.is_primary ? '' : 'info'"
                    :effect="spec.is_primary ? 'light' : 'plain'"
                    style="margin-right:4px; margin-bottom:2px"
                  >
                    {{ spec.name }}
                  </el-tag>
                </template>
                <span v-else-if="row.specialty" style="color:#606266">{{ row.specialty }}</span>
                <span v-else style="color:#c0c4cc">-</span>
              </template>
              <span v-else style="color:#c0c4cc">-</span>
            </template>
          </el-table-column>
          <el-table-column label="技能" min-width="140" show-overflow-tooltip>
            <template #header>
              <span>
                技能
                <el-tooltip placement="top">
                  <template #content>
                    设计说明:替主管回答"谁能处理什么、熟练度如何"。<br/>
                    口径:技能矩阵 person_skill_tags,标签+熟练度1-5,人工维护。<br/>
                    取舍:只做人工标签,不从接待记录自动推断——自动推断误判<br/>
                    会污染排班决策,等积累足够再评估。
                  </template>
                  <el-icon style="vertical-align:-2px"><InfoFilled /></el-icon>
                </el-tooltip>
              </span>
            </template>
            <template #default="{ row }">
              <template v-if="row._nodeType === 'person' && row.skill_tags && row.skill_tags.length">
                <el-tag
                  v-for="st in row.skill_tags"
                  :key="st.skill_tag"
                  size="small"
                  type="success"
                  effect="plain"
                  style="margin-right:4px; margin-bottom:2px"
                >
                  {{ st.skill_tag }}·L{{ st.level }}
                </el-tag>
              </template>
              <span v-else style="color:#c0c4cc">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="role" label="角色" width="90" column-key="role" :filtered-value="roleFilter" :filters="[{text:'客服',value:'agent'},{text:'专员',value:'specialist'},{text:'专项组长',value:'specialty_lead'},{text:'主管',value:'supervisor'},{text:'管理员',value:'admin'}]">
            <template #default="{ row }">
              <template v-if="row._nodeType === 'person'">{{ roleLabels[row.role] || row.role }}</template>
              <span v-else style="color:#c0c4cc">-</span>
            </template>
          </el-table-column>
          <!-- 专项负责人（业务口径）· #91
               设计说明（Design Rationale）
               ─────────────────────────────────────────────
               目的（替谁回答什么问题）
                 替**管理员/主管**回答「这个专项的业务负责人是谁」。
                 此前页面只有「小组」+「专项」两个维度，**没有专项负责人列** ——
                 而专项负责人与小组组长是两套独立体系，缺列会导致
                 "我明明有负责人划分，为什么界面上看不到"（用户实测反馈）。

               逻辑（为什么单列且带限定语）
                 · **单列**而非并入「小组」：两者是不同体系，同一人可能
                   在某专项任负责人、在行政组仅为成员（如吴晓龙负责退款挽回
                   专项，但他不属于该专项成员）；合并会掩盖这一点。
                 · 列名写「**专项**负责人」并配 ⓘ 说明 —— 只写「负责人」
                   会被读成行政组长，正是用户困惑的来源。

               口径（数据判据）
                 · 来源：`specialties.current_lead_id`（+ `specialty_leadership_history`）
                 · role='lead' → 「专项负责人」；role='deputy' → 「专项副负责人」
                 · ⚠️ ≠ 行政组长（`groups.current_leader_id`），两套体系独立

               取舍（不做什么及为什么）
                 · **不在本列做「转负责人」按钮** —— 该操作已有专门接口
                   （`POST /specialties/{id}/transfer-lead`）且需填写生效日期与
                   变更原因（SCD 历史留痕）；塞进列表行内会绕过审计要求。
                 · **不显示历史负责人**（只显示现任）—— 历史属追溯场景，
                   放在专项详情更合适，列表列应保持"当前状态"单一语义。
          -->
          <el-table-column label="专项负责人" width="130" show-overflow-tooltip>
            <template #default="{ row }">
              <template v-if="row._nodeType === 'specialty'">
                <span v-if="row.current_lead" style="color:#16a34a; font-weight:600">
                  {{ row.current_lead.display_name }}
                </span>
                <span v-else-if="row.current_deputy_lead" style="color:#b45309">
                  {{ row.current_deputy_lead.display_name }}<span class="page-sub">(副)</span>
                </span>
                <el-tag v-else size="small" type="warning" effect="plain">待指派</el-tag>
              </template>
              <span v-else style="color:#c0c4cc">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="employment_status" label="在职" width="75" column-key="employment_status" :filtered-value="empStatusFilter" :filters="[{text:'正式',value:'regular'},{text:'试用期',value:'probation'},{text:'待入职',value:'active'},{text:'调岗',value:'transferred'},{text:'离职',value:'resigned'},{text:'非活跃',value:'inactive'}]">
            <template #default="{ row }">
              <template v-if="row._nodeType === 'person'">
                <el-tag :type="employmentStatusTag(row.employment_status)" size="small">{{ employmentStatusLabels[row.employment_status] || row.employment_status }}</el-tag>
              </template>
              <span v-else style="color:#c0c4cc">-</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <template v-if="row._nodeType === 'person'">
                <el-button text type="primary" size="small" @click.stop="selectPerson(row)">详情</el-button>
                <el-button text type="warning" size="small" @click.stop="toggleActive(row)">{{ row.is_active ? '停用' : '启用' }}</el-button>
              </template>
              <span v-else style="color:#c0c4cc">-</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="mt-8 flex-between">
          <span style="color:#909399; font-size:12px">部门 {{ deptCount }} 个 · 小组 {{ groupCount }} 个 · 人员 {{ total }} 人</span>
        </div>
      </el-card>

      <!-- 右栏:详情/编辑 -->
      <el-card class="right-panel" shadow="never">
        <div v-if="!currentDetail" class="empty-tip">
          <el-empty description="点击左侧人员查看/编辑详情" />
        </div>
        <div v-else class="detail-content">
          <!-- 头部:姓名 + 部门 + 操作 -->
          <div class="detail-header mb-12">
            <div class="flex gap-8" style="align-items:center; flex-wrap:wrap">
              <el-input v-model="editForm.display_name" style="width: 180px" size="default" />
              <el-tag v-if="currentDetail.department" :type="currentDetail.department === '店铺' ? 'warning' : ''" size="small">{{ currentDetail.department }}</el-tag>
              <el-tag size="small" :type="currentDetail.is_confirmed ? 'success' : 'warning'">{{ currentDetail.is_confirmed ? '已确认' : '待确认' }}</el-tag>
              <el-tag size="small" :type="currentDetail.is_active ? 'success' : 'danger'">{{ currentDetail.is_active ? '启用' : '停用' }}</el-tag>
              <span style="color:#909399; font-size:12px">别名 {{ currentDetail.alias_count }} · ID {{ currentDetail.id }}</span>
            </div>
            <div class="flex gap-8 mt-8">
              <el-button size="small" type="primary" :icon="Check" :loading="submitting" @click="handleSavePerson">保存</el-button>
              <el-button size="small" type="success" :disabled="currentDetail.is_confirmed" @click="confirmPerson(currentDetail)">确认归并</el-button>
              <el-button size="small" :type="currentDetail.is_active ? 'warning' : 'success'" @click="toggleActive(currentDetail)">{{ currentDetail.is_active ? '停用' : '启用' }}</el-button>
              <el-button size="small" :icon="Timer" @click="goTimeline(currentDetail)">查看时序</el-button>
            </div>
          </div>

          <!-- 基本信息 inline 可编辑 -->
          <el-form :model="editForm" label-position="top" size="small" class="edit-grid">
            <el-form-item label="部门">
              <el-select v-model="editForm.department" filterable allow-create default-first-option placeholder="选择/输入" style="width:100%">
                <el-option label="企微" value="企微" />
                <el-option label="店铺" value="店铺" />
                <el-option label="研发" value="研发" />
              </el-select>
            </el-form-item>
            <el-form-item label="角色">
              <el-select v-model="editForm.role" style="width:100%">
                <el-option label="客服" value="agent" />
                <el-option label="专员" value="specialist" />
                <el-option label="专项组长" value="specialty_lead" />
                <el-option label="主管" value="supervisor" />
                <el-option label="管理员" value="admin" />
              </el-select>
            </el-form-item>
            <el-form-item label="在职状态">
              <el-select v-model="editForm.employment_status" style="width:100%">
                <el-option label="正式" value="regular" />
                <el-option label="试用期" value="probation" />
                <el-option label="待入职" value="active" />
                <el-option label="调岗" value="transferred" />
                <el-option label="离职" value="resigned" />
                <el-option label="非活跃" value="inactive" />
              </el-select>
            </el-form-item>
            <el-form-item label="入职日期">
              <el-date-picker v-model="editForm.hire_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
            <el-form-item label="试用期到期">
              <el-date-picker v-model="editForm.probation_end_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
            <el-form-item label="转正日期">
              <el-date-picker v-model="editForm.regularization_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
            <el-form-item label="离职日期">
              <el-date-picker v-model="editForm.resignation_date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
            </el-form-item>
            <el-form-item label="当前岗位">
              <span class="mirror-field">{{ currentDetail.position || '-' }}</span>
            </el-form-item>
            <el-form-item label="当前分组">
              <template v-if="currentDetail.current_group">
                <el-tag size="small" type="primary">{{ currentDetail.current_group.name }}</el-tag>
              </template>
              <span v-else class="mirror-field">{{ currentDetail.group_name || '-' }}</span>
            </el-form-item>
            <el-form-item label="当前专项" class="full-row">
              <template v-if="currentDetail.current_specialties && currentDetail.current_specialties.length">
                <div class="flex gap-4" style="flex-wrap: wrap">
                  <el-tag 
                    v-for="(spec, idx) in currentDetail.current_specialties" 
                    :key="idx"
                    size="small" 
                    :type="spec.is_primary ? '' : 'info'"
                    :effect="spec.is_primary ? 'light' : 'plain'"
                  >
                    {{ spec.name }}
                    <span v-if="spec.difficulty_weight && spec.difficulty_weight !== 1" style="opacity:0.7; margin-left:2px">×{{ spec.difficulty_weight }}</span>
                  </el-tag>
                </div>
              </template>
              <span v-else class="mirror-field">{{ currentDetail.specialty || '-' }}</span>
            </el-form-item>
            <el-form-item label="备注" class="full-row">
              <el-input v-model="editForm.remark" type="textarea" :rows="2" placeholder="可选" />
            </el-form-item>
          </el-form>
          <div style="color:#909399; font-size:11px; margin-bottom:8px">
            * 岗位/专项/分组是任职镜像,需下方任职历史变更
          </div>

          <!-- 技能标签(B3-7): 副本编辑,保存时 PUT 全量替换 -->
          <div class="flex-between mb-8 mt-12">
            <span class="section-title">
              技能标签
              <el-tooltip placement="top">
                <template #content>
                  设计说明:替主管回答"这个人能顶什么岗"。<br/>
                  操作:添加标签(默认 L3)→调熟练度→保存;<br/>
                  保存为全量替换(以保存时列表为准)。<br/>
                  取舍:标签不自动生成,纯人工维护,宁缺勿错。
                </template>
                <el-icon style="vertical-align:-2px; color:#909399"><InfoFilled /></el-icon>
              </el-tooltip>
            </span>
            <el-button size="small" type="primary" :loading="skillSaving" :disabled="!detailSkills.length && !(currentDetail.skill_tags||[]).length" @click="saveDetailSkills">
              保存技能
            </el-button>
          </div>
          <div class="skill-edit-box">
            <el-tag
              v-for="(st, idx) in detailSkills"
              :key="st.skill_tag"
              closable
              type="success"
              effect="plain"
              style="margin: 0 6px 6px 0"
              @close="removeDetailSkill(idx)"
            >
              {{ st.skill_tag }}
              <el-rate
                :model-value="st.level"
                :max="5"
                size="small"
                style="display:inline-flex; vertical-align:middle; margin-left:4px"
                @update:model-value="(v) => (st.level = v || 1)"
              />
            </el-tag>
            <el-select
              size="small"
              filterable
              allow-create
              default-first-option
              placeholder="+ 添加技能"
              style="width: 140px"
              @change="addDetailSkill"
            >
              <el-option v-for="t in skillTagsList" :key="t.skill_tag" :label="t.skill_tag" :value="t.skill_tag" />
            </el-select>
          </div>

          <!-- 绩效·排班联动(B3-7): 摘要 + 跳转,不做页内深分析 -->
          <div class="flex-between mb-8 mt-12">
            <span class="section-title">绩效·排班联动</span>
            <span class="flex gap-8">
              <el-button size="small" text type="primary" @click="goReceptionAnalysis">接待分析</el-button>
              <el-button size="small" text type="primary" @click="goSchedulePage">排班管理</el-button>
            </span>
          </div>
          <div v-loading="linksLoading" class="link-summary-box">
            <div class="link-row">
              <span class="link-label">近7天接待</span>
              <template v-if="personLinks.perf">
                <span style="font-weight:600">{{ personLinks.perf.total }}</span> 条
                <span style="color:#909399; margin-left:8px">日均 {{ personLinks.perf.avg }} · 主类 {{ personLinks.perf.category }} · {{ personLinks.perf.group_name }}</span>
              </template>
              <span v-else style="color:#c0c4cc">近7天无接待记录</span>
            </div>
            <div class="link-row">
              <span class="link-label">本周排班</span>
              <template v-if="personLinks.schedules.length">
                <el-tag
                  v-for="s in personLinks.schedules.slice(0, 7)"
                  :key="s.id"
                  size="small"
                  effect="plain"
                  style="margin-right:4px"
                >
                  {{ s.schedule_date.slice(5) }} {{ s.shift_name || (s.start_time || '') }}
                </el-tag>
              </template>
              <span v-else style="color:#c0c4cc">本周未排班</span>
            </div>
          </div>

          <!-- 任职历史 -->
          <div class="flex-between mb-8">
            <span class="section-title">任职历史</span>
            <el-button size="small" type="primary" :icon="Plus" @click="openAssignmentDialog()">新增任职</el-button>
          </div>
          <el-table v-loading="assignmentsLoading" :data="assignments" size="small" border max-height="180">
            <el-table-column prop="effective_from" label="生效起" width="100" />
            <el-table-column label="生效止" width="100">
              <template #default="{ row }">{{ row.effective_to || '至今' }}</template>
            </el-table-column>
            <el-table-column label="当前" width="50">
              <template #default="{ row }"><el-tag v-if="row.is_current" type="success" size="small">是</el-tag></template>
            </el-table-column>
            <el-table-column label="岗位" width="70"><template #default="{ row }">{{ row.position || '-' }}</template></el-table-column>
            <el-table-column label="专项" width="90"><template #default="{ row }">{{ row.specialty || '-' }}</template></el-table-column>
            <el-table-column label="分组" width="90"><template #default="{ row }">{{ row.group_name || '-' }}</template></el-table-column>
            <el-table-column label="来源" width="80"><template #default="{ row }">{{ sourceLabels[row.source] || row.source }}</template></el-table-column>
            <el-table-column label="操作" width="110" fixed="right">
              <template #default="{ row }">
                <el-button text type="primary" size="small" @click="openAssignmentDialog(row)">编辑</el-button>
                <el-button text type="danger" size="small" @click="handleDeleteAssignment(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>

          <!-- 别名列表 -->
          <div class="flex-between mb-8 mt-12">
            <span class="section-title">别名列表 ({{ currentDetail.aliases?.length || 0 }})</span>
            <el-button size="small" type="warning" :disabled="!selectedDetailAliasIds.length" @click="handleSplit">拆出选中 ({{ selectedDetailAliasIds.length }})</el-button>
          </div>
          <el-table :data="currentDetail.aliases" size="small" border max-height="200" @selection-change="onDetailAliasSelect">
            <el-table-column type="selection" width="38" :selectable="(row) => !row.is_system" />
            <el-table-column prop="alias" label="别名原值" min-width="120" show-overflow-tooltip />
            <el-table-column label="来源(首次)" width="140">
              <template #default="{ row }">
                <span>{{ row.source_table_label || row.source_table }}</span>
                <span v-if="row.source_field_label" style="color:#909399"> / {{ row.source_field_label }}</span>
              </template>
            </el-table-column>
            <el-table-column label="出现位置" min-width="200">
              <template #default="{ row }">
                <div v-if="row.occurrences && row.occurrences.length">
                  <el-tooltip v-for="(occ, i) in row.occurrences" :key="i" :content="`${occ.table} / ${occ.field}`" placement="top">
                    <el-tag size="small" type="info" style="margin: 0 4px 2px 0">{{ occ.table_label }}/{{ occ.field_label }} ×{{ occ.count }}</el-tag>
                  </el-tooltip>
                </div>
                <span v-else style="color:#c0c4cc; font-size:11px">业务表未出现(HR导入别名)</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>
    </div>

    <!-- 归并到新建人员 对话框 -->
    <el-dialog v-model="mergeNewVisible" title="归并到新建人员" width="480px">
      <el-form label-width="100px">
        <el-form-item label="已选别名"><span>{{ selectedAliasIds.length }} 个</span></el-form-item>
        <el-form-item label="显示名" required>
          <el-input v-model="mergeNewForm.display_name" placeholder="归并后的统一显示名" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="mergeNewForm.role" style="width: 100%">
            <el-option label="客服" value="agent" /><el-option label="专员" value="specialist" /><el-option label="专项组长" value="specialty_lead" /><el-option label="主管" value="supervisor" /><el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="部门">
          <el-select v-model="mergeNewForm.department" style="width: 100%" filterable allow-create default-first-option placeholder="选择或输入部门">
            <el-option label="企微" value="企微" /><el-option label="店铺" value="店铺" /><el-option label="研发" value="研发" />
          </el-select>
        </el-form-item>
        <el-form-item label="组"><el-input v-model="mergeNewForm.group_name" placeholder="可选(将记为初始任职)" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeNewVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleMergeToNew">确认归并</el-button>
      </template>
    </el-dialog>

    <!-- 归并到已有人员 对话框 -->
    <el-dialog v-model="mergeExistVisible" title="归并到已有人员" width="480px">
      <el-form label-width="100px">
        <el-form-item label="已选别名"><span>{{ selectedAliasIds.length }} 个</span></el-form-item>
        <el-form-item label="目标人员" required>
          <el-select v-model="mergeExistTargetId" filterable placeholder="选择目标人员" style="width: 100%">
            <el-option v-for="p in confirmedPersons" :key="p.id" :label="`${p.display_name} (${p.alias_count}别名)`" :value="p.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mergeExistVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleMergeToExisting">确认归并</el-button>
      </template>
    </el-dialog>

    <!-- 归并建议 对话框 -->
    <el-dialog v-model="suggestionsVisible" title="归并建议(归一化重复的别名组)" width="700px">
      <div v-if="suggestions.length">
        <el-card v-for="(group, gi) in suggestions" :key="gi" class="mb-12" shadow="never">
          <template #header>
            <div class="flex-between">
              <span><span style="font-weight: 600; color: #409eff">{{ group.normalized }}</span><span style="color: #909399; margin-left: 8px">({{ group.aliases.length }} 个别名)</span></span>
              <el-button size="small" type="primary" @click="quickMerge(group)">一键归并</el-button>
            </div>
          </template>
          <el-table :data="group.aliases" size="small" border>
            <el-table-column prop="alias" label="别名原值" min-width="140" show-overflow-tooltip />
            <el-table-column label="来源表" width="180">
              <template #default="{ row }"><span>{{ row.source_table_label || row.source_table }}</span></template>
            </el-table-column>
            <el-table-column prop="person_id" label="当前人员" width="90" />
          </el-table>
        </el-card>
      </div>
      <el-empty v-else description="暂无归并建议(所有别名归一化值唯一)" />
    </el-dialog>

    <!-- 批量归并结果 对话框 -->
    <el-dialog v-model="batchResultVisible" title="批量归并结果" width="640px">
      <el-descriptions :column="2" border class="mb-16">
        <el-descriptions-item label="成功归并"><span style="color: #67c23a; font-weight: 600">{{ batchResult.merged_count }}</span> 条</el-descriptions-item>
        <el-descriptions-item label="跳过(歧义/无建议)"><span style="color: #909399">{{ batchResult.skipped_count }}</span> 条</el-descriptions-item>
      </el-descriptions>
      <div v-if="batchResult.merged.length" class="mb-16">
        <div style="font-weight: 600; margin-bottom: 8px">已归并明细</div>
        <el-table :data="batchResult.merged" size="small" border max-height="260">
          <el-table-column prop="alias" label="别名原值" min-width="160" show-overflow-tooltip />
          <el-table-column label="归并到" width="120"><template #default="{ row }"><span style="color: #67c23a; font-weight: 600">{{ row.target_name }}</span></template></el-table-column>
          <el-table-column label="匹配方式" width="100"><template #default="{ row }"><el-tag size="small" :type="row.match_source === 'normalized' ? 'success' : 'primary'">{{ row.match_source === 'normalized' ? '名匹配' : '首字母' }}</el-tag></template></el-table-column>
        </el-table>
      </div>
      <div v-if="batchResult.skipped.length">
        <div style="font-weight: 600; margin-bottom: 8px">跳过明细(留人工)</div>
        <el-table :data="batchResult.skipped" size="small" border max-height="160">
          <el-table-column prop="alias" label="别名原值" min-width="160" show-overflow-tooltip />
          <el-table-column label="原因" width="120"><template #default="{ row }"><el-tag size="small" type="info">{{ row.reason }}</el-tag></template></el-table-column>
        </el-table>
      </div>
      <template #footer><el-button type="primary" @click="batchResultVisible = false">关闭</el-button></template>
    </el-dialog>

    <!-- 任职记录 新增/编辑 对话框 -->
    <el-dialog v-model="assignmentDialogVisible" :title="assignmentForm.id ? '编辑任职记录' : '新增任职记录'" width="520px">
      <el-form label-width="100px">
        <el-form-item label="生效起始" required>
          <el-date-picker v-model="assignmentForm.effective_from" type="date" value-format="YYYY-MM-DD" placeholder="选择生效日期" style="width: 100%" :disabled="!!assignmentForm.id" />
        </el-form-item>
        <el-form-item label="生效结束">
          <el-date-picker v-model="assignmentForm.effective_to" type="date" value-format="YYYY-MM-DD" placeholder="留空=至今(当前任职)" style="width: 100%" />
        </el-form-item>
        <el-form-item label="岗位">
          <el-select v-model="assignmentForm.position" clearable style="width: 100%">
            <el-option label="客服" value="客服" /><el-option label="投诉专员" value="投诉专员" /><el-option label="组长" value="组长" /><el-option label="副主管" value="副主管" /><el-option label="主管" value="主管" />
          </el-select>
        </el-form-item>
        <el-form-item label="专项">
          <el-select v-model="assignmentForm.specialty" clearable filterable style="width: 100%">
            <el-option v-for="s in specialtiesList" :key="s.id" :label="s.name" :value="s.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="分组"><el-input v-model="assignmentForm.group_name" placeholder="可选" /></el-form-item>
        <el-form-item label="变更原因"><el-input v-model="assignmentForm.change_reason" type="textarea" :rows="2" placeholder="可选" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignmentDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleSubmitAssignment">确认</el-button>
      </template>
    </el-dialog>

    <!-- 批量编辑 对话框 -->
    <el-dialog v-model="batchEditVisible" title="批量编辑人员" width="560px">
      <div style="color:#606266; margin-bottom:12px">已选 {{ selectedPersonIds.length }} 人 · 勾选要修改的字段并设值,未勾选的不改</div>
      <el-form label-width="110px" size="small">
        <el-form-item label="部门">
          <el-checkbox v-model="batchApply.department" style="width:90px">修改</el-checkbox>
          <el-select v-model="batchForm.department" filterable allow-create default-first-option placeholder="选择/输入" style="width: calc(100% - 100px)">
            <el-option label="企微" value="企微" /><el-option label="店铺" value="店铺" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色">
          <el-checkbox v-model="batchApply.role" style="width:90px">修改</el-checkbox>
          <el-select v-model="batchForm.role" style="width: calc(100% - 100px)">
            <el-option label="客服" value="agent" /><el-option label="专员" value="specialist" /><el-option label="专项组长" value="specialty_lead" /><el-option label="主管" value="supervisor" /><el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="在职状态">
          <el-checkbox v-model="batchApply.employment_status" style="width:90px">修改</el-checkbox>
          <el-select v-model="batchForm.employment_status" style="width: calc(100% - 100px)">
            <el-option label="正式" value="regular" />
            <el-option label="试用期" value="probation" />
            <el-option label="待入职" value="active" />
            <el-option label="调岗" value="transferred" />
            <el-option label="离职" value="resigned" />
            <el-option label="非活跃" value="inactive" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用/停用">
          <el-checkbox v-model="batchApply.is_active" style="width:90px">修改</el-checkbox>
          <el-select v-model="batchForm.is_active" style="width: calc(100% - 100px)">
            <el-option label="启用" :value="true" /><el-option label="停用" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="确认状态">
          <el-checkbox v-model="batchApply.is_confirmed" style="width:90px">修改</el-checkbox>
          <el-select v-model="batchForm.is_confirmed" style="width: calc(100% - 100px)">
            <el-option label="已确认" :value="true" /><el-option label="待确认" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="入职日期">
          <el-checkbox v-model="batchApply.hire_date" style="width:90px">修改</el-checkbox>
          <el-date-picker v-model="batchForm.hire_date" type="date" value-format="YYYY-MM-DD" style="width: calc(100% - 100px)" />
        </el-form-item>
        <el-divider content-position="left">任职记录(为每人建一条今天生效的任职)</el-divider>
        <el-form-item label="分组">
          <el-checkbox v-model="batchApply.group_name" style="width:90px">修改</el-checkbox>
          <el-input v-model="batchForm.group_name" placeholder="可选" style="width: calc(100% - 100px)" />
        </el-form-item>
        <el-form-item label="专项">
          <el-checkbox v-model="batchApply.specialty" style="width:90px">修改</el-checkbox>
          <el-select v-model="batchForm.specialty" clearable filterable style="width: calc(100% - 100px)">
            <el-option v-for="s in specialtiesList" :key="s.id" :label="s.name" :value="s.name" />
          </el-select>
        </el-form-item>
        <el-divider content-position="left">技能打标(独立接口,不影响任职)</el-divider>
        <el-form-item label="技能">
          <el-checkbox v-model="batchApply.skill" style="width:90px">修改</el-checkbox>
          <div style="width: calc(100% - 100px); display:flex; gap:6px; flex-wrap:wrap">
            <el-select v-model="batchForm.skill_mode" style="width: 110px">
              <el-option label="打上标签" value="add" /><el-option label="移除标签" value="remove" />
            </el-select>
            <el-select v-model="batchForm.skill_tag" filterable allow-create default-first-option placeholder="技能标签" style="flex:1; min-width:110px">
              <el-option v-for="t in skillTagsList" :key="t.skill_tag" :label="t.skill_tag" :value="t.skill_tag" />
            </el-select>
            <el-select v-if="batchForm.skill_mode === 'add'" v-model="batchForm.skill_level" style="width: 90px">
              <el-option v-for="lv in [1,2,3,4,5]" :key="lv" :label="`L${lv}`" :value="lv" />
            </el-select>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="batchEditVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleBatchUpdate">确认批量更新</el-button>
      </template>
    </el-dialog>

    <!-- 技能矩阵 对话框(B3-7): 行=在职人员, 列=技能标签, 格子点击轮换 1→3→5→删 -->
    <el-dialog v-model="skillMatrixVisible" title="技能矩阵" width="90%" top="6vh">
      <div class="flex-between mb-8">
        <span style="color:#909399; font-size:12px">
          替主管回答"谁会什么、熟练度如何"——点击格子循环 空白→L1→L3→L5→移除。
          <el-tooltip placement="top">
            <template #content>
              设计说明:矩阵是全司技能的一屏总览,服务排班/顶岗决策。<br/>
              口径:仅在职人员;标签人工维护(person_skill_tags)。<br/>
              取舍:① 不做自动推断(误判污染决策);② 单元格只做<br/>
              三档轮换(1/3/5),不做逐格 1-5 微调,保证批量操作效率;③<br/>
              不做导出——需要分发时截图即可,不值得加代码。
            </template>
            <el-icon style="vertical-align:-2px"><InfoFilled /></el-icon>
          </el-tooltip>
        </span>
        <el-tag size="small" type="info">{{ skillMatrix.persons.length }} 人 × {{ skillMatrix.tags.length }} 技能</el-tag>
      </div>
      <el-table v-loading="skillMatrixLoading" :data="skillMatrix.persons" size="small" border max-height="65vh">
        <el-table-column prop="display_name" label="人员" width="120" fixed="left" show-overflow-tooltip />
        <el-table-column prop="department" label="部门" width="70" show-overflow-tooltip />
        <el-table-column
          v-for="tag in skillMatrix.tags"
          :key="tag"
          :label="tag"
          min-width="72"
          align="center"
        >
          <template #default="{ row }">
            <el-button
              link
              size="small"
              :type="row.skills.some(s => s.skill_tag === tag) ? 'success' : 'info'"
              style="width:100%; font-weight:600"
              @click="toggleMatrixCell(row, tag)"
            >
              {{ row.skills.find(s => s.skill_tag === tag)?.level || '·' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button type="primary" @click="skillMatrixVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 导入人力快照 对话框 -->
    <el-dialog v-model="importDialogVisible" title="导入人力快照" width="900px">
      <el-steps :active="importStep" finish-status="success" class="mb-16">
        <el-step title="上传文件" /><el-step title="确认列映射" /><el-step title="预览差异" /><el-step title="完成" />
      </el-steps>
      <div v-if="importStep === 0">
        <el-upload :auto-upload="true" :show-file-list="false" :http-request="handleUploadFile" accept=".xlsx,.xls,.csv">
          <el-button type="primary" :loading="importParsing">选择 HR 人力数据文件</el-button>
        </el-upload>
        <div class="mt-8" style="color: #909399; font-size: 12px">支持 .xlsx/.xls/.csv,需包含 姓名/客服名/名称1/名称2/专项/岗位/更新时间 列</div>
      </div>
      <div v-if="importStep === 1">
        <div class="mb-8" style="color: #606266; font-size: 13px">文件:{{ importFilename }} | 共 {{ importPreview.total_rows }} 行 | 解析编码:{{ importPreview.detected_encoding || '-' }}</div>
        <el-table :data="importMappingRows" size="small" border max-height="360">
          <el-table-column prop="target_field" label="目标字段" width="160" />
          <el-table-column label="源列">
            <template #default="{ row }">
              <el-select v-model="row.source_column" clearable filterable style="width: 100%">
                <el-option v-for="c in importPreview.columns" :key="c" :label="c" :value="c" />
              </el-select>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div v-if="importStep === 2">
        <div class="mb-8" style="font-size: 13px; color: #606266">
          预览:共 {{ importDiff.summary.total }} 行 —
          <el-tag size="small" type="success">新建 {{ importDiff.summary.new }}</el-tag>
          <el-tag size="small" type="primary" class="ml-4">更新 {{ importDiff.summary.update }}</el-tag>
          <el-tag size="small" type="info" class="ml-4">跳过 {{ importDiff.summary.skip }}</el-tag>
          <el-tag size="small" type="danger" class="ml-4">冲突 {{ importDiff.summary.conflict }}</el-tag>
          <span v-if="importDiff.summary.conflict > 0" class="ml-8" style="color:#f56c6c">冲突行将在确认时跳过(需先在归并面板处理)</span>
        </div>
        <el-table :data="importDiff.rows" size="small" border max-height="420">
          <el-table-column prop="row_idx" label="行" width="50" />
          <el-table-column prop="display_name" label="姓名" width="100" />
          <el-table-column label="匹配人员" width="110"><template #default="{ row }">{{ row.matched_name || '-' }}</template></el-table-column>
          <el-table-column label="动作" width="80"><template #default="{ row }"><el-tag size="small" :type="actionTagType(row.action)">{{ actionLabel(row.action) }}</el-tag></template></el-table-column>
          <el-table-column label="变更明细" min-width="260">
            <template #default="{ row }">
              <span v-if="row.action === 'conflict'" style="color:#f56c6c">{{ row.changes.reason }}</span>
              <span v-else>
                <span v-if="row.changes.aliases_to_add && row.changes.aliases_to_add.length">将加别名:{{ row.changes.aliases_to_add.join('、') }} </span>
                <span v-if="row.changes.position_change"> 任职岗位:{{ row.changes.position_change.old || '无' }}→{{ row.changes.position_change.new || '无' }} </span>
                <span v-if="row.changes.specialty_change"> 专项:{{ row.changes.specialty_change.old || '无' }}→{{ row.changes.specialty_change.new || '无' }} </span>
                <span v-if="row.changes.will_confirm"> 标记确认 </span>
                <span v-if="row.changes.assignment === 'new'"> 新任职({{ row.snapshot_date }}) </span>
                <span v-if="row.action === 'skip'" style="color:#909399">无变更</span>
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div v-if="importStep === 3">
        <el-result :icon="importResult.success ? 'success' : 'error'" :title="importResult.success ? '导入完成' : '导入失败'">
          <template #sub-title>
            <div v-if="importResult.success">
              <div>总行数:{{ importResult.total_rows }}</div>
              <div>新增任职:{{ importResult.inserted_rows }} | 更新:{{ importResult.updated_rows }} | 失败:{{ importResult.failed_rows }}</div>
            </div>
            <div v-else style="color: #f56c6c">{{ importResult.error }}</div>
          </template>
        </el-result>
      </div>
      <template #footer>
        <el-button @click="importDialogVisible = false">关闭</el-button>
        <el-button v-if="importStep === 1" type="primary" :loading="previewing" @click="handlePreviewImport">预览差异</el-button>
        <el-button v-if="importStep === 2" :loading="importing" type="primary" @click="handleConfirmImport">确认合并</el-button>
        <el-button v-if="importStep === 2" @click="importStep = 1">返回修改映射</el-button>
        <el-button v-if="importStep === 3 && importResult.success" @click="resetImportDialog">再次导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
/**
 * 设计说明（Design Rationale）· B3-7 人员技能矩阵与绩效/排班联动 (#255)
 * ─────────────────────────────────────────────────────────────────
 * 目的（替谁回答什么问题）
 *   替主管/管理员回答三个问题：
 *   ① "谁会什么、熟练度如何"——技能列+技能矩阵弹窗（排班/顶岗依据）；
 *   ② "这个人最近状态如何"——详情面板近7天接待摘要（判断是否适合顶岗）；
 *   ③ "这周他排了什么班"——详情面板本周排班摘要（结合①②做调配）。
 *
 * 逻辑（为什么这样组织）
 *   技能是人员的稳定属性 → 放列表列（扫一眼可比）+ 矩阵弹窗（全司一屏总览）+
 *   详情面板（单人精修）；绩效/排班是易变事实 → 只做"摘要+跳转"两行，
 *   不在人员页内复制分析页职责（分析看板才是真身，此处为投影并已声明）。
 *
 * 口径
 *   技能: person_skill_tags 表，人工维护，熟练度 1-5，仅在职人员入矩阵；
 *   接待摘要: /analytics/reception/group-detail 近7天，按 display_name 匹配；
 *   排班摘要: /schedules 本周一至周日，按 person_id 精确匹配。
 *
 * 取舍（不做什么及原因）
 *   ① 不从接待记录自动推断技能——误判会污染排班决策，宁缺勿错；
 *   ② 矩阵单元格只做 空白→L1→L3→L5→移除 三档轮换，不做逐格 1-5 微调；
 *   ③ 不做技能导出——需要分发时截图即可；
 *   ④ 绩效只给 7 天摘要不给趋势图——趋势在接待分析页（真身），重复展示违反投影纪律。
 *
 * 工单: #255（B3-7 人员管理重设计）
 */
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Connection, Plus, Edit, Upload, Download, Check, Timer, Grid, InfoFilled } from '@element-plus/icons-vue'
import {
  listPersons,
  getPerson,
  updatePerson,
  mergeAliases,
  mergeToNew,
  splitAliases,
  listUnmergedAliases,
  listMergeSuggestions,
  refreshAliases,
  rebuildAliases,
  batchMergeBySuggestion,
  batchCreateByNormalized,
  batchImportByPrefix,
  batchUpdatePersons,
  exportPersons,
  listSkillTags,
  getSkillMatrix,
  putPersonSkills,
  batchSkills,
} from '@/api/person'
import { listGroups } from '@/api/group'
import { listSpecialties } from '@/api/specialty'
import {
  listAssignments,
  createAssignment,
  updateAssignment,
  deleteAssignment,
  importSnapshot,
  previewSnapshot,
} from '@/api/staffAssignment'
import { listSchedules } from '@/api/schedule'
import { reception } from '@/api/analytics'
import { parseUpload } from '@/api/upload'

const list = ref([])
const treeList = ref([])
const total = ref(0)
const deptCount = ref(0)
const groupCount = ref(0)
const loading = ref(false)
const page = ref(1)
const router = useRouter()
const filter = reactive({ status: 'confirmed', department: '', group_id: null, specialty_id: null, role: '', employment_status: '', skill_tag: '' })
const sort = reactive({ sort_by: 'id', sort_order: 'desc' })
const groupsList = ref([])
const specialtiesList = ref([])
const deptFilter = ref([])
const roleFilter = ref([])
const empStatusFilter = ref([])
const refreshing = ref(false)
const rebuilding = ref(false)
const importingShop = ref(false)
const importingQw = ref(false)
const importingRnd = ref(false)
const submitting = ref(false)
const mergingAliasId = ref(null)
const batchMerging = ref(false)
const batchCreating = ref(false)
const batchResultVisible = ref(false)
const batchResult = ref({ merged: [], skipped: [], merged_count: 0, skipped_count: 0 })

// 待归并别名(折叠)
const aliasCollapse = ref([])
const unmergedAliases = ref([])
const selectedAliasIds = ref([])

// 详情/右栏
const currentPerson = ref(null)
const currentDetail = ref(null)
const selectedDetailAliasIds = ref([])
const editForm = reactive({
  display_name: '', department: '', role: 'agent', employment_status: 'active',
  hire_date: '', probation_end_date: '', regularization_date: '', resignation_date: '',
  remark: '', is_active: true,
})

// 列表批量选择
const selectedPersonIds = ref([])

// 任职历史
const assignments = ref([])
const assignmentsLoading = ref(false)

// 归并对话框
const mergeNewVisible = ref(false)
const mergeExistVisible = ref(false)
const mergeExistTargetId = ref(null)
const confirmedPersons = ref([])
const mergeNewForm = reactive({ display_name: '', role: 'agent', department: '', group_name: '' })

const suggestionsVisible = ref(false)
const suggestions = ref([])

const assignmentDialogVisible = ref(false)
const assignmentForm = reactive({ id: null, effective_from: '', effective_to: '', position: '', specialty: '', group_name: '', change_reason: '' })

// 批量编辑
const batchEditVisible = ref(false)
const batchApply = reactive({ department: false, role: false, employment_status: false, is_active: false, is_confirmed: false, hire_date: false, group_name: false, specialty: false, skill: false })
const batchForm = reactive({ department: '', role: '', employment_status: '', is_active: true, is_confirmed: true, hire_date: '', group_name: '', specialty: '', skill_tag: '', skill_level: 3, skill_mode: 'add' })

// ---------- B3-7 技能矩阵 (#255) ----------
// 设计说明（Design Rationale）见文件头部注释块 + 页面两处 ⓘ 气泡（技能列头/矩阵弹窗）
const skillTagsList = ref([])          // 技能标签字典(标签+使用人数)
const skillMatrixVisible = ref(false)
const skillMatrixLoading = ref(false)
const skillMatrix = ref({ tags: [], persons: [] })
const detailSkills = ref([])           // 详情面板技能编辑副本 [{skill_tag, level}]
const skillSaving = ref(false)
const personLinks = ref({ perf: null, schedules: [] })   // 绩效/排班联动摘要
const linksLoading = ref(false)

async function loadSkillTags() {
  try {
    const res = await listSkillTags()
    skillTagsList.value = res.data
  } catch (e) {
    console.error('加载技能标签失败', e)
  }
}

async function openSkillMatrix() {
  skillMatrixVisible.value = true
  skillMatrixLoading.value = true
  try {
    const res = await getSkillMatrix()
    skillMatrix.value = res.data
  } finally {
    skillMatrixLoading.value = false
  }
}

// 矩阵单元格: 无标签点击=新增(level 1), 有标签点击=轮换 1→3→5→删除
async function toggleMatrixCell(person, tag) {
  const cur = person.skills.find(s => s.skill_tag === tag)
  const nextLevel = !cur ? 1 : cur.level >= 5 ? null : cur.level >= 3 ? 5 : 3
  try {
    if (nextLevel === null) {
      await batchSkills({ person_ids: [person.person_id], skill_tag: tag, mode: 'remove' })
      person.skills = person.skills.filter(s => s.skill_tag !== tag)
    } else {
      await batchSkills({ person_ids: [person.person_id], skill_tag: tag, level: nextLevel, mode: 'add' })
      if (cur) cur.level = nextLevel
      else person.skills.push({ skill_tag: tag, level: nextLevel })
    }
    loadSkillTags()
  } catch (e) {
    ElMessage.error('保存失败')
  }
}

// 详情面板: 技能编辑(副本改完一次 PUT 全量替换)
function addDetailSkill(tag) {
  if (!tag || detailSkills.value.some(s => s.skill_tag === tag)) return
  detailSkills.value.push({ skill_tag: tag, level: 3 })
}
function removeDetailSkill(idx) {
  detailSkills.value.splice(idx, 1)
}
async function saveDetailSkills() {
  if (!currentDetail.value) return
  skillSaving.value = true
  try {
    await putPersonSkills(currentDetail.value.id, detailSkills.value)
    currentDetail.value.skill_tags = detailSkills.value.map(s => ({ ...s }))
    // 同步左侧列表行
    const row = list.value.find(p => p.id === currentDetail.value.id)
    if (row) row.skill_tags = currentDetail.value.skill_tags
    ElMessage.success('技能已保存')
    loadSkillTags()
  } finally {
    skillSaving.value = false
  }
}

// ---------- 绩效·排班联动 ----------
async function loadPersonLinks(p) {
  linksLoading.value = true
  personLinks.value = { perf: null, schedules: [] }
  try {
    // 近 7 天接待: 从 group-detail 的 people 里按名字匹配(接口无 person_id 维度)
    const end = new Date(); const start = new Date(Date.now() - 6 * 86400000)
    const fmt = d => d.toISOString().slice(0, 10)
    // 本周一到周日排班
    const now = new Date()
    const mon = new Date(now); mon.setDate(now.getDate() - ((now.getDay() + 6) % 7))
    const sun = new Date(mon); sun.setDate(mon.getDate() + 6)
    const [perfRes, schRes] = await Promise.all([
      reception.groupDetail({ start: `${fmt(start)} 00:00:00`, end: `${fmt(end)} 23:59:59` }),
      listSchedules({ start: fmt(mon), end: fmt(sun), person_id: p.id }),
    ])
    let perf = null
    for (const g of (perfRes.data?.groups || [])) {
      const hit = (g.people || []).find(x => x.name === p.display_name)
      if (hit) { perf = { total: hit.total, avg: hit.avg, group_name: g.group_name, category: hit.category_type }; break }
    }
    if (!perf) {
      const hit = (perfRes.data?.ungrouped || []).find(x => x.name === p.display_name)
      if (hit) perf = { total: hit.total, avg: hit.avg, group_name: '未分组', category: hit.category_type }
    }
    personLinks.value = { perf, schedules: schRes.data || [] }
  } catch (e) {
    console.error('加载绩效/排班联动失败', e)
  } finally {
    linksLoading.value = false
  }
}
function goReceptionAnalysis() {
  router.push('/dashboards/reception')
}
function goSchedulePage() {
  router.push('/management/schedule')
}

// 导入快照
const importDialogVisible = ref(false)
const importStep = ref(0)
const importParsing = ref(false)
const importing = ref(false)
const previewing = ref(false)
const importFilename = ref('')
const importStoredPath = ref('')
const importPreview = ref({ columns: [], total_rows: 0, detected_encoding: null, sheet_name: null, sheet_names: [], encoding: null })
const importMappingRows = ref([])
const importDiff = ref({ summary: { total: 0, new: 0, update: 0, skip: 0, conflict: 0 }, rows: [] })
const importResult = ref({ success: false, total_rows: 0, inserted_rows: 0, updated_rows: 0, failed_rows: 0, error: '' })

const roleLabels = { admin: '管理员', supervisor: '主管', specialty_lead: '专项组长', specialist: '专员', agent: '客服' }
const sourceLabels = { manual: '手工', xlsx_import: '快照导入' }
const employmentStatusLabels = { 
  active: '待入职', probation: '试用期', regular: '正式', 
  transferred: '调岗', resigned: '离职', inactive: '非活跃' 
}
function employmentStatusTag(s) {
  return { 
    active: 'info', probation: 'warning', regular: 'success', 
    transferred: 'warning', resigned: 'danger', inactive: 'info' 
  }[s] || 'info'
}

async function loadData() {
  loading.value = true
  try {
    const res = await listPersons({
      page: 1,
      page_size: 5000,
      status: filter.status,
      sort_by: sort.sort_by,
      sort_order: sort.sort_order,
      ...(filter.department ? { department: filter.department } : {}),
      ...(filter.group_id ? { group_id: filter.group_id } : {}),
      ...(filter.specialty_id ? { specialty_id: filter.specialty_id } : {}),
      ...(filter.role ? { role: filter.role } : {}),
      ...(filter.employment_status ? { employment_status: filter.employment_status } : {}),
      ...(filter.skill_tag ? { skill_tag: filter.skill_tag } : {}),
    })
    const flat = res.data.items
    list.value = flat
    total.value = res.data.total
    const { tree, nDept, nGroup } = buildPersonTree(flat)
    treeList.value = tree
    deptCount.value = nDept
    groupCount.value = nGroup
  } finally {
    loading.value = false
  }
}

async function loadGroups() {
  try {
    const res = await listGroups({ is_active: true })
    groupsList.value = res.data
  } catch (e) {
    console.error('加载分组列表失败', e)
  }
}

async function loadSpecialties() {
  try {
    const res = await listSpecialties({ is_active: true, include_temporary: true })
    specialtiesList.value = res.data
  } catch (e) {
    console.error('加载专项列表失败', e)
  }
}

function buildPersonTree(flatItems) {
  // 层级: 部门 → 小组 → 人员(三级, 无小组入"未分组"
  const deptMap = new Map()
  const _deptOrder = []
  const _groupOrder = new Map()  // deptKey → [groupKeys 有序
  for (const p of flatItems) {
    const deptKey = p.department || '未分配'
    const groupKey = p.group_name || '未分组'
    if (!deptMap.has(deptKey)) {
      const deptNode = {
        id: `D_${deptKey}`,
        _nodeType: 'dept',
        display_name: deptKey,
        department: deptKey === '未分配' ? '' : deptKey,
        group_name: '',
        role: '',
        employment_status: '',
        is_active: true,
        _personCount: 0,
        children: [],
      }
      deptMap.set(deptKey, deptNode)
      _deptOrder.push(deptKey)
      _groupOrder.set(deptKey, [])
    }
    const deptNode = deptMap.get(deptKey)
    // 找或建 group 子节点
    let groupNode = deptNode.children.find(c => c._groupKey === groupKey)
    if (!groupNode) {
      groupNode = {
        id: `G_${deptKey}_${groupKey}`,
        _nodeType: 'group',
        _groupKey: groupKey,
        display_name: groupKey,
        department: deptKey === '未分配' ? '' : deptKey,
        group_name: groupKey === '未分组' ? '' : groupKey,
        role: '',
        employment_status: '',
        is_active: true,
        _personCount: 0,
        children: [],
      }
      deptNode.children.push(groupNode)
      _groupOrder.get(deptKey).push(groupKey)
    }
    // 人员打标后加入 group
    const personNode = { ...p, _nodeType: 'person' }
    groupNode.children.push(personNode)
    groupNode._personCount += 1
    deptNode._personCount += 1
  }
  // 部门排序: 未分配放末尾; 小组内人员按原顺序保留(flat已按sort排序)
  _deptOrder.sort((a, b) => {
    if (a === '未分配') return 1
    if (b === '未分配') return -1
    return a.localeCompare(b, 'zh-CN')
  })
  const tree = _deptOrder.map(k => {
    const n = deptMap.get(k)
    // 小组排序: 未分组放末尾
    n.children.sort((a, b) => {
      const ak = a._groupKey, bk = b._groupKey
      if (ak === '未分组') return 1
      if (bk === '未分组') return -1
      return ak.localeCompare(bk, 'zh-CN')
    })
    return n
  })
  const nDept = tree.length
  const nGroup = tree.reduce((s, d) => s + d.children.length, 0)
  return { tree, nDept, nGroup }
}

function isPersonRow(row) { return row && row._nodeType === 'person' }
function onRowClick(row) { if (isPersonRow(row)) selectPerson(row) }

function onStatusChange() { page.value = 1; loadData() }

function onSortChange({ prop, order }) {
  if (order) {
    sort.sort_by = prop === 'display_name' ? 'display_name' : (prop || 'id')
    sort.sort_order = order === 'ascending' ? 'asc' : 'desc'
  } else {
    sort.sort_by = 'id'; sort.sort_order = 'desc'
  }
  loadData()
}

function onFilterChange(filters) {
  if (filters.department !== undefined) filter.department = filters.department[0] || ''
  if (filters.role !== undefined) filter.role = filters.role[0] || ''
  if (filters.employment_status !== undefined) filter.employment_status = filters.employment_status[0] || ''
  loadData()
}

async function loadUnmerged() {
  const res = await listUnmergedAliases()
  unmergedAliases.value = res.data
}

function onAliasSelect(rows) { selectedAliasIds.value = rows.map((r) => r.id) }
function onPersonSelect(rows) {
  // 只选真实人员(id为数字),排除部门/小组虚拟节点
  selectedPersonIds.value = rows.filter(r => r && r._nodeType === 'person').map(r => r.id)
}
function onDetailAliasSelect(rows) { selectedDetailAliasIds.value = rows.map((r) => r.id) }

// 选中人员 → 右栏显示 + 填充 editForm
async function selectPerson(row) {
  if (!row) return
  currentPerson.value = row
  currentDetail.value = null
  assignments.value = []
  personLinks.value = { perf: null, schedules: [] }
  const res = await getPerson(row.id)
  currentDetail.value = res.data
  detailSkills.value = (res.data.skill_tags || []).map(s => ({ ...s }))
  fillEditForm(res.data)
  loadAssignments(row.id)
  if (row._nodeType === 'person' || !row._nodeType) loadPersonLinks(res.data)
}

function fillEditForm(d) {
  editForm.display_name = d.display_name || ''
  editForm.department = d.department || ''
  editForm.role = d.role || 'agent'
  editForm.employment_status = d.employment_status || 'active'
  editForm.hire_date = d.hire_date || ''
  editForm.probation_end_date = d.probation_end_date || ''
  editForm.regularization_date = d.regularization_date || ''
  editForm.resignation_date = d.resignation_date || ''
  editForm.remark = d.remark || ''
  editForm.is_active = d.is_active
}

async function loadAssignments(personId) {
  assignmentsLoading.value = true
  try {
    const res = await listAssignments(personId)
    assignments.value = res.data
  } finally {
    assignmentsLoading.value = false
  }
}

// 保存 inline 编辑
async function handleSavePerson() {
  if (!currentPerson.value) return
  if (!editForm.display_name.trim()) { ElMessage.warning('请填写显示名'); return }
  submitting.value = true
  try {
    await updatePerson(currentPerson.value.id, {
      display_name: editForm.display_name.trim(),
      department: editForm.department || null,
      role: editForm.role,
      employment_status: editForm.employment_status,
      hire_date: editForm.hire_date || null,
      probation_end_date: editForm.probation_end_date || null,
      regularization_date: editForm.regularization_date || null,
      resignation_date: editForm.resignation_date || null,
      remark: editForm.remark || null,
      is_active: editForm.is_active,
    })
    ElMessage.success('已保存')
    await loadData()
    const res = await getPerson(currentPerson.value.id)
    currentDetail.value = res.data
    fillEditForm(res.data)
  } finally {
    submitting.value = false
  }
}

async function confirmPerson(row) {
  await updatePerson(row.id, { is_confirmed: true })
  ElMessage.success('已确认归并')
  await loadData()
  if (currentPerson.value?.id === row.id) {
    const res = await getPerson(row.id)
    currentDetail.value = res.data
  }
}

async function toggleActive(row) {
  await updatePerson(row.id, { is_active: !row.is_active })
  ElMessage.success(row.is_active ? '已停用' : '已启用')
  await loadData()
  if (currentPerson.value?.id === row.id) {
    const res = await getPerson(row.id)
    currentDetail.value = res.data
    fillEditForm(res.data)
  }
}

// 跳转人员审计时序页
function goTimeline(row) {
  router.push({ name: 'AuditPerson', query: { person_id: row.id, name: row.display_name } })
}

// 归并到新建
function openMergeToNew() {
  mergeNewForm.display_name = ''
  mergeNewForm.role = 'agent'
  mergeNewForm.department = '企微'
  mergeNewForm.group_name = ''
  mergeNewVisible.value = true
}
async function handleMergeToNew() {
  if (!mergeNewForm.display_name.trim()) { ElMessage.warning('请填写显示名'); return }
  submitting.value = true
  try {
    await mergeToNew(mergeNewForm.display_name.trim(), selectedAliasIds.value, {
      department: mergeNewForm.department || undefined,
      ...(mergeNewForm.group_name ? { group_name: mergeNewForm.group_name } : {}),
    })
    ElMessage.success('归并成功')
    mergeNewVisible.value = false
    selectedAliasIds.value = []
    loadData(); loadUnmerged()
  } finally { submitting.value = false }
}

async function openMergeToExisting() {
  const res = await listPersons({ page: 1, page_size: 100, status: 'confirmed' })
  confirmedPersons.value = res.data.items
  mergeExistTargetId.value = null
  mergeExistVisible.value = true
}
async function handleMergeToExisting() {
  if (!mergeExistTargetId.value) { ElMessage.warning('请选择目标人员'); return }
  submitting.value = true
  try {
    await mergeAliases(mergeExistTargetId.value, selectedAliasIds.value)
    ElMessage.success('归并成功')
    mergeExistVisible.value = false
    selectedAliasIds.value = []
    loadData(); loadUnmerged()
  } finally { submitting.value = false }
}

async function handleSplit() {
  if (!currentPerson.value) return
  await ElMessageBox.confirm(`确认从 ${currentPerson.value.display_name} 拆出 ${selectedDetailAliasIds.value.length} 个别名?`, '提示', { type: 'warning' })
  submitting.value = true
  try {
    await splitAliases(currentPerson.value.id, selectedDetailAliasIds.value)
    ElMessage.success('拆分成功')
    selectedDetailAliasIds.value = []
    const res = await getPerson(currentPerson.value.id)
    currentDetail.value = res.data
    loadData()
  } finally { submitting.value = false }
}

async function loadSuggestions() {
  const res = await listMergeSuggestions()
  suggestions.value = res.data
  suggestionsVisible.value = true
}
async function quickMerge(group) {
  const aliasIds = group.aliases.map((a) => a.id)
  const displayName = group.aliases[0].alias
  await ElMessageBox.confirm(`将 ${aliasIds.length} 个别名归并为 "${displayName}"?`, '一键归并', { type: 'warning' })
  submitting.value = true
  try {
    await mergeToNew(displayName, aliasIds)
    ElMessage.success('归并成功')
    const res = await listMergeSuggestions()
    suggestions.value = res.data
    loadData(); loadUnmerged()
  } finally { submitting.value = false }
}

async function handleRefresh() {
  refreshing.value = true
  try {
    const res = await refreshAliases()
    ElMessage.success(`提取完成: ${JSON.stringify(res.data)}`)
    loadData(); loadUnmerged()
  } finally { refreshing.value = false }
}

async function handleRebuild() {
  await ElMessageBox.confirm(
    '将清空所有「待归并别名」(person_id 为空,含历史脏残留),并用最新归一化规则从当前数据全量重新提取。\n已归并到 HR 的别名不受影响(仅更新其归一化值)。\n\n确认重建?',
    '全量重建别名',
    { type: 'warning', confirmButtonText: '确认重建', cancelButtonText: '取消' }
  )
  rebuilding.value = true
  try {
    const res = await rebuildAliases()
    const d = res.data
    ElMessage.success(`重建完成: 新别名 ${d.new_aliases} / 系统别名 ${d.system_aliases} / 跳过 ${d.skipped} / 重算归一化 ${d.re_normalized} / 清理脏残留 ${d.deleted_stale}`)
    loadData(); loadUnmerged()
  } finally { rebuilding.value = false }
}

async function handleBatchImportShop() {
  try {
    await ElMessageBox.confirm('将扫描待归并的「店铺-XXX」别名,按姓名去重建 Person 并归入「店铺」。\n\n确认纳入?', '批量纳入店铺客服', { type: 'warning', confirmButtonText: '确认纳入', cancelButtonText: '取消' })
  } catch { return }
  importingShop.value = true
  try {
    const res = await batchImportByPrefix('店铺', '店铺')
    const d = res.data
    ElMessage.success(`纳入完成: 新建 ${d.created} 人 / 归并到已有 ${d.merged_to_existing} / 别名归并 ${d.aliases_assigned}`)
    loadData(); loadUnmerged()
  } finally { importingShop.value = false }
}

async function handleBatchImportQw() {
  try {
    await ElMessageBox.confirm('将扫描待归并的「企微-XXX」别名,按姓名去重建 Person 并归入「企微」。\n\n确认纳入?', '批量纳入企微客服', { type: 'warning', confirmButtonText: '确认纳入', cancelButtonText: '取消' })
  } catch { return }
  importingQw.value = true
  try {
    const res = await batchImportByPrefix('企微', '企微')
    const d = res.data
    ElMessage.success(`纳入完成: 新建 ${d.created} 人 / 归并到已有 ${d.merged_to_existing} / 别名归并 ${d.aliases_assigned}`)
    loadData(); loadUnmerged()
  } finally { importingQw.value = false }
}

async function handleBatchImportRnd() {
  try {
    await ElMessageBox.confirm('将扫描待归并的「研发-XXX」别名,按姓名去重建 Person 并归入「研发」部门。\n\n确认纳入?', '批量纳入研发', { type: 'warning', confirmButtonText: '确认纳入', cancelButtonText: '取消' })
  } catch { return }
  importingRnd.value = true
  try {
    const res = await batchImportByPrefix('研发', '研发')
    const d = res.data
    ElMessage.success(`纳入完成: 新建 ${d.created} 人 / 归并到已有 ${d.merged_to_existing} / 别名归并 ${d.aliases_assigned}`)
    loadData(); loadUnmerged()
  } finally { importingRnd.value = false }
}

async function mergeAliasToSuggested(row) {
  if (!row.suggested_hr_person_id) return
  try {
    await ElMessageBox.confirm(`将别名「${row.alias}」按拼音首字母归并到 HR 人员「${row.suggested_hr_person_name}」?`, '首字母匹配归并', { type: 'warning', confirmButtonText: '确认归并', cancelButtonText: '取消' })
  } catch { return }
  mergingAliasId.value = row.id
  try {
    await mergeAliases(row.suggested_hr_person_id, [row.id])
    ElMessage.success(`已归并到「${row.suggested_hr_person_name}」`)
    loadUnmerged(); loadData()
  } finally { mergingAliasId.value = null }
}

async function handleBatchMerge() {
  const selected = selectedAliasIds.value
  const candidates = selected.length
    ? unmergedAliases.value.filter(a => selected.includes(a.id) && a.initial_match_type === 'unique')
    : unmergedAliases.value.filter(a => a.initial_match_type === 'unique')
  if (!candidates.length) { ElMessage.warning('没有可按建议归并的别名(需有唯一建议)'); return }
  const byTarget = {}
  candidates.forEach(a => { byTarget[a.suggested_hr_person_name] = (byTarget[a.suggested_hr_person_name] || 0) + 1 })
  const summary = Object.entries(byTarget).map(([n, c]) => `${n}(${c})`).join('、')
  try {
    await ElMessageBox.confirm(`将按系统建议归并 ${candidates.length} 个别名到对应 HR:\n${summary}\n\n歧义/无建议的将跳过。`, '批量按建议归并', { type: 'warning', confirmButtonText: '确认批量归并', cancelButtonText: '取消' })
  } catch { return }
  batchMerging.value = true
  try {
    const res = await batchMergeBySuggestion(selected)
    batchResult.value = res.data
    batchResultVisible.value = true
    loadUnmerged(); loadData()
  } finally { batchMerging.value = false }
}

// 批量按归一名(alias_normalized)新建人员 — 用于无建议目标的别名一键归一
async function handleBatchCreateByNormalized() {
  const selected = selectedAliasIds.value
  if (!selected.length) { ElMessage.warning('请先选择要归一的别名'); return }
  const candidates = unmergedAliases.value.filter(a => selected.includes(a.id))
  if (!candidates.length) { ElMessage.warning('未找到选中的别名'); return }
  // 按 alias_normalized 分组预览
  const byNorm = {}
  candidates.forEach(a => {
    const k = a.alias_normalized || a.alias
    if (!byNorm[k]) byNorm[k] = { count: 0, sample: a.alias }
    byNorm[k].count += 1
  })
  const groups = Object.entries(byNorm).map(([name, info]) => ({ name, count: info.count, sample: info.sample }))
  const preview = groups.map(g => g.name === g.sample ? `${g.name}(${g.count})` : `${g.name}(${g.count},如:${g.sample})`).join('、')
  try {
    await ElMessageBox.confirm(
      `将按归一名新建 ${groups.length} 个 Person,归并 ${candidates.length} 个别名:\n${preview}\n\n` +
      `· display_name = 归一名(alias_normalized),部门=空,角色=客服,已确认\n` +
      `· "用户提现"等系统操作类型 alias_normalized 与原值相同,建 Person 名即原名(用户原意"使用原先的")\n` +
      `· 若已有同名 Person(忽略大小写)则归并到该 Person,不重复建`,
      '按归一名批量新建',
      { type: 'warning', confirmButtonText: '确认批量归一', cancelButtonText: '取消' }
    )
  } catch { return }
  batchCreating.value = true
  try {
    const res = await batchCreateByNormalized(selected)
    const d = res.data
    ElMessage.success(`新建 ${d.created} 人 / 归并到已有 ${d.merged_to_existing} 人 / 别名归并 ${d.aliases_assigned} 条`)
    selectedAliasIds.value = []
    loadData(); loadUnmerged()
  } finally { batchCreating.value = false }
}

// 批量编辑
function openBatchEdit() {
  Object.keys(batchApply).forEach(k => batchApply[k] = false)
  batchForm.department = ''
  batchForm.role = ''
  batchForm.employment_status = ''
  batchForm.is_active = true
  batchForm.is_confirmed = true
  batchForm.hire_date = ''
  batchForm.group_name = ''
  batchForm.specialty = ''
  batchForm.skill_tag = ''
  batchForm.skill_level = 3
  batchForm.skill_mode = 'add'
  batchEditVisible.value = true
}

async function handleBatchUpdate() {
  const payload = { ids: selectedPersonIds.value }
  if (batchApply.department) payload.department = batchForm.department
  if (batchApply.role) payload.role = batchForm.role
  if (batchApply.employment_status) payload.employment_status = batchForm.employment_status
  if (batchApply.is_active) payload.is_active = batchForm.is_active
  if (batchApply.is_confirmed) payload.is_confirmed = batchForm.is_confirmed
  if (batchApply.hire_date) payload.hire_date = batchForm.hire_date
  if (batchApply.group_name) payload.group_name = batchForm.group_name
  if (batchApply.specialty) payload.specialty = batchForm.specialty
  const hasField = Object.keys(payload).some(k => k !== 'ids')
  const hasSkill = batchApply.skill && batchForm.skill_tag
  if (!hasField && !hasSkill) { ElMessage.warning('请勾选至少一个要修改的字段'); return }
  submitting.value = true
  try {
    if (hasField) {
      const res = await batchUpdatePersons(payload)
      const d = res.data
      ElMessage.success(`已更新 ${d.updated} 人,建任职 ${d.assignments_created} 条`)
    }
    if (hasSkill) {
      const res2 = await batchSkills({
        person_ids: selectedPersonIds.value,
        skill_tag: batchForm.skill_tag,
        level: batchForm.skill_level,
        mode: batchForm.skill_mode,
      })
      ElMessage.success(`技能打标:影响 ${res2.data.affected} 人(${batchForm.skill_mode === 'add' ? '添加/覆盖' : '移除'} ${batchForm.skill_tag})`)
    }
    batchEditVisible.value = false
    selectedPersonIds.value = []
    await loadData()
    loadSkillTags()
    if (currentPerson.value) {
      const res3 = await getPerson(currentPerson.value.id)
      currentDetail.value = res3.data
      detailSkills.value = (res3.data.skill_tags || []).map(s => ({ ...s }))
      fillEditForm(res3.data)
      loadAssignments(currentPerson.value.id)
    }
  } finally { submitting.value = false }
}

// ---------- 任职记录 ----------
function openAssignmentDialog(row = null) {
  if (row) {
    assignmentForm.id = row.id
    assignmentForm.effective_from = row.effective_from
    assignmentForm.effective_to = row.effective_to || ''
    assignmentForm.position = row.position || ''
    assignmentForm.specialty = row.specialty || ''
    assignmentForm.group_name = row.group_name || ''
    assignmentForm.change_reason = row.change_reason || ''
  } else {
    assignmentForm.id = null
    assignmentForm.effective_from = ''
    assignmentForm.effective_to = ''
    assignmentForm.position = currentDetail.value?.position || ''
    assignmentForm.specialty = currentDetail.value?.specialty || ''
    assignmentForm.group_name = currentDetail.value?.group_name || ''
    assignmentForm.change_reason = ''
  }
  assignmentDialogVisible.value = true
}

async function handleSubmitAssignment() {
  if (!assignmentForm.effective_from) { ElMessage.warning('请选择生效日期'); return }
  const pid = currentPerson.value.id
  const payload = {
    effective_from: assignmentForm.effective_from,
    effective_to: assignmentForm.effective_to || null,
    position: assignmentForm.position || null,
    specialty: assignmentForm.specialty || null,
    group_name: assignmentForm.group_name || null,
    change_reason: assignmentForm.change_reason || null,
  }
  submitting.value = true
  try {
    if (assignmentForm.id) {
      await updateAssignment(pid, assignmentForm.id, payload)
      ElMessage.success('已更新')
    } else {
      await createAssignment(pid, payload)
      ElMessage.success('已新增')
    }
    assignmentDialogVisible.value = false
    await loadAssignments(pid)
    const res = await getPerson(pid)
    currentDetail.value = res.data
    fillEditForm(res.data)
    loadData()
  } finally { submitting.value = false }
}

async function handleDeleteAssignment(row) {
  if (!currentPerson.value) return
  await ElMessageBox.confirm(`确认删除 ${row.effective_from} 起的任职记录?${row.is_current ? '(当前任职,将自动提升前一条为当前)' : ''}`, '提示', { type: 'warning' })
  submitting.value = true
  try {
    await deleteAssignment(currentPerson.value.id, row.id)
    ElMessage.success('已删除')
    await loadAssignments(currentPerson.value.id)
    const res = await getPerson(currentPerson.value.id)
    currentDetail.value = res.data
    fillEditForm(res.data)
    loadData()
  } finally { submitting.value = false }
}

// ---------- 导入人力快照 ----------
function openImportDialog() { resetImportDialog(); importDialogVisible.value = true }
function resetImportDialog() {
  importStep.value = 0
  importFilename.value = ''
  importStoredPath.value = ''
  importPreview.value = { columns: [], total_rows: 0, detected_encoding: null, sheet_name: null, sheet_names: [], encoding: null }
  importMappingRows.value = []
  importDiff.value = { summary: { total: 0, new: 0, update: 0, skip: 0, conflict: 0 }, rows: [] }
  importResult.value = { success: false, total_rows: 0, inserted_rows: 0, updated_rows: 0, failed_rows: 0, error: '' }
}

async function handleUploadFile(option) {
  importParsing.value = true
  try {
    const fd = new FormData()
    fd.append('data_type', 'staff')
    fd.append('file', option.file)
    const res = await parseUpload(fd)
    const data = res.data
    importFilename.value = data.filename || option.file.name
    importStoredPath.value = data.stored_path
    const sheetNames = data.sheet_names || []
    importPreview.value = {
      columns: data.columns || [],
      total_rows: data.total_rows || 0,
      detected_encoding: data.detected_encoding,
      sheet_name: sheetNames.length ? sheetNames[0] : null,
      sheet_names: sheetNames,
      encoding: data.detected_encoding || null,
    }
    const suggested = data.suggested_mapping || {}
    const allTargets = ['display_name', 'alias_kefu', 'alias_name1', 'alias_name2', 'remark', 'specialty', 'position', 'snapshot_date', 'hire_date', 'probation_end_date', 'regularization_date', 'resignation_date']
    importMappingRows.value = allTargets.map((t) => ({ target_field: t, source_column: suggested[t] || '' }))
    importStep.value = 1
  } catch (e) {
    ElMessage.error('文件解析失败')
  } finally {
    importParsing.value = false
  }
}

function buildColumnMapping() {
  const column_mapping = {}
  for (const row of importMappingRows.value) {
    if (row.source_column) column_mapping[row.target_field] = row.source_column
  }
  return column_mapping
}

async function handlePreviewImport() {
  const column_mapping = buildColumnMapping()
  if (!column_mapping.display_name && !column_mapping.alias_name1 && !column_mapping.alias_name2 && !column_mapping.alias_kefu) {
    ElMessage.warning('至少需要映射 姓名 或 名称1/名称2/客服名 之一'); return
  }
  previewing.value = true
  try {
    const res = await previewSnapshot({ stored_path: importStoredPath.value, filename: importFilename.value, sheet_name: importPreview.value.sheet_name, encoding: importPreview.value.encoding, column_mapping })
    importDiff.value = res.data
    importStep.value = 2
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '预览失败')
  } finally { previewing.value = false }
}

function actionTagType(action) { return { new: 'success', update: 'primary', skip: 'info', conflict: 'danger' }[action] || 'info' }
function actionLabel(action) { return { new: '新建', update: '更新', skip: '跳过', conflict: '冲突' }[action] || action }

async function handleConfirmImport() {
  const column_mapping = buildColumnMapping()
  if (!column_mapping.display_name && !column_mapping.alias_name1 && !column_mapping.alias_name2 && !column_mapping.alias_kefu) {
    ElMessage.warning('至少需要映射 姓名 或 名称1/名称2/客服名 之一'); return
  }
  importing.value = true
  try {
    const res = await importSnapshot({ stored_path: importStoredPath.value, filename: importFilename.value, sheet_name: importPreview.value.sheet_name, encoding: importPreview.value.encoding, column_mapping })
    importResult.value = { success: true, total_rows: res.data.total_rows, inserted_rows: res.data.inserted_rows, updated_rows: res.data.updated_rows, failed_rows: res.data.failed_rows, error: '' }
    importStep.value = 3
    loadData(); loadUnmerged()
  } catch (e) {
    importResult.value = { success: false, total_rows: 0, inserted_rows: 0, updated_rows: 0, failed_rows: 0, error: e?.response?.data?.detail || e?.message || '导入失败' }
    importStep.value = 3
  } finally { importing.value = false }
}

async function handleExport() {
  importing.value = true
  try {
    const res = await exportPersons()
    // 响应拦截器对 blob 成功响应返回 response.data(即 Blob);兼容返回完整 response 的情况
    const blobData = res instanceof Blob ? res : res?.data
    const blob = new Blob([blobData], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
    let fname = '人力资源数据_导出.xlsx'
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
    ElMessage.success('已导出')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '导出失败')
  } finally { importing.value = false }
}

onMounted(() => {
  loadGroups()
  loadSpecialties()
  loadData()
  loadUnmerged()
  loadSkillTags()
})
</script>

<style scoped>
.persons-page :deep(.el-card__body) { padding: 10px; }
.split-layout { display: flex; gap: 10px; align-items: stretch; }
.left-panel { width: 58%; min-width: 480px; display: flex; flex-direction: column; }
.right-panel { flex: 1; min-width: 0; overflow: auto; }
.right-panel :deep(.el-card__body) { padding: 12px; max-height: calc(100vh - 120px); overflow: auto; }
.empty-tip { display: flex; align-items: center; justify-content: center; height: 60vh; }
.detail-header { padding-bottom: 6px; border-bottom: 1px solid #ebeef5; }
.detail-header .flex.gap-8 { gap: 6px !important; }
.detail-content { padding: 0; }
.section-title { font-weight: 600; font-size: 14px; }
.mirror-field { color: #606266; }
.edit-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px 8px; }
.edit-grid :deep(.el-form-item) { margin-bottom: 4px; }
.edit-grid :deep(.el-form-item__label) { padding-bottom: 2px; line-height: 1.2; }
.edit-grid .full-row { grid-column: 1 / -1; }
.alias-collapse :deep(.el-collapse-item__header) { padding: 0 12px; }
.alias-collapse :deep(.el-collapse-item__content) { padding-bottom: 8px; }
/* B3-7 技能/联动区 */
.skill-edit-box { padding: 6px 8px; border: 1px dashed #dcdfe6; border-radius: 4px; margin-bottom: 8px; }
.skill-edit-box :deep(.el-rate) { height: 14px; }
.skill-edit-box :deep(.el-rate__icon) { font-size: 12px; margin-right: 0; }
.link-summary-box { padding: 6px 8px; background: #f5f7fa; border-radius: 4px; min-height: 52px; }
.link-row { display: flex; align-items: baseline; gap: 8px; padding: 3px 0; font-size: 13px; flex-wrap: wrap; }
.link-label { color: #909399; font-size: 12px; flex-shrink: 0; width: 64px; }
</style>
