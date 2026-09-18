<template>
  <div class="page">
    <div class="header">
      <div class="title-block">
        <h2>消息渠道</h2>
        <p class="sub">选择 Agent 并配置飞书等渠道后，可在手机端机器人对话框中直接对话</p>
        <p class="public-base" :class="{ missing: !publicBaseUrl }">
          <span class="label">公网基址</span>
          <template v-if="publicBaseUrl">{{ publicBaseUrl }}</template>
          <template v-else>
            未配置 PUBLIC_BASE_URL — 本地请用 <code>scripts/start-local.sh</code>（会自动跑 cloudflared），或手动执行 <code>scripts/feishu-cloudflared.sh</code> 后重启 API
          </template>
        </p>
      </div>
      <div class="header-actions">
        <el-radio-group v-model="scope" @change="load">
          <el-radio-button value="all">所有</el-radio-button>
          <el-radio-button value="mine">我的</el-radio-button>
        </el-radio-group>
        <el-button type="primary" @click="openForm()">
          <el-icon><Plus /></el-icon>
          新建渠道
        </el-button>
      </div>
    </div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in list" :key="row.id" class="card">
        <div class="card-head">
          <span class="card-title">
            <el-icon v-if="row.visibility === 'private'" class="vis-icon"><Lock /></el-icon>
            <el-icon v-else class="vis-icon pub"><Unlock /></el-icon>
            {{ row.name }}
          </span>
          <el-tag size="small" :type="row.enabled ? 'success' : 'info'">
            {{ row.enabled ? '启用' : '停用' }}
          </el-tag>
        </div>
        <div class="card-meta">
          <div><span class="label">类型</span> {{ providerLabel(row.provider) }}</div>
          <div>
            <span class="label">权限</span>
            {{ row.visibility === 'public' ? '公共' : '私有' }}
          </div>
          <div class="bind-row">
            <span class="label">绑定 Agent</span>
            <el-tag v-if="row.agent_id" size="small" type="success" effect="plain">
              {{ row.agent_name || agentName(row.agent_id) }}
            </el-tag>
            <el-tag v-else size="small" type="danger" effect="plain">未绑定（手机无法对话）</el-tag>
          </div>
          <div><span class="label">创建人</span> {{ row.creator || '-' }}</div>
          <div><span class="label">更新</span> {{ row.modified_at || '-' }}</div>
        </div>
        <div v-if="row.webhook_path" class="url-box">
          <div class="url-row" :class="{ warn: webhookIsLocal(row) }" :title="fullWebhook(row)">
            {{ fullWebhook(row) }}
          </div>
          <el-button size="small" @click="copyWebhook(row)">复制 Webhook</el-button>
          <div v-if="webhookIsLocal(row)" class="url-warn">
            当前 URL 含 localhost / 127.0.0.1 或非 HTTPS，飞书 / Telegram Webhook 无法使用。请配置 PUBLIC_BASE_URL（cloudflared 公网域名）。
          </div>
        </div>
        <div v-if="row.provider === 'feishu'" class="hint">
          飞书消息会写入绑定 Agent 的「飞书」会话；可点「打开对话」在 Web 查看完整记录。群 @ 需权限 <code>im:message.group_at_msg:readonly</code> 并发布。
        </div>
        <div v-if="row.provider === 'telegram'" class="hint">
          <template v-if="(row.config?.mode || 'polling') === 'polling'">
            模式 Long Polling：无需公网；API 进程后台拉取更新。保存或点「同步 Webhook」会自动清除 Telegram 侧 webhook。
          </template>
          <template v-else>
            模式 Webhook：需公网 HTTPS；保存或点「同步 Webhook」会自动调用 setWebhook。消息写入 Agent 的「Telegram」会话。
          </template>
        </div>
        <div v-if="row.last_error" class="err">{{ row.last_error }}</div>
        <div class="card-actions">
          <el-switch
            :model-value="row.enabled"
            inline-prompt
            active-text="开"
            inactive-text="关"
            @change="(v) => toggleEnabled(row, v)"
          />
          <el-button size="small" type="success" plain :disabled="!row.agent_id" @click="openWebChat(row)">
            打开对话
          </el-button>
          <el-button size="small" @click="openLogs(row)">日志</el-button>
          <el-button
            v-if="row.provider === 'telegram'"
            size="small"
            type="warning"
            plain
            :loading="syncingId === row.id"
            @click="syncTelegram(row)"
          >
            同步 Webhook
          </el-button>
          <el-button v-if="row.provider === 'mock'" size="small" type="warning" plain @click="testMock(row)">
            测试
          </el-button>
          <el-button size="small" type="primary" plain @click="openForm(row)">编辑</el-button>
          <el-button size="small" type="danger" link @click="onDelete(row)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !list.length" description="暂无消息渠道">
        <template #description>
          <p>暂无消息渠道</p>
          <p class="empty-hint">新建渠道并绑定 Agent；可用 Mock 先验证「收消息 → Agent → 回消息」</p>
        </template>
      </el-empty>
    </div>

    <el-dialog
      v-model="formVisible"
      :title="form.id ? '编辑渠道' : '新建渠道'"
      width="640px"
      destroy-on-close
      @closed="resetForm"
    >
      <el-form :model="form" label-width="120px">
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="如：客服飞书机器人" />
        </el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="form.provider" :disabled="!!form.id" style="width:100%">
            <el-option
              v-for="p in providers"
              :key="p"
              :label="providerLabel(p)"
              :value="p"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="绑定 Agent" required>
          <el-select v-model="form.agent_id" filterable clearable style="width:100%" placeholder="选择 Agent（未绑定则手机无法对话）">
            <el-option v-if="form.provider === 'feishu'" label="Release Agent（仅发布通知）" value="release-agent" />
            <el-option v-for="a in agents" :key="a.id" :label="`${a.name} (${a.id})`" :value="a.id" />
          </el-select>
          <div class="field-tip">选中的 Agent 会处理该渠道所有私聊/群消息并回发到飞书等客户端</div>
        </el-form-item>
        <el-form-item label="权限">
          <el-radio-group v-model="form.visibility">
            <el-radio value="public">公共</el-radio>
            <el-radio value="private">私有</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.visibility === 'private'" label="授权用户">
          <el-input v-model="form.allowedUsersStr" placeholder="逗号分隔用户名" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <template v-if="form.provider === 'feishu'">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            style="margin-bottom:12px"
            title="开通：飞书开放平台创建应用 → 机器人能力 → 事件订阅 im.message.receive_v1（须 HTTPS）→ 权限必开：读取单聊消息 + 接收群聊中@机器人消息(im:message.group_at_msg:readonly) + 以应用身份发消息 → 发布版本 → 拉机器人进群后 @机器人 说话"
          />
          <el-alert
            type="warning"
            :closable="false"
            show-icon
            style="margin-bottom:12px"
            title="群聊无反应时：私聊能回、群不能回 = 通常缺「接收群聊中@机器人消息」权限或未把机器人拉进群；改权限后必须重新发布应用版本"
          />
          <el-form-item label="App ID"><el-input v-model="form.config.app_id" /></el-form-item>
          <el-form-item label="App Secret"><el-input v-model="form.config.app_secret" type="password" show-password /></el-form-item>
          <el-form-item label="Verification Token"><el-input v-model="form.config.verification_token" /></el-form-item>
          <el-form-item v-if="form.agent_id === 'release-agent'" label="通知 Chat ID">
            <el-input v-model="form.config.release_chat_id" placeholder="飞书 chat_id 或 open_id" />
            <div class="field-tip">发布成功、失败或回滚后，Release Agent 会向该目标推送一次通知。</div>
          </el-form-item>
        </template>
        <template v-else-if="form.provider === 'dingtalk'">
          <el-form-item label="App Secret"><el-input v-model="form.config.app_secret" type="password" show-password placeholder="用于签名校验" /></el-form-item>
        </template>
        <template v-else-if="form.provider === 'telegram'">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            style="margin-bottom:12px"
            title="开通：Telegram @BotFather 创建机器人 → 复制 Bot Token → 绑定 Agent。拉入群聊后，仅 @机器人 的消息会启动任务；完成后会引用原消息并 @发起人。Webhook 需公网 HTTPS；Polling 适合本地。"
          />
          <el-alert
            type="warning"
            :closable="false"
            show-icon
            style="margin-bottom:12px"
            title="Webhook 与 Polling 互斥：选 Polling 时会清除 Telegram 侧 webhook；选 Webhook 时须先配置公网基址。群聊建议填写允许 Chat ID 白名单。"
          />
          <el-form-item label="Bot Token"><el-input v-model="form.config.bot_token" type="password" show-password /></el-form-item>
          <el-form-item label="模式">
            <el-select v-model="form.config.mode" style="width:100%">
              <el-option label="Long Polling（本地）" value="polling" />
              <el-option label="Webhook（公网）" value="webhook" />
            </el-select>
          </el-form-item>
          <el-form-item label="允许 Chat ID">
            <el-input v-model="form.config.allowed_chat_ids" placeholder="逗号分隔，空=全部" />
          </el-form-item>
        </template>
        <template v-else-if="form.provider === 'qq'">
          <el-form-item label="OneBot HTTP">
            <el-input v-model="form.config.onebot_http_url" placeholder="http://127.0.0.1:5700" />
          </el-form-item>
          <el-form-item label="Access Token"><el-input v-model="form.config.access_token" type="password" show-password /></el-form-item>
          <el-form-item label="Secret"><el-input v-model="form.config.secret" type="password" show-password placeholder="可选，签名校验" /></el-form-item>
          <el-form-item label="群白名单">
            <el-input v-model="form.config.allowed_group_ids" placeholder="逗号分隔 group_id，空=不限制" />
          </el-form-item>
        </template>
        <template v-else-if="form.provider === 'wecom'">
          <el-form-item label="Corp ID"><el-input v-model="form.config.corp_id" /></el-form-item>
          <el-form-item label="Agent ID"><el-input v-model="form.config.agent_id" /></el-form-item>
          <el-form-item label="Secret"><el-input v-model="form.config.secret" type="password" show-password /></el-form-item>
          <el-form-item label="Token"><el-input v-model="form.config.token" /></el-form-item>
          <el-form-item label="EncodingAESKey"><el-input v-model="form.config.encoding_aes_key" type="password" show-password /></el-form-item>
        </template>
        <template v-else-if="form.provider === 'mock'">
          <el-alert type="info" :closable="false" title="Mock 渠道用于本地联调，保存后点「测试」即可触发已绑定 Agent。" />
        </template>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="logsVisible" title="渠道日志" width="720px" destroy-on-close>
      <el-table :data="logs" size="small" max-height="420" stripe>
        <el-table-column prop="created_at" label="时间" width="170" />
        <el-table-column prop="level" label="级别" width="80" />
        <el-table-column prop="message" label="消息" min-width="160" />
        <el-table-column prop="detail" label="详情" min-width="220" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Lock, Unlock } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'

const router = useRouter()
const loading = ref(false)
const saving = ref(false)
const scope = ref('all')
const list = ref([])
const agents = ref([])
const providers = ref([])
const labels = ref({})
const publicBaseUrl = ref('')
const formVisible = ref(false)
const logsVisible = ref(false)
const logs = ref([])
const syncingId = ref('')
const form = reactive(emptyForm())

function emptyForm() {
  return {
    id: '',
    name: '',
    provider: 'mock',
    enabled: true,
    agent_id: '',
    visibility: 'private',
    allowedUsersStr: '',
    config: { mode: 'polling' },
  }
}

function providerLabel(p) {
  return labels.value[p] || p
}

function agentName(id) {
  if (!id) return '-'
  const a = agents.value.find((x) => x.id === id)
  return a ? a.name : id
}

function originFallback() {
  return window.location.origin.replace(':5173', ':8000').replace(/\/$/, '')
}

function fullWebhook(row) {
  if (!row?.webhook_path) return ''
  const base = (publicBaseUrl.value || '').replace(/\/$/, '') || originFallback()
  return `${base}${row.webhook_path}`
}

function webhookIsLocal(row) {
  const url = fullWebhook(row)
  if (!url) return true
  try {
    const u = new URL(url)
    const host = (u.hostname || '').toLowerCase()
    if (u.protocol !== 'https:') return true
    if (host === 'localhost' || host === '127.0.0.1' || host === '0.0.0.0' || host === '::1') return true
    return false
  } catch {
    return true
  }
}

async function copyWebhook(row) {
  const url = fullWebhook(row)
  if (!url) return
  if (webhookIsLocal(row)) {
    ElMessage.warning('当前非公网 HTTPS：飞书 / Telegram Webhook 不可用，请先配置 PUBLIC_BASE_URL')
    return
  }
  try {
    await navigator.clipboard.writeText(url)
    ElMessage.success('Webhook 已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选中 URL')
  }
}

function openWebChat(row) {
  if (!row?.agent_id) {
    ElMessage.warning('请先绑定 Agent')
    return
  }
  const q = row.web_session_id
    ? { session: row.web_session_id }
    : {}
  router.push({ path: `/agents/${row.agent_id}/chat`, query: q })
}

function resetForm() {
  Object.assign(form, emptyForm())
}

function applyPublicBase(fromList, fromProviders) {
  const v = (fromList || fromProviders || '').trim().replace(/\/$/, '')
  publicBaseUrl.value = v
}

async function load() {
  loading.value = true
  try {
    const [ch, ag, pv] = await Promise.all([
      postCgi('/pages/page_channel.cgi', { action: 'list', scope: scope.value }),
      getCgi('/pages/page_agent.cgi', { action: 'list', scope: 'all' }),
      postCgi('/pages/page_channel.cgi', { action: 'providers' }),
    ])
    const chData = ch.data
    if (Array.isArray(chData)) {
      list.value = chData
      applyPublicBase('', pv.data?.public_base_url)
    } else {
      list.value = chData?.channels || []
      applyPublicBase(chData?.public_base_url, pv.data?.public_base_url)
    }
    agents.value = ag.data || []
    providers.value = pv.data?.providers || []
    labels.value = pv.data?.labels || {}
  } finally {
    loading.value = false
  }
}

function notifyTelegramSync(sync) {
  if (!sync) return
  const msg = sync.message || (sync.ok ? 'Telegram 已同步' : 'Telegram 同步失败')
  if (sync.ok) ElMessage.success(msg)
  else ElMessage.warning(msg)
}

function openForm(row) {
  if (row) {
    Object.assign(form, {
      id: row.id,
      name: row.name,
      provider: row.provider,
      enabled: row.enabled,
      agent_id: row.agent_id,
      visibility: row.visibility || 'private',
      allowedUsersStr: (row.allowed_users || []).join(','),
      config: { mode: 'polling', ...(row.config || {}) },
    })
  } else {
    resetForm()
  }
  formVisible.value = true
}

async function save() {
  if (!form.name?.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  if (!form.agent_id) {
    ElMessage.warning('请绑定 Agent（未绑定则手机端无法对话）')
    return
  }
  const allowed_users = form.allowedUsersStr
    ? form.allowedUsersStr.split(',').map((s) => s.trim()).filter(Boolean)
    : []
  saving.value = true
  try {
    const res = await postCgi('/pages/page_channel.cgi', {
      action: form.id ? 'update' : 'create',
      id: form.id || undefined,
      name: form.name,
      provider: form.provider,
      enabled: form.enabled,
      agent_id: form.agent_id,
      visibility: form.visibility,
      allowed_users,
      config: form.config,
    })
    const sync = res.data?.telegram_sync
    if (sync) {
      if (sync.ok) ElMessage.success(`保存成功：${sync.message || 'Telegram 已同步'}`)
      else ElMessage.warning(`已保存，但 Telegram 同步失败：${sync.message || '未知错误'}`)
    } else {
      ElMessage.success('保存成功')
    }
    formVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row, v) {
  const res = await postCgi('/pages/page_channel.cgi', {
    action: 'update',
    id: row.id,
    enabled: v,
  })
  row.enabled = v
  notifyTelegramSync(res.data?.telegram_sync)
}

async function syncTelegram(row) {
  syncingId.value = row.id
  try {
    const res = await postCgi('/pages/page_channel.cgi', {
      action: 'sync_telegram',
      id: row.id,
    })
    notifyTelegramSync(res.data?.telegram_sync)
  } finally {
    syncingId.value = ''
  }
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除渠道「${row.name}」？`, '确认', { type: 'warning' })
  await postCgi('/pages/page_channel.cgi', { action: 'delete', id: row.id })
  ElMessage.success('已删除')
  load()
}

async function openLogs(row) {
  const res = await postCgi('/pages/page_channel.cgi', { action: 'logs', id: row.id, limit: 80 })
  logs.value = res.data || []
  logsVisible.value = true
}

async function testMock(row) {
  if (!row.agent_id) {
    ElMessage.warning('请先绑定 Agent')
    return
  }
  await postCgi('/pages/page_channel.cgi', {
    action: 'test_mock',
    id: row.id,
    text: '你好，请用一句话介绍你自己。',
  })
  ElMessage.success('已提交测试，稍后查看日志')
}

onMounted(load)
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }
.header {
  display: flex; justify-content: space-between; align-items: flex-start;
  flex-wrap: wrap; gap: 12px;
}
.header-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.title-block h2 { margin: 0; font-size: 18px; color: #303133; }
.sub { margin: 6px 0 0; color: #909399; font-size: 13px; }
.public-base {
  margin: 8px 0 0; font-size: 12px; color: #606266; line-height: 1.5;
  word-break: break-all;
}
.public-base.missing { color: #e6a23c; }
.public-base code {
  font-size: 11px; background: #f5f7fa; padding: 1px 4px; border-radius: 3px;
}
.card-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px;
}
.card {
  background: #fff; border-radius: 8px; padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06);
}
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.card-title {
  font-weight: 600; font-size: 15px; color: #303133;
  display: inline-flex; align-items: center; gap: 6px;
}
.vis-icon { color: #909399; }
.vis-icon.pub { color: #67c23a; }
.card-meta { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #606266; margin-bottom: 10px; }
.bind-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.label { color: #909399; margin-right: 6px; }
.url-box { display: flex; flex-direction: column; gap: 8px; margin-bottom: 10px; }
.url-row {
  font-family: Menlo, Monaco, Consolas, monospace; font-size: 11px;
  color: #909399; background: #f5f7fa; padding: 8px; border-radius: 4px;
  word-break: break-all;
}
.url-row.warn { color: #e6a23c; background: #fdf6ec; }
.url-warn { font-size: 12px; color: #e6a23c; line-height: 1.4; }
.hint {
  font-size: 12px; color: #606266; line-height: 1.5; margin-bottom: 10px;
  background: #f0f9eb; border-radius: 4px; padding: 8px 10px;
}
.hint code { font-size: 11px; }
.err { color: #f56c6c; font-size: 12px; margin-bottom: 8px; word-break: break-all; }
.card-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.empty-hint { color: #909399; font-size: 13px; }
.field-tip { margin-top: 6px; font-size: 12px; color: #909399; line-height: 1.4; }
</style>
