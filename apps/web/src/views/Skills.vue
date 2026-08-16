<template>
  <div class="page">
    <div class="header">
      <el-radio-group v-model="scope" @change="load">
        <el-radio-button value="all">所有</el-radio-button>
        <el-radio-button value="mine">我的</el-radio-button>
      </el-radio-group>
      <div class="header-actions">
        <el-input
          v-model="keyword"
          placeholder="搜索 Skill 名称或描述..."
          clearable
          style="width:260px"
          @keyup.enter="doSearch"
        />
        <el-button type="primary" @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button type="primary" @click="openUpload">上传 Skill</el-button>
      </div>
    </div>

    <div class="summary">共 {{ list.length }} 条，已显示 {{ filtered.length }} 条</div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">
            <el-icon class="skill-icon"><Promotion /></el-icon>
            {{ row.name }}
          </span>
          <el-tag size="small" :type="row.visibility === 'public' ? 'success' : 'info'" effect="plain">
            {{ row.visibility === 'public' ? '公共' : '私有' }}
          </el-tag>
        </div>
        <div class="card-meta">
          <div><span class="label">ID</span> {{ row.id }}</div>
          <div><span class="label">创建人</span> {{ row.creator || '-' }}</div>
          <div><span class="label">时间</span> {{ row.modified_at || '-' }}</div>
        </div>
        <div v-if="row.zip_name" class="zip-row">
          <el-icon><FolderOpened /></el-icon>
          <span>{{ row.zip_name }}</span>
        </div>
        <div v-if="row.description" class="card-desc">{{ row.description }}</div>
        <div v-if="tagList(row.tags).length" class="card-tags">
          <el-tag v-for="tag in tagList(row.tags)" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
        </div>
        <div class="card-actions">
          <el-button
            v-if="canManage(row)"
            size="small"
            type="success"
            plain
            @click="openEdit(row)"
          >
            编辑
          </el-button>
          <el-button size="small" type="warning" plain @click="viewSkill(row)">
            <el-icon><Document /></el-icon>
            查看
          </el-button>
          <el-button size="small" type="primary" plain :loading="downloadingId === row.id" @click="downloadSkill(row)">
            <el-icon><Download /></el-icon>
            下载
          </el-button>
          <el-button
            v-if="canManage(row)"
            size="small"
            type="danger"
            link
            @click="onDelete(row)"
          >
            删除
          </el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !filtered.length" description="暂无 Skill" />
    </div>

    <el-dialog v-model="uploadVisible" title="上传 Skill" width="560px" destroy-on-close @closed="resetUploadForm">
      <el-form :model="uploadForm" label-width="100px">
        <el-form-item label="Skill 名称" required>
          <el-input v-model="uploadForm.name" placeholder="请输入 Skill 名称" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="uploadForm.tags" placeholder="多个标签用逗号分隔，如：PDF,文档处理" />
        </el-form-item>
        <el-form-item label="描述" required>
          <el-input
            v-model="uploadForm.description"
            type="textarea"
            :rows="4"
            placeholder="请详细描述 Skill 的功能"
          />
        </el-form-item>
        <el-form-item label="上传文件" required>
          <el-upload
            drag
            :auto-upload="false"
            :limit="1"
            accept=".zip"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="upload-text">点击选择或拖拽 Skill 压缩包 (.zip)</div>
          </el-upload>
        </el-form-item>
        <el-form-item label="权限管理">
          <el-radio-group v-model="uploadForm.visibility">
            <el-radio value="public">公共</el-radio>
            <el-radio value="private">私有</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="uploadForm.visibility === 'private'" label="授权用户">
          <el-input
            v-model="uploadForm.allowedUsersStr"
            placeholder="如：user1,user2,user3"
          />
          <div class="field-hint">输入允许访问的用户名，多个用逗号分隔，不填写仅本人可见</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="editVisible"
      title="编辑 Skill"
      width="560px"
      destroy-on-close
      @closed="resetEditForm"
    >
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="Skill 名称" required>
          <el-input v-model="editForm.name" placeholder="请输入 Skill 名称" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="editForm.tags" placeholder="多个标签用逗号分隔" />
        </el-form-item>
        <el-form-item label="描述" required>
          <el-input v-model="editForm.description" type="textarea" :rows="4" placeholder="Skill 功能描述" />
        </el-form-item>
        <el-form-item label="权限管理">
          <el-radio-group v-model="editForm.visibility">
            <el-radio value="public">公共</el-radio>
            <el-radio value="private">私有</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="editForm.visibility === 'private'" label="授权用户">
          <el-input v-model="editForm.allowedUsersStr" placeholder="如：user1,user2" />
          <div class="field-hint">多个用户名用逗号分隔；留空则仅创建人可见</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="viewVisible"
      :title="viewTitle"
      width="80%"
      top="5vh"
      destroy-on-close
      class="skill-view-dialog"
    >
      <div v-loading="viewLoading" class="view-wrap">
        <div v-if="viewContent" class="md-body" v-html="markdownHtml" />
        <el-empty v-else-if="!viewLoading" description="暂无 SKILL.md 内容" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Promotion,
  FolderOpened,
  Document,
  Download,
  UploadFilled,
} from '@element-plus/icons-vue'
import { marked } from 'marked'
import { getCgi, postCgi } from '../api'
import { getCurrentUsername, getShell } from '../session'

marked.setOptions({ breaks: true, gfm: true })

const list = ref([])
const loading = ref(false)
const saving = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const currentUser = ref('')
const roles = ref([])
const uploadVisible = ref(false)
const editVisible = ref(false)
const uploading = ref(false)
const uploadFile = ref(null)
const downloadingId = ref('')
const viewVisible = ref(false)
const viewLoading = ref(false)
const viewContent = ref('')
const viewTitle = ref('Skill 详情')

const uploadForm = reactive({
  name: '',
  tags: '',
  description: '',
  visibility: 'private',
  allowedUsersStr: '',
})

const editForm = reactive({
  id: '',
  name: '',
  tags: '',
  description: '',
  visibility: 'private',
  allowedUsersStr: '',
})

const filtered = computed(() => {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((r) =>
    r.name?.toLowerCase().includes(kw) ||
    r.description?.toLowerCase().includes(kw) ||
    r.tags?.toLowerCase().includes(kw) ||
    r.id?.toLowerCase().includes(kw)
  )
})

const markdownHtml = computed(() => marked.parse(viewContent.value || ''))

function tagList(tags) {
  if (!tags) return []
  return tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
}

async function loadCurrentUser() {
  currentUser.value = getCurrentUsername()
  roles.value = getShell()?.roles || []
}

function canManage(row) {
  if (roles.value.includes('master') || roles.value.includes('admin')) return true
  return row.creator === currentUser.value
}

function openEdit(row) {
  Object.assign(editForm, {
    id: row.id,
    name: row.name || '',
    tags: row.tags || '',
    description: row.description || '',
    visibility: row.visibility || 'private',
    allowedUsersStr: (row.allowed_users || []).join(','),
  })
  editVisible.value = true
}

function resetEditForm() {
  Object.assign(editForm, {
    id: '',
    name: '',
    tags: '',
    description: '',
    visibility: 'private',
    allowedUsersStr: '',
  })
}

async function submitEdit() {
  if (!editForm.name?.trim()) {
    ElMessage.warning('请填写 Skill 名称')
    return
  }
  if (!editForm.description?.trim()) {
    ElMessage.warning('请填写描述')
    return
  }
  const allowed_users = editForm.allowedUsersStr
    ? editForm.allowedUsersStr.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    : []
  saving.value = true
  try {
    await postCgi('/pages/page_skills.cgi', {
      action: 'update',
      id: editForm.id,
      name: editForm.name.trim(),
      tags: editForm.tags.trim(),
      description: editForm.description.trim(),
      visibility: editForm.visibility,
      allowed_users,
    })
    ElMessage.success('保存成功')
    editVisible.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_skills.cgi', { action: 'list', scope: scope.value })
    list.value = res.data || []
  } finally {
    loading.value = false
  }
}

function doSearch() {
  searchKw.value = keyword.value
}

function resetSearch() {
  keyword.value = ''
  searchKw.value = ''
}

function openUpload() {
  resetUploadForm()
  uploadVisible.value = true
}

function resetUploadForm() {
  uploadForm.name = ''
  uploadForm.tags = ''
  uploadForm.description = ''
  uploadForm.visibility = 'private'
  uploadForm.allowedUsersStr = ''
  uploadFile.value = null
}

function onFileChange(file) {
  uploadFile.value = file.raw
  if (!uploadForm.name && file.name) {
    uploadForm.name = file.name.replace(/\.zip$/i, '')
  }
}

function onFileRemove() {
  uploadFile.value = null
}

async function submitUpload() {
  if (!uploadForm.name?.trim()) {
    ElMessage.warning('请填写 Skill 名称')
    return
  }
  if (!uploadForm.description?.trim()) {
    ElMessage.warning('请填写描述')
    return
  }
  if (!uploadFile.value) {
    ElMessage.warning('请上传 Skill 压缩包')
    return
  }
  const allowedUsers = uploadForm.allowedUsersStr
    ? uploadForm.allowedUsersStr.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    : []
  const fd = new FormData()
  fd.append('action', 'upload')
  fd.append('name', uploadForm.name.trim())
  fd.append('tags', uploadForm.tags.trim())
  fd.append('description', uploadForm.description.trim())
  fd.append('visibility', uploadForm.visibility)
  fd.append('allowed_users', JSON.stringify(allowedUsers))
  fd.append('file', uploadFile.value)
  uploading.value = true
  try {
    const res = await fetch('/pages/page_skills.cgi', { method: 'POST', body: fd, credentials: 'include' })
    const data = await res.json()
    if (data.code !== 0) {
      ElMessage.error(data.msg || '上传失败')
      return
    }
    ElMessage.success('上传成功')
    uploadVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function viewSkill(row) {
  viewTitle.value = row.name
  viewContent.value = ''
  viewVisible.value = true
  viewLoading.value = true
  try {
    const res = await getCgi('/pages/page_skills.cgi', { action: 'view_md', id: row.id })
    viewContent.value = res.data?.content || ''
  } finally {
    viewLoading.value = false
  }
}

async function downloadSkill(row) {
  downloadingId.value = row.id
  try {
    const res = await fetch(
      `/pages/page_skills.cgi?action=download&id=${encodeURIComponent(row.id)}`,
      { credentials: 'include' }
    )
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.msg || err.detail || '下载失败')
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = row.zip_name || `${row.name}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e.message || '下载失败')
  } finally {
    downloadingId.value = ''
  }
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除 Skill「${row.name}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/page_skills.cgi', { action: 'delete', id: row.id })
  ElMessage.success('删除成功')
  load()
}

onMounted(async () => {
  await loadCurrentUser()
  await load()
})
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px; min-height: 400px; }
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
.header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.summary { font-size: 13px; color: #909399; margin-bottom: 16px; }
.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 1200px) { .card-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 768px) { .card-grid { grid-template-columns: 1fr; } }
.card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px;
  transition: box-shadow 0.2s;
  display: flex;
  flex-direction: column;
}
.card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.card-head {
  margin-bottom: 10px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 8px;
}
.skill-icon { color: #67c23a; font-size: 18px; }
.card-meta { font-size: 13px; color: #606266; line-height: 1.8; margin-bottom: 8px; }
.card-meta .label { color: #909399; margin-right: 6px; }
.zip-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #606266;
  margin-bottom: 10px;
}
.card-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 10px;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; flex: 1; }
.card-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  align-items: center;
  border-top: 1px solid #f0f0f0;
  padding-top: 12px;
  margin-top: auto;
}
.upload-icon { font-size: 48px; color: #409eff; margin-bottom: 8px; }
.upload-text { color: #606266; font-size: 14px; }
.field-hint { font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.4; }
.view-wrap {
  min-height: 360px;
  max-height: calc(100vh - 220px);
  overflow: auto;
}
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3) {
  margin: 1em 0 0.5em;
  line-height: 1.3;
}
.md-body :deep(h1) { font-size: 1.6em; border-bottom: 1px solid #eee; padding-bottom: 0.3em; }
.md-body :deep(h2) { font-size: 1.35em; }
.md-body :deep(h3) { font-size: 1.15em; }
.md-body :deep(p),
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 0.6em 0;
  line-height: 1.7;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 1.5em;
}
.md-body :deep(code) {
  background: #f5f7fa;
  padding: 0.15em 0.4em;
  border-radius: 4px;
  font-size: 0.9em;
}
.md-body :deep(pre) {
  background: #282c34;
  color: #abb2bf;
  padding: 12px 16px;
  border-radius: 6px;
  overflow: auto;
}
.md-body :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}
.md-body :deep(blockquote) {
  margin: 0.8em 0;
  padding: 0.4em 1em;
  border-left: 4px solid #409eff;
  background: #f0f7ff;
  color: #606266;
}
.md-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 8px 12px;
}
.md-body :deep(th) {
  background: #f5f7fa;
}
</style>
