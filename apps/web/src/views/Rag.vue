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
          placeholder="搜索知识库名称或描述..."
          clearable
          style="width:280px"
          @keyup.enter="doSearch"
        />
        <el-button type="primary" @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button type="primary" @click="openUpload">
          <el-icon><Upload /></el-icon>
          上传知识库
        </el-button>
      </div>
    </div>

    <div class="summary">共 {{ list.length }} 条，已显示 {{ filtered.length }} 条</div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">
            <el-icon class="rag-icon"><Collection /></el-icon>
            {{ row.name }}
          </span>
          <el-tag size="small" :type="row.visibility === 'public' ? 'success' : 'info'" effect="plain">
            {{ row.visibility === 'public' ? '公共' : '私有' }}
          </el-tag>
        </div>
        <div class="card-meta">
          <div><span class="label">ID</span> {{ row.id }}</div>
          <div><span class="label">分片数</span> {{ row.chunk_count ?? 0 }}</div>
          <div><span class="label">索引</span>
            <el-tag size="small" :type="indexTagType(row)" effect="plain">
              {{ indexLabel(row) }}
            </el-tag>
          </div>
          <div><span class="label">创建人</span> {{ row.creator || '-' }}</div>
          <div><span class="label">时间</span> {{ row.modified_at || '-' }}</div>
        </div>
        <div v-if="row.index_error" class="card-error">{{ row.index_error }}</div>
        <div v-if="row.description" class="card-desc">{{ row.description }}</div>
        <div class="card-actions">
          <el-button size="small" type="warning" plain @click="openSearch(row)">
            <el-icon><Search /></el-icon>
            检索
          </el-button>
          <el-button size="small" :loading="reindexId === row.id" @click="reindex(row)">重建索引</el-button>
          <el-button
            v-if="row.creator === currentUser"
            size="small"
            type="danger"
            link
            @click="onDelete(row)"
          >
            删除
          </el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !filtered.length" description="暂无 RAG 知识库">
        <template #description>
          <p>暂无 RAG 知识库</p>
          <p class="empty-hint">点击右上角「上传知识库」添加文档</p>
        </template>
      </el-empty>
    </div>

    <!-- 上传 -->
    <el-dialog v-model="uploadVisible" title="上传知识库" width="560px" destroy-on-close @closed="resetUpload">
      <el-form :model="uploadForm" label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="uploadForm.name" placeholder="请输入知识库名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="uploadForm.description" type="textarea" :rows="3" placeholder="简要描述知识库内容" />
        </el-form-item>
        <el-form-item label="可见性">
          <el-radio-group v-model="uploadForm.visibility">
            <el-radio value="public">公共</el-radio>
            <el-radio value="private">私有</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="文档文件" required>
          <el-upload
            drag
            :auto-upload="false"
            :limit="1"
            accept=".txt,.md,.pdf,.docx,.csv,.json"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="upload-text">点击或拖拽上传文档（txt/md/pdf/docx/csv/json，≤32MB）</div>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传并索引</el-button>
      </template>
    </el-dialog>

    <!-- 检索 -->
    <el-dialog v-model="searchVisible" :title="`检索 · ${searchTarget?.name || ''}`" width="640px" destroy-on-close>
      <el-input
        v-model="searchQuery"
        placeholder="输入检索问题..."
        clearable
        @keyup.enter="doRagSearch"
      >
        <template #append>
          <el-button :loading="searchLoading" @click="doRagSearch">检索</el-button>
        </template>
      </el-input>
      <div v-loading="searchLoading" class="search-results">
        <el-empty v-if="!searchLoading && !searchResults.length" description="输入问题后点击检索" />
        <div v-for="(item, i) in searchResults" :key="i" class="result-item">
          <div class="result-score">相关度 {{ item.score?.toFixed?.(3) ?? item.score ?? '-' }}</div>
          <div class="result-text">{{ item.snippet || item.text || item.content || '-' }}</div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Collection, Search, Upload, UploadFilled } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { getCurrentUsername } from '../session'

const list = ref([])
const loading = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const currentUser = ref('')
const reindexId = ref('')

const uploadVisible = ref(false)
const uploading = ref(false)
const uploadFile = ref(null)
const uploadForm = reactive({ name: '', description: '', visibility: 'private' })

const searchVisible = ref(false)
const searchTarget = ref(null)
const searchQuery = ref('')
const searchResults = ref([])
const searchLoading = ref(false)

const scopedList = computed(() => {
  if (scope.value === 'mine') return list.value.filter((r) => r.creator === currentUser.value)
  return list.value
})

const filtered = computed(() => {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return scopedList.value
  return scopedList.value.filter(
    (r) =>
      r.name?.toLowerCase().includes(kw) ||
      r.description?.toLowerCase().includes(kw) ||
      r.id?.toLowerCase().includes(kw)
  )
})

async function loadCurrentUser() {
  currentUser.value = getCurrentUsername()
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_rag.cgi', { action: 'list' })
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
  resetUpload()
  uploadVisible.value = true
}

function resetUpload() {
  uploadForm.name = ''
  uploadForm.description = ''
  uploadForm.visibility = 'private'
  uploadFile.value = null
}

function onFileChange(file) {
  uploadFile.value = file.raw
  if (!uploadForm.name && file.name) {
    uploadForm.name = file.name.replace(/\.[^.]+$/, '')
  }
}

function onFileRemove() {
  uploadFile.value = null
}

function indexLabel(row) {
  const s = row.index_status || 'pending'
  if (s === 'ready') return '就绪'
  if (s === 'error') return '失败'
  return '待索引'
}

function indexTagType(row) {
  const s = row.index_status || 'pending'
  if (s === 'ready') return 'success'
  if (s === 'error') return 'danger'
  return 'info'
}

async function submitUpload() {
  if (!uploadForm.name?.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  if (!uploadFile.value) {
    ElMessage.warning('请选择文档文件')
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('action', 'upload')
    fd.append('name', uploadForm.name.trim())
    fd.append('description', uploadForm.description.trim())
    fd.append('visibility', uploadForm.visibility)
    fd.append('file', uploadFile.value)
    const res = await fetch('/pages/page_rag.cgi', { method: 'POST', body: fd, credentials: 'include' })
    const data = await res.json()
    if (data.code !== 0) {
      ElMessage.error(data.msg || '上传失败')
      return
    }
    ElMessage.success(data.msg || '上传成功')
    uploadVisible.value = false
    load()
  } finally {
    uploading.value = false
  }
}

async function reindex(row) {
  reindexId.value = row.id
  try {
    const res = await postCgi('/pages/page_rag.cgi', { action: 'reindex', id: row.id })
    const status = res.data?.index_status
    const err = res.data?.index_error
    if (status === 'error') {
      ElMessage.warning(err || '索引失败')
    } else {
      ElMessage.success(`索引完成，共 ${res.data?.chunk_count ?? 0} 个分片`)
    }
    load()
  } finally {
    reindexId.value = ''
  }
}

function openSearch(row) {
  searchTarget.value = row
  searchQuery.value = ''
  searchResults.value = []
  searchVisible.value = true
}

async function doRagSearch() {
  if (!searchQuery.value.trim()) {
    ElMessage.warning('请输入检索问题')
    return
  }
  searchLoading.value = true
  try {
    const res = await postCgi('/pages/page_rag.cgi', {
      action: 'search',
      query: searchQuery.value.trim(),
      corpus_ids: searchTarget.value ? [searchTarget.value.id] : [],
    })
    searchResults.value = res.data || []
  } finally {
    searchLoading.value = false
  }
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除知识库「${row.name}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/page_rag.cgi', { action: 'delete', id: row.id })
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
  display: flex;
  flex-direction: column;
  transition: box-shadow 0.2s;
}
.card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 10px; }
.card-title { font-size: 16px; font-weight: 600; color: #303133; display: flex; align-items: center; gap: 8px; min-width: 0; }
.rag-icon { color: #9c27b0; font-size: 18px; flex-shrink: 0; }
.card-meta { font-size: 13px; color: #606266; line-height: 1.8; margin-bottom: 8px; }
.card-meta .label { color: #909399; margin-right: 6px; }
.card-desc {
  font-size: 13px; color: #606266; line-height: 1.6; margin-bottom: 10px; flex: 1;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.card-error { font-size: 12px; color: #f56c6c; margin-bottom: 8px; line-height: 1.4; }
.card-actions {
  display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end;
  border-top: 1px solid #f0f0f0; padding-top: 12px; margin-top: auto;
}
.empty-hint { font-size: 12px; color: #909399; margin-top: 4px; }
.upload-icon { font-size: 48px; color: #409eff; margin-bottom: 8px; }
.upload-text { color: #606266; font-size: 14px; }
.search-results { margin-top: 16px; min-height: 120px; max-height: 400px; overflow: auto; }
.result-item {
  border: 1px solid #ebeef5; border-radius: 8px; padding: 12px; margin-bottom: 10px; background: #fafbfc;
}
.result-score { font-size: 12px; color: #909399; margin-bottom: 6px; }
.result-text { font-size: 13px; color: #303133; line-height: 1.6; white-space: pre-wrap; }
</style>
