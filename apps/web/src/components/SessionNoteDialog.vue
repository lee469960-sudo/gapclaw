<template>
  <el-dialog v-model="visible" title="备忘" width="560px" @open="loadNote">
    <el-input
      v-model="content"
      type="textarea"
      :rows="12"
      placeholder="在这里写备忘，例如：约定的术语、偏好、需要时刻提醒模型的上下文..."
    />
    <p class="hint">此备忘是本会话的备忘，将会作为 prompt，附加在每次发送给大模型的消息前。</p>
    <template #footer>
      <div class="footer-row">
        <el-button @click="viewSummary">查看总结</el-button>
        <div class="footer-right">
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="summaryVisible"
    title="会话滚动总结"
    width="620px"
    @open="loadSummary"
  >
    <div v-if="summaryEntries.length" class="rolling-list">
      <div v-for="(line, idx) in summaryEntries" :key="idx" class="rolling-item">
        {{ line }}
      </div>
    </div>
    <el-empty v-else description="尚无滚动总结条目" :image-size="64" />
    <el-input
      v-model="summaryContent"
      type="textarea"
      :rows="6"
      class="summary-edit"
      placeholder="可在此编辑全部滚动日志（每行一条）后保存"
    />
    <p class="hint">
      每轮任务结束后会自动追加一条带时间戳的滚动总结；也可点「生成一条」基于最近回复追加。
      条目上限约 40 条，超出将删除最旧记录。
    </p>
    <template #footer>
      <div class="footer-row">
        <div class="footer-left">
          <el-button type="warning" plain :loading="summaryGenerating" @click="generateSummary">
            生成一条
          </el-button>
          <el-button type="danger" plain :disabled="!summaryContent" @click="clearSummary">
            清空
          </el-button>
        </div>
        <div class="footer-right">
          <el-button @click="summaryVisible = false">关闭</el-button>
          <el-button type="primary" :loading="summarySaving" @click="saveSummary">保存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getCgi, postCgi } from '../api'

const props = defineProps({
  modelValue: Boolean,
  agentId: String,
  sessionId: String,
})

const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const content = ref('')
const saving = ref(false)
const summaryVisible = ref(false)
const summaryContent = ref('')
const summarySaving = ref(false)
const summaryGenerating = ref(false)

const summaryEntries = computed(() =>
  (summaryContent.value || '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean),
)

async function loadNote() {
  if (!props.agentId || !props.sessionId) return
  const res = await getCgi('/pages/page_agent_chat.cgi', {
    action: 'get_note',
    agent_id: props.agentId,
    session_id: props.sessionId,
  })
  content.value = res.data?.content || ''
}

async function save() {
  saving.value = true
  try {
    await postCgi('/pages/page_agent_chat.cgi?action=save_note', {
      agent_id: props.agentId,
      session_id: props.sessionId,
      content: content.value,
    })
    ElMessage.success('备忘已保存')
    visible.value = false
  } finally {
    saving.value = false
  }
}

async function loadSummary() {
  if (!props.agentId || !props.sessionId) return
  const res = await getCgi('/pages/page_agent_chat.cgi', {
    action: 'get_summary',
    agent_id: props.agentId,
    session_id: props.sessionId,
  })
  summaryContent.value = res.data?.content || ''
}

async function viewSummary() {
  summaryVisible.value = true
  await loadSummary()
}

async function generateSummary() {
  if (!props.agentId || !props.sessionId) return
  summaryGenerating.value = true
  try {
    const res = await postCgi('/pages/page_agent_chat.cgi?action=generate_summary', {
      agent_id: props.agentId,
      session_id: props.sessionId,
    })
    summaryContent.value = res.data?.content || ''
    ElMessage.success('已追加一条滚动总结')
  } catch (e) {
    // api interceptor already toasts on code !== 0
  } finally {
    summaryGenerating.value = false
  }
}

async function clearSummary() {
  try {
    await ElMessageBox.confirm('确认清空全部滚动总结条目？', '清空', { type: 'warning' })
  } catch {
    return
  }
  summaryContent.value = ''
  summarySaving.value = true
  try {
    await postCgi('/pages/page_agent_chat.cgi?action=save_summary', {
      agent_id: props.agentId,
      session_id: props.sessionId,
      content: '',
    })
    ElMessage.success('已清空')
  } finally {
    summarySaving.value = false
  }
}

async function saveSummary() {
  summarySaving.value = true
  try {
    await postCgi('/pages/page_agent_chat.cgi?action=save_summary', {
      agent_id: props.agentId,
      session_id: props.sessionId,
      content: summaryContent.value,
    })
    ElMessage.success('滚动总结已保存')
    summaryVisible.value = false
  } finally {
    summarySaving.value = false
  }
}

defineExpose({ openSummary: viewSummary })
</script>

<style scoped>
.hint { font-size: 12px; color: #909399; margin: 12px 0 0; line-height: 1.6; }
.footer-row { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.footer-left, .footer-right { display: flex; gap: 8px; }
.rolling-list {
  max-height: 280px;
  overflow: auto;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-primary, #303133);
}
.rolling-item + .rolling-item { margin-top: 6px; }
.summary-edit { margin-top: 4px; }
</style>
