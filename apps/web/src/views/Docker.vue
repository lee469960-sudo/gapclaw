<template>
  <div class="page" v-loading="loading">
    <!-- 顶栏 -->
    <div class="header">
      <div class="header-left">
        <el-icon class="docker-icon"><Box /></el-icon>
        <span class="page-title">Docker 管理</span>
        <el-tag
          :type="info.connected ? 'success' : 'danger'"
          size="small"
          effect="dark"
          round
        >
          {{ info.connected ? '已连接' : '未连接' }}
        </el-tag>
      </div>
      <div class="header-actions">
        <el-input
          v-model="keyword"
          placeholder="搜索容器名称、镜像..."
          clearable
          style="width:280px"
          @keyup.enter="doSearch"
          @clear="resetSearch"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>
        <el-button type="primary" @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <!-- Docker 信息 -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-icon connect">
          <el-icon><Connection /></el-icon>
        </div>
        <div class="stat-body">
          <div class="stat-label">连接状态</div>
          <div class="stat-value">{{ info.connected ? '已连接' : '未连接' }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon version">
          <el-icon><Cpu /></el-icon>
        </div>
        <div class="stat-body">
          <div class="stat-label">Docker 版本</div>
          <div class="stat-value">{{ info.version || '-' }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon cpu">
          <el-icon><Monitor /></el-icon>
        </div>
        <div class="stat-body">
          <div class="stat-label">CPU 核心</div>
          <div class="stat-value">{{ info.cpu_count ?? '-' }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon mem">
          <el-icon><Coin /></el-icon>
        </div>
        <div class="stat-body">
          <div class="stat-label">内存</div>
          <div class="stat-value">{{ formatMem(info.memory_mb) }}</div>
        </div>
      </div>
    </div>

    <el-alert
      v-if="info.error"
      type="warning"
      :title="info.error"
      show-icon
      :closable="false"
      class="error-alert"
    />

    <!-- 容器 / 镜像 -->
    <div class="panel">
      <el-tabs v-model="activeTab">
        <el-tab-pane name="containers">
          <template #label>
            <span class="tab-label">
              <el-icon><Box /></el-icon>
              容器
              <el-badge :value="filteredContainers.length" :max="999" type="primary" />
            </span>
          </template>
          <div class="tab-summary">
            共 {{ containers.length }} 个容器，显示 {{ filteredContainers.length }} 个
          </div>
          <el-table
            :data="filteredContainers"
            stripe
            border
            size="default"
            empty-text="暂无容器"
            class="data-table"
          >
            <el-table-column prop="name" label="名称" min-width="140">
              <template #default="{ row }">
                <span class="name-cell">{{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="id" label="ID" width="110">
              <template #default="{ row }">
                <span class="mono">{{ row.id }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="image" label="镜像" min-width="220" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono image-cell">{{ row.image }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="120" align="center">
              <template #default="{ row }">
                <el-tag :type="statusType(row.status)" size="small" effect="light" round>
                  {{ row.status }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane name="images">
          <template #label>
            <span class="tab-label">
              <el-icon><Picture /></el-icon>
              镜像
              <el-badge :value="filteredImages.length" :max="999" type="primary" />
            </span>
          </template>
          <div class="tab-summary">
            共 {{ images.length }} 个镜像，显示 {{ filteredImages.length }} 个
          </div>
          <el-table
            :data="filteredImages"
            stripe
            border
            size="default"
            empty-text="暂无镜像"
            class="data-table"
          >
            <el-table-column prop="id" label="ID" width="110">
              <template #default="{ row }">
                <span class="mono">{{ row.id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="标签" min-width="260" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono image-cell">{{ formatTags(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="size" label="大小" width="110" align="right" />
            <el-table-column prop="created" label="创建时间" width="170" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  Box,
  Search,
  Refresh,
  Connection,
  Cpu,
  Monitor,
  Coin,
  Picture,
} from '@element-plus/icons-vue'
import { getCgi } from '../api'

const loading = ref(false)
const info = ref({})
const containers = ref([])
const images = ref([])
const keyword = ref('')
const searchKw = ref('')
const activeTab = ref('containers')

function formatMem(mb) {
  if (mb == null || mb === '') return '-'
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${mb} MB`
}

function formatTags(row) {
  const tags = row.tags || []
  if (!tags.length || (tags.length === 1 && tags[0] === '<none>')) {
    return row.tag || row.id || '<none>'
  }
  return tags.join(', ')
}

function statusType(status) {
  const s = (status || '').toLowerCase()
  if (s.includes('running') || s.includes('up')) return 'success'
  if (s.includes('exited') || s.includes('dead')) return 'info'
  if (s.includes('paused')) return 'warning'
  if (s.includes('restarting')) return 'warning'
  return 'info'
}

function matchRow(text) {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return true
  return (text || '').toLowerCase().includes(kw)
}

const filteredContainers = computed(() =>
  containers.value.filter((row) =>
    matchRow([row.name, row.id, row.image, row.status].join(' '))
  )
)

const filteredImages = computed(() =>
  images.value.filter((row) =>
    matchRow([row.id, row.tag, formatTags(row), row.size, row.created].join(' '))
  )
)

function doSearch() {
  searchKw.value = keyword.value
}

function resetSearch() {
  keyword.value = ''
  searchKw.value = ''
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_docker.cgi')
    info.value = res.data?.info || {}
    containers.value = res.data?.containers || []
    images.value = res.data?.images || []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page {
  background: var(--gap-card-bg);
  border-radius: 8px;
  padding: 16px 20px;
  min-height: 400px;
  color: var(--gap-text);
}
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.docker-icon {
  font-size: 22px;
  color: var(--gap-primary);
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--gap-text);
}
.header-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 20px;
}
@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 560px) {
  .stat-grid { grid-template-columns: 1fr; }
}
.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  border: 1px solid var(--gap-card-border);
  border-radius: 10px;
  background: var(--gap-card-bg);
  transition: box-shadow 0.2s;
}
.stat-card:hover {
  box-shadow: 0 2px 12px var(--gap-shadow);
}
.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.stat-icon.connect { background: rgba(64, 158, 255, 0.12); color: var(--gap-primary); }
.stat-icon.version { background: rgba(103, 194, 58, 0.12); color: #67c23a; }
.stat-icon.cpu { background: rgba(230, 162, 60, 0.15); color: #e6a23c; }
.stat-icon.mem { background: var(--gap-hover-bg); color: var(--gap-text-muted); }
.stat-label {
  font-size: 12px;
  color: var(--gap-text-muted);
  margin-bottom: 4px;
}
.stat-value {
  font-size: 18px;
  font-weight: 600;
  color: var(--gap-text);
  line-height: 1.2;
}
.error-alert { margin-bottom: 16px; }
.panel {
  border: 1px solid var(--gap-card-border);
  border-radius: 10px;
  padding: 4px 16px 16px;
  background: var(--gap-card-bg);
}
.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tab-summary {
  font-size: 13px;
  color: var(--gap-text-muted);
  margin-bottom: 12px;
}
.data-table {
  width: 100%;
}
.data-table :deep(.el-table__header th) {
  background: var(--gap-hover-bg);
  color: var(--gap-text-secondary);
  font-weight: 600;
}
.mono {
  font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  color: var(--gap-text-secondary);
}
.name-cell {
  font-weight: 500;
  color: var(--gap-text);
}
.image-cell {
  color: var(--gap-text-secondary);
}
</style>
