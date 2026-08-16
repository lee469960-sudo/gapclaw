<template>
  <div class="page">
    <!-- 角色 Tab -->
    <div class="tabs-bar">
      <el-radio-group v-model="roleTab" @change="onRoleTabChange">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button v-for="r in roleTabs" :key="r.name" :value="r.name">
          {{ r.label || r.name }}
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-input
        v-model="filterUsername"
        placeholder="用户名"
        clearable
        style="width:180px"
        @keyup.enter="applyFilter"
      />
      <el-select v-model="filterStatus" placeholder="全部状态" style="width:130px" clearable>
        <el-option label="全部状态" value="" />
        <el-option label="正常" value="active" />
        <el-option label="停用" value="disabled" />
      </el-select>
      <el-button type="primary" @click="applyFilter">
        <el-icon><Search /></el-icon>
        筛选
      </el-button>
      <el-button @click="resetFilter">重置</el-button>
      <div class="spacer" />
      <el-button type="primary" @click="openForm()">
        <el-icon><Plus /></el-icon>
        添加用户
      </el-button>
    </div>

    <!-- 用户表格 -->
    <el-table
      v-loading="loading"
      :data="pagedList"
      stripe
      border
      class="user-table"
      empty-text="暂无用户"
    >
      <el-table-column label="序号" width="70" align="center">
        <template #default="{ $index }">{{ (page - 1) * pageSize + $index + 1 }}</template>
      </el-table-column>
      <el-table-column prop="username" label="用户名" min-width="120">
        <template #default="{ row }">
          <span class="username">{{ row.username }}</span>
        </template>
      </el-table-column>
      <el-table-column label="角色" min-width="200">
        <template #default="{ row }">
          <div class="role-tags">
            <el-tag
              v-for="r in row.roles || []"
              :key="r"
              size="small"
              :type="roleTagType(r)"
              effect="dark"
              round
            >
              {{ roleLabel(r) }}
            </el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="Token" width="90" align="center">
        <template #default="{ row }">
          <span v-if="row.token_count > 0" class="token-count">{{ row.token_count }}</span>
          <span v-else class="token-none">无</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90" align="center">
        <template #default="{ row }">
          <el-tag :type="row.disabled ? 'danger' : 'primary'" size="small" effect="light" round>
            {{ row.disabled ? '停用' : '正常' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right" align="center">
        <template #default="{ row }">
          <el-button size="small" type="success" plain @click="openForm(row)">编辑</el-button>
          <el-button
            size="small"
            :type="row.disabled ? 'primary' : 'warning'"
            plain
            @click="toggleUser(row)"
          >
            {{ row.disabled ? '启用' : '停用' }}
          </el-button>
          <el-button size="small" type="danger" plain @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div class="pagination-bar">
      <span class="total">共 {{ filteredList.length }} 条</span>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="filteredList.length"
        :page-sizes="[10, 20, 50, 100]"
        layout="prev, pager, next, sizes, jumper"
        background
        @size-change="page = 1"
      />
    </div>

    <!-- 添加/编辑用户 -->
    <el-dialog
      v-model="visible"
      :title="form.old_username ? '编辑用户' : '添加用户'"
      width="520px"
      destroy-on-close
      @closed="resetForm"
    >
      <el-form :model="form" label-width="80px">
        <el-form-item label="用户" required>
          <el-input
            v-model="form.username"
            placeholder="请输入用户"
            :disabled="!!form.old_username"
          />
        </el-form-item>
        <el-form-item label="密码">
          <div class="pwd-row">
            <el-input
              v-model="form.password"
              type="password"
              :placeholder="form.old_username ? '留空则不修改' : '请输入密码'"
              show-password
              autocomplete="new-password"
            />
            <el-button type="button" @click="generatePassword()">生成</el-button>
          </div>
        </el-form-item>
        <el-form-item label="角色" required>
          <el-checkbox-group v-model="form.roles">
            <el-checkbox v-for="r in roleDefinitions" :key="r.name" :value="r.name">
              {{ r.label || r.name }}
            </el-checkbox>
          </el-checkbox-group>
          <div class="field-hint">页面权限由角色决定，请在「角色管理」中配置各角色权限</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" :loading="saving" @click="save">确定</el-button>
        <el-button @click="visible = false">取消</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Plus } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'

const list = ref([])
const loading = ref(false)
const saving = ref(false)
const visible = ref(false)
const roleDefinitions = ref([])

const roleTab = ref('all')
const filterUsername = ref('')
const appliedUsername = ref('')
const filterStatus = ref('')
const appliedStatus = ref('')

const page = ref(1)
const pageSize = ref(20)

const form = reactive({
  username: '',
  password: '',
  roles: ['user'],
  old_username: '',
})

const roleTabs = computed(() => {
  const set = new Set()
  list.value.forEach((u) => (u.roles || []).forEach((r) => set.add(r)))
  return roleDefinitions.value.filter((r) => set.has(r.name))
})

const roleLabelMap = computed(() => {
  const map = {}
  roleDefinitions.value.forEach((r) => { map[r.name] = r.label || r.name })
  return map
})

function roleLabel(name) {
  return roleLabelMap.value[name] || name
}

const filteredList = computed(() => {
  let rows = list.value
  if (roleTab.value !== 'all') {
    rows = rows.filter((u) => (u.roles || []).includes(roleTab.value))
  }
  const kw = appliedUsername.value.trim().toLowerCase()
  if (kw) {
    rows = rows.filter((u) => u.username?.toLowerCase().includes(kw))
  }
  if (appliedStatus.value === 'active') {
    rows = rows.filter((u) => !u.disabled)
  } else if (appliedStatus.value === 'disabled') {
    rows = rows.filter((u) => u.disabled)
  }
  return rows
})

const pagedList = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredList.value.slice(start, start + pageSize.value)
})

function roleTagType(role) {
  if (role === 'master') return 'primary'
  if (role === 'admin') return 'warning'
  return 'info'
}

function generatePassword(len = 12) {
  const size = typeof len === 'number' && len > 0 ? len : 12
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789'
  form.password = Array.from({ length: size }, () =>
    chars[Math.floor(Math.random() * chars.length)]
  ).join('')
}

function resetForm() {
  Object.assign(form, {
    username: '',
    password: '',
    roles: ['user'],
    old_username: '',
  })
}

function onRoleTabChange() {
  page.value = 1
}

function applyFilter() {
  appliedUsername.value = filterUsername.value
  appliedStatus.value = filterStatus.value
  page.value = 1
}

function resetFilter() {
  filterUsername.value = ''
  filterStatus.value = ''
  appliedUsername.value = ''
  appliedStatus.value = ''
  roleTab.value = 'all'
  page.value = 1
}

async function load() {
  loading.value = true
  try {
    const [users, roles] = await Promise.all([
      getCgi('/pages/system_user.cgi', { action: 'list' }),
      getCgi('/pages/system_role.cgi'),
    ])
    list.value = users.data || []
    roleDefinitions.value = roles.data?.definitions || []
  } finally {
    loading.value = false
  }
}

function openForm(row) {
  resetForm()
  if (row) {
    Object.assign(form, {
      username: row.username,
      password: '',
      roles: [...(row.roles || ['user'])],
      old_username: row.username,
    })
  } else {
    generatePassword()
  }
  visible.value = true
}

async function save() {
  if (!form.username?.trim()) {
    ElMessage.warning('请输入用户名')
    return
  }
  if (!form.old_username && !form.password) {
    ElMessage.warning('请设置密码')
    return
  }
  if (!form.roles?.length) {
    ElMessage.warning('请至少选择一个角色')
    return
  }
  saving.value = true
  try {
    const payload = {
      action: form.old_username ? 'update' : 'create',
      username: form.username.trim(),
      old_username: form.old_username || undefined,
      roles: form.roles,
    }
    if (form.password) payload.password = form.password
    const res = await postCgi('/pages/system_user.cgi', payload)
    ElMessage.success(res.msg || '保存成功')
    visible.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function toggleUser(row) {
  const action = row.disabled ? '启用' : '停用'
  await ElMessageBox.confirm(`确定${action}用户「${row.username}」？`, '提示', { type: 'warning' })
  await postCgi('/pages/system_user.cgi', {
    action: 'toggle',
    username: row.username,
    disabled: !row.disabled,
  })
  ElMessage.success(`${action}成功`)
  load()
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除用户「${row.username}」？此操作不可恢复。`, '提示', { type: 'warning' })
  await postCgi('/pages/system_user.cgi', { action: 'delete', username: row.username })
  ElMessage.success('删除成功')
  load()
}

onMounted(load)
</script>

<style scoped>
.page {
  background: #fff;
  border-radius: 8px;
  padding: 16px 20px;
  min-height: 400px;
}
.tabs-bar {
  margin-bottom: 16px;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 12px;
}
.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.spacer { flex: 1; }
.user-table { width: 100%; }
.user-table :deep(.el-table__header th) {
  background: #f5f7fa;
  color: #606266;
  font-weight: 600;
}
.username { font-weight: 500; color: #303133; }
.role-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.token-count { color: #409eff; font-weight: 500; }
.token-none { color: #c0c4cc; }
.pagination-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  margin-top: 16px;
  flex-wrap: wrap;
}
.total { font-size: 13px; color: #909399; margin-right: auto; }
.pwd-row { display: flex; gap: 8px; width: 100%; }
.pwd-row .el-input { flex: 1; }
.field-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 8px;
  line-height: 1.4;
}
</style>
