<template>
  <div class="chat-page">
    <div class="top-bar">
      <div class="top-left">
        <el-button link class="back-btn" @click="closePage">← 返回</el-button>
        <div class="titles">
          <div class="session-title">{{ sessionName }}</div>
          <div class="session-sub">
            Agent: {{ agent?.name || agentId }}({{ shortId(agentId) }}) |
            Session: {{ sessionName }}({{ shortId(sessionId) }})
          </div>
          <div v-if="isImSession" class="im-banner">
            此会话同步消息渠道（飞书等）对话；在手机端与机器人聊天也会出现在这里。
          </div>
          <div v-if="inboundNotice" class="inbound-banner">
            <span class="inbound-text">
              {{ inboundNotice.sender ? inboundNotice.sender + '：' : '' }}消息渠道有新消息
            </span>
            <el-button link type="primary" size="small" @click="switchToInboundSession">查看</el-button>
          </div>
        </div>
      </div>
      <div class="top-actions">
        <ThemeSwitch />
        <el-button @click="noteVisible = true"><el-icon><EditPen /></el-icon> 备忘</el-button>
        <el-button @click="noteDlg?.openSummary()"><el-icon><Document /></el-icon> 查看总结</el-button>
        <el-button @click="tickVisible = true"><el-icon><Clock /></el-icon> 定时</el-button>
        <el-button @click="sidebarCollapsed = !sidebarCollapsed">
          <el-icon><Fold /></el-icon> {{ sidebarCollapsed ? '展开目录' : '收起目录' }}
        </el-button>
        <el-button @click="clearHistory"><el-icon><Delete /></el-icon> 清空对话</el-button>
        <el-tag v-if="running" type="warning">运行中</el-tag>
      </div>
    </div>

    <div class="main-body">
      <div v-show="!sidebarCollapsed" class="sidebar">
        <CodeWorkspacePanel
          v-if="agent?.profile === 'code'"
          ref="codeWpRef"
          :agent-id="agentId"
          :session-id="sessionId"
          :code-run-id="activeCodeRunId"
          :sandbox-id="agentSandboxId"
        />
        <WorkplacePanel v-else
          ref="wpRef"
          :agent-id="agentId"
          :sandbox-id="agentSandboxId"
        />
      </div>
      <div class="chat-main">
        <div v-if="agent?.profile === 'code'" class="code-agent-context">
          <el-tag type="primary">Code Agent</el-tag>
          <span>{{ agent.code_project_name || '项目不可访问' }}</span>
          <el-tag :type="agent.code_project_availability?.ready ? 'success' : 'danger'">
            Manifest {{ projectStatusLabel(agent.code_project_availability) }}
          </el-tag>
          <router-link
            v-if="!agent.code_project_availability?.ready"
            to="/code-projects"
          >
            管理 Code Projects
          </router-link>
        </div>
        <div class="chat-body" ref="scrollRef" @scroll.passive="onChatScroll">
          <div v-if="!messages.length && !streaming" class="empty-chat">
            <el-icon :size="48" color="#dcdfe6"><ChatDotRound /></el-icon>
            <p>开始和 Agent 对话吧</p>
          </div>

          <div
            v-for="item in displayMessages"
            :key="item.key"
            v-memo="[item.key, item.m.content, item.stepCount, isExecOpen(item.execKey), item.stepsLoading, item.steps.length, item.steps[0]?.title, item.steps[item.steps.length - 1]?.title, toolDetailRevision]"
            :class="['msg-row', item.m.role === 'user' ? 'user-row' : 'assistant-row']"
          >
            <div v-if="item.m.role !== 'user'" class="agent-avatar">
              <span>∞</span>
            </div>
            <div class="msg-col">
              <div
                v-if="item.m.role === 'assistant' && item.stepCount > 0"
                class="exec-card"
              >
                <div class="exec-header" role="button" @click="toggleExec(item.execKey)">
                  <el-icon class="exec-arrow" :class="{ open: isExecOpen(item.execKey) }"><ArrowRight /></el-icon>
                  <span class="exec-title">执行过程</span>
                  <el-progress
                    v-if="messageProgress(item.m).visible"
                    class="exec-progress-ring"
                    type="circle"
                    :percentage="messageProgress(item.m).percent"
                    :width="38"
                    :stroke-width="4"
                    :status="messageProgress(item.m).status"
                    :title="`任务进度 ${messageProgress(item.m).percent}%`"
                  />
                  <span class="exec-badge">{{ item.stepCount }} 个步骤</span>
                </div>
                <div v-if="isExecOpen(item.execKey)" class="exec-steps">
                  <div
                    v-if="execSummary(item.m).lines.length || execSummary(item.m).files.length"
                    class="exec-summary"
                  >
                    <div
                      v-for="(line, li) in execSummary(item.m).lines"
                      :key="`line-${li}`"
                      class="exec-summary-line"
                    >
                      {{ line }}
                    </div>
                    <div v-if="execSummary(item.m).files.length" class="exec-summary-files">
                      <button
                        v-for="(file, fi) in execSummary(item.m).files"
                        :key="`file-${fi}`"
                        type="button"
                        class="exec-file-link"
                        @click.stop="downloadWorkplaceFile(file.path)"
                      >
                        {{ file.label }}
                      </button>
                    </div>
                  </div>
                  <div v-if="item.stepsLoading" class="exec-older">加载中…</div>
                  <template v-else>
                    <div v-if="item.older" class="exec-older">更早 {{ item.older }} 步已折叠</div>
                    <div
                      v-for="(step, si) in (item.steps.length ? item.steps : [NO_TOOL_PLACEHOLDER_STEP])"
                      :key="si"
                      :class="['exec-step', { 'is-expandable': isStepDetailExpandable(step) }]"
                      :role="isStepDetailExpandable(step) ? 'button' : undefined"
                      :tabindex="isStepDetailExpandable(step) ? 0 : undefined"
                      @click="isStepDetailExpandable(step) && toggleToolDetail(toolStepKey(item.execKey, si))"
                      @keydown.enter.prevent="isStepDetailExpandable(step) && toggleToolDetail(toolStepKey(item.execKey, si))"
                    >
                      <span class="step-glyph" aria-hidden="true">{{ stepGlyph(step) }}</span>
                      <div class="step-body">
                        <div class="step-title">
                          <span>{{ stepTitle(step) }}</span>
                          <span v-if="!isStepDetailExpandable(step) && stepInlineDetail(step)" class="step-inline-detail"> · {{ stepInlineDetail(step) }}</span>
                        </div>
                        <template v-if="isStepDetailExpandable(step) && isToolDetailOpen(toolStepKey(item.execKey, si))">
                          <pre class="step-snippet">{{ executionStepDetail(step) }}</pre>
                          <pre v-if="step.snippet" class="step-snippet">{{ step.snippet }}</pre>
                        </template>
                        <template v-else>
                          <div v-if="(step.status === 'error' || step.action === 'cte_attempt') && stepErrorDetail(step)" class="step-content">{{ stepErrorDetail(step) }}</div>
                          <pre v-if="step.snippet" class="step-snippet">{{ step.snippet }}</pre>
                        </template>
                        <div v-if="step.checkpoint_path || step.sql_checkpoint_path" class="step-checkpoints">
                          <button v-if="step.checkpoint_path" type="button" @click.stop="downloadWorkplaceFile(step.checkpoint_path)">轮次记录</button>
                          <button v-if="step.sql_checkpoint_path" type="button" @click.stop="downloadWorkplaceFile(step.sql_checkpoint_path)">SQL 草稿</button>
                        </div>
                      </div>
                      <el-icon v-if="isStepDetailExpandable(step)" class="step-detail-arrow" :class="{ open: isToolDetailOpen(toolStepKey(item.execKey, si)) }"><ArrowRight /></el-icon>
                      <el-icon v-if="step.status === 'done'" class="step-status done"><CircleCheck /></el-icon>
                      <el-icon v-else-if="step.status === 'error'" class="step-status error"><CircleClose /></el-icon>
                      <el-icon v-else class="step-status running is-loading"><Loading /></el-icon>
                    </div>
                  </template>
                </div>
              </div>
              <div :class="['msg-bubble', item.m.role, item.m.role === 'assistant' ? 'md-body' : '']">
                <div v-if="item.sourceLabel" class="src-tag">{{ item.sourceLabel }}</div>
                <pre v-if="item.m.role === 'user'" class="content">{{ item.m.content }}</pre>
                <div
                  v-else
                  class="content md-render"
                  :class="{ 'is-empty-fallback': !String(item.m.content || '').trim() }"
                  v-html="renderMessage(item.m)"
                  v-sql-hydrate
                  @click="onMarkdownClick"
                />
              </div>
              <div class="msg-meta">
                <span>{{ item.m.created_at || '' }}</span>
                <el-button link size="small" @click="copyText(item.m.role === 'assistant' ? assistantDisplayContent(item.m) : item.m.content)">复制</el-button>
                <el-button v-if="item.m.role === 'user' && item.m.id" link size="small" type="danger" @click="deleteMessage(item.m)">删除</el-button>
              </div>
            </div>
            <div v-if="item.m.role === 'user'" class="user-avatar">
              <el-icon><User /></el-icon>
            </div>
          </div>

          <div v-if="streaming" class="msg-row assistant-row">
            <div class="agent-avatar"><span>∞</span></div>
            <div class="msg-col">
              <div class="exec-card">
                <div class="exec-header" role="button" @click="toggleExec('live')">
                  <el-icon class="exec-arrow" :class="{ open: isExecOpen('live') }"><ArrowRight /></el-icon>
                  <span class="exec-title">{{ liveExecTitle }}</span>
                  <div class="exec-live-context" :title="liveProgress.contextLabel">
                    <el-progress
                      class="exec-progress-ring"
                      type="circle"
                      :percentage="liveProgress.percent"
                      :width="38"
                      :stroke-width="4"
                    />
                    <span>{{ liveProgress.contextLabel }}</span>
                  </div>
                  <span class="exec-badge">{{ liveStepsBadge }}</span>
                </div>
                <div v-if="isExecOpen('live')" class="exec-steps">
                  <div v-if="!liveStepVisibleCount" class="exec-step">
                    <span class="step-glyph" aria-hidden="true">⏳</span>
                    <div class="step-body">
                      <div class="step-title">{{ liveStatusText }} · 已用时 {{ liveElapsedSeconds }} 秒</div>
                      <div class="step-content">{{ liveProgress.contextLabel }}</div>
                    </div>
                    <el-icon class="step-status running is-loading"><Loading /></el-icon>
                  </div>
                  <template v-else>
                    <div v-if="liveStepsView.older" class="exec-older">更早 {{ liveStepsView.older }} 步已折叠</div>
                    <div
                      v-for="(step, si) in liveStepsView.steps"
                      :key="`${step.type}-${step.iteration || si}-${step.action || ''}-${si}`"
                      v-memo="[step.status, step.title, step.iteration, step.action, step.content?.length, step.preview?.length, step.snippet?.length, toolDetailRevision]"
                      :class="['exec-step', { 'is-expandable': isStepDetailExpandable(step) }]"
                      :role="isStepDetailExpandable(step) ? 'button' : undefined"
                      :tabindex="isStepDetailExpandable(step) ? 0 : undefined"
                      @click="isStepDetailExpandable(step) && toggleToolDetail(toolStepKey('live', si))"
                      @keydown.enter.prevent="isStepDetailExpandable(step) && toggleToolDetail(toolStepKey('live', si))"
                    >
                      <span class="step-glyph" aria-hidden="true">{{ stepGlyph(step) }}</span>
                      <div class="step-body">
                        <div class="step-title">
                          <span>{{ stepTitle(step) }}</span>
                          <span v-if="!isStepDetailExpandable(step) && stepInlineDetail(step)" class="step-inline-detail"> · {{ stepInlineDetail(step) }}</span>
                        </div>
                        <template v-if="isStepDetailExpandable(step) && isToolDetailOpen(toolStepKey('live', si))">
                          <pre class="step-snippet">{{ executionStepDetail(step) }}</pre>
                          <pre v-if="step.snippet" class="step-snippet">{{ step.snippet }}</pre>
                        </template>
                        <template v-else>
                          <div v-if="(step.status === 'error' || step.action === 'cte_attempt') && stepErrorDetail(step)" class="step-content">{{ stepErrorDetail(step) }}</div>
                          <pre v-if="step.snippet" class="step-snippet">{{ step.snippet }}</pre>
                        </template>
                        <div v-if="step.checkpoint_path || step.sql_checkpoint_path" class="step-checkpoints">
                          <button v-if="step.checkpoint_path" type="button" @click.stop="downloadWorkplaceFile(step.checkpoint_path)">轮次记录</button>
                          <button v-if="step.sql_checkpoint_path" type="button" @click.stop="downloadWorkplaceFile(step.sql_checkpoint_path)">SQL 草稿</button>
                        </div>
                      </div>
                      <el-icon v-if="isStepDetailExpandable(step)" class="step-detail-arrow" :class="{ open: isToolDetailOpen(toolStepKey('live', si)) }"><ArrowRight /></el-icon>
                      <el-icon v-if="step.status === 'done'" class="step-status done"><CircleCheck /></el-icon>
                      <el-icon v-else-if="step.status === 'error'" class="step-status error"><CircleClose /></el-icon>
                      <el-icon v-else class="step-status running is-loading"><Loading /></el-icon>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="chat-input">
          <el-upload :show-file-list="false" :http-request="onChatUpload">
            <el-button circle><el-icon><Upload /></el-icon></el-button>
          </el-upload>
          <VoiceInputButton @text="onVoiceText" />
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            placeholder="输入消息... (Ctrl+Enter 发送)"
            @keydown.ctrl.enter="send"
          />
          <el-button type="primary" :loading="sending" @click="send">发送</el-button>
          <el-button v-if="running || sending" @click="stop">停止</el-button>
        </div>
      </div>
    </div>

    <SessionNoteDialog
      ref="noteDlg"
      v-model="noteVisible"
      :agent-id="agentId"
      :session-id="sessionId"
    />
    <SessionTickDialog
      v-model="tickVisible"
      :agent-id="agentId"
      :session-id="sessionId"
    />
  </div>
</template>

<script setup>
import { ref, shallowRef, computed, onMounted, onUnmounted, nextTick, watch, triggerRef } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  EditPen, Document, Clock, Fold, Delete, Upload, ChatDotRound,
  ArrowRight, CircleCheck, CircleClose, Loading, User,
} from '@element-plus/icons-vue'
import { getCgi, postCgi, getCheckStatus } from '../api'
import api from '../api'
import { marked } from 'marked'
import {
  enhanceMarkdownHtml,
  extractFinalDisplayContent,
  highlightCodeToHtml,
  toggleCodeSnippet,
  prepareMarkdownForPreview,
} from '../utils/markdownPreview'
import WorkplacePanel from '../components/WorkplacePanel.vue'
import CodeWorkspacePanel from '../components/CodeWorkspacePanel.vue'
import SessionNoteDialog from '../components/SessionNoteDialog.vue'
import SessionTickDialog from '../components/SessionTickDialog.vue'
import ThemeSwitch from '../components/ThemeSwitch.vue'
import VoiceInputButton from '../components/VoiceInputButton.vue'

const route = useRoute()
const router = useRouter()
const agentId = route.params.id
const sessionId = ref(route.query.session || '')
const agent = ref(null)
const activeCodeRunId = ref('')
const messages = shallowRef([])
const hydratedSteps = ref({})
const input = ref('')
const sending = ref(false)
const streaming = ref(false)
const liveSteps = ref([])
const seenCodeEventKeys = new Set()
const running = ref(false)
const liveStatusText = ref('正在处理请求')
const liveElapsedSeconds = ref(0)
const sidebarCollapsed = ref(false)
const noteVisible = ref(false)
const noteDlg = ref(null)
const tickVisible = ref(false)
const scrollRef = ref(null)
const wpRef = ref(null)
const codeWpRef = ref(null)
const execOpen = ref({})
const toolDetailOpen = ref({})
const toolDetailRevision = ref(0)
const unreadSessions = ref({})
const inboundNotice = ref(null)
let ws = null
let statusTimer = null
let statusEtag = ''
let statusBackoffMs = 10000
const STATUS_BACKOFF_STEPS = [10000, 30000, 60000]
let resumePollTimer = null
let wsReconnectTimer = null
let wsPingTimer = null
let wsRetryMs = 1000
let pageAlive = true
let intentionalWsClose = false
let scrollRaf = 0
let scrollThrottleTimer = 0
let liveStepsFlushRaf = 0
let liveStepsBaseIndex = 0
/** User clicked 停止 — ignore further step WS until next send. */
let userRequestedStop = false
let pendingScrollAfterSteps = false
let userPinnedBottom = true
let liveStartedAt = 0
let liveElapsedTimer = null
const SCROLL_NEAR_BOTTOM_PX = 80
const SCROLL_THROTTLE_MS = 150
const DONE_CONTENT_PREVIEW_MAX = 500
const mdCache = new Map()

function startLiveElapsed() {
  if (!liveStartedAt) liveStartedAt = Date.now()
  liveElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - liveStartedAt) / 1000))
  if (liveElapsedTimer) return
  liveElapsedTimer = setInterval(() => {
    liveElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - liveStartedAt) / 1000))
  }, 1000)
}

function resetLiveElapsed() {
  if (liveElapsedTimer) clearInterval(liveElapsedTimer)
  liveElapsedTimer = null
  liveStartedAt = 0
  liveElapsedSeconds.value = 0
  liveStatusText.value = '正在处理请求'
}

marked.setOptions({ breaks: true, gfm: true })

function isHiddenStep(s) {
  return !s || !!s.hidden
}

/** Shown when assistant replied but no tool/MCP steps were recorded. */
const NO_TOOL_PLACEHOLDER_STEP = Object.freeze({
  type: 'info',
  action: 'no_tools',
  title: '本轮未调用 MCP/Shell',
  status: 'done',
})

const sessionName = computed(() => {
  const s = agent.value?.session_list?.find((x) => x.session_id === sessionId.value)
  return s?.name || sessionId.value
})

const isImSession = computed(() => {
  const s = agent.value?.session_list?.find((x) => x.session_id === sessionId.value)
  if (s?.source && String(s.source).startsWith('im:')) return true
  const n = s?.name || sessionName.value || ''
  return ['飞书', '钉钉', 'Telegram', 'QQ', '企业微信', 'Mock渠道'].includes(n) || String(n).startsWith('IM:')
})

const agentSandboxId = computed(() => agent.value?.sandbox || agent.value?.sandbox_id || '')

function projectStatusLabel(availability) {
  const labels = {
    ready: '已就绪',
    project_disabled: '项目已停用',
    project_environment_not_allowed: '环境不支持',
    manifest_missing: '尚未发布',
    manifest_invalid: 'Manifest 无效',
    code_project_unauthorized: '项目不可访问',
  }
  return labels[availability?.reason] || availability?.reason || '状态未知'
}

function reloadWorkspacePanels() {
  // CodeAgent edits happen in its run-bound Workspace; the standard panel is
  // still used by legacy agents. Reload both refs so a completed run is
  // visible immediately even when the run id itself did not change.
  codeWpRef.value?.load?.()
  wpRef.value?.load?.()
}

function messageSourceLabel(m) {
  const src = m?.meta?.source || ''
  if (!src || !String(src).startsWith('im:')) return ''
  const chatType = m?.meta?.chat_type === 'group' ? '群聊' : '私聊'
  const map = {
    'im:feishu': '飞书',
    'im:dingtalk': '钉钉',
    'im:telegram': 'Telegram',
    'im:qq': 'QQ',
    'im:wecom': '企微',
    'im:mock': 'Mock',
  }
  const username = String(m?.meta?.sender_username || '').replace(/^@/, '').trim()
  const displayName = String(m?.meta?.sender_display_name || '').trim()
  const userId = String(m?.meta?.user_id || '').trim()
  const initiator = username ? `@${username}` : (displayName || userId)
  const source = `${map[src] || src}·${chatType}`
  return initiator ? `${source} · 发起人：${initiator}` : source
}
function shortId(id) {
  return id ? id.slice(0, 8) : ''
}

function syncSessionToUrl() {
  if (!sessionId.value) return
  if (route.query.session === sessionId.value) return
  router.replace({
    query: { ...route.query, session: sessionId.value },
  })
}

function messageStepCount(m) {
  const meta = m?.meta || {}
  if (typeof meta.step_count === 'number' && meta.step_count > 0) return meta.step_count
  if (Array.isArray(meta.steps) && meta.steps.length) return meta.steps.length
  // Any assistant bubble → always keep exec-card header (≥1), even if content is empty
  if (m?.role === 'assistant') return 1
  return 0
}

function metaSavedPaths(meta) {
  return Array.isArray(meta?.saved_paths) ? meta.saved_paths.filter((x) => String(x || '').trim()) : []
}

function cteAttemptStageLabel(stage) {
  const map = {
    catalog_list: 'MCP 列表',
    resource_select: '资源选择',
    schema_describe: '表结构获取',
    sql_compose: 'SQL 生成',
    sql_validate: 'SQL 校验',
    method_compose: '指标统计方法生成',
    query_count: 'COUNT 校验',
    query_page: '分页查询',
  }
  return map[String(stage || '').trim()] || String(stage || '').trim() || '未知阶段'
}

function summarizeSavedFile(path) {
  const rel = String(path || '').trim()
  if (!rel) return null
  const name = rel.split('/').pop() || rel
  if (name === 'final_sql.sql') return { path: rel, label: 'SQL final_sql.sql' }
  if (name === 'draft_sql.sql') return { path: rel, label: 'SQL draft_sql.sql' }
  return { path: rel, label: name }
}

function execSummary(message) {
  const meta = message?.meta || {}
  const lines = []
  const files = metaSavedPaths(meta)
    .map(summarizeSavedFile)
    .filter(Boolean)
  const attemptsUsed = Number(meta.cte_attempts_used) || 0
  const attemptsLimit = Number(meta.cte_attempt_limit) || 0
  const errors = Array.isArray(meta.cte_attempt_errors) ? meta.cte_attempt_errors : []
  const artifactCount = Number(meta.attempt_artifact_count) || 0
  const latestArtifact = meta.latest_attempt_artifact || {}
  if (meta.context_available_percent != null) {
    lines.push(`上下文可用: ${clampPercent(meta.context_available_percent)}%`)
  }
  if (meta.progress_percent != null) {
    lines.push(`任务进度: ${clampPercent(meta.progress_percent)}%`)
  }
  if (artifactCount > 0) {
    lines.push(`轮次中间产物: ${artifactCount} 份`)
    for (const path of [latestArtifact.json_path, latestArtifact.sql_path]) {
      const file = summarizeSavedFile(path)
      if (file && !files.some((item) => item.path === file.path)) files.push(file)
    }
  }
  if (attemptsUsed > 0) {
    const roundText = attemptsLimit > 0
      ? `CTE 已执行 ${attemptsUsed}/${attemptsLimit} 轮`
      : `CTE 已执行 ${attemptsUsed} 轮`
    lines.push(roundText)
  }
  if (errors.length) {
    const last = errors[errors.length - 1] || {}
    const stage = cteAttemptStageLabel(last.stage)
    const attempt = Number(last.attempt) || attemptsUsed || errors.length
    const detail = String(last.error || '').trim()
    lines.push(
      detail
        ? `最近失败: 第 ${attempt} 轮 · ${stage} · ${detail}`
        : `最近失败: 第 ${attempt} 轮 · ${stage}`,
    )
  }
  const runId = String(meta.export_run_id || '').trim()
  const taskTitle = String(meta.task_title || '').trim()
  if (meta.context_continuity) {
    lines.unshift(`上下文已继承${taskTitle ? `: ${taskTitle}` : ''}`)
  } else if (taskTitle) {
    lines.unshift(`任务: ${taskTitle}`)
  }
  if (runId) {
    lines.push(`Run ID: ${runId}`)
  }
  return { lines, files }
}

function clampPercent(value, fallback = 0) {
  const n = Number(value)
  if (!Number.isFinite(n)) return fallback
  return Math.max(0, Math.min(100, Math.round(n)))
}

function messageProgress(message) {
  const meta = message?.meta || {}
  const hasProgress = meta.progress_percent != null || !!meta.export_run_id
  return {
    visible: hasProgress,
    percent: clampPercent(meta.progress_percent),
    status: undefined,
  }
}

const EMPTY_ASSISTANT_FALLBACK = '（本轮未产生文字回复；详见执行过程）'

function assistantDisplayContent(m) {
  const text = extractFinalDisplayContent(m?.content || '').trim()
  return text || EMPTY_ASSISTANT_FALLBACK
}

const displayMessages = computed(() =>
  messages.value.map((m, i) => {
    const key = m.id != null ? String(m.id) : `idx-${i}`
    const ek = execKey(m, i)
    const hydrated = hydratedSteps.value[ek]
    const baseCount = messageStepCount(m)
    const stepCount = Math.max(
      Number(hydrated?.step_count) || 0,
      baseCount,
    )
    return {
      key,
      m,
      i,
      execKey: ek,
      stepCount,
      steps: hydrated?.steps || [],
      older: hydrated?.older || 0,
      stepsLoading: !!hydrated?.loading,
      sourceLabel: messageSourceLabel(m),
    }
  }),
)

/** Lightweight count — no collapse (used when exec panel is collapsed). */
const liveStepVisibleCount = computed(() =>
  liveSteps.value.reduce((n, s) => n + (isHiddenStep(s) ? 0 : 1), 0),
)

const liveStepsBadge = computed(() => {
  const steps = liveSteps.value.filter((s) => !isHiddenStep(s))
  const n = steps.length
  if (!n) return '0 个步骤'
  return allStepsDone(steps) ? `${n} 个步骤 √` : `${n} 个步骤`
})

const liveExecTitle = computed(() => {
  const steps = liveSteps.value.filter((s) => !isHiddenStep(s))
  const active = [...steps].reverse().find((s) => s.status !== 'done') || steps[steps.length - 1]
  return active?.title || '执行过程'
})

const liveProgress = computed(() => {
  const steps = liveSteps.value.filter((s) => !isHiddenStep(s))
  const explicit = [...steps].reverse().find((s) => s.progress_percent != null)
  const context = [...steps].reverse().find(
    (s) => s.context_available_percent != null || s.task_relation || s.task_title,
  ) || explicit
  const done = steps.filter((s) => s.status === 'done').length
  const fallback = steps.length
    ? Math.min(18, 5 + Math.round((done / Math.max(1, steps.length)) * 10))
    : 3
  const percent = clampPercent(explicit?.progress_percent, fallback)
  const contextPercent = clampPercent(context?.context_available_percent, 100)
  const relation = String(context?.task_relation || '').trim()
  const taskTitle = String(context?.task_title || '').trim()
  const runId = String(explicit?.export_run_id || context?.export_run_id || '').trim()
  const continuity = context?.context_continuity || ['continue', 'revise'].includes(relation)
  const contextState = continuity
    ? '上下文已继承'
    : (steps.length ? '任务上下文已建立' : '正在建立任务上下文')
  const parts = [`会话上下文可用 ${contextPercent}%`, contextState]
  if (taskTitle) parts.push(taskTitle)
  if (runId) parts.push(`Run ${runId}`)
  if (explicit?.progress_percent != null) parts.push(`任务进度 ${percent}%`)
  return { percent, contextPercent, contextLabel: parts.join(' · ') }
})

/** Collapse only while the live exec panel is open. */
const visibleLiveSteps = computed(() => {
  if (!isExecOpen('live')) return []
  return collapseLlmSteps(liveSteps.value.filter((s) => !isHiddenStep(s)))
})

const liveStepsView = computed(() => {
  if (!isExecOpen('live')) {
    return { total: 0, older: 0, steps: [] }
  }
  const all = visibleLiveSteps.value
  return { total: all.length, older: 0, steps: all }
})

function isTempSetupPreview(preview) {
  if (!preview) return false
  if (/写入文件|^#+\s|^\s*[-*]\s/m.test(preview)) return false
  return /task|mkdir|工作目录|创建.*(文件夹|目录)/i.test(preview)
}

function collapseLlmSteps(steps) {
  const out = []
  for (const step of steps) {
    const copy = { ...step }
    if (copy.type === 'llm' && copy.preview && isTempSetupPreview(copy.preview)) {
      copy.preview = null
    }
    const prev = out[out.length - 1]
    if (copy.type === 'llm' && !stepDetail(copy) && prev?.type === 'llm' && !stepDetail(prev)) {
      out[out.length - 1] = { ...prev, iteration: copy.iteration, title: copy.title, status: copy.status }
      continue
    }
    out.push(copy)
  }
  return out
}

function stepLabel(step) {
  if (step.type === 'llm') return `LLM 推理 (第 ${step.iteration || '?'} 轮)`
  if (step.type === 'model_route') return step.title || '模型路由'
  if (step.action === 'skill_loaded') return step.title || '已加载 Skills'
  if (step.type === 'info' || step.action === 'mcp_loaded') return step.title || '已加载 MCPs'
  if (step.type === 'tool') return step.title || `工具 · ${step.action || ''}`
  return step.title || '步骤'
}

function stepTitle(step) {
  return step.title || stepLabel(step)
}

function stepInlineDetail(step) {
  // Keep the execution row compact while making completed CodeAgent phases
  // useful at a glance (for example: "Runtime 结果 · coding done"). Error
  // details remain in the dedicated block below the row.
  if (!step || step.status === 'error' || step.action === 'cte_attempt') return ''
  const detail = stepDetail(step).replace(/\s+/g, ' ').trim()
  return detail.length > 180 ? `${detail.slice(0, 180)}…` : detail
}

function stepGlyph(step) {
  if (step.type === 'llm') return '🤖'
  if (step.type === 'model_route') return '🧭'
  if (step.action === 'skill_loaded' || step.action === 'skill_read_md') return '📘'
  if ((step.action || '').startsWith('code_')) return '🧰'
  if (step.action === 'no_tools' || step.action === 'no_tools_export' || step.action === 'conversational_reply') return '💬'
  if (step.type === 'info' || step.action === 'mcp_loaded') return '🔌'
  if (step.action === 'shell') return '💻'
  if (step.action === 'mcp_tool_call' || step.action === 'httpmcp_call') return '🔧'
  if (step.action === 'file_write' || step.action === 'file_read' || step.action === 'file_search_replace') return '📄'
  if (step.type === 'tool') return '🔧'
  return '•'
}

function stepDetail(step) {
  if (isHiddenStep(step)) return ''
  if (step.content) return sanitizeStepDetail(step.content)
  if (step.preview) {
    return sanitizeStepDetail(step.preview)
      .split('\n')
      .filter((line) => !/^(SHELL:|MCP:|HTTPMCP:|执行命令:)/.test(line.trim()))
      .join('\n')
      .trim()
  }
  return ''
}

function toolStepDetail(step) {
  const detail = stepDetail(step)
  if (detail) return detail
  // Earlier messages stored only the action title.  Make the expansion
  // visibly meaningful instead of leaving a rotated arrow with an empty pane.
  return '此历史步骤未保存工具输出；后续执行会显示已脱敏、限长的执行结果。'
}

function modelRouteStepDetail(step) {
  const detail = step?.detail
  if (!detail || typeof detail !== 'object') {
    return '此历史模型路由步骤未保存审计详情。'
  }
  // The API has already allowlisted this object. Keep a second allowlist at
  // render time so a future wire-format expansion cannot expose prompts/keys.
  const safe = {
    version: Number(detail.version || 1),
    kind: String(detail.kind || ''),
    decision_id: String(detail.decision_id || ''),
    policy_id: String(detail.policy_id || ''),
    policy_version: Number(detail.policy_version || 0),
    role: String(detail.role || ''),
    frozen_model_id: String(detail.frozen_model_id || ''),
    candidate_ids: Array.isArray(detail.candidate_ids) ? detail.candidate_ids.map((id) => String(id)) : [],
    exclusions: Array.isArray(detail.exclusions)
      ? detail.exclusions.map((item) => ({ model_id: String(item?.model_id || ''), reason: String(item?.reason || '') }))
      : [],
    failure: String(detail.failure || ''),
    duration_ms: Number(detail.duration_ms || 0),
  }
  return JSON.stringify(safe, null, 2)
}

function executionStepDetail(step) {
  return step?.type === 'model_route' ? modelRouteStepDetail(step) : toolStepDetail(step)
}

function sanitizeStepDetail(text) {
  let cleaned = String(text || '')
  if (!cleaned) return ''
  if (/^\s*FINAL\s*[:：]/im.test(cleaned)) {
    const matches = [...cleaned.matchAll(/^\s*FINAL\s*[:：]\s*/gim)]
    if (matches.length) {
      const last = matches[matches.length - 1]
      cleaned = cleaned.slice(last.index + last[0].length)
    }
  }
  cleaned = cleaned
    .replace(/^\s*(?:Actually|Wait|Looking at|Let me|So I need|The previous turn|I think|I notice)\b.*$/gim, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return cleaned
}

function sanitizeStepSnippet(text) {
  // Code is literal text; prose cleanup must not remove valid source lines.
  const cleaned = String(text || '').replace(/\u0000/g, '')
  return cleaned.length > 3200 ? `${cleaned.slice(0, 3200)}\n…（片段已截断）` : cleaned
}

function stepErrorDetail(step) {
  const detail = stepDetail(step)
  if (!detail) return ''
  return detail.length > 160 ? `${detail.slice(0, 160)}…` : detail
}

function stepsBadgeText(steps) {
  const n = steps.length
  return allStepsDone(steps) ? `${n} 个步骤 √` : `${n} 个步骤`
}

function renderMessage(m) {
  const text = assistantDisplayContent(m)
  const key = `${m.id ?? 'noid'}::${text.length}::${text.slice(0, 64)}::${text.slice(-32)}`
  const cached = mdCache.get(key)
  if (cached !== undefined) return cached
  const prepared = prepareMarkdownForPreview(text)
  const html = enhanceMarkdownHtml(marked.parse(prepared))
  mdCache.set(key, html)
  if (mdCache.size > 200) {
    const first = mdCache.keys().next().value
    mdCache.delete(first)
  }
  return html
}

async function downloadWorkplaceFile(relPath) {
  const path = String(relPath || '').trim().replace(/^\/+/, '')
  if (!path) return
  try {
    const res = await api.get('/pages/page_agent_chat.cgi', {
      params: { action: 'download_workplace', agent_id: agentId, path },
      responseType: 'blob',
    })
    const blob = res.data
    if (!blob || blob.size === 0) {
      ElMessage.error('文件为空，无法下载')
      return
    }
    const ctype = String(res.headers?.['content-type'] || '')
    if (ctype.includes('application/json') || ctype.includes('text/json') || ctype.includes('text/plain')) {
      const text = await blob.text()
      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed.code === 'number' && parsed.code !== 0) {
          ElMessage.error(parsed.msg || '下载失败')
          return
        }
      } catch {
        // not JSON error payload — fall through only if looks like binary miss
      }
      if (text.trim().startsWith('{') && text.includes('"code"')) {
        ElMessage.error('下载失败')
        return
      }
    }
    const name = path.split('/').pop() || 'download'
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e?.msg || e?.message || '下载失败')
  }
}

async function hydrateSqlBlocks(root) {
  if (!root) return
  const nodes = root.querySelectorAll?.('.md-sql-inline[data-sql-loaded="false"]') || []
  if (!nodes.length) return
  for (const node of nodes) {
    node.setAttribute('data-sql-loaded', 'true')
    const path = node.getAttribute('data-wp-path') || ''
    const codeEl = node.querySelector('.md-sql-inline-code')
    if (!path || !codeEl) continue
    try {
      const result = await api.get('/pages/page_agent_chat.cgi', {
        params: { action: 'view_workplace', agent_id: agentId, path },
      })
      const content = result?.data?.content
      if (content && String(content).trim()) {
        codeEl.innerHTML = highlightCodeToHtml(content, 'sql')
      } else {
        codeEl.textContent = '（空文件或无内容）'
      }
    } catch {
      codeEl.textContent = '（加载失败）'
    }
  }
}

const vSqlHydrate = {
  mounted(el) {
    nextTick(() => hydrateSqlBlocks(el))
  },
  updated(el) {
    nextTick(() => hydrateSqlBlocks(el))
  },
}

async function onMarkdownClick(e) {
  const actionButton = e.target?.closest?.('button.md-code-action')
  if (actionButton) {
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
      return
    }
  }
  const a = e.target?.closest?.('a.wp-download')
  if (!a) return
  e.preventDefault()
  downloadWorkplaceFile(a.getAttribute('data-wp-path') || '')
}

function allStepsDone(steps) {
  return steps.length > 0 && steps.every((s) => s.status === 'done')
}

function execKey(m, i) {
  return String(m?.id ?? `idx-${i}`)
}

function isExecOpen(key) {
  // Default collapsed — only open when explicitly set true
  return execOpen.value[String(key)] === true
}

function isStepDetailExpandable(step) {
  return step?.type === 'tool' || step?.type === 'model_route'
}

function toolStepKey(execKey, index) {
  return `${String(execKey)}:${index}`
}

function isToolDetailOpen(key) {
  return toolDetailOpen.value[String(key)] === true
}

function toggleToolDetail(key) {
  const normalized = String(key)
  toolDetailOpen.value = {
    ...toolDetailOpen.value,
    [normalized]: !isToolDetailOpen(normalized),
  }
  toolDetailRevision.value += 1
}

function toggleExec(key) {
  const k = String(key)
  const opening = !isExecOpen(k)
  execOpen.value = { ...execOpen.value, [k]: opening }
  if (opening) ensureHistorySteps(k)
}

function visibleLiveStepSnapshot() {
  return collapseLlmSteps(liveSteps.value.filter((s) => !isHiddenStep(s)))
}

function liveStepCountForMeta() {
  const n = visibleLiveStepSnapshot().length
  return n || liveStepVisibleCount.value || 0
}

async function ensureHistorySteps(execKeyStr) {
  const k = String(execKeyStr)
  const existing = hydratedSteps.value[k]
  if (existing?.steps?.length || existing?.loading) return
  const item = displayMessages.value.find((x) => x.execKey === k)
  const mid = item?.m?.id
  const preservedCount = existing?.step_count ?? messageStepCount(item?.m) ?? 0
  if (!mid || String(mid).startsWith('tmp-')) {
    // Fallback: use liveSteps snapshot if this is the just-finished turn
    const live = visibleLiveStepSnapshot()
    const steps = live.length ? live : [NO_TOOL_PLACEHOLDER_STEP]
    hydratedSteps.value = {
      ...hydratedSteps.value,
      [k]: {
        steps,
        older: 0,
        step_count: Math.max(steps.length, preservedCount, 1),
        loading: false,
      },
    }
    return
  }
  hydratedSteps.value = {
    ...hydratedSteps.value,
    [k]: { ...(existing || {}), loading: true, steps: existing?.steps || [], older: existing?.older || 0 },
  }
  try {
    const res = await getCgi('/pages/page_agent_chat.cgi', {
      action: 'get_message_steps',
      agent_id: agentId,
      message_id: mid,
      limit: 0,
    })
    const data = res.data || {}
    const stepsRaw = collapseLlmSteps((data.steps || []).filter((s) => !isHiddenStep(s)))
    const steps = stepsRaw.length ? stepsRaw : [NO_TOOL_PLACEHOLDER_STEP]
    hydratedSteps.value = {
      ...hydratedSteps.value,
      [k]: {
        steps,
        older: 0,
        step_count: Math.max(
          Number(data.step_count) || 0,
          steps.length,
          preservedCount,
          1,
        ),
        loading: false,
      },
    }
  } catch {
    // Keep header visible — never wipe step_count to 0 on fetch failure
    hydratedSteps.value = {
      ...hydratedSteps.value,
      [k]: {
        steps: existing?.steps?.length
          ? existing.steps
          : [NO_TOOL_PLACEHOLDER_STEP],
        older: existing?.older || 0,
        step_count: Math.max(preservedCount || 0, existing?.step_count || 0, 1),
        loading: false,
      },
    }
  }
}

function closePage() {
  if (window.history.length > 1) {
    router.back()
  } else {
    window.close()
    router.push('/agents')
  }
}

async function loadAgent() {
  const res = await getCgi('/pages/page_agent.cgi', { action: 'get', id: agentId })
  agent.value = res.data
  const list = res.data?.session_list || []
  if (!sessionId.value || !list.find((s) => s.session_id === sessionId.value)) {
    sessionId.value = list[0]?.session_id || agentId
  }
  syncSessionToUrl()
}

async function hydrateActiveCodeRun() {
  if (agent.value?.profile !== 'code' || !sessionId.value || activeCodeRunId.value) return
  try {
    const res = await getCgi('/pages/page_agent_chat.cgi', {
      action: 'get_code_result',
      agent_id: agentId,
      session_id: sessionId.value,
    })
    const runId = String(res.data?.run_id || '').trim()
    const terminal = res.data?.terminal === true
    const workspaceAvailable = res.data?.workspace?.available === true
    if (runId && (!terminal || workspaceAvailable)) activeCodeRunId.value = runId
  } catch {
    // A session without a CodeAgent Run should keep the empty-state panel.
  }
}

async function loadHistory({ preserveHydrationFrom = null } = {}) {
  const preserveKey = preserveHydrationFrom ? String(preserveHydrationFrom) : ''
  const preserved = preserveKey ? hydratedSteps.value[preserveKey] : null
  const res = await getCgi('/pages/page_agent_chat.cgi', {
    action: 'get_history',
    agent_id: agentId,
    session_id: sessionId.value,
  })
  messages.value = res.data || []
  const next = {}
  if (preserved && (preserved.steps?.length || preserved.step_count > 0)) {
    const list = messages.value
    for (let i = list.length - 1; i >= 0; i--) {
      const m = list[i]
      if (m?.role !== 'assistant' || !m.id || String(m.id).startsWith('tmp-')) continue
      const ek = String(m.id)
      next[ek] = {
        steps: preserved.steps || [],
        older: preserved.older || 0,
        step_count: Math.max(
          Number(preserved.step_count) || 0,
          (preserved.steps || []).length,
          messageStepCount(m),
        ),
        loading: false,
      }
      break
    }
  }
  hydratedSteps.value = next
  scrollBottom()
  if (agent.value?.profile === 'code') {
    hydrateLatestCodeHistory().catch(() => {})
  }
}

function stepsFromCodeEvents(events) {
  const steps = []
  const openByAction = {}
  const seen = new Set()
  for (const event of events || []) {
    const profile = event?.profile && event.profile.profile ? event.profile : event
    const key = profile.sequence != null
      ? `seq:${profile.sequence}`
      : `${profile.phase || ''}:${profile.status || ''}:${profile.summary || profile.reason || ''}`
    if (seen.has(key)) continue
    seen.add(key)
    const step = profileEventToStep({ profile })
    if (!step) continue
    const openIndex = openByAction[step.action]
    if (openIndex != null && steps[openIndex]?.status !== 'done' && steps[openIndex]?.status !== 'error') {
      steps[openIndex] = { ...steps[openIndex], ...step }
    } else {
      openByAction[step.action] = steps.length
      steps.push(step)
    }
  }
  return collapseLlmSteps(steps.filter((s) => !isHiddenStep(s)))
}

async function fetchCodeEventSteps() {
  if (agent.value?.profile !== 'code' || !activeCodeRunId.value) return []
  const events = []
  let since = 0
  for (let page = 0; page < 5; page += 1) {
    const res = await getCgi('/pages/page_agent_chat.cgi', {
      action: 'get_code_events',
      agent_id: agentId,
      code_run_id: activeCodeRunId.value,
      since,
      limit: 200,
    })
    const batch = res.data?.events || []
    events.push(...batch)
    if (!res.data?.has_more || !batch.length) break
    since = Number(res.data.next_since) || events.length
  }
  return stepsFromCodeEvents(events)
}

async function hydrateLatestCodeHistory() {
  await nextTick()
  const latest = [...displayMessages.value].reverse().find(
    (item) => item.m?.role === 'assistant' && item.stepCount > 0,
  )
  if (!latest) return
  execOpen.value = { ...execOpen.value, [latest.execKey]: true }
  if (!activeCodeRunId.value) await hydrateActiveCodeRun()
  try {
    const eventSteps = await fetchCodeEventSteps()
    if (eventSteps.length) {
      hydratedSteps.value = {
        ...hydratedSteps.value,
        [latest.execKey]: {
          steps: eventSteps,
          older: 0,
          step_count: Math.max(eventSteps.length, latest.stepCount, 1),
          loading: false,
        },
      }
      return
    }
  } catch {
    // Fall back to persisted message steps when the event endpoint is unavailable.
  }
  await ensureHistorySteps(latest.execKey)
}

function applyDoneMessage(content, { truncated = false, stepCount = 0 } = {}) {
  const count = Math.max(
    Number(stepCount) || 0,
    liveStepCountForMeta(),
    1, // always keep exec-card for assistant replies
  )
  // Truncated WS preview must not become the lasting bubble — loadHistory owns the full text.
  if (truncated) {
    const list = messages.value.slice()
    const last = list[list.length - 1]
    const tmpId = (last && last.role === 'assistant' && String(last.id || '').startsWith('tmp-'))
      ? last.id
      : `tmp-${Date.now()}`
    const placeholder = {
      role: 'assistant',
      content: '（回复已生成，正在加载全文…）',
      id: tmpId,
      created_at: '',
      meta: {
        ...(last?.role === 'assistant' ? (last.meta || {}) : {}),
        step_count: Math.max(count, messageStepCount(last), 1),
        saved_paths: last?.meta?.saved_paths || [],
      },
    }
    if (last && last.role === 'assistant' && String(last.id || '').startsWith('tmp-')) {
      list[list.length - 1] = { ...last, ...placeholder, id: last.id }
    } else {
      list.push(placeholder)
    }
    messages.value = list
    return String(list[list.length - 1]?.id || tmpId)
  }
  const text = (content || '').trim() || EMPTY_ASSISTANT_FALLBACK
  const list = messages.value.slice()
  const last = list[list.length - 1]
  if (last && last.role === 'assistant' && String(last.id || '').startsWith('tmp-')) {
    list[list.length - 1] = {
      ...last,
      content: text,
      meta: {
        ...(last.meta || {}),
        step_count: Math.max(count, messageStepCount(last), 1),
      },
    }
  } else {
    list.push({
      role: 'assistant',
      content: text,
      id: `tmp-${Date.now()}`,
      created_at: '',
      meta: { step_count: count, saved_paths: [] },
    })
  }
  messages.value = list
  return String(list[list.length - 1]?.id || '')
}

async function finishRunFromDone(content, { truncated = false } = {}) {
  const snapshot = visibleLiveStepSnapshot()
  const stepCount = Math.max(snapshot.length, liveStepCountForMeta(), 1)
  const tmpKey = applyDoneMessage(content, { truncated, stepCount })
  if (tmpKey) {
    const steps = snapshot.length ? snapshot : [NO_TOOL_PLACEHOLDER_STEP]
    hydratedSteps.value = {
      ...hydratedSteps.value,
      [tmpKey]: {
        steps,
        older: 0,
        step_count: Math.max(stepCount, steps.length, 1),
        loading: false,
      },
    }
  }
  markRunFinished()
  await loadHistory({ preserveHydrationFrom: tmpKey })
}

async function checkStatus({ forIdlePoll = false } = {}) {
  const prev = running.value
  const res = await getCheckStatus(
    '/pages/page_agent_chat.cgi',
    {
      action: 'check_status',
      agent_id: agentId,
      session_id: sessionId.value,
    },
    statusEtag,
  )
  if (res?.etag) statusEtag = res.etag
  if (res?.data?.notModified) {
    if (forIdlePoll) bumpStatusBackoff(false)
    return running.value
  }
  const next = !!res.data?.running
  if (userRequestedStop && next) {
    // Backend may still be winding down; keep UI stopped until done / idle.
    if (forIdlePoll) bumpStatusBackoff(false)
    return false
  }
  if (!next) userRequestedStop = false
  running.value = next
  if (prev && !next) {
    reloadWorkspacePanels()
  }
  if (forIdlePoll) bumpStatusBackoff(next !== prev)
  return next
}

function bumpStatusBackoff(changed) {
  if (changed) {
    statusBackoffMs = STATUS_BACKOFF_STEPS[0]
    return
  }
  const idx = STATUS_BACKOFF_STEPS.indexOf(statusBackoffMs)
  const next = STATUS_BACKOFF_STEPS[Math.min(idx + 1, STATUS_BACKOFF_STEPS.length - 1)]
  statusBackoffMs = next > 0 ? next : STATUS_BACKOFF_STEPS[0]
}

function clearIdleStatusPoll() {
  if (statusTimer) {
    clearTimeout(statusTimer)
    statusTimer = null
  }
}

function scheduleIdleStatusPoll() {
  clearIdleStatusPoll()
  statusTimer = setTimeout(async () => {
    statusTimer = null
    if (document.hidden) return
    try {
      await checkStatus({ forIdlePoll: true })
    } catch {
      /* keep polling */
    }
    scheduleIdleStatusPoll()
  }, statusBackoffMs)
}

function resetIdleStatusPoll() {
  statusBackoffMs = STATUS_BACKOFF_STEPS[0]
  scheduleIdleStatusPoll()
}

function clearResumePoll() {
  if (resumePollTimer) {
    clearInterval(resumePollTimer)
    resumePollTimer = null
  }
}

function markRunFinished() {
  streaming.value = false
  liveSteps.value = []
  liveStepsBaseIndex = 0
  sending.value = false
  running.value = false
  resetLiveElapsed()
  clearResumePoll()
}

function applyStepEvent(data) {
  if (userRequestedStop) return
  streaming.value = true
  sending.value = true
  running.value = true
  const op = data.op
  const steps = liveSteps.value
  let shouldScroll = false

  if (op === 'append' && data.step) {
    const absIdx = typeof data.index === 'number' ? data.index : liveStepsBaseIndex + steps.length
    const localIdx = absIdx - liveStepsBaseIndex
    if (localIdx < 0) {
      // Already trimmed away — ignore
    } else if (localIdx === steps.length) {
      steps.push(data.step)
      shouldScroll = true
    } else if (localIdx < steps.length) {
      steps[localIdx] = data.step
      shouldScroll = true
    } else {
      // Gap after trim: do not fill with placeholder "…" steps
      steps.push(data.step)
      shouldScroll = true
    }
    trimLiveStepsArray()
    scheduleLiveStepsFlush()
  } else if (op === 'patch' && data.step) {
    const absIdx = typeof data.index === 'number'
      ? data.index
      : liveStepsBaseIndex + Math.max(0, steps.length - 1)
    const localIdx = absIdx - liveStepsBaseIndex
    if (localIdx >= 0 && localIdx < steps.length) {
      Object.assign(steps[localIdx], data.step)
      scheduleLiveStepsFlush()
    } else if (localIdx >= steps.length && data.step) {
      steps.push(data.step)
      trimLiveStepsArray()
      scheduleLiveStepsFlush()
      shouldScroll = true
    }
    // localIdx < 0: patch for trimmed step — ignore; no scroll on patch
  } else if (Array.isArray(data.steps)) {
    liveStepsBaseIndex = 0
    liveSteps.value = data.steps
    shouldScroll = true
    scheduleLiveStepsFlush()
  } else if (data.step) {
    steps.push(data.step)
    trimLiveStepsArray()
    shouldScroll = true
    scheduleLiveStepsFlush()
  }

  if (shouldScroll) {
    pendingScrollAfterSteps = true
    scheduleLiveStepsFlush()
  }
}

function profileEventToStep(data) {
  const profile = data?.profile || {}
  if (profile.profile !== 'code') return null
  const phaseLabels = {
    prepare: '准备运行环境',
    verify_baseline: '捕获验证基线',
    runtime_started: '启动 Claude Code Runtime',
    runtime_result: 'Claude Code Runtime 结果',
    skill_loaded: '加载 Claude Code Skill',
    mcp_loaded: '加载 Claude Code MCP',
    tool_call: 'Claude Code 工具调用',
    file_changed: 'Claude Code 文件变化',
    test_run: 'Claude Code 测试执行',
    verifier_failed_retrying: 'Verifier 失败，继续 Claude Code 修复',
    verifier_passed: 'Verifier 已通过',
    artifact_sealed: '结果工件已生成',
    verify: '执行验证',
    seal: '整理任务结果',
    cleanup: '清理 Sandbox',
    publish: '执行运行步骤',
    terminate: '结束 Code Run',
  }
  const rawStatus = String(profile.status || 'running').toLowerCase()
  // `runtime_started` is an acknowledgement that startup succeeded, not a
  // long-running operation. Other terminal aliases are accepted so a runtime
  // event cannot leave a completed step spinning indefinitely.
  const terminalStatuses = new Set(['completed', 'done', 'passed', 'success'])
  const status = rawStatus === 'failed' || rawStatus === 'error'
    ? 'error'
    : (profile.phase === 'runtime_started' && rawStatus === 'started')
      || (profile.phase === 'prepare' && rawStatus === 'started')
      || terminalStatuses.has(rawStatus)
      ? 'done'
      : 'running'
  const snippet = sanitizeStepSnippet(profile.snippet || profile.output || '')
  return {
    type: 'info',
    action: `code_${profile.phase || 'profile'}`,
    title: `${phaseLabels[profile.phase] || 'CodeAgent 运行阶段'} · ${profile.status || 'running'}`,
    status,
    content: profile.reason || profile.summary || profile.skill_name || profile.mcp_name
      || profile.command || profile.path || profile.artifact_id || profile.release_id
      || (profile.exit_code != null ? `exit_code=${profile.exit_code}` : ''),
    snippet,
  }
}

function findOpenCodeStepIndex(action) {
  const steps = liveSteps.value
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    const step = steps[index]
    if (step?.action === action && step.status !== 'done' && step.status !== 'error') {
      return liveStepsBaseIndex + index
    }
  }
  return null
}

function applyCodeRuntimeEvent(event) {
  const profile = event?.profile || event || {}
  const key = profile.sequence != null
    ? `${activeCodeRunId.value}:${profile.sequence}`
    : `${activeCodeRunId.value}:${profile.phase || ''}:${profile.status || ''}:${profile.summary || profile.reason || ''}`
  if (seenCodeEventKeys.has(key)) return
  seenCodeEventKeys.add(key)
  const step = profileEventToStep({ profile })
  if (!step) return
  const openIndex = findOpenCodeStepIndex(step.action)
  applyStepEvent({
    type: 'step',
    op: openIndex == null ? 'append' : 'patch',
    ...(openIndex == null ? {} : { index: openIndex }),
    step,
  })
}

async function loadCodeEvents() {
  if (agent.value?.profile !== 'code' || !activeCodeRunId.value) return
  const res = await getCgi('/pages/page_agent_chat.cgi', {
    action: 'get_code_events', agent_id: agentId, code_run_id: activeCodeRunId.value, since: 0, limit: 200,
  })
  for (const event of (res.data?.events || [])) applyCodeRuntimeEvent(event)
}

function startResumePoll() {
  if (resumePollTimer) return
  resumePollTimer = setInterval(async () => {
    try {
      // WS already streams steps — only poll history as disconnect fallback
      if (ws && ws.readyState === WebSocket.OPEN) {
        const still = await checkStatus()
        // The run is queued asynchronously and startup events can precede the
        // socket subscription. Replay persisted events while it is active so
        // those early phases are not lost from the conversation view.
        await loadCodeEvents()
        if (!still) {
          markRunFinished()
          await loadHistory()
          reloadWorkspacePanels()
        }
        return
      }
      const still = await checkStatus()
      await loadHistory()
      if (!still) {
        markRunFinished()
        reloadWorkspacePanels()
      }
    } catch {
      /* keep polling while page alive */
    }
  }, 1500)
}

function scheduleLiveStepsFlush() {
  if (liveStepsFlushRaf) return
  liveStepsFlushRaf = requestAnimationFrame(() => {
    liveStepsFlushRaf = 0
    triggerRef(liveSteps)
    if (pendingScrollAfterSteps) {
      pendingScrollAfterSteps = false
      scrollBottom()
    }
  })
}

function trimLiveStepsArray() {
  // No keep-cap: retain full execution history for MCP/LLM rounds.
}

async function resumeIfRunning() {
  const isRun = await checkStatus()
  if (isRun) {
    streaming.value = true
    sending.value = true
    liveStatusText.value = '任务仍在运行，正在恢复进度'
    startLiveElapsed()
    execOpen.value = { ...execOpen.value, live: true }
    startResumePoll()
    return
  }
  // 刷新后空闲：拉齐历史；勿清掉刚提交的本地 sending 态
  if (!sending.value) {
    clearResumePoll()
    streaming.value = false
    liveSteps.value = []
    liveStepsBaseIndex = 0
    running.value = false
  }
  await loadHistory()
}

function scheduleWsReconnect() {
  if (!pageAlive || wsReconnectTimer) return
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null
    if (!pageAlive) return
    connectWs()
  }, wsRetryMs)
  wsRetryMs = Math.min(wsRetryMs * 2, 10000)
}

function clearWsTimers() {
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer)
    wsReconnectTimer = null
  }
  if (wsPingTimer) {
    clearInterval(wsPingTimer)
    wsPingTimer = null
  }
}

function connectWs() {
  if (!pageAlive || !sessionId.value) return
  clearWsTimers()
  const prev = ws
  ws = null
  intentionalWsClose = true
  try {
    prev?.close()
  } catch { /* ignore */ }
  intentionalWsClose = false

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const socket = new WebSocket(`${proto}//${location.host}/pages/page_agent_chat.ws`)
  ws = socket

  socket.onopen = () => {
    wsRetryMs = 1000
    socket.send(JSON.stringify({ agent_id: agentId, session_id: sessionId.value }))
    loadCodeEvents().catch(() => {})
    if (wsPingTimer) clearInterval(wsPingTimer)
    wsPingTimer = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send('ping')
    }, 25000)
  }

  socket.onmessage = (ev) => {
    let data
    try {
      data = JSON.parse(ev.data)
    } catch {
      return
    }
    if (data.type === 'pong' || data.type === 'connected') return
    if (data.type === 'status') {
      if (userRequestedStop) return
      const text = String(data.content || '').trim()
      if (text) liveStatusText.value = text.replace(/\.{3}$/, '')
      startLiveElapsed()
    }
    if (data.type === 'user_message') {
      if (data.session_id === sessionId.value) appendIncomingUserMessage(data)
      return
    }
    if (data.type === 'inbound') {
      if (data.session_id !== sessionId.value) {
        unreadSessions.value = {
          ...unreadSessions.value,
          [data.session_id]: (unreadSessions.value[data.session_id] || 0) + 1,
        }
        inboundNotice.value = {
          session_id: data.session_id,
          sender: data.sender,
          content: data.content,
        }
      }
      return
    }
    if (data.type === 'step') {
      if (userRequestedStop) return
      startLiveElapsed()
      applyStepEvent(data)
    }
    if (data.type === 'profile') {
      if (userRequestedStop) return
      startLiveElapsed()
      applyCodeRuntimeEvent(data)
    }
    if (data.type === 'done') {
      userRequestedStop = false
      const truncated = !!data.content_truncated
        || (typeof data.content === 'string' && data.content.length > DONE_CONTENT_PREVIEW_MAX)
      // Snapshot live steps before clear; migrate hydration onto real message id
      finishRunFromDone(data.content, { truncated }).then(() => {
        checkStatus()
        reloadWorkspacePanels()
      })
    }
  }

  socket.onerror = () => {
    /* onclose will schedule reconnect */
  }

  socket.onclose = () => {
    if (wsPingTimer) {
      clearInterval(wsPingTimer)
      wsPingTimer = null
    }
    // Only reconnect when the active socket drops unexpectedly
    if (ws !== socket) return
    ws = null
    if (!intentionalWsClose && pageAlive) scheduleWsReconnect()
  }
}

async function send() {
  if (!input.value.trim()) return
  if (agent.value?.profile === 'code') {
    await loadAgent()
    if (!agent.value?.code_project_availability?.ready) {
      ElMessage.error(`${projectStatusLabel(agent.value?.code_project_availability)}，请先管理 Code Project`)
      return
    }
  }
  const text = input.value.trim()
  messages.value = [...messages.value, { role: 'user', content: text }]
  input.value = ''
  userRequestedStop = false
  sending.value = true
  streaming.value = true
  resetLiveElapsed()
  startLiveElapsed()
  liveSteps.value = []
  liveStepsBaseIndex = 0
  execOpen.value = { ...execOpen.value, live: true }
  running.value = true
  userPinnedBottom = true
  scrollBottom()
  const submitted = await postCgi('/pages/page_agent_chat.cgi?action=submit_chat', {
    agent_id: agentId,
    session_id: sessionId.value,
    message: text,
    workplace_dir: wpRef.value?.getSelectedFolder?.() || '',
    workplace_files: wpRef.value?.getSelectedFiles?.() || [],
  })
  const nextCodeRunId = String(submitted.data?.code_run_id || '').trim()
  // A grill/clarification response intentionally has no new Run id. Keep
  // showing the last run's Workspace instead of replacing it with an empty
  // "等待 CodeAgent Run" panel. A real new Run always replaces this id.
  if (nextCodeRunId) {
    activeCodeRunId.value = nextCodeRunId
  } else {
    await hydrateActiveCodeRun()
  }
  // The run is enqueued asynchronously; hydrate any startup/profile events
  // emitted before the WebSocket observed the new Run id.
  loadCodeEvents().catch(() => {})
  startResumePoll()
}

async function stop() {
  userRequestedStop = true
  await postCgi('/pages/page_agent_chat.cgi?action=stop_chat', {
    agent_id: agentId,
    session_id: sessionId.value,
  })
  markRunFinished()
}

async function clearHistory() {
  await ElMessageBox.confirm('确定清空当前会话的所有对话？', '清空对话', { type: 'warning' })
  await postCgi('/pages/page_agent_chat.cgi?action=clear_history', {
    agent_id: agentId,
    session_id: sessionId.value,
  })
  messages.value = []
  hydratedSteps.value = {}
  mdCache.clear()
}

async function deleteMessage(m) {
  await ElMessageBox.confirm('确定删除这条消息？', '删除', { type: 'warning' })
  await postCgi('/pages/page_agent_chat.cgi?action=delete_messages', {
    agent_id: agentId,
    session_id: sessionId.value,
    message_ids: [m.id],
  })
  await loadHistory()
}

function copyText(text) {
  navigator.clipboard.writeText(text || '')
  ElMessage.success('已复制')
}

async function onChatUpload({ file }) {
  const curPath = wpRef.value?.currentPath?.value ?? ''
  const fd = new FormData()
  fd.append('file', file)
  fd.append('agent_id', agentId)
  fd.append('path', curPath)
  const res = await api.post('/pages/page_agent_chat.cgi/upload', fd)
  const rel = res.data?.path || file.name
  const full = `/workplace/${rel}`.replace(/\/+/g, '/')
  const ref = `[文件] ${full}`
  input.value = input.value ? `${input.value}\n${ref}` : ref
  wpRef.value?.load?.()
}

function onVoiceText(text) {
  const t = (text || '').trim()
  if (!t) return
  input.value = input.value ? `${input.value}\n${t}` : t
}

function onChatScroll() {
  const el = scrollRef.value
  if (!el) return
  userPinnedBottom = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_NEAR_BOTTOM_PX
}

function scrollBottom() {
  if (!userPinnedBottom) return
  if (scrollThrottleTimer) return
  scrollThrottleTimer = setTimeout(() => {
    scrollThrottleTimer = 0
    if (!userPinnedBottom) return
    if (scrollRaf) return
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0
      nextTick(() => {
        const el = scrollRef.value
        if (el && userPinnedBottom) el.scrollTop = el.scrollHeight
      })
    })
  }, SCROLL_THROTTLE_MS)
}

function appendIncomingUserMessage(data) {
  const content = (data.content || '').trim()
  if (!content) return
  const list = messages.value || []
  const last = list[list.length - 1]
  if (last && last.role === 'user' && (last.content || '').trim() === content) return
  messages.value = [...list, { role: 'user', content, id: `tmp-${Date.now()}`, created_at: '', meta: {} }]
  userPinnedBottom = true
  scrollBottom()
}

function clearInboundNotice(sid) {
  if (!sid) return
  const next = { ...unreadSessions.value }
  delete next[sid]
  unreadSessions.value = next
  if (inboundNotice.value?.session_id === sid) inboundNotice.value = null
}

function switchToInboundSession() {
  const sid = inboundNotice.value?.session_id
  if (!sid) return
  clearInboundNotice(sid)
  router.push({ query: { ...route.query, session: sid } })
}

watch(() => route.query.session, async (sid) => {
  if (sid && sid !== sessionId.value) {
    sessionId.value = sid
    activeCodeRunId.value = ''
    clearInboundNotice(sid)
    clearResumePoll()
    await loadHistory()
    await hydrateActiveCodeRun()
    connectWs()
    await resumeIfRunning()
  }
})

async function onVisibilityChange() {
  if (document.hidden) {
    clearIdleStatusPoll()
    return
  }
  resetIdleStatusPoll()
  connectWs()
  await resumeIfRunning()
}

onMounted(async () => {
  pageAlive = true
  await loadAgent()
  await loadHistory()
  await hydrateActiveCodeRun()
  connectWs()
  await resumeIfRunning()
  resetIdleStatusPoll()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  pageAlive = false
  intentionalWsClose = true
  clearResumePoll()
  clearWsTimers()
  resetLiveElapsed()
  if (liveStepsFlushRaf) {
    cancelAnimationFrame(liveStepsFlushRaf)
    liveStepsFlushRaf = 0
  }
  if (scrollThrottleTimer) {
    clearTimeout(scrollThrottleTimer)
    scrollThrottleTimer = 0
  }
  if (scrollRaf) {
    cancelAnimationFrame(scrollRaf)
    scrollRaf = 0
  }
  try {
    ws?.close()
  } catch { /* ignore */ }
  ws = null
  clearIdleStatusPoll()
  document.removeEventListener('visibilitychange', onVisibilityChange)
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
.session-title { font-size: 18px; font-weight: 600; color: var(--gap-chat-top-text); }
.session-sub { font-size: 12px; color: var(--gap-text-muted); margin-top: 2px; }
.im-banner {
  margin-top: 6px; font-size: 12px; color: #067a3a;
  background: #f0f9eb; border-radius: 4px; padding: 4px 8px; display: inline-block;
}
.inbound-banner {
  margin-top: 6px; font-size: 12px; color: #b8860b;
  background: #fff7e6; border-radius: 4px; padding: 4px 8px; display: inline-flex;
  align-items: center; gap: 6px;
}
.top-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.top-actions .el-button {
  color: var(--gap-chat-top-text);
  border-color: var(--gap-card-border);
  background: var(--gap-hover-bg);
}
.main-body { display: flex; flex: 1; min-height: 0; }
.sidebar { width: 340px; flex-shrink: 0; height: 100%; overflow: hidden; }
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--gap-chat-body-bg); }
.code-agent-context { margin: 12px 16px 0; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.chat-body { flex: 1; overflow: auto; padding: 12px 16px; background: var(--gap-chat-body-bg); }
.empty-chat { text-align: center; color: var(--gap-text-muted); padding-top: 80px; }
.empty-chat p { margin-top: 12px; }

.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  align-items: flex-start;
}
.user-row { flex-direction: row-reverse; }
.agent-avatar, .user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 14px;
}
.agent-avatar {
  background: linear-gradient(135deg, #409eff, #337ecc);
  color: #fff;
  font-weight: 700;
}
.user-avatar {
  background: var(--gap-hover-bg);
  color: var(--gap-text-muted);
  border: 1px solid var(--gap-card-border);
}
.msg-col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.assistant-row .msg-col {
  flex: 1;
  max-width: calc(100% - 42px);
  width: 100%;
}
.user-row .msg-col {
  max-width: min(560px, 78%);
  align-items: flex-end;
}

.exec-card {
  background: var(--gap-chat-bubble-bg);
  border: 1px solid var(--gap-card-border);
  border-radius: 10px;
  overflow: hidden;
  width: 100%;
}
.exec-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  background: var(--gap-hover-bg);
}
.exec-arrow {
  transition: transform 0.2s;
  color: var(--gap-text-muted);
  flex-shrink: 0;
}
.exec-arrow.open { transform: rotate(90deg); }
.exec-title { font-weight: 600; color: var(--gap-text); }
.exec-progress-ring {
  flex: 0 0 auto;
  margin-left: auto;
}
.exec-progress-ring :deep(.el-progress__text) {
  min-width: 0;
  font-size: 10px !important;
  color: var(--gap-text);
}
.exec-live-context {
  min-width: 0;
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--gap-text-muted);
  font-size: 12px;
}
.exec-live-context .exec-progress-ring { margin-left: 0; }
.exec-live-context span {
  max-width: min(420px, 42vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.exec-badge {
  margin-left: auto;
  padding: 2px 10px;
  border-radius: 6px;
  background: var(--gap-card-border);
  color: var(--gap-text-muted);
  font-size: 12px;
  line-height: 1.6;
  white-space: nowrap;
}
.exec-progress-ring + .exec-badge,
.exec-live-context + .exec-badge { margin-left: 0; }
.exec-steps {
  padding: 0 14px;
  max-height: 280px;
  overflow-y: auto;
}
.exec-summary {
  padding: 10px 0 6px;
  border-bottom: 1px solid var(--gap-card-border);
}
.exec-summary-line {
  font-size: 12px;
  line-height: 1.55;
  color: var(--gap-text-muted);
  word-break: break-word;
}
.exec-summary-line + .exec-summary-line {
  margin-top: 4px;
}
.exec-summary-files {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.exec-file-link {
  appearance: none;
  border: 0;
  background: var(--gap-hover-bg);
  color: var(--gap-primary);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
}
.exec-file-link:hover {
  background: var(--gap-card-border);
}
.exec-older {
  padding: 8px 0 4px;
  font-size: 12px;
  color: var(--gap-text-muted);
}
.exec-step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--gap-card-border);
}
.exec-step.is-expandable { cursor: pointer; }
.exec-step.is-expandable:hover { background: var(--gap-hover-bg); }
.exec-step.is-expandable:focus-visible {
  outline: 2px solid var(--gap-primary);
  outline-offset: -2px;
}
.exec-step:last-child { border-bottom: none; }
.step-glyph {
  flex-shrink: 0;
  width: 1.4em;
  text-align: center;
  font-size: 14px;
  line-height: 1;
}
.step-body {
  flex: 1;
  min-width: 0;
}
.step-status { flex-shrink: 0; font-size: 16px; }
.step-status.done { color: #67c23a; }
.step-status.error { color: #f56c6c; }
.step-status.running { color: #409eff; }
.step-detail-arrow {
  flex-shrink: 0;
  color: var(--gap-text-muted);
  font-size: 13px;
  transition: transform 0.2s;
}
.step-detail-arrow.open { transform: rotate(90deg); }
.step-title {
  font-size: 13px;
  color: var(--gap-text);
  display: flex;
  min-width: 0;
  align-items: baseline;
  line-height: 1.45;
}
.step-inline-detail {
  min-width: 0;
  overflow: hidden;
  color: var(--gap-text-muted);
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.step-title > span:first-child { flex-shrink: 0; }
.step-content {
  font-size: 12px;
  color: var(--gap-text-muted);
  margin-top: 4px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}
.step-snippet {
  box-sizing: border-box;
  margin: 6px 0 0;
  max-height: min(420px, 55vh);
  overflow: auto;
  padding: 8px 10px;
  border: 1px solid var(--gap-card-border);
  border-radius: 6px;
  background: var(--gap-hover-bg);
  color: var(--gap-text);
  font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.step-checkpoints {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.step-checkpoints button {
  appearance: none;
  border: 1px solid var(--gap-card-border);
  border-radius: 5px;
  background: var(--gap-hover-bg);
  color: var(--gap-primary);
  cursor: pointer;
  font-size: 12px;
  line-height: 1.4;
  padding: 3px 7px;
}

.msg-bubble {
  border-radius: 10px;
  overflow: hidden;
  width: 100%;
}
.assistant-row .msg-bubble {
  max-width: 100%;
}
.user-row .msg-bubble {
  width: auto;
  max-width: 100%;
}
.msg-bubble .src-tag {
  font-size: 11px;
  padding: 6px 12px 0;
  color: #67c23a;
  font-weight: 500;
}
.msg-bubble.user .src-tag { color: rgba(255, 255, 255, 0.9); }
.msg-bubble.user {
  background: #409eff;
  color: #fff;
}
.msg-bubble.assistant {
  background: var(--gap-chat-bubble-bg);
  border: 1px solid var(--gap-card-border);
  color: var(--gap-text);
}
.content {
  white-space: pre-wrap;
  padding: 10px 12px;
  margin: 0;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.55;
  word-break: break-word;
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
.content.md-render.is-empty-fallback {
  color: var(--gap-text-muted);
  font-style: italic;
}
.content.md-render details {
  margin: 10px 0 0;
  padding: 8px 10px;
  border: 1px solid var(--gap-card-border);
  border-radius: 6px;
  background: var(--gap-hover-bg);
}
.content.md-render summary {
  cursor: pointer;
  color: var(--gap-text);
  font-weight: 600;
}

.msg-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--gap-text-muted);
}

.chat-input {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 16px 20px;
  border-top: 1px solid var(--gap-card-border);
  background: var(--gap-chat-top-bg);
}
.chat-input .el-textarea { flex: 1; }
.chat-input :deep(.el-textarea__inner) {
  background: var(--gap-chat-input-bg);
  color: var(--gap-text);
  box-shadow: 0 0 0 1px var(--gap-card-border) inset;
}
.chat-input :deep(.el-textarea__inner:focus) {
  box-shadow: 0 0 0 1px var(--gap-primary) inset;
}
</style>
