<template>
  <div class="page">
    <div class="header">
      <div>
        <h2>Code Projects</h2>
        <p>管理 CodeAgent 项目和 Git 仓库配置。</p>
      </div>
      <el-button type="primary" @click="openProject()">新建项目</el-button>
    </div>

    <el-alert
      v-if="errorMessage"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
      class="block"
    />

    <el-table v-loading="loading" :data="projects" stripe>
      <el-table-column prop="name" label="项目" min-width="180" />
      <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
      <el-table-column label="环境" width="190">
        <template #default="{ row }">{{ row.environment_tier }}</template>
      </el-table-column>
      <el-table-column label="状态" width="180">
        <template #default="{ row }">
          <el-tag :type="row.availability?.ready ? 'success' : 'warning'">
            {{ availabilityLabel(row.availability) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="330" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openProject(row)">编辑</el-button>
          <el-button size="small" type="primary" plain @click="openManifest(row)">Manifest</el-button>
          <el-button
            size="small"
            :type="row.enabled ? 'warning' : 'success'"
            plain
            @click="toggleProject(row)"
          >
            {{ row.enabled ? '停用' : '启用' }}
          </el-button>
        </template>
      </el-table-column>
      <template #empty><el-empty description="暂无可访问的 Code Project" /></template>
    </el-table>

    <el-dialog v-model="projectVisible" :title="projectForm.id ? '编辑项目' : '新建项目'" width="620px">
      <el-form label-width="110px">
        <el-form-item label="名称" required><el-input v-model="projectForm.name" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="projectForm.description" type="textarea" /></el-form-item>
        <el-form-item label="环境 tier">
          <el-select v-model="projectForm.environment_tier" style="width:100%">
            <el-option label="内部非生产" value="internal_non_production" />
          </el-select>
        </el-form-item>
        <el-form-item label="可见性">
          <el-radio-group v-model="projectForm.visibility">
            <el-radio value="private">私有</el-radio>
            <el-radio value="public">公共</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="授权用户">
          <el-input v-model="projectForm.allowedUsers" placeholder="用户名以逗号分隔" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="projectVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveProject">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="manifestVisible" size="760px" :title="`Manifest · ${selectedProject?.name || ''}`">
      <el-alert
        :title="availabilityLabel(selectedProject?.availability)"
        :type="selectedProject?.availability?.ready ? 'success' : 'warning'"
        show-icon
        :closable="false"
        class="block"
      />
      <div v-if="!draft && history.length" class="toolbar">
        <el-button type="primary" plain @click="startRevision">基于当前版本创建修订</el-button>
      </div>

      <el-form label-position="top" class="manifest-form">
        <section>
          <h3>Git 仓库</h3>
          <div class="section-hint">Manifest 只保存 Git 连接信息。Sandbox、模型、Skill、MCP 和策略在 CodeAgent 编辑页配置。</div>
          <el-form-item label="来源类型" :error="fieldError('source_type')">
            <el-select v-model="manifestForm.source_type" clearable style="width:100%">
              <el-option
                v-for="sourceType in manifestOptions.source_types"
                :key="sourceType"
                :label="sourceType"
                :value="sourceType"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="仓库位置" :error="fieldError('source_locator')">
            <el-input
              v-model="manifestForm.source_locator"
              placeholder="Git 仓库地址，例如 http://g.example.com/group/repo.git"
            />
            <div class="form-hint">{{ sourcePolicyHint }}</div>
          </el-form-item>
          <el-form-item label="只读凭据引用" :error="fieldError('credential_ref')">
            <el-select v-model="manifestForm.credential_ref" clearable style="width:100%">
              <el-option
                v-for="credential in manifestOptions.credential_references"
                :key="credential.reference_id"
                :label="`${credential.label} · ${credential.reference_id}`"
                :value="credential.reference_id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="新增凭据名称">
            <el-input
              v-model="manifestForm.credential_label"
              placeholder="可选；默认使用项目名生成"
            />
          </el-form-item>
          <el-form-item label="Git 账号名" :error="fieldError('credential_username')">
            <el-input
              v-model="manifestForm.credential_username"
              autocomplete="username"
              placeholder="用于 Git HTTP Basic 认证"
            />
          </el-form-item>
          <el-form-item label="Git 密码/Token" :error="fieldError('credential_password')">
            <el-input
              v-model="manifestForm.credential_password"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="不会回显；保存后仅保留凭据引用"
            />
            <div class="form-hint">填写账号名和密码/Token 后，保存草稿会创建只读凭据并自动绑定到当前 Manifest。</div>
          </el-form-item>
          <el-form-item label="请求 ref" :error="fieldError('requested_ref')">
            <el-input v-model="manifestForm.requested_ref" placeholder="例如 main、release/v1 或 commit SHA" />
          </el-form-item>
        </section>

      </el-form>

      <div class="toolbar">
        <el-button :loading="saving" @click="saveDraft">保存草稿</el-button>
        <el-button
          type="success"
          :disabled="!draft || hasValidationErrors"
          :loading="publishing"
          @click="publishDraft"
        >发布 Git 配置</el-button>
      </div>

      <el-divider>已发布历史</el-divider>
      <el-collapse v-if="history.length">
        <el-collapse-item v-for="item in history" :key="item.id" :name="item.id">
          <template #title>v{{ item.version }} · {{ item.published_at || '已发布' }}</template>
          <div class="manifest-summary">
            <div>
              <span>来源类型</span>
              <code>{{ item.source_type || '-' }}</code>
            </div>
            <div>
              <span>仓库地址</span>
              <code>{{ item.source_locator || item.repository || '-' }}</code>
            </div>
            <div>
              <span>凭据引用</span>
              <code>{{ item.credential_ref || '无' }}</code>
            </div>
            <div>
              <span>请求 ref</span>
              <code>{{ item.requested_ref || '-' }}</code>
            </div>
            <div>
              <span>发布时间</span>
              <code>{{ item.published_at || '-' }}</code>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
      <el-empty v-else description="暂无已发布版本" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getCgi, postCgi } from '../api'

const projects = ref([])
const loading = ref(false)
const saving = ref(false)
const publishing = ref(false)
const errorMessage = ref('')
const projectVisible = ref(false)
const manifestVisible = ref(false)
const selectedProject = ref(null)
const draft = ref(null)
const history = ref([])
const manifestOptions = ref({
  source_types: [], remote_origins: [], local_roots: [],
  credential_references: [],
})

const projectForm = reactive({
  id: '', name: '', description: '', environment_tier: 'internal_non_production',
  visibility: 'private', allowedUsers: '',
})
const manifestForm = reactive({
  source_type: '', source_locator: '', credential_ref: '', requested_ref: '',
  credential_label: '', credential_username: '', credential_password: '',
})
const jsonErrors = reactive({})
const selectionErrors = reactive({})

const hasValidationErrors = computed(() =>
  Object.keys(draft.value?.validation_errors || {}).length > 0
  || Object.values(jsonErrors).some(Boolean)
)

const sourcePolicyHint = computed(() => {
  if (manifestForm.source_type === 'local') {
    return manifestOptions.value.local_roots.length
      ? `允许的本地根：${manifestOptions.value.local_roots.join('、')}`
      : '没有已批准的本地仓库根'
  }
  return manifestOptions.value.remote_origins.length
    ? `允许的远程 origin：${manifestOptions.value.remote_origins.join('、')}`
    : '没有已批准的远程 origin'
})

function availabilityLabel(value) {
  const labels = {
    ready: '已就绪', project_disabled: '项目已停用',
    project_environment_not_allowed: '环境不支持', manifest_missing: '尚未发布 Manifest',
    manifest_invalid: '已发布 Manifest 无效',
  }
  return labels[value?.reason] || value?.reason || '状态未知'
}

function fieldError(field) {
  return jsonErrors[field] || selectionErrors[field]
    || draft.value?.validation_errors?.[field] || ''
}

function lines(value) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function fillManifest(value = {}) {
  Object.keys(jsonErrors).forEach((key) => delete jsonErrors[key])
  Object.keys(selectionErrors).forEach((key) => delete selectionErrors[key])
  Object.assign(manifestForm, {
    source_type: value.source_type || '',
    source_locator: value.source_locator || value.repository || '',
    credential_ref: value.credential_ref || '',
    credential_label: '',
    credential_username: '',
    credential_password: '',
    requested_ref: value.requested_ref || value.base_commit || '',
  })
}

async function loadProjects() {
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await getCgi('/pages/page_code_project.cgi', { action: 'list' })
    projects.value = res.data || []
  } catch (error) {
    errorMessage.value = error?.message || '加载 Code Projects 失败'
  } finally {
    loading.value = false
  }
}

function openProject(row) {
  Object.assign(projectForm, {
    id: row?.id || '', name: row?.name || '', description: row?.description || '',
    environment_tier: row?.environment_tier || 'internal_non_production',
    visibility: row?.visibility || 'private',
    allowedUsers: (row?.allowed_users || []).join(','),
  })
  projectVisible.value = true
}

async function saveProject() {
  saving.value = true
  try {
    await postCgi('/pages/page_code_project.cgi', {
      action: projectForm.id ? 'update' : 'create', id: projectForm.id || undefined,
      name: projectForm.name, description: projectForm.description,
      environment_tier: projectForm.environment_tier, visibility: projectForm.visibility,
      allowed_users: lines(projectForm.allowedUsers.replaceAll(',', '\n')),
    })
    projectVisible.value = false
    ElMessage.success('项目已保存')
    await loadProjects()
  } finally { saving.value = false }
}

async function toggleProject(row) {
  await ElMessageBox.confirm(`确认${row.enabled ? '停用' : '启用'}项目 ${row.name}？`, '项目状态')
  await postCgi('/pages/page_code_project.cgi', {
    action: 'set_enabled', id: row.id, enabled: !row.enabled,
  })
  await loadProjects()
}

async function openManifest(row) {
  selectedProject.value = row
  const [draftRes, historyRes, optionsRes] = await Promise.all([
    getCgi('/pages/page_code_project.cgi', { action: 'get_draft', id: row.id }),
    getCgi('/pages/page_code_project.cgi', { action: 'history', id: row.id }),
    getCgi('/pages/page_code_project.cgi', { action: 'manifest_options', id: row.id }),
  ])
  draft.value = draftRes.data || null
  history.value = historyRes.data || []
  manifestOptions.value = optionsRes.data
  fillManifest(draft.value || history.value[0] || {})
  manifestVisible.value = true
}

function startRevision() {
  draft.value = null
  fillManifest(history.value[0] || {})
}

function manifestPayload() {
  const payload = {
    action: 'save_draft', project_id: selectedProject.value.id,
    manifest_id: draft.value?.id || undefined,
    source_type: manifestForm.source_type,
    source_locator: manifestForm.source_locator,
    credential_ref: manifestForm.credential_ref,
    requested_ref: manifestForm.requested_ref,
  }
  if (
    manifestForm.credential_label.trim()
    || manifestForm.credential_username.trim()
    || manifestForm.credential_password
  ) {
    payload.credential_label = manifestForm.credential_label
    payload.credential_username = manifestForm.credential_username
    payload.credential_password = manifestForm.credential_password
  }
  return payload
}

async function saveDraft() {
  const payload = manifestPayload()
  if (!payload) {
    ElMessage.warning('请先修正 Manifest JSON 字段')
    return
  }
  saving.value = true
  try {
    Object.keys(selectionErrors).forEach((key) => delete selectionErrors[key])
    const res = await postCgi('/pages/page_code_project.cgi', payload)
    draft.value = res.data
    fillManifest(draft.value)
    const optionsRes = await getCgi('/pages/page_code_project.cgi', {
      action: 'manifest_options', id: selectedProject.value.id,
    })
    manifestOptions.value = optionsRes.data
    ElMessage.success('草稿已保存')
  } catch (error) {
    Object.assign(selectionErrors, error?.data?.field_errors || {})
  } finally { saving.value = false }
}

async function publishDraft() {
  await ElMessageBox.confirm('发布后该 Git 配置版本不可修改，确认继续？', '发布 Git 配置')
  publishing.value = true
  try {
    await postCgi('/pages/page_code_project.cgi', {
      action: 'publish', project_id: selectedProject.value.id, manifest_id: draft.value.id,
    })
    ElMessage.success('Git 配置已发布')
    manifestVisible.value = false
    await loadProjects()
  } finally { publishing.value = false }
}

onMounted(loadProjects)
</script>

<style scoped>
.page { padding: 4px; }
.header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 18px; }
.header h2 { margin: 0 0 6px; }
.header p { margin: 0; color: var(--gap-text-muted); }
.block { margin-bottom: 16px; }
.manifest-form section { border: 1px solid var(--gap-border); border-radius: 8px; padding: 4px 16px 12px; margin-bottom: 16px; }
.manifest-form h3 { margin-bottom: 10px; }
.section-hint { margin: -4px 0 14px; color: var(--gap-text-muted); font-size: 13px; }
.toolbar { display: flex; gap: 10px; margin: 14px 0; }
.form-hint { margin-top: 6px; color: var(--gap-text-muted); font-size: 12px; word-break: break-all; }
.manifest-summary { display: grid; gap: 10px; padding: 4px 0 8px; }
.manifest-summary div { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 10px; align-items: start; }
.manifest-summary span { color: var(--gap-text-muted); }
.manifest-summary code { white-space: pre-wrap; word-break: break-all; }
</style>
