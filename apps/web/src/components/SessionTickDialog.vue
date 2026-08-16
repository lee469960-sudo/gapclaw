<template>
  <el-dialog v-model="visible" width="640px" @open="loadTicks">
    <template #header>
      <span class="dlg-title"><el-icon><Clock /></el-icon> 本会话定时器</span>
    </template>

    <div class="toolbar">
      <el-button type="primary" @click="openForm()">+ 新增定时器</el-button>
      <el-button @click="loadTicks">刷新</el-button>
      <span class="count">共 {{ ticks.length }} 条</span>
    </div>

    <div v-loading="loading" class="tick-list">
      <div v-for="t in ticks" :key="t.tick_id" class="tick-item">
        <div class="tick-main">
          <div class="tick-cron">{{ t.cron }}</div>
          <div class="tick-msg">{{ t.message || '(无消息)' }}</div>
        </div>
        <div class="tick-actions">
          <el-switch v-model="t.enabled" @change="toggleTick(t)" />
          <el-button link @click="openForm(t)">编辑</el-button>
          <el-button link type="danger" @click="removeTick(t)">删除</el-button>
        </div>
      </div>
      <div v-if="!loading && !ticks.length" class="empty">
        暂无定时器，点击左上角「新增定时器」创建
      </div>
    </div>
  </el-dialog>

  <el-dialog v-model="formVisible" :title="form.tick_id ? '编辑定时器' : '新增定时器'" width="480px">
    <el-form label-width="100px">
      <el-form-item label="Cron 表达式">
        <el-input v-model="form.cron" placeholder="0 9 * * *" />
        <div class="form-tip">示例：0 9 * * * 每天 9:00；*/5 * * * * 每 5 分钟</div>
      </el-form-item>
      <el-form-item label="触发消息">
        <el-input v-model="form.message" type="textarea" :rows="4" placeholder="定时触发时发送给 Agent 的消息" />
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="form.enabled" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="formVisible = false">取消</el-button>
      <el-button type="primary" :loading="formSaving" @click="saveForm">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Clock } from '@element-plus/icons-vue'
import { postCgi } from '../api'

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

const ticks = ref([])
const loading = ref(false)
const formVisible = ref(false)
const formSaving = ref(false)
const form = reactive({ tick_id: '', cron: '0 9 * * *', message: '', enabled: true })

async function loadTicks() {
  if (!props.agentId || !props.sessionId) return
  loading.value = true
  try {
    const res = await postCgi('/pages/page_agent_chat.cgi?action=list_ticks', {
      agent_id: props.agentId,
      session_id: props.sessionId,
    })
    ticks.value = res.data || []
  } finally {
    loading.value = false
  }
}

function openForm(row) {
  if (row) {
    Object.assign(form, { tick_id: row.tick_id, cron: row.cron, message: row.message, enabled: row.enabled })
  } else {
    Object.assign(form, { tick_id: '', cron: '0 9 * * *', message: '', enabled: true })
  }
  formVisible.value = true
}

async function saveForm() {
  if (!form.cron.trim()) {
    ElMessage.warning('请填写 Cron 表达式')
    return
  }
  formSaving.value = true
  try {
    if (form.tick_id) {
      await postCgi('/pages/page_agent_chat.cgi?action=update_tick', {
        tick_id: form.tick_id,
        cron: form.cron.trim(),
        message: form.message,
        enabled: form.enabled,
      })
    } else {
      await postCgi('/pages/page_agent_chat.cgi?action=add_tick', {
        agent_id: props.agentId,
        session_id: props.sessionId,
        cron: form.cron.trim(),
        message: form.message,
        enabled: form.enabled,
      })
    }
    ElMessage.success('保存成功')
    formVisible.value = false
    loadTicks()
  } finally {
    formSaving.value = false
  }
}

async function toggleTick(t) {
  await postCgi('/pages/page_agent_chat.cgi?action=toggle_tick', {
    tick_id: t.tick_id,
    enabled: t.enabled,
  })
}

async function removeTick(t) {
  await ElMessageBox.confirm('确定删除该定时器？', '删除', { type: 'warning' })
  await postCgi('/pages/page_agent_chat.cgi?action=delete_tick', { tick_id: t.tick_id })
  ElMessage.success('已删除')
  loadTicks()
}
</script>

<style scoped>
.dlg-title { display: flex; align-items: center; gap: 6px; font-weight: 600; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.count { margin-left: auto; color: #909399; font-size: 13px; }
.tick-list { min-height: 160px; border: 1px solid #ebeef5; border-radius: 8px; padding: 8px; }
.tick-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px; border-bottom: 1px solid #f0f0f0;
}
.tick-item:last-child { border-bottom: none; }
.tick-cron { font-weight: 600; font-family: monospace; }
.tick-msg { font-size: 13px; color: #606266; margin-top: 4px; }
.tick-actions { display: flex; align-items: center; gap: 8px; }
.empty { text-align: center; color: #c0c4cc; padding: 48px 16px; }
.form-tip { font-size: 12px; color: #909399; margin-top: 4px; }
</style>
