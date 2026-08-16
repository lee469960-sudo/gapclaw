<template>
  <div class="page">
    <div class="header">
      <div class="header-left">
        <el-icon class="page-icon"><FolderOpened /></el-icon>
        <span class="page-title">文件管理</span>
      </div>
      <div class="header-actions">
        <el-input
          v-model="keyword"
          placeholder="搜索文件名..."
          clearable
          style="width:220px"
          @keyup.enter="applyFilter"
          @clear="resetFilter"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button type="primary" @click="applyFilter">搜索</el-button>
        <el-button @click="resetFilter">重置</el-button>
      </div>
    </div>

    <div class="toolbar panel">
      <el-breadcrumb separator="/" class="breadcrumb">
        <el-breadcrumb-item>
          <a class="crumb-link" @click="go('')">
            <el-icon><HomeFilled /></el-icon>
            根目录
          </a>
        </el-breadcrumb-item>
        <el-breadcrumb-item v-for="(p, i) in pathParts" :key="i">
          <a class="crumb-link" @click="go(pathParts.slice(0, i + 1).join('/'))">{{ p }}</a>
        </el-breadcrumb-item>
      </el-breadcrumb>
      <div class="toolbar-actions">
        <el-button @click="mkdir">
          <el-icon><FolderAdd /></el-icon>
          新建文件夹
        </el-button>
        <el-upload :show-file-list="false" :http-request="upload">
          <el-button type="primary">
            <el-icon><Upload /></el-icon>
            上传
          </el-button>
        </el-upload>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="summary">共 {{ entries.length }} 项，显示 {{ filteredEntries.length }} 项</div>

    <el-table
      v-loading="loading"
      :data="filteredEntries"
      stripe
      border
      class="file-table"
      empty-text="当前目录为空"
      @row-dblclick="onDblClick"
    >
      <el-table-column label="名称" min-width="240">
        <template #default="{ row }">
          <div class="name-cell" :class="{ dir: row.is_dir }">
            <el-icon class="file-icon">
              <Folder v-if="row.is_dir" />
              <Document v-else />
            </el-icon>
            <span>{{ row.name }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="90" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row.is_dir ? 'warning' : 'info'" effect="light">
            {{ row.is_dir ? '目录' : '文件' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="110" align="right">
        <template #default="{ row }">{{ formatSize(row.size, row.is_dir) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="280" align="center" fixed="right">
        <template #default="{ row }">
          <el-button v-if="!row.is_dir" link type="success" @click="preview(row)">预览</el-button>
          <el-button v-if="!row.is_dir" link type="primary" @click="download(row)">下载</el-button>
          <el-button link @click="rename(row)">重命名</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <FilePreviewDialog v-model="previewVisible" :title="previewTitle" :path="previewPath" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  FolderOpened,
  Search,
  HomeFilled,
  FolderAdd,
  Upload,
  Refresh,
  Folder,
  Document,
} from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import api from '../api'
import FilePreviewDialog from '../components/FilePreviewDialog.vue'

const route = useRoute()
const router = useRouter()

const curPath = ref(typeof route.query.path === 'string' ? route.query.path : '')
const entries = ref([])
const loading = ref(false)
const keyword = ref('')
const appliedKeyword = ref('')
const previewVisible = ref(false)
const previewTitle = ref('')
const previewPath = ref('')

const pathParts = computed(() => (curPath.value ? curPath.value.split('/').filter(Boolean) : []))

const filteredEntries = computed(() => {
  const kw = appliedKeyword.value.trim().toLowerCase()
  if (!kw) return entries.value
  return entries.value.filter((e) => e.name?.toLowerCase().includes(kw))
})

function formatSize(size, isDir) {
  if (isDir) return '-'
  if (size == null) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

function filePath(name) {
  return [curPath.value, name].filter(Boolean).join('/')
}

function syncPathToUrl(p) {
  const next = p || undefined
  const cur = typeof route.query.path === 'string' ? route.query.path : undefined
  if (cur === next || (!cur && !next)) return
  router.replace({ query: { ...route.query, path: next } })
}

function applyFilter() {
  appliedKeyword.value = keyword.value
}

function resetFilter() {
  keyword.value = ''
  appliedKeyword.value = ''
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_files.cgi', { action: 'list', path: curPath.value })
    entries.value = res.data?.entries || []
  } finally {
    loading.value = false
  }
}

function go(p) {
  curPath.value = p || ''
  syncPathToUrl(curPath.value)
  load()
}

function onDblClick(row) {
  if (row.is_dir) go(filePath(row.name))
  else preview(row)
}

async function mkdir() {
  const { value } = await ElMessageBox.prompt('请输入文件夹名称', '新建文件夹', {
    inputPattern: /\S+/,
    inputErrorMessage: '名称不能为空',
  })
  await postCgi('/pages/page_files.cgi', { action: 'mkdir', path: curPath.value, name: value })
  ElMessage.success('创建成功')
  load()
}

async function rename(row) {
  const { value } = await ElMessageBox.prompt('请输入新名称', '重命名', {
    inputValue: row.name,
    inputPattern: /\S+/,
    inputErrorMessage: '名称不能为空',
  })
  if (value === row.name) return
  await postCgi('/pages/page_files.cgi', {
    action: 'rename',
    path: curPath.value,
    name: row.name,
    new_name: value,
  })
  ElMessage.success('重命名成功')
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除「${row.name}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/page_files.cgi', { action: 'delete', path: curPath.value, name: row.name })
  ElMessage.success('已删除')
  load()
}

function download(row) {
  const path = filePath(row.name)
  window.open(`/pages/page_files.cgi?action=download&path=${encodeURIComponent(path)}`, '_blank')
}

function preview(row) {
  previewTitle.value = row.name
  previewPath.value = filePath(row.name)
  previewVisible.value = true
}

async function upload({ file }) {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('action', 'upload')
  fd.append('path', curPath.value)
  try {
    await api.post('/pages/page_files.cgi', fd)
    ElMessage.success('上传成功')
    load()
  } catch {
    /* interceptor handles error message */
  }
}

onMounted(load)

watch(
  () => route.query.path,
  (p) => {
    const next = typeof p === 'string' ? p : ''
    if (next === curPath.value) return
    curPath.value = next
    load()
  },
)
</script>

<style scoped>
.page {
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px;
  min-height: 400px;
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-icon { font-size: 22px; color: #409eff; }
.page-title { font-size: 18px; font-weight: 600; color: #303133; }
.header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.panel {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.breadcrumb { flex: 1; min-width: 200px; }
.crumb-link {
  cursor: pointer;
  color: #409eff;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.toolbar-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.summary { font-size: 13px; color: #909399; margin-bottom: 12px; }
.file-table { width: 100%; }
.file-table :deep(.el-table__header th) {
  background: #f5f7fa;
  color: #606266;
  font-weight: 600;
}
.name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: default;
}
.name-cell.dir { cursor: pointer; color: #409eff; font-weight: 500; }
.file-icon { font-size: 18px; color: #909399; }
.name-cell.dir .file-icon { color: #e6a23c; }
</style>
