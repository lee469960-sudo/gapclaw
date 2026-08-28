<template>
  <div class="xterm-wrap" @click="focusTerminal">
    <div class="xterm-toolbar" @click.stop>
      <div class="toolbar-left">
        <span v-if="title || variant === 'ssh'" class="label">{{ title || 'SSH 终端' }}</span>
        <template v-if="variant === 'sandbox'">
          <span class="label">Shell</span>
          <el-select v-model="shell" size="small" style="width:100px" @change="reconnect">
            <el-option label="bash" value="/bin/bash" />
            <el-option label="sh" value="/bin/sh" />
          </el-select>
        </template>
        <el-upload :show-file-list="false" :http-request="onUpload" :disabled="!resourceId">
          <el-button size="small" :loading="uploading" :disabled="!resourceId">上传</el-button>
        </el-upload>
        <el-button size="small" :loading="downloading" :disabled="!resourceId" @click="onDownload">
          下载
        </el-button>
        <el-button size="small" @click="reconnect">重连</el-button>
      </div>
      <span class="status" :class="{ ok: connected, err: errored }">{{ statusText }}</span>
    </div>
    <div ref="termRef" class="xterm-panel"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, computed, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import api from '../api'

const props = defineProps({
  wsUrl: { type: String, required: true },
  initPayload: { type: Object, default: () => ({}) },
  variant: { type: String, default: 'sandbox' }, // sandbox | ssh
  title: { type: String, default: '' },
})

const termRef = ref(null)
const shell = ref('/bin/bash')
const connected = ref(false)
const errored = ref(false)
const statusMsg = ref('等待连接...')
const uploading = ref(false)
const downloading = ref(false)
let term, fitAddon, ws, dataDisposable, resizeDisposable
let started = false

const statusText = computed(() => statusMsg.value)
const resourceId = computed(() => props.initPayload?.id || '')
const sandboxId = computed(() => (props.variant === 'sandbox' ? resourceId.value : ''))

function triggerBlobDownload(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

async function blobLooksLikeJsonError(blob) {
  const ctype = String(blob?.type || '')
  if (ctype.includes('json') || ctype.includes('text/plain')) {
    try {
      const parsed = JSON.parse(await blob.text())
      if (parsed && typeof parsed.code === 'number' && parsed.code !== 0) {
        ElMessage.error(parsed.msg || '操作失败')
        return true
      }
    } catch {
      /* not JSON */
    }
  }
  return false
}

async function onUpload({ file }) {
  if (!resourceId.value) return
  if (props.variant === 'ssh') {
    let dir = '.'
    try {
      const { value } = await ElMessageBox.prompt(
        '保存到远程目录，例如 /home/ubuntu 或 . （用户主目录）',
        '上传文件',
        {
          confirmButtonText: '上传',
          cancelButtonText: '取消',
          inputValue: '.',
          inputPlaceholder: '远程目录',
        },
      )
      dir = (value || '.').trim() || '.'
    } catch {
      return
    }
    uploading.value = true
    try {
      const fd = new FormData()
      fd.append('action', 'sftp_upload')
      fd.append('id', resourceId.value)
      fd.append('path', dir)
      fd.append('file', file)
      const res = await api.post('/pages/page_terminal.cgi', fd)
      ElMessage.success(res.msg || `已上传 ${file.name}`)
    } finally {
      uploading.value = false
    }
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('action', 'upload_workplace')
    fd.append('id', sandboxId.value)
    fd.append('path', '')
    fd.append('file', file)
    await api.post('/pages/page_sandbox.cgi', fd)
    ElMessage.success(`已上传到 /workplace/${file.name}`)
  } finally {
    uploading.value = false
  }
}

async function onDownload() {
  if (!resourceId.value) return
  if (props.variant === 'ssh') {
    let path
    try {
      const { value } = await ElMessageBox.prompt(
        '输入远程文件路径，例如 /home/ubuntu/report.md 或 ~/a.txt',
        '下载文件',
        {
          confirmButtonText: '下载',
          cancelButtonText: '取消',
          inputPlaceholder: '远程路径',
          inputValidator: (v) => (!!(v || '').trim() ? true : '请输入路径'),
        },
      )
      path = (value || '').trim()
    } catch {
      return
    }
    downloading.value = true
    try {
      const res = await api.get('/pages/page_terminal.cgi', {
        params: { action: 'sftp_download', id: resourceId.value, path },
        responseType: 'blob',
      })
      if (await blobLooksLikeJsonError(res.data)) return
      const name = path.split('/').filter(Boolean).pop() || 'download'
      triggerBlobDownload(res.data, name)
    } finally {
      downloading.value = false
    }
    return
  }
  let path
  try {
    const { value } = await ElMessageBox.prompt(
      '输入 /workplace 下的相对路径，例如 report.md 或 notes/a.txt',
      '下载文件',
      {
        confirmButtonText: '下载',
        cancelButtonText: '取消',
        inputPlaceholder: '相对路径',
        inputValidator: (v) => (!!(v || '').trim() ? true : '请输入路径'),
      },
    )
    path = (value || '').trim()
  } catch {
    return
  }
  downloading.value = true
  try {
    const res = await api.get('/pages/page_sandbox.cgi', {
      params: { action: 'download_workplace', id: sandboxId.value, path },
      responseType: 'blob',
    })
    if (await blobLooksLikeJsonError(res.data)) return
    const name = path.split('/').filter(Boolean).pop() || 'download'
    triggerBlobDownload(res.data, name)
  } finally {
    downloading.value = false
  }
}

function fitTerminal() {
  try {
    fitAddon?.fit()
  } catch {
    /* dialog may still be animating */
  }
}

function focusTerminal() {
  nextTick(() => {
    fitTerminal()
    term?.focus()
  })
}

function terminalSize() {
  fitTerminal()
  return {
    cols: Math.max(term?.cols || 0, 80),
    rows: Math.max(term?.rows || 0, 24),
  }
}

function sendResize() {
  if (ws?.readyState === WebSocket.OPEN && term) {
    const { cols, rows } = terminalSize()
    ws.send(JSON.stringify({ cols, rows }))
  }
}

function reconnect() {
  connect()
}

function connect() {
  if (ws) {
    ws.close()
    ws = null
  }
  connected.value = false
  errored.value = false
  statusMsg.value = '连接中...'
  term?.clear()
  fitTerminal()

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = props.wsUrl.startsWith('ws') ? props.wsUrl : `${proto}//${location.host}${props.wsUrl}`
  ws = new WebSocket(url)
  ws.binaryType = 'arraybuffer'
  ws.onopen = () => {
    const { cols, rows } = terminalSize()
    const payload = { cols, rows, ...props.initPayload }
    if (props.variant === 'sandbox') {
      payload.shell = shell.value
      payload.workdir = payload.workdir || '/workplace'
    }
    ws.send(JSON.stringify(payload))
  }
  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      try {
        const j = JSON.parse(ev.data)
        if (j.type === 'status') {
          connected.value = true
          statusMsg.value = j.content || '已连接'
          sendResize()
          focusTerminal()
          return
        }
        if (j.type === 'error') {
          errored.value = true
          statusMsg.value = j.content || '连接失败'
          term.writeln(`\r\n\x1b[31m${j.content}\x1b[0m`)
          return
        }
        if (j.type === 'output' || j.content) term.write(j.content || '')
      } catch {
        term.write(ev.data)
      }
    } else {
      connected.value = true
      term.write(new Uint8Array(ev.data))
    }
  }
  ws.onerror = () => {
    errored.value = true
    statusMsg.value = '连接错误'
  }
  ws.onclose = () => {
    if (!errored.value && connected.value) statusMsg.value = '已断开'
    connected.value = false
  }
}

/** Wait for dialog layout, then connect once. */
async function initAndConnect() {
  await nextTick()
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  fitTerminal()
  connect()
  started = true
}

onMounted(() => {
  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    theme: { background: '#1e1e1e' },
    convertEol: true,
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termRef.value)

  dataDisposable = term.onData((data) => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(new TextEncoder().encode(data))
    }
  })
  resizeDisposable = term.onResize(() => sendResize())

  window.addEventListener('resize', fitTerminal)
})

onUnmounted(() => {
  dataDisposable?.dispose()
  resizeDisposable?.dispose()
  ws?.close()
  term?.dispose()
  window.removeEventListener('resize', fitTerminal)
})

watch(
  () => props.initPayload?.id,
  () => {
    if (started) reconnect()
  },
)

defineExpose({ focus: focusTerminal, reconnect, initAndConnect })
</script>

<style scoped>
.xterm-wrap { display: flex; flex-direction: column; height: 70vh; min-height: 400px; }
.xterm-toolbar {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; background: #f5f7fa; border: 1px solid #e4e7ed; border-bottom: none;
  border-radius: 4px 4px 0 0;
}
.toolbar-left { display: flex; align-items: center; gap: 8px; }
.label { font-size: 13px; color: #606266; }
.status { font-size: 13px; color: #909399; }
.status.ok { color: #67c23a; font-weight: 500; }
.status.err { color: #f56c6c; }
.xterm-panel { flex: 1; width: 100%; min-height: 0; background: #1e1e1e; border: 1px solid #e4e7ed; border-radius: 0 0 4px 4px; }
.xterm-panel :deep(.xterm) { height: 100%; }
.xterm-panel :deep(.xterm-viewport) { overflow-y: auto; }
</style>
