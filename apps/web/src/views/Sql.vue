<template>
  <div class="page">
    <!-- 连接配置 -->
    <div class="panel conn-panel">
      <div class="conn-row">
        <span class="field-label">服务器</span>
        <el-select
          v-model="selectedId"
          placeholder="-- 选择已注册的服务器 --"
          clearable
          filterable
          style="width:320px"
          @change="onSelectServer"
        >
          <el-option v-for="s in servers" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-button @click="manageVisible = true">管理</el-button>
        <el-select v-model="conn.db_type" style="width:130px;margin-left:12px" @change="onDbTypeChange">
          <el-option label="MySQL" value="mysql" />
          <el-option label="PostgreSQL" value="postgresql" />
        </el-select>
      </div>
      <div class="conn-grid">
        <div class="field">
          <span class="field-label">IP</span>
          <el-input v-model="conn.host" placeholder="如: 127.0.0.1" />
        </div>
        <div class="field narrow">
          <span class="field-label">端口</span>
          <el-input-number v-model="conn.port" :min="1" :max="65535" controls-position="right" />
        </div>
        <div class="field">
          <span class="field-label">账号</span>
          <el-input v-model="conn.username" />
        </div>
        <div class="field">
          <span class="field-label">密码</span>
          <el-input v-model="conn.password" type="password" show-password placeholder="密码" />
        </div>
        <div class="field">
          <span class="field-label">数据库</span>
          <el-input v-model="conn.database" placeholder="可选" />
        </div>
      </div>
      <div class="conn-grid timeout-row">
        <div class="field narrow">
          <span class="field-label">连接超时(秒)</span>
          <el-input-number v-model="conn.connect_timeout" :min="1" :max="300" controls-position="right" />
        </div>
        <div class="field narrow">
          <span class="field-label">读超时(秒)</span>
          <el-input-number v-model="conn.read_timeout" :min="1" :max="600" controls-position="right" />
        </div>
        <div class="field narrow">
          <span class="field-label">写超时(秒)</span>
          <el-input-number v-model="conn.write_timeout" :min="1" :max="600" controls-position="right" />
        </div>
        <div class="field cache-field">
          <el-checkbox v-model="useCache" @change="onCacheToggle">浏览器缓存</el-checkbox>
        </div>
      </div>
      <div class="conn-actions">
        <el-button type="primary" :loading="schemaLoading" @click="loadSchema">
          <el-icon><FolderOpened /></el-icon>
          加载结构
        </el-button>
        <el-button type="primary" plain @click="openRegister">
          <el-icon><Plus /></el-icon>
          保存服务器
        </el-button>
        <el-button type="warning" :loading="testing" @click="testConn">
          <el-icon><Link /></el-icon>
          测试连接
        </el-button>
        <span v-if="testMsg" :class="['test-msg', testOk ? 'ok' : 'err']">{{ testMsg }}</span>
      </div>
      <div v-if="schema && !schema.error" class="schema-box">
        <div class="schema-head">
          <span>数据库 ({{ schema.databases?.length || 0 }})</span>
          <span v-if="schema.tables?.length">表 ({{ schema.tables.length }})</span>
        </div>
        <div class="schema-body">
          <div class="schema-col">
            <div
              v-for="db in schema.databases"
              :key="db"
              class="schema-item"
              :class="{ active: conn.database === db }"
              @click="pickDatabase(db)"
            >
              {{ db }}
            </div>
          </div>
          <div v-if="schema.tables?.length" class="schema-col">
            <div
              v-for="t in schema.tables"
              :key="t"
              class="schema-item table"
              @click="insertTable(t)"
            >
              {{ t }}
            </div>
          </div>
        </div>
      </div>
      <el-alert v-if="schema?.error" type="error" :title="schema.error" show-icon :closable="false" style="margin-top:12px" />
    </div>

    <!-- SQL 输入 -->
    <div class="panel sql-panel">
      <div class="sql-head">
        <span class="sql-title">SQL 输入</span>
        <span class="sql-hint">Ctrl+Enter 执行 | 支持多语句以分号分隔</span>
      </div>
      <el-input
        ref="sqlInputRef"
        v-model="sql"
        type="textarea"
        :rows="10"
        :placeholder="sqlPlaceholder"
        class="sql-editor"
        @keydown.ctrl.enter.prevent="execute"
      />
      <div class="sql-actions">
        <el-button type="primary" :loading="running" @click="execute">
          <el-icon><VideoPlay /></el-icon>
          执行
        </el-button>
        <el-button @click="formatSql">格式化</el-button>
        <el-button @click="sql = ''">清空</el-button>
        <el-select v-model="rowLimit" style="width:120px">
          <el-option :value="50" label="限制50行" />
          <el-option :value="100" label="限制100行" />
          <el-option :value="200" label="限制200行" />
          <el-option :value="500" label="限制500行" />
          <el-option :value="1000" label="限制1000行" />
        </el-select>
        <div class="spacer" />
        <el-button type="primary" plain @click="openHistory">
          <el-icon><Clock /></el-icon>
          历史记录
        </el-button>
      </div>
    </div>

    <!-- 执行结果 -->
    <div v-if="result" class="panel result-panel">
      <div class="result-meta">
        <span>耗时 {{ result.time_cost ?? '-' }} ms</span>
        <span v-if="result.column_names?.length">返回 {{ result.data?.length || 0 }} 行</span>
        <span v-else-if="result.affected_rows != null">影响 {{ result.affected_rows }} 行</span>
        <span v-if="result.truncated" class="warn">（已截断，仅显示前 {{ rowLimit }} 行）</span>
        <span v-if="result.statement" class="stmt">语句: {{ result.statement }}</span>
      </div>
      <el-table
        v-if="result.column_names?.length"
        :data="tableRows"
        size="small"
        border
        stripe
        max-height="480"
        style="width:100%"
      >
        <el-table-column
          v-for="col in result.column_names"
          :key="col"
          :prop="col"
          :label="col"
          min-width="120"
          show-overflow-tooltip
        />
      </el-table>
      <el-alert v-if="result.error" type="error" :title="result.error" show-icon />
    </div>

    <!-- 注册服务器 -->
    <el-dialog v-model="registerVisible" title="注册服务器" width="520px" destroy-on-close @closed="resetRegister">
      <el-form :model="registerForm" label-width="120px">
        <el-form-item label="服务器名称" required>
          <el-input v-model="registerForm.name" placeholder="如：测试库-北京" />
        </el-form-item>
        <el-form-item label="类型">
          <el-select v-model="registerForm.db_type" style="width:100%">
            <el-option label="MySQL" value="mysql" />
            <el-option label="PostgreSQL" value="postgresql" />
          </el-select>
        </el-form-item>
        <el-form-item label="IP" required>
          <el-input v-model="registerForm.host" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="registerForm.port" :min="1" :max="65535" style="width:100%" />
        </el-form-item>
        <el-form-item label="账号" required>
          <el-input v-model="registerForm.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="registerForm.password" type="password" show-password placeholder="密码" />
        </el-form-item>
        <el-form-item label="默认数据库">
          <el-input v-model="registerForm.database" placeholder="可选" />
        </el-form-item>
        <el-form-item label="连接超时(秒)">
          <el-input-number v-model="registerForm.connect_timeout" :min="1" style="width:100%" />
        </el-form-item>
        <el-form-item label="读超时(秒)">
          <el-input-number v-model="registerForm.read_timeout" :min="1" style="width:100%" />
        </el-form-item>
        <el-form-item label="写超时(秒)">
          <el-input-number v-model="registerForm.write_timeout" :min="1" style="width:100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="registerForm.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRegister">保存</el-button>
      </template>
    </el-dialog>

    <!-- 服务器管理 -->
    <el-dialog v-model="manageVisible" title="服务器管理" width="720px" destroy-on-close @open="load">
      <div class="manage-bar">
        <el-button type="primary" @click="openRegisterFromManage">
          <el-icon><Plus /></el-icon>
          新增
        </el-button>
        <span class="manage-count">共 {{ servers.length }} 台服务器</span>
      </div>
      <el-table v-if="servers.length" :data="servers" size="small" stripe>
        <el-table-column prop="name" label="名称" min-width="160" />
        <el-table-column prop="db_type" label="类型" width="100">
          <template #default="{ row }">{{ row.db_type === 'postgresql' ? 'PostgreSQL' : 'MySQL' }}</template>
        </el-table-column>
        <el-table-column label="地址" min-width="180">
          <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
        </el-table-column>
        <el-table-column prop="database" label="数据库" width="120" />
        <el-table-column prop="creator" label="创建人" width="90" />
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="editServer(row)">编辑</el-button>
            <el-button link type="danger" @click="deleteServer(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty
        v-else
        description='暂无已注册的服务器，点击"新增"或在主界面填好连接信息后点"保存服务器"'
      />
    </el-dialog>

    <!-- SQL 历史 -->
    <el-dialog v-model="historyVisible" title="SQL历史记录" width="760px" destroy-on-close @open="loadHistory">
      <div v-if="history.length" class="history-bar">
        <el-button type="danger" link @click="clearHistory">清空历史</el-button>
      </div>
      <el-table v-if="history.length" :data="history" size="small" stripe max-height="420">
        <el-table-column prop="created_at" label="时间" width="170" />
        <el-table-column label="服务器" width="160">
          <template #default="{ row }">{{ serverName(row.server_id) }}</template>
        </el-table-column>
        <el-table-column prop="sql" label="SQL" min-width="280" show-overflow-tooltip />
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="useHistory(row)">使用</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-else description="暂无历史记录" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Link, FolderOpened, VideoPlay, Clock } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'

const CACHE_KEY = 'gap_sql_conn_cache'
const LEGACY_CACHE_KEYS = ['gclaw_sql_conn_cache']
const SYSTEM_PG_NAME = 'GAP 系统库 (PostgreSQL)'

const servers = ref([])
const selectedId = ref('')
const conn = ref(defaultConn('mysql'))
const useCache = ref(false)
const sql = ref('')
const rowLimit = ref(100)
const running = ref(false)
const testing = ref(false)
const schemaLoading = ref(false)
const saving = ref(false)
const result = ref(null)
const schema = ref(null)
const testMsg = ref('')
const testOk = ref(false)

const registerVisible = ref(false)
const manageVisible = ref(false)
const historyVisible = ref(false)
const registerForm = ref(defaultRegister())
const history = ref([])

const sqlPlaceholder = computed(() =>
  conn.value.db_type === 'postgresql'
    ? `SELECT datname FROM pg_database;\nSELECT * FROM table_name LIMIT 100;\nSELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'table_name';`
    : `SHOW DATABASES;\nSELECT * FROM table_name LIMIT 100;\nDESC table_name;`
)

const tableRows = computed(() => {
  if (!result.value?.column_names?.length) return []
  const cols = result.value.column_names
  return (result.value.data || []).map((row) => {
    const obj = {}
    cols.forEach((c, i) => { obj[c] = row[i] })
    return obj
  })
})

function defaultConn(dbType = 'mysql') {
  const isPg = dbType === 'postgresql'
  return {
    host: '127.0.0.1',
    port: isPg ? 15432 : 3306,
    db_type: dbType,
    // 与平台 SYSTEM_PG_* / compose 默认一致；选中已注册服务器后会覆盖
    username: isPg ? 'gap' : 'root',
    password: isPg ? 'gap' : '',
    database: isPg ? 'gap' : '',
    connect_timeout: 10,
    read_timeout: 60,
    write_timeout: 60,
  }
}

/** 旧版 gclaw 缓存/默认凭据迁移到 gap */
function normalizeLegacyConn(data) {
  if (!data || typeof data !== 'object') return data
  const out = { ...data }
  for (const key of ['username', 'password', 'database']) {
    if (out[key] === 'gclaw') out[key] = 'gap'
  }
  return out
}

function defaultRegister() {
  return { id: '', name: '', remark: '', query_limit: 100, ...defaultConn('mysql') }
}

function onDbTypeChange(type) {
  const keep = { host: conn.value.host, password: conn.value.password, database: conn.value.database }
  const d = defaultConn(type)
  conn.value = { ...d, ...keep, db_type: type }
  if (type === 'postgresql' && conn.value.port === 3306) conn.value.port = 15432
  if (type === 'mysql' && conn.value.port === 15432) conn.value.port = 3306
  persistCache()
}

function loadCache() {
  try {
    let raw = localStorage.getItem(CACHE_KEY)
    let fromLegacy = false
    if (!raw) {
      for (const key of LEGACY_CACHE_KEYS) {
        raw = localStorage.getItem(key)
        if (raw) {
          fromLegacy = true
          localStorage.removeItem(key)
          break
        }
      }
    }
    if (!raw) return
    const data = normalizeLegacyConn(JSON.parse(raw))
    if (data.enabled) {
      useCache.value = true
      conn.value = {
        ...defaultConn(data.db_type || 'mysql'),
        ...data,
        password: data.password || '',
      }
      if (fromLegacy) persistCache()
    }
  } catch { /* ignore */ }
}

function persistCache() {
  if (!useCache.value) {
    localStorage.removeItem(CACHE_KEY)
    return
  }
  localStorage.setItem(CACHE_KEY, JSON.stringify({ enabled: true, ...conn.value }))
}

function onCacheToggle() {
  persistCache()
}

watch(conn, persistCache, { deep: true })

async function load() {
  const res = await getCgi('/pages/page_sql.cgi', { action: 'servers' })
  servers.value = res.data || []
}

async function onSelectServer(id) {
  if (!id) {
    schema.value = null
    return
  }
  const res = await getCgi('/pages/page_sql.cgi', { action: 'server_detail', id })
  if (res.data) {
    conn.value = {
      host: res.data.host,
      port: res.data.port,
      db_type: res.data.db_type || 'mysql',
      username: res.data.username,
      password: res.data.password || '',
      database: res.data.database || '',
      connect_timeout: res.data.connect_timeout ?? 10,
      read_timeout: res.data.read_timeout ?? 60,
      write_timeout: res.data.write_timeout ?? 60,
    }
    schema.value = null
    if (conn.value.db_type === 'postgresql' && !sql.value.trim()) {
      sql.value = 'SELECT datname FROM pg_database;\nSELECT table_name FROM information_schema.tables WHERE table_schema = \'public\' LIMIT 50;'
    }
  }
}

function openRegister() {
  registerForm.value = {
    ...defaultRegister(),
    ...conn.value,
    name: selectedId.value ? (servers.value.find((s) => s.id === selectedId.value)?.name || '') : '',
    id: selectedId.value || '',
  }
  registerVisible.value = true
}

function openRegisterFromManage() {
  registerForm.value = defaultRegister()
  registerVisible.value = true
}

function resetRegister() {
  registerForm.value = defaultRegister()
}

async function editServer(row) {
  const res = await getCgi('/pages/page_sql.cgi', { action: 'server_detail', id: row.id })
  registerForm.value = { ...defaultRegister(), ...res.data, password: res.data?.password || '' }
  registerVisible.value = true
}

async function saveRegister() {
  if (!registerForm.value.name?.trim()) {
    ElMessage.warning('请填写服务器名称')
    return
  }
  saving.value = true
  try {
    const body = { action: 'save_server', ...registerForm.value, query_limit: rowLimit.value }
    if (!body.password) delete body.password
    const res = await postCgi('/pages/page_sql.cgi', body)
    ElMessage.success(res.msg || '保存成功')
    registerVisible.value = false
    await load()
    if (res.data?.id) {
      selectedId.value = res.data.id
      await onSelectServer(res.data.id)
    }
  } finally {
    saving.value = false
  }
}

async function deleteServer(row) {
  await ElMessageBox.confirm(`确定删除服务器「${row.name}」？`, '确认', { type: 'warning' })
  await postCgi('/pages/page_sql.cgi', { action: 'delete_server', id: row.id })
  ElMessage.success('已删除')
  if (selectedId.value === row.id) selectedId.value = ''
  load()
}

async function testConn() {
  testing.value = true
  testMsg.value = ''
  try {
    const res = await postCgi('/pages/page_sql.cgi', { action: 'test', ...conn.value })
    testOk.value = true
    testMsg.value = `连接成功${res.data?.version ? ` · ${String(res.data.version).slice(0, 60)}` : ''}`
  } catch (e) {
    testOk.value = false
    testMsg.value = e?.msg || e?.message || '连接失败'
  } finally {
    testing.value = false
  }
}

async function loadSchema() {
  schemaLoading.value = true
  schema.value = null
  try {
    const body = selectedId.value
      ? { action: 'schema', id: selectedId.value, ...conn.value }
      : { action: 'schema', ...conn.value }
    const res = await postCgi('/pages/page_sql.cgi', body)
    schema.value = res.data
  } catch (e) {
    schema.value = { error: e?.msg || '加载失败' }
  } finally {
    schemaLoading.value = false
  }
}

function pickDatabase(db) {
  conn.value.database = db
  if (selectedId.value) loadSchema()
}

function insertTable(name) {
  const q = conn.value.db_type === 'postgresql'
    ? `SELECT * FROM "${name}" LIMIT ${rowLimit.value};`
    : `SELECT * FROM \`${name}\` LIMIT ${rowLimit.value};`
  sql.value = sql.value ? `${sql.value.trim()}\n${q}` : q
}

async function execute() {
  if (!selectedId.value) {
    ElMessage.warning('请先选择或保存已注册的服务器')
    return
  }
  if (!sql.value.trim()) {
    ElMessage.warning('请输入 SQL')
    return
  }
  running.value = true
  result.value = null
  try {
    const res = await postCgi('/pages/page_sql.cgi', {
      action: 'execute',
      id: selectedId.value,
      sql: sql.value,
      limit: rowLimit.value,
    })
    result.value = res.data
  } catch (e) {
    result.value = { error: e?.msg || '执行失败' }
  } finally {
    running.value = false
  }
}

function formatSql() {
  const keywords = [
    'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'AND', 'OR',
    'ORDER', 'BY', 'GROUP', 'HAVING', 'LIMIT', 'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET',
    'DELETE', 'CREATE', 'ALTER', 'DROP', 'TABLE', 'INDEX', 'VIEW', 'DATABASE', 'SHOW', 'DESC',
    'AS', 'DISTINCT', 'UNION', 'ALL', 'NULL', 'NOT', 'IN', 'EXISTS', 'BETWEEN', 'LIKE',
  ]
  let text = sql.value
  keywords.forEach((kw) => {
    text = text.replace(new RegExp(`\\b${kw}\\b`, 'gi'), kw)
  })
  sql.value = text
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => s.replace(/\s+/g, ' ').trim())
    .join(';\n')
}

async function loadHistory() {
  const res = await getCgi('/pages/page_sql.cgi', { action: 'history' })
  history.value = res.data || []
}

function openHistory() {
  historyVisible.value = true
}

function serverName(id) {
  return servers.value.find((s) => s.id === id)?.name || id || '-'
}

function useHistory(row) {
  sql.value = row.sql
  if (row.server_id) selectedId.value = row.server_id
  historyVisible.value = false
  onSelectServer(row.server_id)
}

async function clearHistory() {
  await ElMessageBox.confirm('确定清空全部 SQL 历史？', '确认', { type: 'warning' })
  await postCgi('/pages/page_sql.cgi', { action: 'clear_history' })
  history.value = []
  ElMessage.success('已清空')
}

function pickDefaultServer() {
  return (
    servers.value.find((s) => s.name === SYSTEM_PG_NAME)
    || servers.value.find((s) => /系统库/.test(s.name || '') && s.db_type === 'postgresql')
    || servers.value.find((s) => s.db_type === 'postgresql')
    || null
  )
}

onMounted(async () => {
  loadCache()
  await load()
  // 启用了浏览器缓存时保留表单，避免系统库覆盖用户上次连接
  if (useCache.value) return
  const pg = pickDefaultServer()
  if (pg) {
    selectedId.value = pg.id
    await onSelectServer(pg.id)
  }
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.panel {
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.conn-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.field-label {
  color: #606266;
  font-size: 13px;
  white-space: nowrap;
  min-width: 72px;
}
.conn-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px 16px;
  margin-bottom: 12px;
}
.conn-grid.timeout-row {
  grid-template-columns: repeat(4, 1fr);
  align-items: end;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field.narrow :deep(.el-input-number) {
  width: 100%;
}
.cache-field {
  justify-content: flex-end;
  padding-bottom: 4px;
}
.conn-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 4px;
}
.test-msg {
  font-size: 13px;
}
.test-msg.ok { color: #67c23a; }
.test-msg.err { color: #f56c6c; }
.schema-box {
  margin-top: 14px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  overflow: hidden;
}
.schema-head {
  display: flex;
  gap: 24px;
  padding: 8px 12px;
  background: #f5f7fa;
  font-size: 13px;
  color: #606266;
}
.schema-body {
  display: flex;
  max-height: 200px;
  overflow: auto;
}
.schema-col {
  flex: 1;
  min-width: 0;
  border-right: 1px solid #ebeef5;
}
.schema-col:last-child { border-right: none; }
.schema-item {
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
  border-bottom: 1px solid #f5f7fa;
}
.schema-item:hover { background: #ecf5ff; }
.schema-item.active { background: #d9ecff; color: #409eff; }
.schema-item.table { font-family: monospace; }
.sql-head {
  display: flex;
  align-items: baseline;
  gap: 16px;
  margin-bottom: 10px;
}
.sql-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.sql-hint {
  font-size: 12px;
  color: #909399;
}
.sql-editor :deep(textarea) {
  font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
  font-size: 13px;
  line-height: 1.5;
}
.sql-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
.spacer { flex: 1; }
.result-panel { margin-bottom: 8px; }
.result-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
  color: #606266;
  margin-bottom: 10px;
}
.result-meta .warn { color: #e6a23c; }
.result-meta .stmt {
  flex: 1 1 100%;
  font-family: monospace;
  color: #909399;
  word-break: break-all;
}
.manage-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 12px;
}
.manage-count { color: #909399; font-size: 13px; }
.history-bar { margin-bottom: 8px; text-align: right; }
@media (max-width: 1200px) {
  .conn-grid { grid-template-columns: repeat(3, 1fr); }
  .conn-grid.timeout-row { grid-template-columns: repeat(2, 1fr); }
}
</style>
