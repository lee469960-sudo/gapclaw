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
          placeholder="搜索名称或备注..."
          clearable
          style="width:280px"
          @keyup.enter="doSearch"
        />
        <el-button type="primary" @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button type="primary" @click="openForm()">
          <el-icon><Plus /></el-icon>
          添加 HttpMCP
        </el-button>
      </div>
    </div>

    <div class="summary">共 {{ list.length }} 条，已显示 {{ filtered.length }} 条</div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">
            <el-icon class="http-icon"><Link /></el-icon>
            {{ row.name }}
          </span>
          <el-tag size="small" effect="plain">v{{ row.version || '1.0.0' }}</el-tag>
        </div>
        <div class="card-meta">
          <div><span class="label">ID</span> {{ row.id }}</div>
          <div><span class="label">工具</span> {{ (row.tools || []).length }} 个</div>
          <div><span class="label">创建人</span> {{ row.creator || '-' }}</div>
          <div><span class="label">时间</span> {{ row.modified_at || '-' }}</div>
        </div>
        <div v-if="row.description" class="card-desc">{{ row.description }}</div>
        <div class="card-tags">
          <el-tag size="small" :type="row.visibility === 'public' ? 'success' : 'info'">
            {{ row.visibility === 'public' ? '公共' : '私有' }}
          </el-tag>
          <el-tag
            v-for="t in (row.tools || []).slice(0, 3)"
            :key="t.id || t.name"
            size="small"
            type="warning"
            effect="plain"
          >
            {{ t.method || 'GET' }} {{ t.name }}
          </el-tag>
          <span v-if="(row.tools || []).length > 3" class="more-tools">+{{ row.tools.length - 3 }}</span>
        </div>
        <div class="card-actions">
          <el-button size="small" type="warning" plain :loading="testingId === row.id" @click="openTest(row)">
            <el-icon><VideoPlay /></el-icon>
            测试
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
      <el-empty v-if="!loading && !filtered.length" description="暂无 HttpMCP">
        <template #description>
          <p>暂无 HttpMCP 配置</p>
          <p class="empty-hint">点击右上角「添加 HttpMCP」注册服务与工具</p>
        </template>
      </el-empty>
    </div>

    <!-- 添加 / 编辑 HttpMCP 服务 -->
    <el-dialog
      v-model="formVisible"
      :title="form.id ? '编辑 HttpMCP' : '添加 HttpMCP'"
      width="720px"
      top="6vh"
      destroy-on-close
      class="httpmcp-dialog"
      @closed="resetForm"
    >
      <section class="section">
        <div class="section-title">基本信息</div>
        <el-form :model="form" label-position="top">
          <div class="row-2">
            <el-form-item label="名称" required>
              <el-input v-model="form.name" placeholder="如：百宝箱" />
            </el-form-item>
            <el-form-item label="版本">
              <el-input v-model="form.version" placeholder="1.0.0" />
            </el-form-item>
          </div>
          <el-form-item label="备注">
            <el-input v-model="form.description" placeholder="备注说明" />
          </el-form-item>
        </el-form>
      </section>

      <section class="section">
        <div class="section-head">
          <div>
            <div class="section-title">授权</div>
            <div class="section-hint">（可选，留空则不验证）</div>
          </div>
          <el-button size="small" @click="addAuth">+ 添加</el-button>
        </div>
        <div v-if="form.auth.length" class="kv-list">
          <div v-for="(item, idx) in form.auth" :key="'auth-' + idx" class="kv-row">
            <el-input v-model="item.key" placeholder="Header Key，如 Authorization" />
            <el-input v-model="item.value" placeholder="值" show-password />
            <el-button type="danger" link @click="form.auth.splice(idx, 1)">删除</el-button>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="section-head">
          <div class="section-title">工具列表</div>
          <el-button type="primary" size="small" @click="openToolForm()">+ 添加工具</el-button>
        </div>
        <el-empty v-if="!form.tools.length" description="暂无工具，点击右上角添加" :image-size="64" />
        <div v-else class="tool-list">
          <div v-for="(t, idx) in form.tools" :key="t.id || idx" class="tool-item">
            <div class="tool-main">
              <el-tag size="small" type="warning" effect="dark">{{ t.method || 'GET' }}</el-tag>
              <span class="tool-name">{{ t.name }}</span>
              <span class="tool-url" :title="t.url">{{ t.url }}</span>
            </div>
            <div class="tool-actions">
              <el-button size="small" link type="primary" @click="openToolForm(t, idx)">编辑</el-button>
              <el-button size="small" link type="danger" @click="form.tools.splice(idx, 1)">删除</el-button>
            </div>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="section-title">权限设置</div>
        <el-radio-group v-model="form.visibility" class="perm-radios">
          <el-radio value="public">公共</el-radio>
          <el-radio value="private">私有</el-radio>
        </el-radio-group>
        <div class="section-hint">输入允许访问的用户名，多个用逗号分隔，不填写仅本人可见</div>
        <el-input
          v-if="form.visibility === 'private'"
          v-model="form.allowedUsersStr"
          placeholder="如：user1,user2,user3"
          style="margin-top:8px"
        />
      </section>

      <template #footer>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        <el-button @click="formVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <!-- 新工具 -->
    <el-dialog
      v-model="toolVisible"
      title="新工具"
      width="640px"
      top="8vh"
      destroy-on-close
      append-to-body
      class="tool-dialog"
    >
      <el-form :model="toolForm" label-position="top">
        <div class="row-2">
          <el-form-item label="名称" required>
            <el-input v-model="toolForm.name" placeholder="工具名称" />
          </el-form-item>
          <el-form-item label="请求方式">
            <el-select v-model="toolForm.method" style="width:100%">
              <el-option label="GET" value="GET" />
              <el-option label="POST" value="POST" />
              <el-option label="PUT" value="PUT" />
              <el-option label="PATCH" value="PATCH" />
              <el-option label="DELETE" value="DELETE" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="URL" required>
          <el-input v-model="toolForm.url" placeholder="http://example.com/api" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="toolForm.description" placeholder="工具描述" />
        </el-form-item>
        <el-form-item label="超时(秒)">
          <el-input-number v-model="toolForm.timeout" :min="1" :max="7200" controls-position="right" />
        </el-form-item>

        <div class="pair-block">
          <div class="pair-head">
            <span>Headers</span>
            <el-button size="small" @click="toolForm.headers.push({ key: '', value: '' })">+ Header</el-button>
          </div>
          <div v-for="(h, i) in toolForm.headers" :key="'h-' + i" class="kv-row">
            <el-input v-model="h.key" placeholder="Key" />
            <el-input v-model="h.value" placeholder="Value" />
            <el-button type="danger" link @click="toolForm.headers.splice(i, 1)">删除</el-button>
          </div>
        </div>

        <div class="pair-block">
          <div class="pair-head">
            <span>fixed_args</span>
            <el-button size="small" @click="toolForm.fixed_args.push({ key: '', value: '' })">+ 固定参数</el-button>
          </div>
          <div v-for="(a, i) in toolForm.fixed_args" :key="'f-' + i" class="kv-row">
            <el-input v-model="a.key" placeholder="参数名" />
            <el-input v-model="a.value" placeholder="固定值" />
            <el-button type="danger" link @click="toolForm.fixed_args.splice(i, 1)">删除</el-button>
          </div>
        </div>

        <div class="pair-block">
          <div class="pair-head">
            <span>args</span>
            <el-button size="small" @click="toolForm.args.push({ name: '', type: 'string', required: false, description: '' })">
              + 参数
            </el-button>
          </div>
          <div v-for="(a, i) in toolForm.args" :key="'a-' + i" class="arg-row">
            <el-input v-model="a.name" placeholder="参数名" />
            <el-select v-model="a.type" style="width:110px">
              <el-option label="string" value="string" />
              <el-option label="number" value="number" />
              <el-option label="boolean" value="boolean" />
              <el-option label="object" value="object" />
            </el-select>
            <el-checkbox v-model="a.required">必填</el-checkbox>
            <el-input v-model="a.description" placeholder="说明" />
            <el-button type="danger" link @click="toolForm.args.splice(i, 1)">删除</el-button>
          </div>
        </div>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="confirmTool">确定</el-button>
        <el-button @click="toolVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <!-- 测试 -->
    <el-dialog v-model="testVisible" :title="`测试 · ${testTarget?.name || ''}`" width="680px" destroy-on-close>
      <el-form label-width="100px">
        <el-form-item v-if="(testTarget?.tools || []).length" label="工具">
          <el-select v-model="testTool" style="width:100%" placeholder="选择工具">
            <el-option
              v-for="t in testTarget.tools"
              :key="t.id || t.name"
              :label="`${t.method || 'GET'} · ${t.name}`"
              :value="t.name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="变量 JSON">
          <el-input
            v-model="testBodyStr"
            type="textarea"
            :rows="6"
            placeholder='如 {"query": "hello"}，可选 "tool": "工具名"'
          />
        </el-form-item>
      </el-form>
      <el-button type="primary" :loading="testing" @click="runTest">发送测试请求</el-button>
      <div v-if="testResult" class="test-result">
        <div class="result-label">响应结果</div>
        <pre>{{ testResult }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Link, VideoPlay } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { getCurrentUsername } from '../session'

const list = ref([])
const loading = ref(false)
const saving = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const currentUser = ref('')
const testingId = ref('')
const testing = ref(false)

const formVisible = ref(false)
const toolVisible = ref(false)
const toolEditIndex = ref(-1)
const testVisible = ref(false)
const testTarget = ref(null)
const testTool = ref('')
const testBodyStr = ref('{}')
const testResult = ref('')

const defaultForm = () => ({
  id: '',
  name: '',
  version: '1.0.0',
  description: '',
  auth: [],
  tools: [],
  visibility: 'private',
  allowedUsersStr: '',
})

const defaultTool = () => ({
  id: '',
  name: '',
  method: 'GET',
  url: '',
  description: '',
  timeout: 300,
  headers: [],
  fixed_args: [],
  args: [],
  body_template: '',
})

const form = reactive(defaultForm())
const toolForm = reactive(defaultTool())

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

function parseAllowedUsers(str) {
  return str ? str.split(/[,，]/).map((s) => s.trim()).filter(Boolean) : []
}

async function loadCurrentUser() {
  currentUser.value = getCurrentUsername()
}

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_httpmcp.cgi', { action: 'list' })
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
  form.auth = []
  form.tools = []
}

function openForm(row) {
  resetForm()
  if (row) {
    Object.assign(form, {
      id: row.id,
      name: row.name,
      version: row.version || '1.0.0',
      description: row.description || '',
      auth: (row.auth || []).map((a) => ({ key: a.key || '', value: a.value || '' })),
      tools: JSON.parse(JSON.stringify(row.tools || [])),
      visibility: row.visibility || 'private',
      allowedUsersStr: (row.allowed_users || []).join(','),
    })
  }
  formVisible.value = true
}

function addAuth() {
  form.auth.push({ key: '', value: '' })
}

function openToolForm(tool, idx = -1) {
  toolEditIndex.value = idx
  Object.assign(toolForm, defaultTool())
  if (tool) {
    Object.assign(toolForm, {
      id: tool.id || '',
      name: tool.name || '',
      method: tool.method || 'GET',
      url: tool.url || '',
      description: tool.description || '',
      timeout: tool.timeout ?? 300,
      headers: (tool.headers || []).map((h) => ({ key: h.key || '', value: h.value || '' })),
      fixed_args: (tool.fixed_args || []).map((a) => ({ key: a.key || '', value: a.value || '' })),
      args: (tool.args || []).map((a) => ({
        name: a.name || '',
        type: a.type || 'string',
        required: !!a.required,
        description: a.description || '',
      })),
      body_template: tool.body_template || '',
    })
  }
  toolVisible.value = true
}

function confirmTool() {
  if (!toolForm.name?.trim() || !toolForm.url?.trim()) {
    ElMessage.warning('请填写工具名称和 URL')
    return
  }
  const payload = {
    id: toolForm.id || `t_${Date.now().toString(36)}`,
    name: toolForm.name.trim(),
    method: toolForm.method,
    url: toolForm.url.trim(),
    description: toolForm.description || '',
    timeout: toolForm.timeout || 300,
    headers: toolForm.headers.filter((h) => h.key?.trim()),
    fixed_args: toolForm.fixed_args.filter((a) => a.key?.trim()),
    args: toolForm.args.filter((a) => a.name?.trim()),
    body_template: toolForm.body_template || '',
  }
  if (toolEditIndex.value >= 0) {
    form.tools[toolEditIndex.value] = payload
  } else {
    form.tools.push(payload)
  }
  toolVisible.value = false
}

async function save() {
  if (!form.name?.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  saving.value = true
  try {
    const res = await postCgi('/pages/page_httpmcp.cgi', {
      action: form.id ? 'update' : 'create',
      id: form.id || undefined,
      name: form.name.trim(),
      version: form.version || '1.0.0',
      description: form.description || '',
      auth: form.auth.filter((a) => a.key?.trim()),
      tools: form.tools,
      visibility: form.visibility,
      allowed_users: parseAllowedUsers(form.allowedUsersStr),
    })
    ElMessage.success(res.msg || '保存成功')
    formVisible.value = false
    load()
  } finally {
    saving.value = false
  }
}

function openTest(row) {
  testTarget.value = row
  testTool.value = row.tools?.[0]?.name || ''
  testBodyStr.value = '{}'
  testResult.value = ''
  testVisible.value = true
}

async function runTest() {
  let testBody = {}
  try {
    testBody = JSON.parse(testBodyStr.value || '{}')
  } catch {
    ElMessage.error('变量必须是合法 JSON')
    return
  }
  testingId.value = testTarget.value?.id
  testing.value = true
  testResult.value = ''
  try {
    const res = await postCgi('/pages/page_httpmcp.cgi', {
      action: 'test',
      id: testTarget.value.id,
      tool: testTool.value || undefined,
      test_body: testBody,
    })
    testResult.value = JSON.stringify(res.data, null, 2)
  } catch (e) {
    testResult.value = e?.msg || e?.message || '测试失败'
  } finally {
    testing.value = false
    testingId.value = ''
  }
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除「${row.name}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/page_httpmcp.cgi', { action: 'delete', id: row.id })
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
  border: 1px solid #ebeef5; border-radius: 8px; padding: 16px;
  display: flex; flex-direction: column; transition: box-shadow 0.2s;
}
.card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 10px; }
.card-title { font-size: 16px; font-weight: 600; color: #303133; display: flex; align-items: center; gap: 8px; min-width: 0; }
.http-icon { color: #e6a23c; font-size: 18px; flex-shrink: 0; }
.card-meta { font-size: 13px; color: #606266; line-height: 1.8; margin-bottom: 8px; }
.card-meta .label { color: #909399; margin-right: 6px; }
.card-desc {
  font-size: 13px; color: #606266; line-height: 1.6; margin-bottom: 10px; flex: 1;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; align-items: center; }
.more-tools { font-size: 12px; color: #909399; }
.card-actions {
  display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end;
  border-top: 1px solid #f0f0f0; padding-top: 12px; margin-top: auto;
}
.empty-hint { font-size: 12px; color: #909399; margin-top: 4px; }

.section { padding: 4px 0 16px; border-bottom: 1px solid #f0f2f5; margin-bottom: 12px; }
.section:last-of-type { border-bottom: none; margin-bottom: 0; }
.section-title { font-size: 15px; font-weight: 600; color: #303133; }
.section-hint { font-size: 12px; color: #909399; margin-top: 4px; }
.section-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.perm-radios { display: block; margin: 8px 0; }
.kv-list, .kv-row, .arg-row { display: flex; flex-direction: column; gap: 8px; }
.kv-row, .arg-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.arg-row {
  grid-template-columns: 1fr 110px auto 1.2fr auto;
}
.tool-list { display: flex; flex-direction: column; gap: 8px; }
.tool-item {
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
  border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 12px;
}
.tool-main { display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1; }
.tool-name { font-weight: 600; color: #303133; flex-shrink: 0; }
.tool-url {
  font-size: 12px; color: #909399; font-family: monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tool-actions { flex-shrink: 0; }
.pair-block { margin-bottom: 16px; }
.pair-head {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 8px; font-size: 13px; color: #606266; font-weight: 500;
}
.test-result { margin-top: 16px; }
.result-label { font-size: 13px; color: #606266; margin-bottom: 8px; font-weight: 600; }
.test-result pre {
  background: #282c34; color: #abb2bf; padding: 12px 16px; border-radius: 8px;
  font-size: 12px; line-height: 1.5; max-height: 360px; overflow: auto; margin: 0;
}
@media (max-width: 640px) {
  .row-2 { grid-template-columns: 1fr; }
  .arg-row { grid-template-columns: 1fr; }
}
</style>
