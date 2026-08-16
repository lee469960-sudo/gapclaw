<template>
  <div class="workplace-panel">
    <div class="panel-header">
      <span class="title"><el-icon><Folder /></el-icon> 工作目录</span>
      <div class="header-actions">
        <el-button class="sandbox-btn" type="primary" circle size="small" @click="openSandbox">
          <el-icon><Odometer /></el-icon>
        </el-button>
        <el-button link @click="load"><el-icon><Refresh /></el-icon></el-button>
        <el-button link @click="promptCreate"><el-icon><Plus /></el-icon></el-button>
        <el-upload :show-file-list="false" :http-request="onUpload" multiple>
          <el-button link><el-icon><Upload /></el-icon></el-button>
        </el-upload>
      </div>
    </div>

    <div class="nav-row">
      <el-button size="small" :disabled="!curPath" @click="goUp">上一层</el-button>
      <el-input :model-value="displayPath" readonly size="small" />
    </div>

    <div v-if="selectionMode" class="select-bar">
      <el-checkbox :model-value="selectAllChecked" @change="toggleSelectAll">全选</el-checkbox>
      <span class="sel-count">已选 {{ selectedPaths.size }}</span>
      <span v-if="selectedFolder" class="sel-target">保存到: {{ selectedFolder }}/</span>
      <el-button size="small" @click="batchMove">移动</el-button>
      <el-button size="small" type="danger" link @click="batchDelete">删除</el-button>
      <el-button size="small" link @click="clearSelection">取消</el-button>
      <div class="sel-tip">提示: Ctrl/⌘+点击 / Shift+点击</div>
    </div>

    <el-input v-model="keyword" placeholder="搜索文件或文件夹..." clearable size="small" class="search">
      <template #prefix><el-icon><Search /></el-icon></template>
    </el-input>

    <div v-loading="loading" class="file-list">
      <div
        v-for="(item, idx) in filteredEntries"
        :key="item.path"
        class="file-item"
        :class="{ selected: selectedPaths.has(item.path), 'is-dir': item.is_dir }"
        @click="onItemClick($event, item, idx)"
        @dblclick="onDblClick(item)"
      >
        <el-checkbox
          :model-value="selectedPaths.has(item.path)"
          @click.stop
          @dblclick.stop
          @change="(v) => toggleSelect(item.path, v)"
        />
        <el-icon class="file-icon">
          <Folder v-if="item.is_dir" />
          <Document v-else />
        </el-icon>
        <div class="file-info">
          <div class="file-name">{{ item.name }}</div>
          <div class="file-meta">{{ formatMeta(item) }}</div>
        </div>
        <el-dropdown trigger="click" @command="(cmd) => onCommand(cmd, item)" @click.stop @dblclick.stop>
          <el-button link size="small" @click.stop>操作</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-if="!item.is_dir" command="download">下载</el-dropdown-item>
              <el-dropdown-item command="link">链接</el-dropdown-item>
              <el-dropdown-item v-if="!item.is_dir" command="view">显示</el-dropdown-item>
              <el-dropdown-item v-if="!item.is_dir" command="edit">编辑</el-dropdown-item>
              <el-dropdown-item command="zip">压缩</el-dropdown-item>
              <el-dropdown-item v-if="!item.is_dir && item.name.endsWith('.zip')" command="unzip">解压</el-dropdown-item>
              <el-dropdown-item command="rename">改名</el-dropdown-item>
              <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <el-empty v-if="!loading && !filteredEntries.length" description="空目录" :image-size="60" />
    </div>

    <WorkplaceFilePreviewDialog
      v-model="viewVisible"
      :title="viewTitle"
      :agent-id="props.agentId"
      :path="viewPath"
      :mode="viewMode"
      :editable="viewEditable"
      @save="saveEdit"
    />

    <SandboxTerminalDialog v-model="sandboxVisible" :sandbox-id="sandboxId" />
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Folder, Document, Refresh, Plus, Upload, Search, Odometer } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import api from '../api'
import SandboxTerminalDialog from './SandboxTerminalDialog.vue'
import WorkplaceFilePreviewDialog from './WorkplaceFilePreviewDialog.vue'
import { isCodeFilename } from '../utils/codeLanguage'

const props = defineProps({
  agentId: { type: String, required: true },
  sandboxId: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()

const sandboxVisible = ref(false)

const curPath = ref(typeof route.query.wp === 'string' ? route.query.wp : '')
const entries = ref([])
const loading = ref(false)
const keyword = ref('')
const selectedPaths = ref(new Set())
const lastClickIndex = ref(-1)
const viewVisible = ref(false)
const viewTitle = ref('')
const viewPath = ref('')
const viewMode = ref('text')
const viewEditable = ref(false)
const editPath = ref('')

const displayPath = computed(() => `/workplace${curPath.value ? '/' + curPath.value : ''}`)
const selectionMode = computed(() => selectedPaths.value.size > 0)
const filteredEntries = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return entries.value
  return entries.value.filter((e) => e.name.toLowerCase().includes(kw))
})
const selectAllChecked = computed(() =>
  filteredEntries.value.length > 0 && filteredEntries.value.every((e) => selectedPaths.value.has(e.path))
)

const selectedFolder = computed(() => {
  for (const p of selectedPaths.value) {
    const item = entries.value.find((e) => e.path === p)
    if (item?.is_dir) return p
  }
  return ''
})

function formatMeta(item) {
  if (item.is_dir) return item.modified || ''
  const size = item.size < 1024 ? `${item.size} B` : `${(item.size / 1024).toFixed(1)} KB`
  return `${size}${item.modified ? ' · ' + item.modified : ''}`
}

function wpPath(rel) {
  return `/workplace/${rel}`.replace(/\/+/g, '/')
}

function syncWpToUrl(p) {
  const next = p || undefined
  const cur = typeof route.query.wp === 'string' ? route.query.wp : undefined
  if (cur === next || (!cur && !next)) return
  router.replace({ query: { ...route.query, wp: next } })
}

function setPath(p) {
  curPath.value = p || ''
  syncWpToUrl(curPath.value)
  clearSelection()
  load()
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_agent_chat.cgi', {
      action: 'list_workplace',
      agent_id: props.agentId,
      path: curPath.value,
    })
    entries.value = res.data || []
  } finally {
    loading.value = false
  }
}

function goUp() {
  if (!curPath.value) return
  const parts = curPath.value.split('/').filter(Boolean)
  parts.pop()
  setPath(parts.join('/'))
}

function enterDir(item) {
  setPath(item.path)
}

function onDblClick(item) {
  if (item.is_dir) enterDir(item)
}

async function openSandbox() {
  if (!props.sandboxId) {
    ElMessage.warning('当前 Agent 未绑定沙箱')
    return
  }
  try {
    await postCgi('/pages/page_sandbox.cgi', { action: 'start', id: props.sandboxId })
  } catch {
    // 拦截器已提示
  }
  sandboxVisible.value = true
}

function toggleSelect(path, checked) {
  const next = new Set(selectedPaths.value)
  if (checked) next.add(path)
  else next.delete(path)
  selectedPaths.value = next
}

function onItemClick(ev, item, idx) {
  if (ev.shiftKey && lastClickIndex.value >= 0) {
    const start = Math.min(lastClickIndex.value, idx)
    const end = Math.max(lastClickIndex.value, idx)
    const next = new Set(selectedPaths.value)
    for (let i = start; i <= end; i++) next.add(filteredEntries.value[i].path)
    selectedPaths.value = next
    return
  }
  if (ev.metaKey || ev.ctrlKey) {
    const next = new Set(selectedPaths.value)
    if (next.has(item.path)) next.delete(item.path)
    else next.add(item.path)
    selectedPaths.value = next
    lastClickIndex.value = idx
    return
  }
  lastClickIndex.value = idx
}

function toggleSelectAll(checked) {
  if (checked) {
    selectedPaths.value = new Set(filteredEntries.value.map((e) => e.path))
  } else {
    clearSelection()
  }
}

function clearSelection() {
  selectedPaths.value = new Set()
  lastClickIndex.value = -1
}

async function wpPost(action, extra = {}) {
  return postCgi(`/pages/page_agent_chat.cgi?action=${action}`, {
    agent_id: props.agentId,
    ...extra,
  })
}

async function promptCreate() {
  const { value } = await ElMessageBox.prompt('名称', '新建', { inputPlaceholder: '文件夹名或文件名' })
  if (!value?.trim()) return
  const name = value.trim()
  const path = curPath.value ? `${curPath.value}/${name}` : name
  if (name.includes('.')) {
    await wpPost('save_workplace', { path, content: '' })
  } else {
    await wpPost('mkdir_workplace', { path })
  }
  ElMessage.success('创建成功')
  load()
}

async function onUpload({ file }) {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('agent_id', props.agentId)
  fd.append('path', curPath.value)
  await api.post('/pages/page_agent_chat.cgi/upload', fd)
  ElMessage.success('上传成功')
  load()
}

async function downloadFile(item) {
  const res = await api.get('/pages/page_agent_chat.cgi', {
    params: { action: 'download_workplace', agent_id: props.agentId, path: item.path },
    responseType: 'blob',
  })
  const blob = res.data
  if (!blob || blob.size === 0) {
    ElMessage.error('文件为空，无法下载')
    return
  }
  const ctype = String(res.headers?.['content-type'] || '')
  if (ctype.includes('json') || ctype.includes('text/plain')) {
    try {
      const text = await blob.text()
      const parsed = JSON.parse(text)
      if (parsed && typeof parsed.code === 'number' && parsed.code !== 0) {
        ElMessage.error(parsed.msg || '下载失败')
        return
      }
    } catch {
      /* not an error payload */
    }
  }
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = item.name
  a.click()
  URL.revokeObjectURL(url)
}

function copyLink(item) {
  navigator.clipboard.writeText(wpPath(item.path))
  ElMessage.success('已复制链接')
}

function previewMode(name) {
  const lower = (name || '').toLowerCase()
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'markdown'
  if (lower.endsWith('.pdf')) return 'pdf'
  if (lower.endsWith('.xlsx') || lower.endsWith('.xlsm') || lower.endsWith('.xls')) return 'excel'
  if (isCodeFilename(name)) return 'code'
  return 'text'
}

async function viewFile(item, editable = false) {
  viewTitle.value = item.name
  viewPath.value = item.path
  viewMode.value = previewMode(item.name)
  viewEditable.value = editable
  editPath.value = item.path
  viewVisible.value = true
}

async function saveEdit(content) {
  if (typeof content === 'object' && content?.type === 'excel') {
    await wpPost('save_excel_workplace', {
      path: editPath.value,
      content: JSON.stringify(content.sheets || []),
    })
  } else {
    await wpPost('save_workplace', { path: editPath.value, content })
  }
  ElMessage.success('已保存')
  viewVisible.value = false
  load()
}

async function onCommand(cmd, item) {
  if (cmd === 'download') return downloadFile(item)
  if (cmd === 'link') return copyLink(item)
  if (cmd === 'view') return viewFile(item, false)
  if (cmd === 'edit') return viewFile(item, true)
  if (cmd === 'zip') {
    await wpPost('zip_workplace', { path: item.path })
    ElMessage.success('已压缩')
    return load()
  }
  if (cmd === 'unzip') {
    await wpPost('unzip_workplace', { path: item.path })
    ElMessage.success('已解压')
    return load()
  }
  if (cmd === 'rename') {
    const { value } = await ElMessageBox.prompt('新名称', '改名', { inputValue: item.name })
    if (!value?.trim()) return
    await wpPost('rename_workplace', { path: item.path, new_name: value.trim() })
    ElMessage.success('已改名')
    return load()
  }
  if (cmd === 'delete') {
    await ElMessageBox.confirm(`确定删除 ${item.name}？`, '删除', { type: 'warning' })
    await wpPost('delete_workplace', { path: item.path })
    ElMessage.success('已删除')
    return load()
  }
}

async function batchMove() {
  const { value } = await ElMessageBox.prompt('目标目录（相对 workplace）', '移动', {
    inputPlaceholder: curPath.value || '留空表示根目录',
    inputValue: curPath.value,
  })
  if (value === null) return
  const destDir = value.trim()
  for (const p of selectedPaths.value) {
    const name = p.split('/').pop()
    const dest = destDir ? `${destDir}/${name}` : name
    await wpPost('move_workplace', { path: p, dest })
  }
  ElMessage.success('移动完成')
  clearSelection()
  load()
}

async function batchDelete() {
  await ElMessageBox.confirm(`确定删除选中的 ${selectedPaths.value.size} 项？`, '批量删除', { type: 'warning' })
  for (const p of selectedPaths.value) {
    await wpPost('delete_workplace', { path: p })
  }
  ElMessage.success('已删除')
  clearSelection()
  load()
}

watch(() => props.agentId, (_id, prev) => {
  if (prev !== undefined && prev !== _id) {
    curPath.value = ''
    syncWpToUrl('')
  }
  clearSelection()
  load()
}, { immediate: true })

watch(
  () => route.query.wp,
  (p) => {
    const next = typeof p === 'string' ? p : ''
    if (next === curPath.value) return
    curPath.value = next
    clearSelection()
    load()
  },
)

function getSelectedFiles() {
  const preferred = []
  const others = []
  for (const p of selectedPaths.value) {
    const item = entries.value.find((e) => e.path === p)
    if (!item || item.is_dir) continue
    const name = (item.name || p).toLowerCase()
    if (/\.(xlsx|xlsm|xls|csv)$/.test(name)) preferred.push(p)
    else others.push(p)
  }
  return preferred.length ? preferred : others
}

defineExpose({
  currentPath: curPath,
  load,
  uploadToCurrent: onUpload,
  getSelectedFolder: () => selectedFolder.value,
  getSelectedFiles,
})
</script>

<style scoped>
.workplace-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-right: 1px solid var(--gap-card-border);
  background: var(--gap-chat-sidebar-bg);
  min-width: 280px;
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  border-bottom: 1px solid var(--gap-card-border);
  background: var(--gap-card-bg);
  color: var(--gap-text);
}
.title { display: flex; align-items: center; gap: 6px; font-weight: 600; color: var(--gap-text); }
.header-actions { display: flex; align-items: center; gap: 4px; }
.sandbox-btn { flex-shrink: 0; }
.nav-row { display: flex; gap: 8px; padding: 10px 12px; background: var(--gap-card-bg); }
.search { margin: 0 12px 8px; }
.select-bar {
  padding: 8px 12px;
  background: var(--gap-hover-bg);
  border-bottom: 1px solid var(--gap-card-border);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.sel-count { color: #409eff; font-weight: 600; }
.sel-target { color: #67c23a; font-size: 12px; }
.sel-tip { width: 100%; font-size: 12px; color: var(--gap-text-muted); }
.file-list { flex: 1; overflow: auto; padding: 0 8px 8px; }
.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 8px;
  border-radius: 6px;
  cursor: pointer;
  background: var(--gap-card-bg);
  margin-bottom: 6px;
  border: 1px solid var(--gap-card-border);
}
.file-item.is-dir { cursor: pointer; }
.file-item.is-dir:hover { background: var(--gap-hover-bg); }
.file-item.selected { background: var(--gap-hover-bg); border-color: var(--gap-primary); }
.file-icon { font-size: 18px; color: #409eff; flex-shrink: 0; }
.file-info { flex: 1; min-width: 0; }
.file-name { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--gap-text); }
.file-meta { font-size: 12px; color: var(--gap-text-muted); margin-top: 2px; }
</style>
