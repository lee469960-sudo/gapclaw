<template>
  <div class="page">
    <div class="header">
      <el-radio-group v-model="scope" @change="load">
        <el-radio-button value="all">所有</el-radio-button>
        <el-radio-button value="mine">我的</el-radio-button>
      </el-radio-group>
      <div class="header-actions">
        <el-input v-model="keyword" placeholder="搜索 LLM..." clearable style="width:220px" @keyup.enter="doSearch" />
        <el-button @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button type="primary" @click="openForm()">+ 添加 LLM</el-button>
      </div>
    </div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">{{ row.name }}</span>
          <el-tag size="small" :type="row.type === 'group' ? 'warning' : 'primary'">
            {{ row.type === 'group' ? 'LLM组' : 'LLM' }}
          </el-tag>
        </div>
        <div class="card-meta">
          <div><span class="label">ID</span> {{ row.id }}</div>
          <div><span class="label">创建人</span> {{ row.creator }}</div>
          <div><span class="label">时间</span> {{ row.modified_at || '-' }}</div>
        </div>
        <div class="card-tags">
          <el-tag v-if="row.model" size="small" effect="plain">{{ row.model }}</el-tag>
          <el-tag v-if="row.provider" size="small" type="info" effect="plain">{{ row.provider }}</el-tag>
          <el-tag size="small" :type="row.visibility === 'public' ? 'success' : 'info'">
            {{ row.visibility === 'public' ? '公开' : '私有' }}
          </el-tag>
          <el-tag v-if="row.type === 'llm'" size="small" :type="routingStatus(row).eligible ? 'success' : 'info'">
            {{ routingStatus(row).eligible ? '可自动路由' : routingStatus(row).reason }}
          </el-tag>
        </div>
        <div class="card-actions">
          <el-button size="small" @click="testLlm(row)">测试</el-button>
          <el-button size="small" @click="openForm(row)">编辑</el-button>
          <el-button size="small" type="danger" link @click="onDelete(row)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !filtered.length" description="暂无 LLM" />
    </div>

    <el-dialog v-model="visible" :title="form.id ? '编辑 LLM' : '添加 LLM'" width="560px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="类型">
          <el-radio-group v-model="form.type">
            <el-radio value="llm">LLM</el-radio>
            <el-radio value="group">LLM组</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.type === 'llm'">
          <el-button type="primary" plain size="small" @click="applyAnthropicPreset">Anthropic Claude</el-button>
          <el-button type="primary" plain size="small" @click="applyMinMaxPreset(false)">MiniMax 国际</el-button>
          <el-button type="primary" plain size="small" @click="applyMinMaxPreset(true)">MiniMax 国内</el-button>
        </el-form-item>
        <el-form-item label="名称" required><el-input v-model="form.name" /></el-form-item>
        <template v-if="form.type === 'llm'">
          <el-form-item label="Provider"><el-input v-model="form.provider" /></el-form-item>
          <el-form-item label="Base URL">
            <el-input v-model="form.base_url" :placeholder="form.provider === 'anthropic' ? 'https://api.anthropic.com' : 'https://api.minimaxi.com/v1'" />
            <div v-if="form.provider === 'anthropic'" class="field-hint">Claude Code 使用 Anthropic API；CodeAgent 请绑定此类型的单个 LLM</div>
            <div v-else class="field-hint">MiniMax 请用 OpenAI 兼容地址，末尾带 /v1，不要用 /anthropic</div>
          </el-form-item>
          <el-form-item label="API Key">
            <el-input
              v-model="form.api_key"
              type="password"
              show-password
              :placeholder="form.id ? '留空则不修改' : '填写完整 API Key'"
            />
          </el-form-item>
          <el-form-item label="Model"><el-input v-model="form.model" /></el-form-item>
          <el-divider content-position="left">自动路由能力</el-divider>
          <el-form-item label="启用自动路由"><el-switch v-model="form.routingCapabilities.enabled" /></el-form-item>
          <template v-if="form.routingCapabilities.enabled">
            <el-form-item label="角色"><el-checkbox-group v-model="form.routingCapabilities.roles"><el-checkbox v-for="role in ROUTING_ROLES" :key="role" :value="role">{{ role }}</el-checkbox></el-checkbox-group></el-form-item>
            <el-form-item label="模态"><el-checkbox-group v-model="form.routingCapabilities.modalities"><el-checkbox v-for="modality in ROUTING_MODALITIES" :key="modality" :value="modality">{{ modality }}</el-checkbox></el-checkbox-group></el-form-item>
            <el-form-item label="运行时"><el-checkbox-group v-model="form.routingCapabilities.runtimes"><el-checkbox value="react">react</el-checkbox></el-checkbox-group></el-form-item>
            <div class="field-hint">{{ routingStatus({ type: 'llm', routing_capabilities: form.routingCapabilities }).eligible ? '此模型满足自动路由的基础声明，可加入角色模型组。' : routingStatus({ type: 'llm', routing_capabilities: form.routingCapabilities }).reason }}</div>
          </template>
        </template>
        <el-form-item v-else label="成员 ID">
          <el-input v-model="form.membersStr" placeholder="逗号分隔 LLM ID" />
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item label="可见性">
          <el-radio-group v-model="form.visibility">
            <el-radio value="private">私有</el-radio>
            <el-radio value="public">公开</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getCgi, postCgi } from '../api'

const list = ref([])
const loading = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const visible = ref(false)
const form = reactive({})
const ROUTING_ROLES = ['general', 'react_code', 'planner', 'multimodal', 'fast']
const ROUTING_MODALITIES = ['text', 'image', 'audio', 'video']

function routingStatus(row) {
  if (row.type !== 'llm') return { eligible: false, reason: 'LLM组不可路由' }
  const cap = row.routing_capabilities || {}
  if (!cap.enabled) return { eligible: false, reason: '未启用自动路由' }
  if (!Array.isArray(cap.roles) || !cap.roles.length) return { eligible: false, reason: '缺少角色' }
  if (!Array.isArray(cap.modalities) || !cap.modalities.length) return { eligible: false, reason: '缺少模态' }
  if (!Array.isArray(cap.runtimes) || !cap.runtimes.includes('react')) return { eligible: false, reason: '未声明 react 运行时' }
  return { eligible: true, reason: '' }
}

const filtered = computed(() => {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((r) =>
    r.name?.toLowerCase().includes(kw) ||
    r.model?.toLowerCase().includes(kw) ||
    r.id?.toLowerCase().includes(kw)
  )
})

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_llm.cgi', { action: 'list', scope: scope.value })
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

function applyMinMaxPreset(domestic = false) {
  form.type = 'llm'
  form.name = form.name || 'MinMax'
  form.provider = 'openai'
  form.base_url = domestic ? 'https://api.minimaxi.com/v1' : 'https://api.minimax.io/v1'
  form.model = 'MiniMax-M3'
  form.description = domestic ? 'MiniMax 国内 OpenAI 兼容' : 'MiniMax 国际 OpenAI 兼容'
}

function applyAnthropicPreset() {
  form.type = 'llm'
  form.name = form.name || 'Claude'
  form.provider = 'anthropic'
  form.base_url = 'https://api.anthropic.com'
  form.model = 'sonnet'
  form.description = 'Anthropic Claude（CodeAgent）'
}

function openForm(row) {
  if (row) {
    Object.assign(form, {
      ...row,
      api_key: '',
      membersStr: (row.members || []).join(','),
      routingCapabilities: { enabled: false, roles: [], modalities: [], runtimes: [], ...(row.routing_capabilities || {}) },
    })
  } else {
    Object.assign(form, {
      id: '',
      type: 'llm',
      name: '',
      provider: 'openai',
      base_url: '',
      api_key: '',
      model: '',
      membersStr: '',
      description: '',
      visibility: 'private',
      routingCapabilities: { enabled: false, roles: [], modalities: [], runtimes: ['react'] },
    })
  }
  visible.value = true
}

async function save() {
  if (!form.name?.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  const members = form.membersStr
    ? form.membersStr.split(',').map((s) => s.trim()).filter(Boolean)
    : []
  const payload = {
    action: form.id ? 'update' : 'create',
    id: form.id || undefined,
    type: form.type,
    name: form.name,
    provider: form.provider,
    base_url: form.base_url,
    model: form.model,
    members,
    routing_capabilities: form.type === 'llm' ? form.routingCapabilities : undefined,
    description: form.description,
    visibility: form.visibility,
  }
  if (form.api_key?.trim()) payload.api_key = form.api_key.trim()
  await postCgi('/pages/page_llm.cgi', payload)
  ElMessage.success('保存成功')
  visible.value = false
  load()
}

async function testLlm(row) {
  if (row.type === 'group') {
    ElMessage.info('LLM 组暂不支持直接测试')
    return
  }
  const { value } = await ElMessageBox.prompt('输入测试消息', 'LLM 测试', { inputValue: '你好' })
  const res = await postCgi('/pages/page_llm.cgi', { action: 'test', id: row.id, message: value })
  ElMessageBox.alert(res.data?.reply || '无回复', '测试结果')
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除 ${row.name}？`)
  await postCgi('/pages/page_llm.cgi', { action: 'delete', id: row.id })
  load()
}

onMounted(load)
</script>

<style scoped>
.page { background: #fff; border-radius: 8px; padding: 16px; min-height: 400px; }
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
.header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 1200px) { .card-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 768px) { .card-grid { grid-template-columns: 1fr; } }
.card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 16px;
  transition: box-shadow 0.2s;
}
.card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.card-title { font-size: 16px; font-weight: 600; color: #303133; }
.card-meta { font-size: 13px; color: #606266; line-height: 1.8; margin-bottom: 10px; }
.card-meta .label { color: #909399; margin-right: 6px; }
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.card-actions { display: flex; gap: 8px; border-top: 1px solid #f0f0f0; padding-top: 12px; }
.field-hint { font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.4; }
</style>
