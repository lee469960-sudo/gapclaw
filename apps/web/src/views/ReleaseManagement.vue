<template>
  <div class="page" v-loading="loading">
    <div class="header">
      <div class="header-left"><el-icon><Promotion /></el-icon><span class="page-title">Release Agent</span></div>
      <el-button @click="load"><el-icon><Refresh /></el-icon>刷新</el-button>
    </div>
    <el-alert v-if="status.state === 'reconciliation_required'" type="warning" :closable="false" title="发布状态需要与 Runner 对账；请稍后刷新，若持续存在请由管理员检查 Runner 状态。系统不会自动继续部署或显示虚假的发布成功。" />
    <el-card class="card" shadow="never">
      <template #header>当前发布</template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="状态"><el-tag :type="statusType(current.status)">{{ current.status || '暂无记录' }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="目标">{{ current.target_id || status.target_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="发布">{{ current.release_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="时间">{{ current.occurred_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="健康结果">{{ current.health_result || '-' }}</el-descriptions-item>
        <el-descriptions-item label="自动回滚">{{ current.rollback_result || '-' }}</el-descriptions-item>
        <el-descriptions-item label="API digest" :span="2"><span class="mono">{{ current.api_image || '-' }}</span></el-descriptions-item>
        <el-descriptions-item label="Web digest" :span="2"><span class="mono">{{ current.web_image || '-' }}</span></el-descriptions-item>
      </el-descriptions>
      <el-alert v-if="current.failure_summary" class="failure" type="error" :closable="false" :title="current.failure_summary" />
    </el-card>
    <el-card class="card" shadow="never"><template #header>发布历史</template>
      <el-table :data="history" empty-text="暂无已存档发布记录"><el-table-column prop="occurred_at" label="时间" width="170"/><el-table-column prop="release_id" label="发布" min-width="150"/><el-table-column prop="status" label="状态" width="160"><template #default="{row}"><el-tag :type="statusType(row.status)">{{ row.status }}</el-tag></template></el-table-column><el-table-column prop="health_result" label="健康" width="110"/><el-table-column prop="rollback_result" label="回滚" width="110"/><el-table-column prop="failure_summary" label="说明" min-width="180" show-overflow-tooltip/></el-table>
    </el-card>
    <el-card v-if="isAdmin" class="card" shadow="never">
      <template #header>受确认回滚</template>
      <template v-if="rollbackTarget">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="已知健康发布">{{ rollbackTarget.release_id }}</el-descriptions-item>
          <el-descriptions-item label="目标">{{ rollbackTarget.target_id }}</el-descriptions-item>
        </el-descriptions>
        <p class="rollback-hint">只能回滚至 Runner 记录的上述已知健康发布，不能指定镜像、摘要或其他版本。</p>
        <el-button type="warning" @click="rollbackDialog = true">确认回滚</el-button>
      </template>
      <el-alert v-else type="info" :closable="false" :title="rollbackUnavailableReason || 'Runner 尚无可用的已知健康版本，当前不能回滚。'" />
    </el-card>
    <el-dialog v-model="rollbackDialog" title="确认回滚" width="480px" destroy-on-close>
      <p>将请求 Runner 回滚至以下已知健康版本：</p>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="发布">{{ rollbackTarget?.release_id }}</el-descriptions-item>
        <el-descriptions-item label="目标">{{ rollbackTarget?.target_id }}</el-descriptions-item>
      </el-descriptions>
      <el-form class="rollback-form" label-position="top">
        <el-form-item label="确认短语">
          <el-input v-model="rollbackConfirmation" placeholder="请输入 ROLLBACK" autocomplete="off" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rollbackDialog = false">取消</el-button>
        <el-button type="warning" :loading="rollbackSubmitting" :disabled="rollbackConfirmation !== 'ROLLBACK'" @click="submitRollback">确认回滚</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Promotion, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getCgi, postCgi } from '../api'
import { getShell } from '../session'

const loading = ref(false); const status = ref({}); const history = ref([])
const current = computed(() => status.value.current || {})
const isAdmin = computed(() => {
  const roles = getShell()?.roles || []
  return roles.includes('master') || roles.includes('admin')
})
const rollbackTarget = ref(null)
const rollbackUnavailableReason = ref('')
const rollbackDialog = ref(false)
const rollbackConfirmation = ref('')
const rollbackSubmitting = ref(false)
const statusType = (value) => ({ succeeded: 'success', rolled_back: 'warning', reconciliation_required: 'danger', failed: 'danger' }[value] || 'info')
function rollbackUnavailableMessage (detail) {
  if (detail === 'release_runner_no_healthy_release') return 'Runner 尚无可用的已知健康版本，当前不能回滚。'
  return 'Runner 当前不可达或无法确认回滚基线。请稍后刷新并完成发布状态对账；系统不会显示虚假的发布成功。'
}
async function loadRollbackTarget () {
  if (!isAdmin.value) return
  const result = await getCgi('/api/release-management/rollback-target', {}, { validateStatus: (code) => code === 200 || code === 409 || code === 503 })
  rollbackTarget.value = result.data || null
  rollbackUnavailableReason.value = rollbackTarget.value ? '' : rollbackUnavailableMessage(result.detail)
}
async function load () { loading.value = true; try { const [s, h] = await Promise.all([getCgi('/api/release-management/status'), getCgi('/api/release-management/history')]); status.value = s.data || {}; history.value = h.data?.items || []; await loadRollbackTarget() } finally { loading.value = false } }
async function submitRollback () {
  if (!rollbackTarget.value || rollbackConfirmation.value !== 'ROLLBACK') return
  rollbackSubmitting.value = true
  try {
    await postCgi('/api/release-management/rollback', {
      displayed_release_id: rollbackTarget.value.release_id,
      displayed_target_id: rollbackTarget.value.target_id,
      confirmation: rollbackConfirmation.value,
    })
    ElMessage.success('已提交受确认回滚请求')
    rollbackDialog.value = false
    rollbackConfirmation.value = ''
    await load()
  } finally { rollbackSubmitting.value = false }
}
onMounted(load)
</script>

<style scoped>
.header { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }.header-left { display:flex; gap:8px; align-items:center; }.page-title { font-size:20px; font-weight:600; }.card { margin-top:16px; }.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; overflow-wrap:anywhere; }.failure { margin-top:16px; }.rollback-hint { color:var(--el-text-color-secondary); margin:14px 0; }.rollback-form { margin-top:16px; }
</style>
