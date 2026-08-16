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
          placeholder="搜索群名称或描述..."
          clearable
          style="width:260px"
          @keyup.enter="doSearch"
        />
        <el-button type="primary" @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button @click="openTaskList">
          <el-icon><List /></el-icon>
          任务列表
        </el-button>
        <el-button type="primary" @click="openForm()">+ 创建群</el-button>
      </div>
    </div>

    <div class="summary">共 {{ list.length }} 条，已显示 {{ filtered.length }} 条</div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head" @click="enterChat(row)">
          <span class="card-title">
            <el-icon class="group-icon"><UserFilled /></el-icon>
            <el-icon v-if="row.visibility === 'private'" class="vis-icon"><Lock /></el-icon>
            <el-icon v-else class="vis-icon pub"><Unlock /></el-icon>
            {{ row.name }}
          </span>
        </div>
        <div class="card-meta">
          <div><span class="label">ID</span> {{ row.id }}</div>
          <div><span class="label">创建人</span> {{ row.creator || '-' }}</div>
          <div><span class="label">时间</span> {{ row.modified_at || row.created_at || '-' }}</div>
        </div>
        <div class="card-tags">
          <el-tag size="small" type="info">{{ memberLabel(row) }}</el-tag>
        </div>
        <div class="card-desc">{{ row.description || '暂无描述' }}</div>
        <div class="card-actions">
          <el-button size="small" @click.stop="openMembers(row)">成员</el-button>
          <el-button size="small" type="primary" plain @click.stop="openForm(row)">编辑</el-button>
          <el-button
            v-if="row.creator === currentUser"
            size="small"
            type="danger"
            link
            @click.stop="onDelete(row)"
          >
            删除
          </el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !filtered.length" description="暂无智能体群" />
    </div>
    <div v-if="!loading && filtered.length" class="load-tip">已全部加载</div>

    <!-- 创建/编辑群 -->
    <el-dialog
      v-model="formVisible"
      :title="form.id ? '编辑群' : '创建群'"
      width="560px"
      destroy-on-close
      @closed="resetForm"
    >
      <el-form :model="form" label-width="100px">
        <el-form-item label="群名称" required>
          <el-input v-model="form.name" placeholder="请输入群名称" />
        </el-form-item>
        <el-form-item label="群描述">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="简要描述群的用途" />
        </el-form-item>
        <el-form-item label="群公告">
          <el-input
            v-model="form.announcement"
            type="textarea"
            :rows="3"
            placeholder="群公告内容（群聊界面可见）"
          />
        </el-form-item>
        <el-form-item label="历史长度">
          <div class="history-row">
            <el-input-number v-model="form.history_length" :min="0" :max="100" controls-position="right" />
            <span class="unit">轮</span>
          </div>
          <div class="field-hint">每轮 = 用户发言 + Agent 回复（0 = 不携带历史）</div>
        </el-form-item>
        <el-form-item label="权限管理">
          <el-radio-group v-model="form.visibility">
            <el-radio value="public">公共</el-radio>
            <el-radio value="private">私有</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.visibility === 'private'" label="授权用户">
          <el-input v-model="form.allowedUsersStr" placeholder="如：user1,user2,user3" />
          <div class="field-hint">输入允许访问的用户名，多个用逗号分隔，不填写仅本人可见</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ form.id ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <!-- 成员管理 -->
    <el-dialog v-model="memberVisible" :title="`${memberGroup?.name || ''} · 成员管理`" width="680px" destroy-on-close>
      <div class="member-toolbar">
        <el-select v-model="memberPick.agent_id" placeholder="选择 Agent" filterable style="width:220px">
          <el-option v-for="a in agents" :key="a.id" :label="a.name" :value="a.id" />
        </el-select>
        <el-input v-model="memberPick.name" placeholder="群内别名（可选）" style="width:160px" />
        <el-button type="primary" :disabled="!memberPick.agent_id" @click="addMember">添加成员</el-button>
        <el-button @click="enterChat(memberGroup)">进入群聊</el-button>
      </div>
      <el-table :data="memberGroup?.members || []" size="small" stripe>
        <el-table-column label="Agent" min-width="140">
          <template #default="{ row }">{{ agentName(row.agent_id) }}</template>
        </el-table-column>
        <el-table-column prop="agent_id" label="Agent ID" width="100" />
        <el-table-column prop="session_id" label="Session ID" width="100" show-overflow-tooltip />
        <el-table-column prop="name" label="别名" width="120" />
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeMember(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <h4 class="section-title">群会话</h4>
      <el-table :data="memberGroup?.session_list || []" size="small">
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="session_id" label="Session ID" show-overflow-tooltip />
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="primary" @click="enterChat(memberGroup, row.session_id)">进入</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-button size="small" style="margin-top:8px" @click="addSession">新建会话</el-button>
    </el-dialog>

    <!-- 群任务列表 -->
    <el-dialog v-model="taskVisible" title="群任务列表" width="820px" destroy-on-close @opened="loadTasks">
      <div class="task-toolbar">
        <el-input
          v-model="taskKeyword"
          placeholder="按任务 / 群 / 会话 / 创建人 / PM 搜索..."
          clearable
          style="flex:1"
          @keyup.enter="loadTasks"
        />
        <el-button type="primary" @click="loadTasks">
          <el-icon><Search /></el-icon>
          搜索
        </el-button>
        <el-button @click="loadTasks">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <span class="task-count">共 {{ tasks.length }} 条</span>
      </div>
      <el-table v-loading="taskLoading" :data="tasks" size="small" stripe max-height="420" empty-text="暂无群任务">
        <el-table-column prop="task" label="任务" min-width="180" show-overflow-tooltip />
        <el-table-column prop="group_name" label="群" width="120" />
        <el-table-column prop="session_name" label="会话" width="100" />
        <el-table-column prop="creator" label="创建人" width="90" />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="160" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UserFilled, Lock, Unlock, List, Search, Refresh } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { getCurrentUsername } from '../session'

const router = useRouter()
const list = ref([])
const agents = ref([])
const loading = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const currentUser = ref('')
const formVisible = ref(false)
const saving = ref(false)
const memberVisible = ref(false)
const memberGroup = ref(null)
const taskVisible = ref(false)
const taskLoading = ref(false)
const taskKeyword = ref('')
const tasks = ref([])

const form = reactive({
  id: '',
  name: '',
  description: '',
  announcement: '',
  history_length: 10,
  visibility: 'private',
  allowedUsersStr: '',
})

const memberPick = reactive({ agent_id: '', name: '' })

const filtered = computed(() => {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((r) =>
    r.name?.toLowerCase().includes(kw) ||
    r.description?.toLowerCase().includes(kw) ||
    r.id?.toLowerCase().includes(kw)
  )
})

function memberLabel(row) {
  const n = row.member_count ?? (row.members || []).length
  return `${n} 个成员`
}

function agentName(id) {
  return agents.value.find((a) => a.id === id)?.name || id
}

function statusType(s) {
  if (s === 'done') return 'success'
  if (s === 'running') return 'warning'
  if (s === 'error') return 'danger'
  return 'info'
}

async function loadCurrentUser() {
  currentUser.value = getCurrentUsername()
}

async function loadAgents() {
  try {
    const res = await getCgi('/pages/page_agent.cgi', { action: 'list', scope: 'all' })
    agents.value = res.data || []
  } catch {
    agents.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_group.cgi', { action: 'list', scope: scope.value })
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

function resetForm() {
  Object.assign(form, {
    id: '',
    name: '',
    description: '',
    announcement: '',
    history_length: 10,
    visibility: 'private',
    allowedUsersStr: '',
  })
}

function openForm(row) {
  resetForm()
  if (row) {
    Object.assign(form, {
      id: row.id,
      name: row.name,
      description: row.description || '',
      announcement: row.announcement || '',
      history_length: row.history_length ?? 10,
      visibility: row.visibility || 'private',
      allowedUsersStr: (row.allowed_users || []).join(','),
    })
  }
  formVisible.value = true
}

async function save() {
  if (!form.name?.trim()) {
    ElMessage.warning('请填写群名称')
    return
  }
  const allowedUsers = form.allowedUsersStr
    ? form.allowedUsersStr.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    : []
  saving.value = true
  try {
    await postCgi('/pages/page_group.cgi', {
      action: form.id ? 'update' : 'create',
      id: form.id || undefined,
      name: form.name.trim(),
      description: form.description.trim(),
      announcement: form.announcement.trim(),
      history_length: form.history_length,
      visibility: form.visibility,
      allowed_users: allowedUsers,
    })
    ElMessage.success('保存成功')
    formVisible.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除群「${row.name}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/page_group.cgi', { action: 'delete', id: row.id })
  ElMessage.success('删除成功')
  load()
}

async function openMembers(row) {
  const res = await getCgi('/pages/page_group.cgi', { action: 'get', id: row.id })
  memberGroup.value = res.data || row
  memberPick.agent_id = ''
  memberPick.name = ''
  memberVisible.value = true
}

async function addMember() {
  if (!memberPick.agent_id || !memberGroup.value?.id) return
  await postCgi('/pages/page_group.cgi', {
    action: 'add_member',
    group_id: memberGroup.value.id,
    agent_id: memberPick.agent_id,
    session_id: memberPick.agent_id,
    name: memberPick.name.trim(),
  })
  ElMessage.success('添加成功')
  const res = await getCgi('/pages/page_group.cgi', { action: 'get', id: memberGroup.value.id })
  memberGroup.value = res.data
  memberPick.agent_id = ''
  memberPick.name = ''
  load()
}

async function removeMember(row) {
  await ElMessageBox.confirm('确定移除该成员？', '提示')
  await postCgi('/pages/page_group.cgi', {
    action: 'remove_member',
    group_id: memberGroup.value.id,
    agent_id: row.agent_id,
    session_id: row.session_id,
  })
  const res = await getCgi('/pages/page_group.cgi', { action: 'get', id: memberGroup.value.id })
  memberGroup.value = res.data
  load()
}

async function addSession() {
  const { value } = await ElMessageBox.prompt('会话名称', '新建群会话', { inputValue: '新会话' }).catch(() => ({ value: null }))
  if (!value) return
  await postCgi('/pages/page_group.cgi', {
    action: 'add_group_session',
    group_id: memberGroup.value.id,
    session_name: value,
  })
  const res = await getCgi('/pages/page_group.cgi', { action: 'get', id: memberGroup.value.id })
  memberGroup.value = res.data
}

function enterChat(row, sessionId) {
  if (!row?.id) return
  const sid = sessionId || row.session_list?.[0]?.session_id || row.id
  router.push(`/groups/${row.id}/chat?session=${sid}`)
}

function openTaskList() {
  taskKeyword.value = ''
  taskVisible.value = true
}

async function loadTasks() {
  taskLoading.value = true
  try {
    const res = await getCgi('/pages/page_group.cgi', {
      action: 'list_tasks',
      keyword: taskKeyword.value.trim(),
    })
    tasks.value = res.data || []
  } finally {
    taskLoading.value = false
  }
}

onMounted(async () => {
  await loadCurrentUser()
  await loadAgents()
  await load()
})
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px; min-height: 400px; }
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
.header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.summary { font-size: 13px; color: #909399; margin-bottom: 16px; }
.load-tip { text-align: center; color: #c0c4cc; font-size: 13px; padding: 16px 0; }
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
.card-head { cursor: pointer; margin-bottom: 10px; }
.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 6px;
}
.group-icon { color: #409eff; font-size: 18px; }
.vis-icon { color: #909399; font-size: 14px; }
.vis-icon.pub { color: #67c23a; }
.card-meta { font-size: 13px; color: #606266; line-height: 1.8; margin-bottom: 8px; }
.card-meta .label { color: #909399; margin-right: 6px; }
.card-tags { margin-bottom: 8px; }
.card-desc {
  font-size: 13px;
  color: #909399;
  line-height: 1.6;
  margin-bottom: 12px;
  flex: 1;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  border-top: 1px solid #f0f0f0;
  padding-top: 12px;
  margin-top: auto;
}
.field-hint { font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.4; }
.history-row { display: flex; align-items: center; gap: 8px; }
.unit { color: #909399; font-size: 13px; }
.member-toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
.section-title { margin: 16px 0 8px; font-size: 14px; color: #303133; }
.task-toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.task-count { color: #909399; font-size: 13px; margin-left: auto; white-space: nowrap; }
</style>
