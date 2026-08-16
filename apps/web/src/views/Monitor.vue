<template>
  <div class="page" v-loading="loading">
    <!-- 顶栏 -->
    <div class="header">
      <div class="header-left">
        <el-icon class="page-icon"><DataLine /></el-icon>
        <span class="page-title">系统监控</span>
        <el-tag
          :type="data.docker_connected ? 'success' : 'info'"
          size="small"
          effect="dark"
          round
        >
          Docker {{ data.docker_connected ? '已连接' : '未连接' }}
        </el-tag>
      </div>
      <div class="header-actions">
        <span v-if="data.time" class="update-time">更新于 {{ data.time }}</span>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-switch
          v-model="autoRefresh"
          active-text="自动刷新"
          inactive-text=""
          @change="onAutoRefreshChange"
        />
      </div>
    </div>

    <!-- 资源指标 -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-icon cpu">
          <el-icon><Cpu /></el-icon>
        </div>
        <div class="stat-body">
          <div class="stat-label">CPU 核数</div>
          <div class="stat-value">{{ data.cpu_count ?? '-' }}</div>
          <div class="stat-sub">逻辑处理器</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon mem">
          <el-icon><Coin /></el-icon>
        </div>
        <div class="stat-body">
          <div class="stat-label">内存</div>
          <div class="stat-value">{{ formatMem(data.memory_mb) }}</div>
          <div class="stat-sub">Docker 可用内存</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon disk">
          <el-icon><FolderOpened /></el-icon>
        </div>
        <div class="stat-body">
          <div class="stat-label">磁盘已用</div>
          <div class="stat-value">{{ formatDisk(data.disk_used_gb) }}</div>
          <div class="stat-sub">
            共 {{ formatDisk(data.disk_total_gb) }}
            <span v-if="diskPercent != null"> · {{ diskPercent }}%</span>
          </div>
          <el-progress
            v-if="diskPercent != null"
            :percentage="diskPercent"
            :stroke-width="6"
            :color="diskProgressColor"
            :show-text="false"
            class="disk-bar"
          />
        </div>
      </div>
    </div>

    <!-- 平台信息 + 业务统计 -->
    <div class="panel-row">
      <div class="panel info-panel">
        <div class="panel-title">
          <el-icon><Monitor /></el-icon>
          系统信息
        </div>
        <div class="info-list">
          <div class="info-item">
            <span class="info-label">主机</span>
            <span class="info-value mono">{{ data.hostname || '-' }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">平台</span>
            <span class="info-value platform">{{ data.platform || '-' }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">Docker</span>
            <span class="info-value">
              <el-tag
                :type="data.docker_connected ? 'success' : 'danger'"
                size="small"
                effect="light"
                round
              >
                {{ data.docker_connected ? '已连接' : '未连接' }}
              </el-tag>
            </span>
          </div>
          <div class="info-item">
            <span class="info-label">时间</span>
            <span class="info-value">{{ data.time || '-' }}</span>
          </div>
        </div>
      </div>

      <div class="panel biz-panel">
        <div class="panel-title">
          <el-icon><Odometer /></el-icon>
          运行状态
        </div>
        <div class="biz-grid">
          <div class="biz-item">
            <div class="biz-num">{{ data.total_agents ?? 0 }}</div>
            <div class="biz-label">智能体总数</div>
          </div>
          <div class="biz-item highlight">
            <div class="biz-num">{{ data.running_agents ?? 0 }}</div>
            <div class="biz-label">运行中任务</div>
          </div>
          <div class="biz-item">
            <div class="biz-num">{{ data.active_sandboxes ?? 0 }}</div>
            <div class="biz-label">活跃沙箱</div>
          </div>
          <div class="biz-item">
            <div class="biz-num">{{ data.chat_jobs ?? 0 }}</div>
            <div class="biz-label">对话任务</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  DataLine,
  Refresh,
  Cpu,
  Coin,
  FolderOpened,
  Monitor,
  Odometer,
} from '@element-plus/icons-vue'
import { getCgi } from '../api'

const loading = ref(false)
const data = ref({})
const autoRefresh = ref(false)
let timer = null

const diskPercent = computed(() => {
  const used = data.value.disk_used_gb
  const total = data.value.disk_total_gb
  if (!total || total <= 0) return null
  return Math.min(100, Math.round((used / total) * 100))
})

const diskProgressColor = computed(() => {
  const p = diskPercent.value
  if (p == null) return '#409eff'
  if (p >= 90) return '#f56c6c'
  if (p >= 75) return '#e6a23c'
  return '#409eff'
})

function formatMem(mb) {
  if (mb == null || mb === '') return '-'
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Number(mb).toLocaleString()} MB`
}

function formatDisk(gb) {
  if (gb == null || gb === '') return '-'
  return `${Number(gb).toLocaleString()} GB`
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_monitor.cgi')
    data.value = res.data || {}
  } finally {
    loading.value = false
  }
}

function onAutoRefreshChange(on) {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  if (on) {
    timer = setInterval(load, 30000)
  }
}

onMounted(load)
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
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
.page-icon {
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
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.update-time {
  font-size: 12px;
  color: var(--gap-text-muted);
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}
@media (max-width: 900px) {
  .stat-grid { grid-template-columns: 1fr; }
}
.stat-card {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 20px;
  border: 1px solid var(--gap-card-border);
  border-radius: 12px;
  background: var(--gap-card-bg);
  transition: box-shadow 0.2s;
}
.stat-card:hover {
  box-shadow: 0 4px 16px var(--gap-shadow);
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
}
.stat-icon.cpu { background: rgba(230, 162, 60, 0.15); color: #e6a23c; }
.stat-icon.mem { background: rgba(64, 158, 255, 0.12); color: var(--gap-primary); }
.stat-icon.disk { background: rgba(103, 194, 58, 0.12); color: #67c23a; }
.stat-body { flex: 1; min-width: 0; }
.stat-label {
  font-size: 13px;
  color: var(--gap-text-muted);
  margin-bottom: 6px;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--gap-text);
  line-height: 1.2;
}
.stat-sub {
  font-size: 12px;
  color: var(--gap-text-muted);
  margin-top: 4px;
}
.disk-bar {
  margin-top: 10px;
  max-width: 100%;
}
.panel-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 900px) {
  .panel-row { grid-template-columns: 1fr; }
}
.panel {
  border: 1px solid var(--gap-card-border);
  border-radius: 12px;
  padding: 18px 20px;
  background: var(--gap-card-bg);
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--gap-text);
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--gap-card-border);
}
.info-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.info-item {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}
.info-label {
  width: 56px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--gap-text-muted);
}
.info-value {
  flex: 1;
  font-size: 14px;
  color: var(--gap-text);
  word-break: break-all;
}
.info-value.mono {
  font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
  font-weight: 500;
}
.info-value.platform {
  font-size: 13px;
  color: var(--gap-text-secondary);
  line-height: 1.5;
}
.biz-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}
.biz-item {
  text-align: center;
  padding: 16px 12px;
  border-radius: 10px;
  background: var(--gap-hover-bg);
  transition: background 0.2s;
}
.biz-item.highlight {
  background: rgba(64, 158, 255, 0.08);
  border: 1px solid rgba(64, 158, 255, 0.25);
}
.biz-num {
  font-size: 32px;
  font-weight: 700;
  color: var(--gap-text);
  line-height: 1.2;
}
.biz-item.highlight .biz-num {
  color: var(--gap-primary);
}
.biz-label {
  font-size: 13px;
  color: var(--gap-text-muted);
  margin-top: 6px;
}
</style>
