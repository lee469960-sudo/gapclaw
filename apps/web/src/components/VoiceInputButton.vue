<template>
  <el-button
    circle
    :type="listening ? 'danger' : 'default'"
    :loading="busy"
    :title="buttonTitle"
    :disabled="disabled || busy"
    @click="toggle"
  >
    <el-icon v-if="!busy">
      <Microphone v-if="!listening" />
      <VideoPause v-else />
    </el-icon>
  </el-button>
</template>

<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { Microphone, VideoPause } from '@element-plus/icons-vue'
import api from '../api'

defineProps({
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['text'])

const listening = ref(false)
const busy = ref(false)
let recognition = null
let mediaRecorder = null
let mediaStream = null
const chunks = []
let interimText = ''
let finalText = ''
let mode = 'none' // speech | record

const SpeechRecognitionCtor = typeof window !== 'undefined'
  ? (window.SpeechRecognition || window.webkitSpeechRecognition)
  : null

const buttonTitle = computed(() => {
  if (busy.value) return '转写中...'
  if (listening.value) return mode === 'speech' ? '点击结束语音识别' : '点击停止并上传转写'
  return '语音输入'
})

function getRecognition() {
  if (!SpeechRecognitionCtor) return null
  const rec = new SpeechRecognitionCtor()
  rec.lang = 'zh-CN'
  rec.continuous = true
  rec.interimResults = true
  rec.maxAlternatives = 1
  return rec
}

function startSpeechRecognition() {
  if (!window.isSecureContext && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
    ElMessage.warning('麦克风需要 HTTPS 或 localhost')
  }
  recognition = getRecognition()
  if (!recognition) return false

  interimText = ''
  finalText = ''
  mode = 'speech'

  recognition.onresult = (event) => {
    let interim = ''
    let finals = ''
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const piece = event.results[i][0]?.transcript || ''
      if (event.results[i].isFinal) finals += piece
      else interim += piece
    }
    if (finals) finalText += finals
    interimText = interim
  }
  recognition.onerror = (event) => {
    const err = event.error
    if (err === 'not-allowed') ElMessage.error('麦克风权限被拒绝')
    else if (err === 'no-speech') ElMessage.warning('未检测到语音')
    else if (err !== 'aborted') ElMessage.error(`语音识别失败: ${err}`)
    listening.value = false
  }
  recognition.onend = () => {
    if (!listening.value) return
    // some browsers end early; restart while user still wants listening
    try {
      recognition.start()
    } catch {
      listening.value = false
    }
  }

  try {
    recognition.start()
    listening.value = true
    ElMessage.info('正在听，请说话…再次点击结束')
    return true
  } catch (e) {
    ElMessage.error('无法启动语音识别')
    return false
  }
}

function stopSpeechRecognition() {
  listening.value = false
  if (recognition) {
    try {
      recognition.onend = null
      recognition.stop()
    } catch { /* ignore */ }
    recognition = null
  }
  const text = `${finalText}${interimText}`.trim()
  interimText = ''
  finalText = ''
  if (!text) {
    ElMessage.warning('未识别到有效语音内容')
    return
  }
  emit('text', text)
  ElMessage.success('识别成功')
}

function pickMimeType() {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg',
  ]
  if (typeof MediaRecorder === 'undefined') return ''
  return candidates.find((t) => MediaRecorder.isTypeSupported(t)) || ''
}

async function startServerRecording() {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    ElMessage.error('当前浏览器不支持录音，请使用 Chrome/Edge')
    return
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (e) {
    ElMessage.error(e?.name === 'NotAllowedError' ? '麦克风权限被拒绝' : '无法打开麦克风')
    return
  }
  chunks.length = 0
  mode = 'record'
  const mimeType = pickMimeType()
  mediaRecorder = mimeType
    ? new MediaRecorder(mediaStream, { mimeType })
    : new MediaRecorder(mediaStream)
  mediaRecorder.ondataavailable = (ev) => {
    if (ev.data && ev.data.size > 0) chunks.push(ev.data)
  }
  mediaRecorder.onstop = () => {
    const mime = mediaRecorder?.mimeType || chunks[0]?.type || 'audio/webm'
    cleanupStream()
    finalizeRecording(mime)
  }
  mediaRecorder.start()
  listening.value = true
  ElMessage.info('正在录音…再次点击停止并上传转写')
}

function stopServerRecording() {
  if (!mediaRecorder || mediaRecorder.state === 'inactive') {
    cleanupStream()
    listening.value = false
    return
  }
  mediaRecorder.stop()
  listening.value = false
}

function cleanupStream() {
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop())
    mediaStream = null
  }
  mediaRecorder = null
}

async function finalizeRecording(mime = 'audio/webm') {
  const blob = new Blob(chunks, { type: mime })
  chunks.length = 0
  if (!blob.size) {
    ElMessage.warning('未录到有效音频')
    return
  }
  const ext = mime.includes('mp4') ? 'mp4' : mime.includes('ogg') ? 'ogg' : 'webm'
  const fd = new FormData()
  fd.append('file', blob, `recording.${ext}`)
  busy.value = true
  try {
    const res = await api.post('/pages/page_agent_chat.cgi/transcribe', fd)
    const text = (res.data?.text || '').trim()
    if (!text) {
      ElMessage.warning('未识别到有效语音内容')
      return
    }
    emit('text', text)
    ElMessage.success('转写成功')
  } catch {
    // interceptor already shows error
  } finally {
    busy.value = false
  }
}

async function start() {
  // MiniMax 无 ASR：优先浏览器语音识别（Chrome/Edge 中文）
  if (SpeechRecognitionCtor) {
    if (startSpeechRecognition()) return
  }
  await startServerRecording()
}

function stop() {
  if (mode === 'speech') stopSpeechRecognition()
  else stopServerRecording()
  mode = 'none'
}

function toggle() {
  if (busy.value) return
  if (listening.value) stop()
  else start()
}

onBeforeUnmount(() => {
  if (listening.value) stop()
  cleanupStream()
})
</script>
