<template>
  <el-dialog
    v-model="visible"
    :title="`会话管理 - ${agent?.name || agentId}`"
    width="640px"
    @open="loadSessions"
  >
    <div class="create-row">
      <el-input v-model="newName" placeholder="会话名称" clearable />
      <el-input v-model="newSessionId" placeholder="会话ID (选填)" clearable />
      <el-button type="primary" :loading="creating" @click="createSession">+ 创建</el-button>
    </div>

    <div class="list-title">
      <el-icon><Document /></el-icon>
      会话列表
    </div>

    <div v-loading="loading" class="session-list">
      <div v-for="s in sessions" :key="s.session_id" class="session-card">
        <div class="session-info">
          <div class="session-name">
            {{ s.name }}
            <el-tag v-if="isImSession(s)" size="small" type="success" effect="plain" style="margin-left:6px">渠道</el-tag>
          </div>
          <div class="session-id">ID: {{ shortId(s.session_id) }}</div>
        </div>
        <div class="session-actions">
          <el-button link @click="editSession(s)"><el-icon><Edit /></el-icon></el-button>
          <el-button link @click="cloneSession(s)"><el-icon><CopyDocument /></el-icon></el-button>
          <el-button link type="danger" @click="deleteSession(s)"><el-icon><Delete /></el-icon></el-button>
          <el-button type="primary" circle @click="enterSession(s)"><el-icon><ArrowRight /></el-icon></el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !sessions.length" description="暂无会话" />
    </div>

    <template #footer>
      <el-button type="primary" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Edit, CopyDocument, Delete, ArrowRight } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'

const props = defineProps({
  modelValue: Boolean,
  agentId: String,
  agent: Object,
})

const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const sessions = ref([])
const loading = ref(false)
const creating = ref(false)
const newName = ref('')
const newSessionId = ref('')

function shortId(id) {
  return id ? id.slice(0, 8) : ''
}

function isImSession(s) {
  if (s?.source && String(s.source).startsWith('im:')) return true
  const n = s?.name || ''
  return ['飞书', '钉钉', 'Telegram', 'QQ', '企业微信', 'Mock渠道'].includes(n) || String(n).startsWith('IM:')
}

async function loadSessions() {
  if (!props.agentId) return
  loading.value = true
  try {
    const res = await getCgi('/pages/page_agent.cgi', { action: 'get', id: props.agentId })
    sessions.value = res.data?.session_list || []
  } finally {
    loading.value = false
  }
}

async function createSession() {
  if (!newName.value.trim()) {
    ElMessage.warning('请填写会话名称')
    return
  }
  creating.value = true
  try {
    await postCgi('/pages/page_agent.cgi', {
      action: 'add_session',
      id: props.agentId,
      session_name: newName.value.trim(),
      session_id: newSessionId.value.trim() || undefined,
    })
    ElMessage.success('创建成功')
    newName.value = ''
    newSessionId.value = ''
    await loadSessions()
  } finally {
    creating.value = false
  }
}

async function editSession(s) {
  const { value } = await ElMessageBox.prompt('会话名称', '编辑会话', {
    inputValue: s.name,
    confirmButtonText: '保存',
  })
  if (!value?.trim()) return
  await postCgi('/pages/page_agent.cgi', {
    action: 'update_session',
    id: props.agentId,
    session_id: s.session_id,
    session_name: value.trim(),
  })
  ElMessage.success('已更新')
  loadSessions()
}

async function cloneSession(s) {
  const { value } = await ElMessageBox.prompt('新会话名称', '克隆会话', {
    inputValue: `${s.name}-副本`,
    confirmButtonText: '克隆',
  })
  if (!value?.trim()) return
  await postCgi('/pages/page_agent.cgi', {
    action: 'clone_session',
    id: props.agentId,
    source_session_id: s.session_id,
    session_name: value.trim(),
  })
  ElMessage.success('克隆成功')
  loadSessions()
}

async function deleteSession(s) {
  if (sessions.value.length <= 1) {
    ElMessage.warning('至少保留一个会话')
    return
  }
  await ElMessageBox.confirm(`确定删除会话「${s.name}」？`, '删除会话', { type: 'warning' })
  await postCgi('/pages/page_agent.cgi', {
    action: 'delete_session',
    id: props.agentId,
    session_id: s.session_id,
  })
  ElMessage.success('已删除')
  loadSessions()
}

function enterSession(s) {
  visible.value = false
  const url = `/agents/${props.agentId}/chat?session=${encodeURIComponent(s.session_id)}`
  window.open(url, '_blank', 'noopener,noreferrer')
}
</script>

<style scoped>
.create-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 10px;
  margin-bottom: 20px;
}
.list-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #303133;
}
.session-list { min-height: 120px; }
.session-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  margin-bottom: 10px;
}
.session-name { font-weight: 600; font-size: 15px; }
.session-id { font-size: 13px; color: #909399; margin-top: 4px; }
.session-actions { display: flex; align-items: center; gap: 4px; }
</style>
