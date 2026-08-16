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
          placeholder="搜索名称/主机..."
          clearable
          style="width:240px"
          @keyup.enter="doSearch"
        />
        <el-button type="primary" @click="doSearch">
          <el-icon><Search /></el-icon>
          搜索
        </el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button type="primary" @click="openForm()">
          <el-icon><Plus /></el-icon>
          添加服务器
        </el-button>
      </div>
    </div>

    <div class="summary">共 {{ list.length }} 条，已显示 {{ filtered.length }} 条</div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">
            <el-icon class="term-icon"><Monitor /></el-icon>
            {{ row.name }}
          </span>
          <el-tag size="small" :type="row.visibility === 'public' ? 'success' : 'info'" effect="plain">
            {{ row.visibility === 'public' ? '公共' : '私有' }}
          </el-tag>
        </div>
        <div class="card-meta">
          <div><span class="label">主机</span> {{ row.host }}:{{ row.port }}</div>
          <div><span class="label">用户</span> {{ row.username }}</div>
          <div><span class="label">认证</span> {{ row.auth_type === 'key' ? '私钥' : '密码' }}</div>
          <div><span class="label">创建人</span> {{ row.creator || '-' }}</div>
        </div>
        <div v-if="row.description" class="card-desc">{{ row.description }}</div>
        <div class="card-actions">
          <el-button size="small" type="success" plain @click="connect(row)">
            <el-icon><Connection /></el-icon>
            连接
          </el-button>
          <el-button size="small" :loading="testingId === row.id" @click="testSaved(row)">测试</el-button>
          <el-button
            v-if="canEdit(row)"
            size="small"
            type="primary"
            plain
            @click="openForm(row)"
          >
            编辑
          </el-button>
          <el-button
            v-if="canEdit(row)"
            size="small"
            type="danger"
            link
            @click="onDelete(row)"
          >
            删除
          </el-button>
        </div>
      </div>
      <el-empty
        v-if="!loading && !filtered.length"
        description='暂无 SSH 服务器，点击右上角"添加服务器"按钮添加'
      />
    </div>

    <!-- 添加/编辑 SSH 服务器 -->
    <el-dialog
      v-model="formVisible"
      :title="form.id ? '编辑 SSH 服务器' : '添加 SSH 服务器'"
      width="560px"
      destroy-on-close
      @closed="resetForm"
    >
      <el-form :model="form" label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：测试机A" />
        </el-form-item>
        <el-form-item label="主机" required>
          <div class="host-row">
            <el-input v-model="form.host" placeholder="IP 或域名" />
            <span class="port-label">端口</span>
            <el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="用户名" required>
          <el-input v-model="form.username" placeholder="如：root" />
        </el-form-item>
        <el-form-item label="认证方式">
          <el-radio-group v-model="form.auth_type">
            <el-radio value="password">密码</el-radio>
            <el-radio value="key">私钥</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.auth_type === 'password'" label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="编辑时留空表示不修改"
          />
        </el-form-item>
        <template v-else>
          <el-form-item label="私钥">
            <el-input
              v-model="form.private_key"
              type="textarea"
              :rows="5"
              placeholder="-----BEGIN ... PRIVATE KEY-----"
            />
          </el-form-item>
          <el-form-item label="密码短语">
            <el-input
              v-model="form.passphrase"
              type="password"
              show-password
              placeholder="私钥有密码时填写，编辑留空不修改"
            />
          </el-form-item>
        </template>
        <el-form-item label="备注">
          <el-input v-model="form.description" placeholder="可选" />
        </el-form-item>
        <el-form-item label="权限管理">
          <div class="perm-box">
            <el-radio-group v-model="form.visibility">
              <el-radio value="public">公共</el-radio>
              <el-radio value="private">私有</el-radio>
            </el-radio-group>
            <div class="perm-hint">允许访问的用户名（逗号分隔），不填仅本人可见</div>
            <el-input v-model="form.allowedUsersStr" placeholder="如：user1,user2" />
          </div>
        </el-form-item>
      </el-form>
      <div v-if="testMsg" :class="['test-msg', testOk ? 'ok' : 'err']">{{ testMsg }}</div>
      <template #footer>
        <el-button type="primary" plain :loading="testing" @click="testForm">
          <el-icon><Link /></el-icon>
          测试连接
        </el-button>
        <el-button type="success" :loading="saving" @click="save">保存</el-button>
        <el-button @click="formVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <!-- SSH 终端 -->
    <el-dialog
      v-model="termVisible"
      :title="termTitle"
      width="92%"
      top="4vh"
      destroy-on-close
      class="term-dialog"
      @opened="onTermOpened"
    >
      <XtermPanel
        v-if="termVisible && currentId"
        ref="panelRef"
        variant="ssh"
        :title="termServer?.name || ''"
        ws-url="/pages/page_terminal.ws"
        :init-payload="{ id: currentId }"
      />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus,
  Search,
  Monitor,
  Connection,
  Link,
} from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { getShell, getCurrentUsername } from '../session'
import XtermPanel from '../components/XtermPanel.vue'

const list = ref([])
const loading = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const currentUser = ref('')
const roles = ref([])

const formVisible = ref(false)
const termVisible = ref(false)
const saving = ref(false)
const testing = ref(false)
const testingId = ref('')
const testMsg = ref('')
const testOk = ref(false)

const currentId = ref('')
const termServer = ref(null)
const panelRef = ref(null)

const defaultForm = () => ({
  id: '',
  name: '',
  host: '',
  port: 22,
  username: 'root',
  auth_type: 'password',
  password: '',
  private_key: '',
  passphrase: '',
  description: '',
  visibility: 'private',
  allowedUsersStr: '',
})

const form = reactive(defaultForm())

const filtered = computed(() => {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((r) =>
    r.name?.toLowerCase().includes(kw) ||
    r.host?.toLowerCase().includes(kw) ||
    r.username?.toLowerCase().includes(kw) ||
    r.description?.toLowerCase().includes(kw)
  )
})

const termTitle = computed(() =>
  termServer.value ? `SSH 终端 · ${termServer.value.name}` : 'SSH 终端'
)

function canEdit(row) {
  if (roles.value.includes('master') || roles.value.includes('admin')) return true
  return row.creator === currentUser.value
}

function parseAllowedUsers(str) {
  return str ? str.split(/[,，]/).map((s) => s.trim()).filter(Boolean) : []
}

function formPayload() {
  const body = {
    name: form.name.trim(),
    host: form.host.trim(),
    port: form.port,
    username: form.username.trim(),
    auth_type: form.auth_type,
    description: form.description.trim(),
    visibility: form.visibility,
    allowed_users: parseAllowedUsers(form.allowedUsersStr),
  }
  if (form.id) body.id = form.id
  if (form.password) body.password = form.password
  if (form.private_key) body.private_key = form.private_key
  if (form.passphrase) body.passphrase = form.passphrase
  return body
}

async function loadCurrentUser() {
  const shell = getShell()
  currentUser.value = shell?.username || getCurrentUsername()
  roles.value = shell?.roles || []
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_terminal.cgi', { action: 'list', scope: scope.value })
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
  Object.assign(form, defaultForm())
  testMsg.value = ''
  testOk.value = false
}

function openForm(row) {
  resetForm()
  if (row) {
    Object.assign(form, {
      id: row.id,
      name: row.name,
      host: row.host,
      port: row.port,
      username: row.username,
      auth_type: row.auth_type || 'password',
      description: row.description || '',
      visibility: row.visibility || 'private',
      allowedUsersStr: (row.allowed_users || []).join(','),
    })
  }
  formVisible.value = true
}

async function save() {
  if (!form.name.trim() || !form.host.trim() || !form.username.trim()) {
    ElMessage.warning('请填写名称、主机和用户名')
    return
  }
  saving.value = true
  try {
    const res = await postCgi('/pages/page_terminal.cgi', {
      action: form.id ? 'update' : 'create',
      ...formPayload(),
    })
    ElMessage.success(res.msg || '保存成功')
    formVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function testForm() {
  if (!form.host.trim() || !form.username.trim()) {
    ElMessage.warning('请填写主机和用户名')
    return
  }
  testing.value = true
  testMsg.value = ''
  try {
    const res = await postCgi('/pages/page_terminal.cgi', {
      action: 'test',
      ...formPayload(),
    })
    testOk.value = true
    const latency = res.data?.latency_ms
    testMsg.value = `连接成功${latency != null ? ` · 延迟 ${latency}ms` : ''}`
  } catch (e) {
    testOk.value = false
    testMsg.value = e?.msg || e?.message || '连接失败'
  } finally {
    testing.value = false
  }
}

async function testSaved(row) {
  testingId.value = row.id
  try {
    const res = await postCgi('/pages/page_terminal.cgi', { action: 'test', id: row.id })
    const latency = res.data?.latency_ms
    ElMessage.success(`连接成功${latency != null ? ` · 延迟 ${latency}ms` : ''}`)
  } catch (e) {
    ElMessage.error(e?.msg || '连接失败')
  } finally {
    testingId.value = ''
  }
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除 SSH 服务器「${row.name}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/page_terminal.cgi', { action: 'delete', id: row.id })
  ElMessage.success('删除成功')
  load()
}

function connect(row) {
  currentId.value = row.id
  termServer.value = row
  termVisible.value = true
}

function onTermOpened() {
  nextTick(() => panelRef.value?.initAndConnect?.())
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
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.term-icon { color: #409eff; font-size: 18px; flex-shrink: 0; }
.card-meta { font-size: 13px; color: #606266; line-height: 1.8; margin-bottom: 8px; }
.card-meta .label { color: #909399; margin-right: 6px; }
.card-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 10px;
  flex: 1;
}
.card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  border-top: 1px solid #f0f0f0;
  padding-top: 12px;
  margin-top: auto;
}
.host-row { display: flex; align-items: center; gap: 8px; width: 100%; }
.host-row .el-input { flex: 1; }
.port-label { color: #909399; font-size: 13px; white-space: nowrap; }
.perm-box {
  width: 100%;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
  background: #fafafa;
}
.perm-hint { font-size: 12px; color: #909399; margin: 8px 0; line-height: 1.4; }
.test-msg { font-size: 13px; margin: 0 0 8px 100px; }
.test-msg.ok { color: #67c23a; }
.test-msg.err { color: #f56c6c; }
.term-dialog :deep(.el-dialog__body) { padding-top: 8px; }
</style>
