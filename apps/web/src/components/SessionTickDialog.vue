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
          <div v-if="t.next_run_time" class="tick-next">下次触发：{{ t.next_run_time }}</div>
          <div v-if="t.latestRun" class="tick-next">已执行 {{ t.execution_count || 0 }} 次 · 最近执行：{{ runStateLabel(t.latestRun.state) }}<template v-if="t.latestRun.attempt > 0">（重试 {{ t.latestRun.attempt }}/3）</template>{{ t.latestRun.error_summary || '' }}</div>
          <div v-if="t.latestRun?.chat_message_id" class="tick-next">结果消息：#{{ t.latestRun.chat_message_id }}（已写入本会话）</div>
        </div>
        <div class="tick-actions">
          <el-switch v-model="t.enabled" @change="toggleTick(t)" />
          <el-button link type="primary" @click="runNow(t)">立即执行</el-button>
          <el-button v-if="t.latestRun?.state === 'running'" link type="danger" @click="stopTask(t)">停止</el-button>
          <el-button v-if="t.latestRun?.state === 'failed'" link type="warning" @click="retryRun(t)">重试失败</el-button>
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
      <el-form-item label="计划类型">
        <el-select v-model="form.schedule_type"><el-option label="Cron" value="cron" /><el-option label="间隔" value="interval" /><el-option label="一次" value="once" /></el-select>
      </el-form-item>
      <el-form-item v-if="form.schedule_type === 'cron'" label="Cron 表达式">
        <el-input v-model="form.cron" placeholder="0 9 * * *" />
        <div class="form-tip">示例：0 9 * * * 每天 9:00；*/5 * * * * 每 5 分钟</div>
      </el-form-item>
      <el-form-item v-if="form.schedule_type === 'interval'" label="间隔（秒）"><el-input-number v-model="form.interval_seconds" :min="300" /></el-form-item>
      <el-form-item v-if="form.schedule_type === 'once'" label="执行时间"><el-input v-model="form.run_at" placeholder="2026-09-21T09:00:00+08:00" /></el-form-item>
      <el-form-item label="触发消息">
        <el-input v-model="form.message" type="textarea" :rows="4" placeholder="定时触发时发送给 Agent 的消息" />
      </el-form-item>
      <el-form-item label="时区"><el-select v-model="form.timezone" filterable allow-create default-first-option><el-option v-for="zone in timezones" :key="zone" :label="zone" :value="zone" /></el-select><div class="form-tip">可搜索或输入任意 IANA 时区，默认 Asia/Shanghai</div></el-form-item>
      <el-form-item label="配置快照"><el-switch v-model="form.snapshot_enabled" /></el-form-item>
      <el-form-item label="通知"><el-switch v-model="form.notification_enabled" /></el-form-item>
      <el-form-item v-if="form.notification_enabled" label="渠道 ID"><el-input v-model="form.notification_channel_id" /></el-form-item>
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
const timezones = typeof Intl.supportedValuesOf === 'function'
  ? ['Asia/Shanghai', ...Intl.supportedValuesOf('timeZone').filter(zone => zone !== 'Asia/Shanghai')]
  : ['Asia/Shanghai', 'Asia/Tokyo', 'Asia/Singapore', 'Asia/Hong_Kong', 'UTC', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles']
const form = reactive({ tick_id: '', cron: '0 9 * * *', message: '', enabled: true, schedule_type: 'cron', timezone: 'Asia/Shanghai', interval_seconds: 300, run_at: '', snapshot_enabled: false, notification_enabled: false, notification_channel_id: '' })

async function loadTicks() {
  if (!props.agentId || !props.sessionId) {
    ticks.value = []
    return
  }
  loading.value = true
  try {
    const res = await postCgi('/pages/page_agent_chat.cgi?action=list_scheduled_tasks', {
      agent_id: props.agentId,
      session_id: props.sessionId,
    })
    ticks.value = await Promise.all((res.data || []).map(async t => {
      const row = { ...t, tick_id: t.id, next_run_time: t.next_run_at, execution_count: Number(t.execution_count || 0) }
      try {
        const runs = await postCgi('/pages/page_agent_chat.cgi?action=list_scheduled_task_runs', { task_id: t.id, agent_id: props.agentId, session_id: props.sessionId })
        row.latestRun = (runs.data || [])[0]
      } catch (_) {
        row.latestRun = { state: '不可读取', attempt: 0, error_summary: '' }
      }
      return row
    }))
  } finally {
    loading.value = false
  }
}

function openForm(row) {
  if (!props.agentId || !props.sessionId) {
    ElMessage.warning('Agent 或会话尚未加载完成，不能设置定时器')
    return
  }
  if (row) {
    Object.assign(form, { tick_id: row.tick_id, cron: row.cron, message: row.message, enabled: row.enabled, schedule_type: row.schedule_type || 'cron', timezone: row.timezone || 'Asia/Shanghai', interval_seconds: row.interval_seconds || 300, run_at: row.run_at || '', snapshot_enabled: !!row.snapshot_enabled, notification_enabled: !!row.notification_enabled, notification_channel_id: row.notification_channel_id || '' })
  } else {
    Object.assign(form, { tick_id: '', cron: '0 9 * * *', message: '', enabled: true, schedule_type: 'cron', timezone: 'Asia/Shanghai' })
  }
  formVisible.value = true
}

async function saveForm() {
  if (!props.agentId || !props.sessionId) {
    ElMessage.warning('Agent 或会话尚未加载完成，不能保存定时器')
    return
  }
  if (form.schedule_type === 'cron' && !form.cron.trim()) {
    ElMessage.warning('请填写 Cron 表达式')
    return
  }
  if (form.schedule_type === 'interval' && form.interval_seconds < 300) {
    ElMessage.warning('间隔不得少于 5 分钟')
    return
  }
  if (form.schedule_type === 'once' && (!form.run_at || !/[zZ]|[+-]\d\d:\d\d$/.test(form.run_at))) {
    ElMessage.warning('请填写带时区的一次执行时间')
    return
  }
  formSaving.value = true
  if (!form.message.trim()) {
    formSaving.value = false
    ElMessage.warning('请填写触发消息')
    return
  }
  try {
    if (form.tick_id) {
      await postCgi('/pages/page_agent_chat.cgi?action=update_scheduled_task', {
        task_id: form.tick_id,
        cron: form.cron.trim(),
        schedule_type: form.schedule_type, timezone: form.timezone,
        interval_seconds: form.interval_seconds, run_at: form.run_at,
        snapshot_enabled: form.snapshot_enabled, notification_enabled: form.notification_enabled,
        notification_channel_id: form.notification_channel_id, notification_chat_id: props.sessionId,
        message: form.message,
        enabled: form.enabled,
      })
    } else {
      await postCgi('/pages/page_agent_chat.cgi?action=create_scheduled_task', {
        agent_id: props.agentId,
        session_id: props.sessionId,
        cron: form.cron.trim(),
        schedule_type: form.schedule_type, timezone: form.timezone,
        interval_seconds: form.interval_seconds, run_at: form.run_at,
        snapshot_enabled: form.snapshot_enabled, notification_enabled: form.notification_enabled,
        notification_channel_id: form.notification_channel_id, notification_chat_id: props.sessionId,
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
  await postCgi('/pages/page_agent_chat.cgi?action=toggle_scheduled_task', {
    task_id: t.tick_id,
    enabled: t.enabled,
  })
}

async function runNow(t) {
  await postCgi('/pages/page_agent_chat.cgi?action=run_scheduled_task_now', { task_id: t.tick_id, agent_id: props.agentId, session_id: props.sessionId })
  ElMessage.success('已加入执行队列')
}

async function stopTask(t) {
  await ElMessageBox.confirm('将停止当前执行、取消排队实例，并停用后续触发。已发生的外部工具操作无法回滚。', '停止定时任务', { type: 'warning' })
  await postCgi('/pages/page_agent_chat.cgi?action=stop_scheduled_task', {
    task_id: t.tick_id, agent_id: props.agentId, session_id: props.sessionId,
  })
  ElMessage.success('已请求停止，Worker 会在当前步骤结束后取消')
  loadTicks()
}

async function retryRun(t) {
  await postCgi('/pages/page_agent_chat.cgi?action=retry_scheduled_task_run', { task_id: t.latestRun.id, agent_id: props.agentId, session_id: props.sessionId })
  ElMessage.success('已加入重试队列')
  loadTicks()
}

function runStateLabel(state) {
  return ({ pending: '排队中', running: '执行中', succeeded: '成功', failed: '失败', cancelled: '已取消', skipped: '已跳过' })[state] || state
}

async function removeTick(t) {
  await ElMessageBox.confirm('确定删除该定时器？', '删除', { type: 'warning' })
  await postCgi('/pages/page_agent_chat.cgi?action=delete_scheduled_task', { task_id: t.tick_id })
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
.tick-next { font-size: 12px; color: #909399; margin-top: 4px; }
.tick-actions { display: flex; align-items: center; gap: 8px; }
.empty { text-align: center; color: #c0c4cc; padding: 48px 16px; }
.form-tip { font-size: 12px; color: #909399; margin-top: 4px; }
</style>
