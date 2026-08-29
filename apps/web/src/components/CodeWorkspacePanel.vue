<template>
  <section class="code-workspace-panel">
    <div class="workspace-panel-title">Code Workspace</div>
    <div v-if="!codeRunId" class="workspace-panel-empty">等待 CodeAgent Run</div>
    <template v-else>
      <div v-if="loading" class="workspace-panel-empty">加载 Workspace…</div>
      <div v-else-if="error === 'workspace_not_prepared'" class="workspace-panel-empty">Workspace 正在准备，等待仓库挂载…</div>
      <div v-else-if="error" class="workspace-panel-empty workspace-error">{{ workspaceErrorLabel(error) }}</div>
      <template v-else>
        <div class="workspace-panel-meta">{{ metadata.repository || '仓库' }} · {{ metadata.resolved_commit || '-' }}</div>
        <div class="workspace-panel-tree">
          <button v-for="entry in visibleEntries" :key="entry.path" type="button" :class="['workspace-entry', { 'workspace-entry-changed': isChanged(entry), 'workspace-entry-directory': entry.type === 'directory' }]" :style="{ paddingLeft: `${entry.depth * 14 + 4}px` }" @click="entry.type === 'directory' ? toggleDirectory(entry) : selectEntry(entry)">
            {{ entry.type === 'directory' ? (isExpanded(entry) ? '▾' : '▸') : '·' }} {{ entry.name || entry.path }}
            <span v-if="entry.type === 'file' && isChanged(entry)" class="workspace-entry-badge">已修改</span>
          </button>
          <div v-if="!entries.length" class="workspace-panel-empty">Workspace 文件树为空</div>
        </div>
        <pre v-if="selectedContent !== null" class="workspace-file-preview">{{ selectedContent }}</pre>
        <div v-if="git.available === false" class="workspace-panel-git workspace-git-unavailable">Git metadata unavailable（{{ git.reason || 'git_metadata_missing' }}）</div>
        <div v-else class="workspace-panel-git">Git {{ git.branch || '-' }} · {{ git.clean ? '工作区干净' : `变更 ${git.changed_files?.length || 0} 个文件` }}</div>
      </template>
    </template>
  </section>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { getCgi } from '../api'
const props = defineProps({
  agentId: { type: String, default: '' },
  codeRunId: { type: String, default: '' },
})
const metadata = ref({}); const entries = ref([]); const childrenByPath = ref({}); const expandedPaths = ref(new Set()); const git = ref({}); const selectedContent = ref(null); const loading = ref(false); const error = ref(''); const directoryLoading = ref({})
let workspaceRetryTimer = null
let workspaceLoadToken = 0
let workspaceRetryAttempts = 0
const visibleEntries = computed(() => {
  const flattened = []
  const append = (items, depth) => {
    for (const item of items || []) {
      flattened.push({ ...item, depth })
      if (item.type === 'directory' && expandedPaths.value.has(item.path)) {
        append(childrenByPath.value[item.path] || item.children || [], depth + 1)
      }
    }
  }
  append(entries.value, 0)
  return flattened
})
const changedPaths = computed(() => new Set((git.value.changed_files || []).map((item) => item.path)))
function clearWorkspaceRetry() {
  if (workspaceRetryTimer) {
    clearTimeout(workspaceRetryTimer)
    workspaceRetryTimer = null
  }
}
function scheduleWorkspaceRetry(token) {
  if (workspaceRetryAttempts >= 60) return
  workspaceRetryAttempts += 1
  clearWorkspaceRetry()
  workspaceRetryTimer = setTimeout(() => {
    workspaceRetryTimer = null
    if (token === workspaceLoadToken && props.codeRunId) load()
  }, 1000)
}
async function load() {
  clearWorkspaceRetry()
  const token = ++workspaceLoadToken
  if (!props.codeRunId) return
  loading.value = true; error.value = ''; childrenByPath.value = {}; expandedPaths.value = new Set(); selectedContent.value = null; git.value = {}
  try {
    const base = { agent_id: props.agentId, code_run_id: props.codeRunId }
    const meta = await getCgi('/pages/page_agent_chat.cgi', { action: 'get_code_workspace', ...base })
    if (!meta.data || meta.data.state !== 'ready') throw { reason: meta.data?.state || 'workspace_unsupported' }
    metadata.value = meta.data
    workspaceRetryAttempts = 0
    const tree = await getCgi('/pages/page_agent_chat.cgi', { action: 'list_code_workspace', ...base }); entries.value = tree.data?.entries || []
    const status = await getCgi('/pages/page_agent_chat.cgi', { action: 'get_code_workspace_git', ...base }); git.value = status.data || {}
  } catch (err) {
    error.value = workspaceErrorReason(err)
    // Run creation returns before the background worker clones/mounts the
    // repository. Treat this one state as transient so the panel converges to
    // ready without requiring a page refresh.
    if (error.value === 'workspace_not_prepared' && token === workspaceLoadToken) {
      scheduleWorkspaceRetry(token)
    }
  } finally { loading.value = false }
}
function isExpanded(entry) { return expandedPaths.value.has(entry.path) }
function isChanged(entry) {
  if (changedPaths.value.has(entry.path)) return true
  return entry.type === 'directory' && [...changedPaths.value].some((path) => path.startsWith(`${entry.path}/`))
}
async function toggleDirectory(entry) {
  const next = new Set(expandedPaths.value)
  if (next.has(entry.path)) {
    next.delete(entry.path)
    expandedPaths.value = next
    return
  }
  if (!childrenByPath.value[entry.path] && !directoryLoading.value[entry.path]) {
    directoryLoading.value = { ...directoryLoading.value, [entry.path]: true }
    try {
      const result = await getCgi('/pages/page_agent_chat.cgi', { action: 'list_code_workspace', agent_id: props.agentId, code_run_id: props.codeRunId, path: entry.path })
      childrenByPath.value = { ...childrenByPath.value, [entry.path]: result.data?.entries || [] }
    } finally {
      const nextLoading = { ...directoryLoading.value }
      delete nextLoading[entry.path]
      directoryLoading.value = nextLoading
    }
  }
  next.add(entry.path)
  expandedPaths.value = next
}
async function selectEntry(entry) {
  if (entry.type !== 'file') return
  const result = await getCgi('/pages/page_agent_chat.cgi', { action: 'read_code_workspace_file', agent_id: props.agentId, code_run_id: props.codeRunId, path: entry.path }); selectedContent.value = result.data?.content || ''
}
function workspaceErrorLabel(reason) {
  const labels = { workspace_not_prepared: 'Workspace 尚未准备', workspace_expired: 'Workspace 已过期或已清理', workspace_mount_invalid: 'Workspace 挂载无效', code_run_unauthorized: '无权访问该 CodeAgent Run', workspace_permission_denied: '无权访问 Workspace', workspace_unsupported: '当前服务端不支持 CodeAgent Workspace 预览，请升级服务端或查看对话结果' }
  return labels[reason] || reason || 'Workspace 不可用'
}
function workspaceErrorReason(err) {
  if (err?.response?.status === 404 || err?.code === 'workspace_unsupported') return 'workspace_unsupported'
  return err?.reason || err?.msg || err?.message || 'workspace_unavailable'
}
watch(() => props.codeRunId, () => {
  workspaceRetryAttempts = 0
  load()
}, { immediate: true }); onBeforeUnmount(clearWorkspaceRetry); defineExpose({ load })
</script>
<style scoped>
.code-workspace-panel { height: 100%; padding: 14px; overflow: auto; background: var(--el-bg-color); }
.workspace-panel-title { font-weight: 600; margin-bottom: 10px; }.workspace-panel-empty { color: var(--el-text-color-secondary); padding: 12px 0; }.workspace-error { color: var(--el-color-danger); }.workspace-panel-meta { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 8px; word-break: break-all; }.workspace-entry { display: block; width: 100%; text-align: left; border: 0; background: transparent; padding-top: 4px; padding-bottom: 4px; cursor: pointer; }.workspace-entry-changed { color: var(--el-color-warning); background: color-mix(in srgb, var(--el-color-warning) 12%, transparent); }.workspace-entry-badge { float: right; font-size: 11px; }.workspace-file-preview { white-space: pre-wrap; max-height: 360px; overflow: auto; font-size: 12px; }.workspace-panel-changes { margin-top: 8px; color: var(--el-color-warning); font-size: 12px; }
</style>
