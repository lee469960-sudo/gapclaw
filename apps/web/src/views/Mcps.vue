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
          placeholder="搜索 MCP 名称或功能描述..."
          clearable
          style="width:280px"
          @keyup.enter="doSearch"
        />
        <el-button type="primary" @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button type="primary" @click="openForm()">+ 注册 MCP</el-button>
      </div>
    </div>

    <div class="summary">共 {{ list.length }} 条，已显示 {{ filtered.length }} 条</div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">
            <el-icon class="mcp-icon"><Connection /></el-icon>
            {{ row.name }}
          </span>
          <el-tag size="small" type="info" effect="plain">{{ protocolLabel(row.protocol) }}</el-tag>
        </div>
        <div class="card-meta">
          <div><span class="label">ID</span> {{ row.id }}</div>
          <div><span class="label">创建人</span> {{ row.creator || '-' }}</div>
          <div><span class="label">时间</span> {{ row.modified_at || '-' }}</div>
        </div>
        <div v-if="row.url" class="url-row" :title="row.url">{{ row.url }}</div>
        <div v-if="row.description" class="card-desc">{{ row.description }}</div>
        <div class="card-tags">
          <el-tag v-for="tag in tagList(row.tags)" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
          <el-tag size="small" :type="row.visibility === 'public' ? 'success' : 'info'">
            {{ row.visibility === 'public' ? '公共' : '私有' }}
          </el-tag>
          <el-tag v-if="row.routing_status === 'missing_capability_metadata'" size="small" type="warning">
            缺少描述或标签，仍参与路由
          </el-tag>
        </div>
        <div class="card-actions">
          <el-button size="small" type="warning" plain :loading="connectingId === row.id" @click="openTools(row)">
            <el-icon><Tools /></el-icon>
            工具
          </el-button>
          <el-button size="small" type="primary" plain @click="openForm(row)">编辑</el-button>
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
      <el-empty v-if="!loading && !filtered.length" description="暂无 MCP 数据">
        <template #description>
          <p>暂无 MCP 数据</p>
          <p class="empty-hint">点击右上角「注册 MCP」按钮添加新的 MCP</p>
        </template>
      </el-empty>
    </div>

    <el-dialog
      v-model="formVisible"
      :title="form.id ? '编辑 MCP' : '注册 MCP'"
      width="560px"
      destroy-on-close
      @closed="resetForm"
    >
      <el-form :model="form" label-width="100px">
        <el-form-item label="MCP 名称" required>
          <el-input v-model="form.name" placeholder="请输入 MCP 名称" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="form.tags" placeholder="多个标签用逗号分隔，如：AI,搜索,数据" />
        </el-form-item>
        <el-form-item label="协议类型" required>
          <el-select v-model="form.protocol" placeholder="请选择协议类型" style="width:100%">
            <el-option value="stdio" label="stdio 本地进程（推荐：得到大脑 @getnote/mcp）" />
            <el-option value="sse" label="SSE / Streamable HTTP（远程 MCP 端点）" />
            <el-option value="http" label="HTTP 简易协议" />
          </el-select>
          <div class="field-hint">
            得到大脑没有远程 SSE 地址；使用固定版本 <code>npx -y @getnote/mcp@1.7.2</code>。
            <code>https://openapi.biji.com/open</code> 是 OpenAPI REST，不是 MCP。
          </div>
        </el-form-item>
        <template v-if="form.protocol === 'stdio'">
          <el-form-item label="启动命令" required>
            <el-input v-model="form.command" placeholder="npx" />
          </el-form-item>
          <el-form-item label="参数">
            <el-input v-model="form.commandArgsStr" placeholder='JSON 数组，如 ["-y","@getnote/mcp@1.7.2"]' />
            <div class="field-hint">
              <el-button link type="primary" @click="fillGetnoteStdio">一键填入得到大脑模板</el-button>
            </div>
          </el-form-item>
          <el-form-item label="环境变量">
            <el-input
              v-model="form.commandEnvStr"
              type="textarea"
              :rows="3"
              placeholder='{"GETNOTE_API_KEY":"gk_live_xxx","GETNOTE_CLIENT_ID":"cli_xxx"}'
            />
          </el-form-item>
        </template>
        <template v-else>
          <el-form-item label="MCP 地址" required>
            <el-input v-model="form.url" placeholder="请输入远程 MCP 服务地址" />
          </el-form-item>
          <el-form-item label="请求头">
            <el-input
              v-model="form.headers"
              type="textarea"
              :rows="3"
              placeholder='JSON 格式，如：{"Authorization": "Bearer xxx"}'
            />
          </el-form-item>
        </template>
        <el-form-item label="功能描述" required>
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            placeholder="请详细描述 MCP 的功能"
          />
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
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="toolVisible" :title="toolTitle" width="720px" destroy-on-close @closed="resetTools">
      <div v-loading="toolLoading">
        <el-alert
          v-if="toolListError"
          type="warning"
          :closable="false"
          show-icon
          class="tool-error"
          :title="toolListError"
        />
        <el-table :data="tools" size="small" max-height="280">
          <el-table-column prop="name" label="工具" width="160" />
          <el-table-column prop="description" label="描述" show-overflow-tooltip />
          <el-table-column label="操作" width="80">
            <template #default="{ row }">
              <el-button link type="primary" @click="selectTool(row)">选用</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-form label-width="80px" style="margin-top:16px">
          <el-form-item label="工具名">
            <el-input v-model="selectedTool" placeholder="如 trade_cal" />
          </el-form-item>
          <el-form-item label="参数 JSON">
            <el-input v-model="toolArgs" type="textarea" :rows="5" placeholder='{"exchange":"SSE","start_date":"20250701","end_date":"20250710"}' />
          </el-form-item>
        </el-form>
        <el-button type="primary" :loading="calling" @click="callTool">调用测试</el-button>
        <pre v-if="toolResult" class="result">{{ toolResult }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Tools } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { getCurrentUsername } from '../session'
import { invalidateListCache } from '../listCache'

const list = ref([])
const loading = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const currentUser = ref('')
const formVisible = ref(false)
const saving = ref(false)
const connectingId = ref('')
const toolVisible = ref(false)
const toolLoading = ref(false)
const calling = ref(false)
const toolTitle = ref('MCP 工具')
const tools = ref([])
const selectedTool = ref('')
const toolArgs = ref('{}')
const toolResult = ref('')
const toolListError = ref('')
const currentId = ref('')

const form = reactive({
  id: '',
  name: '',
  tags: '',
  url: '',
  headers: '{}',
  protocol: 'stdio',
  command: 'npx',
  commandArgsStr: '["-y","@getnote/mcp@1.7.2"]',
  commandEnvStr: '{}',
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
    r.url?.toLowerCase().includes(kw) ||
    r.id?.toLowerCase().includes(kw)
  )
})

function tagList(tags) {
  if (!tags) return []
  return tags.split(/[,，]/).map((t) => t.trim()).filter(Boolean)
}

function protocolLabel(p) {
  const v = (p || 'sse').toLowerCase()
  if (v === 'http') return 'HTTP'
  if (v === 'stdio' || v === 'command' || v === 'npx' || v === 'local') return 'stdio'
  return 'SSE'
}

async function loadCurrentUser() {
  currentUser.value = getCurrentUsername()
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_mcp.cgi', { action: 'list', scope: scope.value })
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
    tags: '',
    url: '',
    headers: '{}',
    protocol: 'stdio',
    command: 'npx',
    commandArgsStr: '["-y","@getnote/mcp@1.7.2"]',
    commandEnvStr: '{}',
    description: '',
    visibility: 'private',
    allowedUsersStr: '',
  })
}

function fillGetnoteStdio() {
  form.protocol = 'stdio'
  form.command = 'npx'
  form.commandArgsStr = '["-y","@getnote/mcp@1.7.2"]'
  let key = ''
  let client = ''
  try {
    const h = JSON.parse(form.headers || '{}')
    key = h.Authorization || h.authorization || ''
    client = h['X-Client-ID'] || h['x-client-id'] || ''
  } catch {
    /* ignore */
  }
  form.commandEnvStr = JSON.stringify(
    {
      GETNOTE_API_KEY: key || 'gk_live_xxx',
      GETNOTE_CLIENT_ID: client || 'cli_xxx',
    },
    null,
    2,
  )
  ElMessage.success('已填入得到大脑 stdio 模板，请确认环境变量中的 Key')
}

function openForm(row) {
  resetForm()
  if (row) {
    Object.assign(form, {
      id: row.id,
      name: row.name,
      tags: row.tags || '',
      url: row.url || '',
      headers: row.headers || '{}',
      protocol: row.protocol || 'sse',
      command: row.command || 'npx',
      commandArgsStr: JSON.stringify(row.command_args?.length ? row.command_args : ['-y', '@getnote/mcp@1.7.2']),
      commandEnvStr: JSON.stringify(row.command_env || {}, null, 2),
      description: row.description || '',
      visibility: row.visibility || 'private',
      allowedUsersStr: (row.allowed_users || []).join(','),
    })
  }
  formVisible.value = true
}

async function save() {
  if (!form.name?.trim()) {
    ElMessage.warning('请填写 MCP 名称')
    return
  }
  if (!form.description?.trim()) {
    ElMessage.warning('请填写功能描述')
    return
  }
  const isStdio = ['stdio', 'command', 'npx', 'local'].includes((form.protocol || '').toLowerCase())
  if (isStdio) {
    if (!form.command?.trim()) {
      ElMessage.warning('请填写启动命令')
      return
    }
  } else if (!form.url?.trim()) {
    ElMessage.warning('请填写 MCP 地址')
    return
  }
  let commandArgs = []
  let commandEnv = {}
  try {
    commandArgs = JSON.parse(form.commandArgsStr || '[]')
    if (!Array.isArray(commandArgs)) throw new Error('args')
  } catch {
    ElMessage.warning('参数必须是 JSON 数组')
    return
  }
  try {
    commandEnv = JSON.parse(form.commandEnvStr || '{}')
    if (!commandEnv || typeof commandEnv !== 'object' || Array.isArray(commandEnv)) throw new Error('env')
  } catch {
    ElMessage.warning('环境变量必须是 JSON 对象')
    return
  }
  try {
    JSON.parse(form.headers || '{}')
  } catch {
    ElMessage.warning('请求头必须是合法 JSON')
    return
  }
  const allowedUsers = form.allowedUsersStr
    ? form.allowedUsersStr.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    : []
  saving.value = true
  try {
    await postCgi('/pages/page_mcp.cgi', {
      action: form.id ? 'update' : 'create',
      id: form.id || undefined,
      name: form.name.trim(),
      tags: form.tags.trim(),
      url: form.url.trim(),
      headers: form.headers.trim() || '{}',
      protocol: form.protocol,
      command: form.command.trim(),
      command_args: commandArgs,
      command_env: commandEnv,
      description: form.description.trim(),
      visibility: form.visibility,
      allowed_users: allowedUsers,
    })
    ElMessage.success('保存成功')
    formVisible.value = false
    invalidateListCache('/pages/page_agent.cgi')
    load()
  } finally {
    saving.value = false
  }
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除 MCP「${row.name}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/page_mcp.cgi', { action: 'delete', id: row.id })
  ElMessage.success('删除成功')
  invalidateListCache('/pages/page_agent.cgi')
  load()
}

function resetTools() {
  tools.value = []
  selectedTool.value = ''
  toolArgs.value = '{}'
  toolResult.value = ''
  toolListError.value = ''
  currentId.value = ''
}

async function openTools(row) {
  currentId.value = row.id
  toolTitle.value = `${row.name} · 工具调试`
  toolVisible.value = true
  toolLoading.value = true
  toolResult.value = ''
  toolListError.value = ''
  try {
    const res = await postCgi('/pages/page_mcp.cgi', { action: 'list_tools', id: row.id })
    tools.value = res.data?.tools || []
    if (tools.value.length && !selectedTool.value) {
      selectedTool.value = tools.value[0].name
    }
    if (!tools.value.length) {
      const err = res.data?.error || '未拉取到工具列表'
      toolListError.value = err
      toolResult.value = err
      ElMessage.warning(err)
    }
  } catch (e) {
    tools.value = []
    toolListError.value = e?.msg || e?.message || '拉取工具失败'
    ElMessage.error(toolListError.value)
  } finally {
    toolLoading.value = false
  }
}

function selectTool(row) {
  selectedTool.value = row.name
  toolArgs.value = '{}'
}

async function callTool() {
  if (!selectedTool.value) {
    ElMessage.warning('请选择或填写工具名')
    return
  }
  let args = {}
  try {
    args = JSON.parse(toolArgs.value || '{}')
  } catch {
    ElMessage.warning('参数必须是合法 JSON')
    return
  }
  calling.value = true
  toolResult.value = ''
  try {
    const res = await postCgi('/pages/page_mcp.cgi', {
      action: 'call_tool',
      id: currentId.value,
      tool: selectedTool.value,
      args,
    })
    const raw = res.data?.result
    if (typeof raw === 'string') {
      try {
        toolResult.value = JSON.stringify(JSON.parse(raw), null, 2)
      } catch {
        toolResult.value = raw
      }
    } else {
      toolResult.value = JSON.stringify(raw, null, 2)
    }
  } finally {
    calling.value = false
  }
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
.card-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  align-items: stretch;
}
@media (max-width: 1200px) { .card-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 768px) { .card-grid { grid-template-columns: minmax(0, 1fr); } }
.card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px;
  transition: box-shadow 0.2s;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
}
.card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 10px; min-width: 0; }
.card-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mcp-icon { color: #409eff; font-size: 18px; flex-shrink: 0; }
.card-meta {
  font-size: 13px;
  color: #606266;
  line-height: 1.8;
  margin-bottom: 8px;
  overflow: hidden;
  word-break: break-all;
}
.card-meta .label { color: #909399; margin-right: 6px; }
.url-row {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-desc {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 10px;
  display: -webkit-box;
  -webkit-line-clamp: 3;
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
.field-hint { font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.4; }
.empty-hint { font-size: 13px; color: #909399; margin-top: 4px; }
.tool-error { margin-bottom: 12px; }
.result {
  margin-top: 12px;
  background: #f5f7fa;
  padding: 12px;
  max-height: 280px;
  overflow: auto;
  border-radius: 6px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
