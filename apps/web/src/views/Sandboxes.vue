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
          placeholder="搜索沙箱名称..."
          clearable
          style="width:220px"
          @keyup.enter="doSearch"
        />
        <el-button @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button @click="openImages">镜像管理</el-button>
        <el-button type="primary" @click="openForm()">+ 创建沙箱</el-button>
      </div>
    </div>

    <div class="summary">共 {{ list.length }} 条，已显示 {{ filtered.length }} 条</div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">
            <el-icon v-if="row.visibility === 'private'" class="vis-icon"><Lock /></el-icon>
            <el-icon v-else class="vis-icon pub"><Unlock /></el-icon>
            {{ row.name }}
          </span>
          <el-dropdown trigger="click" @command="(cmd) => lifecycle(row, cmd)">
            <el-button link type="primary">更多</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="start">启动</el-dropdown-item>
                <el-dropdown-item command="stop">停止</el-dropdown-item>
                <el-dropdown-item command="rebuild">重建</el-dropdown-item>
                <el-dropdown-item command="save_image" :disabled="row.status !== 'running'">保存镜像</el-dropdown-item>
                <el-dropdown-item command="edit">编辑</el-dropdown-item>
                <el-dropdown-item command="destroy" divided>销毁</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
        <div class="card-meta">
          <span>{{ row.id?.slice(0, 8) }}</span>
          <span>{{ row.creator }}</span>
          <span>{{ row.created_at || '-' }}</span>
        </div>
        <div class="resource-rows">
          <div class="resource-row">
            <span class="res-icon orange">镜</span>
            <span class="resource-text" :title="row.image">{{ row.image }}</span>
          </div>
          <div class="resource-row">
            <span class="res-icon blue">规</span>
            <span class="resource-text">CPU: {{ row.cpu_count }}核 | 内存: {{ row.memory_mb }}MB</span>
          </div>
          <div class="resource-row">
            <span class="res-icon green">网</span>
            <span class="resource-text">{{ networkLabel(row.network_mode) }}</span>
          </div>
        </div>
        <div class="card-footer">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status || 'stopped' }}</el-tag>
          <el-button type="primary" size="small" @click="openTerminal(row)">
            执行命令
          </el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !filtered.length" description="暂无沙箱" />
    </div>

    <!-- 创建/编辑 -->
    <el-dialog v-model="visible" :title="form.id ? '编辑沙箱' : '创建沙箱'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="沙箱名称" />
        </el-form-item>
        <el-form-item label="镜像">
          <el-select
            v-model="form.image"
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入镜像"
            style="width:100%"
          >
            <el-option v-for="img in imageOptions" :key="img" :label="img" :value="img" />
          </el-select>
        </el-form-item>
        <el-form-item label="CPU">
          <el-input-number v-model="form.cpu_count" :min="1" :max="16" />
        </el-form-item>
        <el-form-item label="内存MB">
          <el-input-number v-model="form.memory_mb" :min="128" :step="128" />
        </el-form-item>
        <el-form-item label="网络">
          <el-select v-model="form.network_mode" style="width:100%" :disabled="!!form.id">
            <el-option label="bridge" value="bridge" />
            <el-option label="host" value="host" />
          </el-select>
          <div v-if="form.id" class="field-hint">创建后网络不可修改，如需更换请新建沙箱</div>
        </el-form-item>
        <el-form-item label="可见性">
          <el-radio-group v-model="form.visibility">
            <el-radio value="private">私有</el-radio>
            <el-radio value="public">公开</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.id && form.status === 'running'">
          <el-button type="warning" @click="openSaveImage(form)">保存为镜像</el-button>
        </el-form-item>
        <el-form-item v-if="form.id">
          <el-button type="success" @click="lifecycle(form, 'start')">启动</el-button>
          <el-button @click="lifecycle(form, 'stop')">停止</el-button>
          <el-button @click="lifecycle(form, 'rebuild')">重建</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 镜像管理 -->
    <el-dialog v-model="imgVisible" title="镜像管理" width="860px" top="6vh" @open="loadImages">
      <div class="img-toolbar">
        <el-input
          v-model="pullName"
          placeholder="输入镜像名, 如 python:3.12-slim、myclaw-base:latest"
          style="flex:1"
        />
        <el-button type="primary" :loading="imgLoading" @click="pullImage">拉取</el-button>
        <el-button type="success" @click="uploadRef?.click()">上传镜像</el-button>
        <input ref="uploadRef" type="file" accept=".tar,.tar.gz,.tgz" hidden @change="onUploadImage" />
        <el-button :loading="imgLoading" @click="loadImages">刷新</el-button>
      </div>
      <el-alert
        v-if="dockerInfo && !dockerInfo.connected"
        type="warning"
        :title="dockerInfo.error || 'Docker 不可用'"
        show-icon
        :closable="false"
        style="margin-bottom:12px"
      />
      <el-table v-loading="imgLoading" :data="images" stripe size="small">
        <el-table-column label="镜像标签" min-width="200">
          <template #default="{ row }">
            <span class="img-tag">{{ row.tag || (row.tags || []).join(', ') }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="id" label="ID" width="120" />
        <el-table-column prop="size" label="大小" width="100" />
        <el-table-column prop="created" label="创建时间" width="170" />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="downloadImage(row)">下载</el-button>
            <el-button link type="danger" @click="deleteImage(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 保存镜像 -->
    <el-dialog v-model="saveImageVisible" title="保存到镜像库" width="480px">
      <el-form :model="saveImageForm" label-width="90px">
        <el-form-item label="镜像名" required>
          <el-input v-model="saveImageForm.name" placeholder="如 my-sandbox-v1" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="saveImageForm.tag" placeholder="latest" />
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon>
          将当前运行中容器保存为本地 Docker 镜像，可在「镜像管理」中查看和使用。
        </el-alert>
      </el-form>
      <template #footer>
        <el-button @click="saveImageVisible = false">取消</el-button>
        <el-button type="primary" :loading="saveImageLoading" @click="confirmSaveImage">保存</el-button>
      </template>
    </el-dialog>

    <!-- 执行命令 -->
    <el-dialog v-model="termVisible" title="在沙箱中执行命令" width="90%" top="5vh" destroy-on-close @opened="onTermOpened">
      <XtermPanel
        v-if="termVisible"
        :key="termSandboxId"
        ref="termPanelRef"
        variant="sandbox"
        ws-url="/pages/page_sandbox.ws"
        :init-payload="{ id: termSandboxId, workdir: '/workplace' }"
      />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onActivated, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Lock, Unlock } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { cachedGetCgi, invalidateListCache } from '../listCache'
import XtermPanel from '../components/XtermPanel.vue'

defineOptions({ name: 'Sandboxes' })

const list = ref([])
const loading = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const visible = ref(false)
const imgVisible = ref(false)
const termVisible = ref(false)
const termSandboxId = ref('')
const imgLoading = ref(false)
const images = ref([])
const dockerInfo = ref(null)
const pullName = ref('')
const uploadRef = ref(null)
const defaultImage = ref('myclaw-base:latest')
const termPanelRef = ref(null)
const form = reactive({})
const saveImageVisible = ref(false)
const saveImageLoading = ref(false)
const saveImageTarget = ref(null)
const saveImageForm = reactive({ name: '', tag: 'latest' })

async function onTermOpened() {
  await nextTick()
  await termPanelRef.value?.initAndConnect()
}

const imageOptions = computed(() => {
  const tags = images.value.map((i) => i.tag || i.tags?.[0]).filter(Boolean)
  const set = new Set([defaultImage.value, 'python:3.12-slim', ...tags])
  if (form.image) set.add(form.image)
  return [...set]
})

const filtered = computed(() => {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((r) =>
    r.name?.toLowerCase().includes(kw) ||
    r.image?.toLowerCase().includes(kw) ||
    r.id?.toLowerCase().includes(kw)
  )
})

function networkLabel(mode) {
  if (mode === 'host') return 'host 网络 (与宿主机共享)'
  return `${mode || 'bridge'} 网络`
}

function statusType(status) {
  if (status === 'running') return 'success'
  if (status === 'error') return 'danger'
  return 'info'
}

async function loadSettings() {
  const res = await getCgi('/pages/page_sandbox.cgi', { action: 'get_setting' })
  defaultImage.value = res.data?.default_sandbox_image || 'myclaw-base:latest'
}

async function load({ background = false } = {}) {
  if (!background) loading.value = true
  try {
    const res = await cachedGetCgi(
      '/pages/page_sandbox.cgi',
      { action: 'list', scope: scope.value },
      15000,
    )
    list.value = res.data || []
  } finally {
    if (!background) loading.value = false
  }
}

function doSearch() {
  searchKw.value = keyword.value
}

function resetSearch() {
  keyword.value = ''
  searchKw.value = ''
}

function openForm(row) {
  if (row) {
    Object.assign(form, { ...row })
  } else {
    Object.assign(form, {
      id: '',
      name: '',
      image: defaultImage.value,
      cpu_count: 2,
      memory_mb: 512,
      network_mode: 'bridge',
      visibility: 'private',
    })
  }
  if (!images.value.length) loadImages()
  visible.value = true
}

async function save() {
  if (!form.name?.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  await postCgi('/pages/page_sandbox.cgi', {
    action: form.id ? 'update' : 'create',
    id: form.id || undefined,
    name: form.name,
    image: form.image,
    cpu_count: form.cpu_count,
    memory_mb: form.memory_mb,
    network_mode: form.network_mode,
    visibility: form.visibility,
  })
  ElMessage.success('保存成功')
  visible.value = false
  invalidateListCache('/pages/page_sandbox.cgi')
  invalidateListCache('/pages/page_agent.cgi')
  load()
}

async function lifecycle(row, act) {
  try {
    if (act === 'edit') {
      openForm(row)
      return
    }
    if (act === 'save_image') {
      openSaveImage(row)
      return
    }
    if (act === 'destroy') {
      await ElMessageBox.confirm(`确定销毁沙箱 ${row.name}？`, '警告', { type: 'warning' })
    }
    if (act === 'start' && row.status === 'running') {
      ElMessage.info('沙箱已在运行')
      return
    }
    const labels = { start: '启动', stop: '停止', rebuild: '重建', destroy: '销毁' }
    const res = await postCgi('/pages/page_sandbox.cgi', { action: act, id: row.id })
    ElMessage.success(res.msg || `${labels[act] || act}成功`)
    visible.value = false
    invalidateListCache('/pages/page_sandbox.cgi')
    if (act === 'destroy') invalidateListCache('/pages/page_agent.cgi')
    await load()
  } catch {
    // 用户取消或 API 报错（拦截器已提示）
  }
}

function slugifyImageName(name) {
  return (name || 'gap-snapshot')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'gap-snapshot'
}

function openSaveImage(row) {
  if (row.status !== 'running') {
    ElMessage.warning('请先启动沙箱后再保存镜像')
    return
  }
  saveImageTarget.value = row
  saveImageForm.name = slugifyImageName(`gap-${row.name}`)
  saveImageForm.tag = 'latest'
  saveImageVisible.value = true
}

async function confirmSaveImage() {
  if (!saveImageForm.name?.trim()) {
    ElMessage.warning('请填写镜像名')
    return
  }
  saveImageLoading.value = true
  try {
    const res = await postCgi('/pages/page_sandbox.cgi', {
      action: 'commit_image',
      id: saveImageTarget.value.id,
      image_name: saveImageForm.name.trim(),
      image_tag: saveImageForm.tag?.trim() || 'latest',
    })
    ElMessage.success(res.msg || '镜像已保存')
    saveImageVisible.value = false
    if (imgVisible.value) await loadImages()
  } finally {
    saveImageLoading.value = false
  }
}

async function openTerminal(row) {
  try {
    const res = await getCgi('/pages/page_sandbox.cgi', { action: 'get', id: row.id })
    const latest = res.data || row
    Object.assign(row, latest)
    if (latest.status !== 'running') {
      ElMessage.warning('请先启动沙箱')
      load()
      return
    }
  } catch {
    if (row.status !== 'running') {
      ElMessage.warning('请先启动沙箱')
      return
    }
  }
  termSandboxId.value = row.id
  termVisible.value = true
}

function openImages() {
  imgVisible.value = true
}

async function loadImages() {
  imgLoading.value = true
  try {
    const [imgRes, infoRes] = await Promise.all([
      getCgi('/pages/page_sandbox.cgi', { action: 'images' }),
      getCgi('/pages/page_sandbox.cgi', { action: 'docker_info' }),
    ])
    images.value = imgRes.data || []
    dockerInfo.value = infoRes.data || {}
  } finally {
    imgLoading.value = false
  }
}

async function pullImage() {
  if (!pullName.value.trim()) {
    ElMessage.warning('请输入镜像名')
    return
  }
  imgLoading.value = true
  try {
    await postCgi('/pages/page_sandbox.cgi', { action: 'pull_image', image_name: pullName.value.trim() })
    ElMessage.success('拉取成功')
    pullName.value = ''
    await loadImages()
  } finally {
    imgLoading.value = false
  }
}

async function parseCgiResponse(res) {
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    const text = await res.text()
    const preview = text.replace(/\s+/g, ' ').slice(0, 80)
    throw new Error(
      res.ok
        ? `服务器返回非 JSON 响应：${preview}`
        : `上传失败 (HTTP ${res.status})，可能是文件过大或网关超时：${preview}`
    )
  }
  return res.json()
}

async function onUploadImage(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  imgLoading.value = true
  try {
    const fd = new FormData()
    fd.append('action', 'import_image')
    fd.append('file', file)
    const res = await fetch('/pages/page_sandbox.cgi', { method: 'POST', body: fd, credentials: 'include' })
    const data = await parseCgiResponse(res)
    if (!res.ok || data.code !== 0) throw new Error(data.msg || `导入失败 (HTTP ${res.status})`)
    ElMessage.success(`导入成功: ${(data.data?.tags || []).join(', ') || file.name}`)
    await loadImages()
  } catch (e) {
    ElMessage.error(e.message || '导入失败')
  } finally {
    imgLoading.value = false
    ev.target.value = ''
  }
}

async function downloadImage(row) {
  const tag = row.tag || row.tags?.[0]
  if (!tag || tag === '<none>') {
    ElMessage.warning('该镜像无标签，无法导出')
    return
  }
  imgLoading.value = true
  try {
    const res = await fetch(
      `/pages/page_sandbox.cgi?action=export_image&image=${encodeURIComponent(tag)}`,
      { credentials: 'include' }
    )
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.msg || '下载失败')
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${tag.replace(/[/:]/g, '_')}.tar`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e.message || '下载失败')
  } finally {
    imgLoading.value = false
  }
}

async function deleteImage(row) {
  const ref = row.tag || row.id || row.full_id
  await ElMessageBox.confirm(`确定删除镜像 ${ref}？`, '警告', { type: 'warning' })
  await postCgi('/pages/page_sandbox.cgi', { action: 'delete_image', id: ref })
  ElMessage.success('删除成功')
  loadImages()
}

onMounted(async () => {
  await loadSettings()
  await load()
})

onActivated(() => {
  load({ background: true })
})
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px; min-height: 400px; }
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
.header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.summary { font-size: 13px; color: #909399; margin-bottom: 16px; }
.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 1200px) { .card-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 768px) { .card-grid { grid-template-columns: 1fr; } }
.card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px;
  min-width: 0;
  transition: box-shadow 0.2s;
}
.card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.card-title { font-size: 16px; font-weight: 600; color: #303133; display: flex; align-items: center; gap: 6px; }
.vis-icon { color: #909399; }
.vis-icon.pub { color: #67c23a; }
.card-meta { display: flex; gap: 12px; font-size: 13px; color: #909399; margin-bottom: 12px; }
.resource-rows { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; min-width: 0; }
.resource-row { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #606266; min-width: 0; }
.res-icon {
  flex: 0 0 22px;
  width: 22px;
  height: 22px;
  min-width: 22px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  line-height: 1;
  font-weight: 600;
  color: #fff;
}
.resource-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.res-icon.orange { background: #e6a23c; }
.res-icon.blue { background: #409eff; }
.res-icon.green { background: #67c23a; }
.card-footer { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #f0f0f0; padding-top: 12px; }
.img-toolbar { display: flex; gap: 8px; margin-bottom: 12px; align-items: center; }
.img-tag { background: #ecf5ff; color: #409eff; padding: 2px 8px; border-radius: 4px; font-size: 13px; }
.field-hint { font-size: 12px; color: #909399; margin-top: 6px; line-height: 1.4; }
</style>
