<template>
  <div class="chat-page">
    <div class="top-bar">
      <div class="top-left">
        <el-button link class="back-btn" @click="closePage">← 返回</el-button>
        <div class="titles">
          <div class="session-title">
            {{ group?.name || '群聊' }}
            <span class="member-badge">{{ memberCount }} 个成员</span>
          </div>
          <div class="session-sub">
            Group: {{ group?.name }}({{ shortId(groupId) }}) |
            Session: {{ sessionName }}({{ shortId(sessionId) }})
          </div>
        </div>
      </div>
      <div class="top-actions">
        <el-button @click="announceVisible = true"><el-icon><Bell /></el-icon> 公告</el-button>
        <el-button @click="openTasks"><el-icon><List /></el-icon> 任务</el-button>
        <el-button @click="sidebarCollapsed = !sidebarCollapsed">
          <el-icon><Fold /></el-icon> {{ sidebarCollapsed ? '展开空间' : '收起空间' }}
        </el-button>
        <el-button @click="clearHistory"><el-icon><Delete /></el-icon> 清空对话</el-button>
        <el-tag v-if="sending" type="warning">运行中</el-tag>
      </div>
    </div>

    <div class="main-body">
      <div v-show="!sidebarCollapsed" class="sidebar">
        <div class="space-label">空间</div>
        <WorkplacePanel
          v-if="spaceAgentId"
          ref="wpRef"
          :agent-id="spaceAgentId"
          :sandbox-id="spaceSandboxId"
        />
        <el-empty v-else description="请先添加群成员以使用空间" />
      </div>

      <div class="chat-main">
        <div class="chat-body" ref="scrollRef">
          <div v-if="!messages.length && !sending" class="empty-chat">
            <el-icon :size="48" color="#dcdfe6"><ChatDotRound /></el-icon>
            <p>开始群聊吧</p>
            <p class="empty-sub">选择要 @ 的智能体，然后发送消息</p>
            <el-popover placement="bottom" :width="360" trigger="hover">
              <template #reference>
                <el-button link type="primary" class="at-hint">@ 使用说明</el-button>
              </template>
              <div class="at-help">
                <p><strong>共享模式</strong>：多个 Agent 收到相同任务</p>
                <p class="example">例：@dba @tushare 汇总本周数据</p>
                <p><strong>分派模式</strong>：不同 Agent 各做不同任务</p>
                <p class="example">例：@dba: 查库表结构 @tushare: 拉取茅台行情</p>
                <p class="example">未 @ 时默认通知全部成员</p>
              </div>
            </el-popover>
          </div>

          <div
            v-for="(m, i) in messages"
            :key="i"
            :class="['msg-row', m.role === 'user' ? 'user-row' : 'assistant-row']"
          >
            <div v-if="m.role !== 'user'" class="agent-avatar">
              <span>{{ agentInitial(m.agent_id) }}</span>
            </div>
            <div class="msg-col">
              <div v-if="m.role === 'assistant'" class="agent-label">{{ agentLabel(m.agent_id) }}</div>
              <div :class="['msg-bubble', m.role, m.role === 'assistant' ? 'md-body' : '']">
                <CollapsibleUserContent
                  v-if="m.role === 'user'"
                  :content="m.content"
                />
                <div v-else class="content md-render" v-html="renderMessage(m)" @click="onMarkdownClick" />
              </div>
              <div class="msg-meta">
                <span>{{ m.created_at || '' }}</span>
                <el-button link size="small" @click="copyText(m.role === 'assistant' ? extractFinalDisplayContent(m.content) : m.content)">复制</el-button>
              </div>
            </div>
            <div v-if="m.role === 'user'" class="user-avatar">
              <el-icon><User /></el-icon>
            </div>
          </div>

          <div v-if="sending" class="msg-row assistant-row">
            <div class="agent-avatar"><span>…</span></div>
            <div class="msg-col">
              <div class="thinking">
                <span class="dot" /><span class="dot" /><span class="dot" />
              </div>
            </div>
          </div>
        </div>

        <div class="input-area">
          <div class="member-bar">
            <span class="member-bar-label">@选择成员：</span>
            <el-tag
              v-for="id in selectedMembers"
              :key="id"
              closable
              size="small"
              @close="toggleMember(id)"
            >
              {{ memberDisplayName(id) }}
            </el-tag>
            <el-popover placement="top-start" :width="240" trigger="click">
              <template #reference>
                <el-button size="small" link type="primary">+ 选择</el-button>
              </template>
              <div class="member-pick-list">
                <div
                  v-for="m in members"
                  :key="m.agent_id"
                  class="member-pick-item"
                  :class="{ active: selectedMembers.includes(m.agent_id) }"
                  @click="toggleMember(m.agent_id)"
                >
                  {{ memberDisplayName(m.agent_id) }}
                </div>
                <el-empty v-if="!members.length" description="暂无成员" :image-size="48" />
              </div>
            </el-popover>
          </div>
          <div class="chat-input">
            <el-upload :show-file-list="false" :http-request="onChatUpload" :disabled="!spaceAgentId">
              <el-button circle :disabled="!spaceAgentId"><el-icon><Upload /></el-icon></el-button>
            </el-upload>
            <VoiceInputButton :disabled="!spaceAgentId" @text="onVoiceText" />
            <el-input
              v-model="input"
              type="textarea"
              :rows="2"
              placeholder="输入消息... 输入 @ 选择成员 (Ctrl+Enter 发送)"
              @keydown.ctrl.enter="send"
              @input="onInputChange"
            />
            <el-button type="primary" :loading="sending" @click="send">发送</el-button>
            <el-button v-if="sending" @click="stop">停止</el-button>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="announceVisible" title="群公告" width="520px">
      <div class="announce-body">{{ group?.announcement || '暂无公告' }}</div>
    </el-dialog>

    <el-dialog v-model="taskVisible" title="群任务" width="720px" @opened="loadTasks">
      <el-table v-loading="taskLoading" :data="tasks" size="small" stripe max-height="360" empty-text="暂无任务">
        <el-table-column prop="task" label="任务" min-width="180" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="creator" label="创建人" width="90" />
        <el-table-column prop="created_at" label="时间" width="160" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Bell, List, Fold, Delete, Upload, ChatDotRound, User,
} from '@element-plus/icons-vue'
import { marked } from 'marked'
import {
  enhanceMarkdownHtml,
  extractFinalDisplayContent,
  toggleCodeSnippet,
  prepareMarkdownForPreview,
} from '../utils/markdownPreview'
import { getCgi, postCgi } from '../api'
import api from '../api'
import WorkplacePanel from '../components/WorkplacePanel.vue'
import VoiceInputButton from '../components/VoiceInputButton.vue'
import CollapsibleUserContent from '../components/CollapsibleUserContent.vue'

marked.setOptions({ breaks: true, gfm: true })

const route = useRoute()
const router = useRouter()
const groupId = route.params.id
const sessionId = ref(route.query.session || '')
const group = ref(null)
const agents = ref([])
const messages = ref([])
const input = ref('')
const sending = ref(false)
const sidebarCollapsed = ref(false)
const announceVisible = ref(false)
const taskVisible = ref(false)
const taskLoading = ref(false)
const tasks = ref([])
const selectedMembers = ref([])
const scrollRef = ref(null)
const wpRef = ref(null)

const members = computed(() => group.value?.members || [])
const memberCount = computed(() => group.value?.member_count ?? members.value.length)
const sessions = computed(() => group.value?.session_list || [])
const sessionName = computed(() => {
  const s = sessions.value.find((x) => x.session_id === sessionId.value)
  return s?.name || sessionId.value
})
const spaceAgentId = computed(() => members.value[0]?.agent_id || '')
const spaceSandboxId = computed(() => {
  const a = agents.value.find((x) => x.id === spaceAgentId.value)
  return a?.sandbox || a?.sandbox_id || ''
})

function shortId(id) {
  return id ? String(id).slice(0, 8) : ''
}

function memberDisplayName(agentId) {
  const m = members.value.find((x) => x.agent_id === agentId)
  if (m?.name) return m.name
  const a = agents.value.find((x) => x.id === agentId)
  return a?.name || agentId
}

function agentLabel(agentId) {
  return memberDisplayName(agentId)
}

function agentInitial(agentId) {
  const name = agentLabel(agentId)
  return name ? name.slice(0, 1).toUpperCase() : 'A'
}

function statusType(s) {
  if (s === 'done') return 'success'
  if (s === 'running') return 'warning'
  if (s === 'error') return 'danger'
  return 'info'
}

function renderMessage(m) {
  const prepared = prepareMarkdownForPreview(extractFinalDisplayContent(m.content || ''))
  return enhanceMarkdownHtml(marked.parse(prepared))
}

function toggleMember(agentId) {
  const idx = selectedMembers.value.indexOf(agentId)
  if (idx >= 0) selectedMembers.value.splice(idx, 1)
  else selectedMembers.value.push(agentId)
}

function onInputChange() {
  const m = input.value.match(/@(\S+)$/)
  if (m) {
    const tag = m[1].toLowerCase()
    const found = members.value.find((mem) =>
      memberDisplayName(mem.agent_id).toLowerCase() === tag ||
      mem.agent_id.toLowerCase() === tag
    )
    if (found && !selectedMembers.value.includes(found.agent_id)) {
      selectedMembers.value.push(found.agent_id)
      input.value = input.value.replace(/@\S+$/, '').trim()
    }
  }
}

function buildMessage() {
  let text = input.value.trim()
  if (!text) return ''
  if (text.includes('@')) return text
  if (selectedMembers.value.length) {
    const tags = selectedMembers.value.map((id) => `@${memberDisplayName(id)}`).join(' ')
    return `${tags} ${text}`.trim()
  }
  return text
}

function scrollBottom() {
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

async function loadAgents() {
  try {
    const res = await getCgi('/pages/page_agent.cgi', { action: 'list', scope: 'all' })
    agents.value = res.data || []
  } catch {
    agents.value = []
  }
}

async function loadHistory() {
  const res = await getCgi('/pages/page_group_chat.cgi', {
    group_id: groupId,
    session_id: sessionId.value,
    action: 'get_history',
  })
  group.value = res.data?.group
  messages.value = res.data?.messages || []
  if (!sessionId.value && sessions.value.length) {
    sessionId.value = sessions.value[0].session_id
  }
  if (sessionId.value && route.query.session !== sessionId.value) {
    router.replace({ query: { ...route.query, session: sessionId.value } })
  }
  scrollBottom()
}

async function send() {
  const text = buildMessage()
  if (!text) return
  messages.value.push({ role: 'user', content: text, created_at: new Date().toLocaleString('zh-CN', { hour12: false }) })
  input.value = ''
  sending.value = true
  scrollBottom()
  try {
    await postCgi('/pages/page_group_chat.cgi', {
      action: 'submit_chat',
      group_id: groupId,
      session_id: sessionId.value,
      message: text,
    })
    await loadHistory()
    wpRef.value?.load?.()
  } finally {
    sending.value = false
  }
}

async function stop() {
  await postCgi('/pages/page_group_chat.cgi', {
    action: 'stop_workflow',
    group_id: groupId,
    session_id: sessionId.value,
  })
  sending.value = false
  ElMessage.info('已请求停止')
}

async function clearHistory() {
  await ElMessageBox.confirm('确定清空当前群会话的所有对话？', '清空对话', { type: 'warning' })
  await postCgi('/pages/page_group_chat.cgi', {
    action: 'clear_history',
    group_id: groupId,
    session_id: sessionId.value,
  })
  messages.value = []
}

async function loadTasks() {
  taskLoading.value = true
  try {
    const res = await postCgi('/pages/page_group.cgi', {
      action: 'list_workflows',
      group_id: groupId,
      session_id: sessionId.value,
    })
    tasks.value = res.data || []
  } finally {
    taskLoading.value = false
  }
}

function openTasks() {
  taskVisible.value = true
}

async function onChatUpload({ file }) {
  if (!spaceAgentId.value) return
  const fd = new FormData()
  fd.append('file', file)
  fd.append('action', 'upload_workplace')
  fd.append('agent_id', spaceAgentId.value)
  const folder = wpRef.value?.getSelectedFolder?.() || ''
  if (folder) fd.append('path', folder)
  await api.post('/pages/page_agent_chat.cgi', fd)
  ElMessage.success('上传成功')
  wpRef.value?.load?.()
}

function onVoiceText(text) {
  const t = (text || '').trim()
  if (!t) return
  input.value = input.value ? `${input.value}\n${t}` : t
}

function copyText(text) {
  navigator.clipboard.writeText(text || '')
  ElMessage.success('已复制')
}

async function onMarkdownClick(e) {
  const actionButton = e.target?.closest?.('button.md-code-action')
  if (!actionButton) return
  e.preventDefault()
  const block = actionButton.closest('.md-code-block')
  const code = block?.querySelector('pre code')
  if (!code) return
  const action = actionButton.getAttribute('data-code-action')
  if (action === 'copy') {
    await navigator.clipboard.writeText(code.textContent || '')
    ElMessage.success('代码已复制')
    return
  }
  if (action === 'format') {
    try {
      const result = toggleCodeSnippet(block, code)
      ElMessage.success(result.formatted ? '代码已格式化' : '已恢复原始代码')
    } catch (err) {
      ElMessage.warning(err?.message || '代码格式化失败')
    }
  }
}

function closePage() {
  if (window.history.length > 1) router.back()
  else router.push('/groups')
}

onMounted(async () => {
  await loadAgents()
  await loadHistory()
})
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--gap-chat-body-bg);
  overflow: hidden;
  color: var(--gap-text);
}
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: var(--gap-chat-top-bg);
  color: var(--gap-chat-top-text);
  border-bottom: 1px solid var(--gap-card-border);
  flex-shrink: 0;
  box-shadow: 0 1px 4px var(--gap-shadow);
}
.top-left { display: flex; align-items: center; gap: 12px; }
.back-btn { color: var(--gap-primary) !important; }
.session-title { font-size: 18px; font-weight: 600; display: flex; align-items: center; gap: 10px; color: var(--gap-chat-top-text); }
.member-badge { font-size: 13px; font-weight: 400; color: var(--gap-text-muted); }
.session-sub { font-size: 12px; color: var(--gap-text-muted); margin-top: 2px; }
.top-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.top-actions .el-button {
  color: var(--gap-chat-top-text);
  border-color: var(--gap-card-border);
  background: var(--gap-hover-bg);
}

.main-body { display: flex; flex: 1; min-height: 0; }
.sidebar {
  width: 340px; flex-shrink: 0; height: 100%; overflow: hidden;
  border-right: 1px solid var(--gap-card-border);
  display: flex; flex-direction: column;
  background: var(--gap-chat-sidebar-bg);
}
.space-label {
  padding: 10px 12px; font-weight: 600; color: var(--gap-text);
  border-bottom: 1px solid var(--gap-card-border);
  background: var(--gap-card-bg);
}
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-body { flex: 1; overflow: auto; padding: 20px 24px; background: var(--gap-chat-body-bg); }
.empty-chat { text-align: center; color: var(--gap-text-muted); padding-top: 80px; }
.empty-chat p { margin-top: 8px; }
.empty-sub { font-size: 13px; }
.at-hint { margin-top: 12px; }
.at-help { font-size: 13px; line-height: 1.7; color: #606266; }
.at-help .example { color: #909399; margin: 4px 0 10px; font-size: 12px; }

.msg-row { display: flex; gap: 12px; margin-bottom: 24px; align-items: flex-start; }
.user-row { flex-direction: row-reverse; }
.agent-avatar, .user-avatar {
  width: 36px; height: 36px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; font-size: 14px;
}
.agent-avatar { background: linear-gradient(135deg, #409eff, #337ecc); color: #fff; font-weight: 700; }
.user-avatar { background: var(--gap-hover-bg); color: var(--gap-text-muted); border: 1px solid var(--gap-card-border); }
.msg-col { max-width: min(820px, 85%); display: flex; flex-direction: column; gap: 6px; }
.user-row .msg-col { align-items: flex-end; }
.agent-label { font-size: 12px; color: var(--gap-text-muted); padding-left: 4px; }

.msg-bubble { border-radius: 10px; overflow: hidden; }
.msg-bubble.user { background: #409eff; color: #fff; }
.msg-bubble.assistant { background: var(--gap-chat-bubble-bg); border: 1px solid var(--gap-card-border); color: var(--gap-text); }
.content {
  white-space: pre-wrap; padding: 14px 16px; margin: 0;
  font-family: inherit; font-size: 14px; line-height: 1.7; word-break: break-word;
  color: inherit;
}
.msg-bubble.user .content { color: #fff; }
.content.md-render {
  white-space: normal;
  padding: 12px 14px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
  color: var(--gap-text);
}

.msg-meta { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--gap-text-muted); }

.thinking { display: flex; gap: 6px; padding: 12px 4px; }
.thinking .dot {
  width: 8px; height: 8px; border-radius: 50%; background: #409eff;
  animation: bounce 1.2s infinite ease-in-out;
}
.thinking .dot:nth-child(2) { animation-delay: 0.15s; }
.thinking .dot:nth-child(3) { animation-delay: 0.3s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.input-area { border-top: 1px solid var(--gap-card-border); background: var(--gap-chat-top-bg); }
.member-bar {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  padding: 10px 20px 0; font-size: 13px; color: var(--gap-text-secondary);
}
.member-bar-label { color: var(--gap-text-muted); white-space: nowrap; }
.member-pick-list { max-height: 200px; overflow: auto; }
.member-pick-item {
  padding: 8px 10px; cursor: pointer; border-radius: 6px; font-size: 13px;
}
.member-pick-item:hover { background: var(--gap-hover-bg); }
.member-pick-item.active { background: var(--gap-hover-bg); color: var(--gap-primary); }

.chat-input {
  display: flex; align-items: flex-end; gap: 10px;
  padding: 12px 20px 16px;
}
.chat-input .el-textarea { flex: 1; }
.chat-input :deep(.el-textarea__inner) {
  background: var(--gap-chat-input-bg);
  color: var(--gap-text);
  box-shadow: 0 0 0 1px var(--gap-card-border) inset;
}

.announce-body { white-space: pre-wrap; line-height: 1.7; color: var(--gap-text-secondary); min-height: 80px; }
</style>
