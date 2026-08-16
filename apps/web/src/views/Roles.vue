<template>
  <div class="page" v-loading="loading">
    <div class="header">
      <div class="header-left">
        <el-icon class="page-icon"><Key /></el-icon>
        <span class="page-title">角色管理</span>
      </div>
      <div class="header-actions">
        <el-button @click="load">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button type="primary" @click="openRoleForm()">
          <el-icon><Plus /></el-icon>
          新建角色
        </el-button>
      </div>
    </div>

    <p class="intro">为每个角色配置可访问的页面权限，然后在「用户管理」中为用户分配角色。</p>

    <el-table :data="definitions" stripe border class="role-table" empty-text="暂无角色">
      <el-table-column type="index" label="序号" width="70" align="center" />
      <el-table-column label="角色标识" prop="name" min-width="120">
        <template #default="{ row }">
          <span class="role-name">{{ row.name }}</span>
          <el-tag v-if="row.builtin" size="small" type="info" effect="plain" style="margin-left:6px">内置</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="显示名称" prop="label" min-width="140" />
      <el-table-column label="页面权限" min-width="120" align="center">
        <template #default="{ row }">
          <el-tag type="primary" effect="light" round>{{ (row.permissions || []).length }} 项</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" align="center" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openPermForm(row)">配置权限</el-button>
          <el-button
            v-if="!row.builtin"
            size="small"
            type="danger"
            plain
            @click="deleteRole(row)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建/编辑角色 -->
    <el-dialog v-model="roleVisible" :title="roleForm._edit ? '编辑角色' : '新建角色'" width="440px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="角色标识" required>
          <el-input
            v-model="roleForm.name"
            placeholder="如 operator、viewer"
            :disabled="roleForm._edit"
          />
        </el-form-item>
        <el-form-item label="显示名称" required>
          <el-input v-model="roleForm.label" placeholder="如 运维人员" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" :loading="saving" @click="saveRole">保存</el-button>
        <el-button @click="roleVisible = false">取消</el-button>
      </template>
    </el-dialog>

    <!-- 配置权限 -->
    <el-dialog
      v-model="permVisible"
      :title="`配置权限 — ${permForm.label || permForm.name}`"
      width="640px"
      top="6vh"
      destroy-on-close
    >
      <div class="perm-toolbar">
        <el-button size="small" @click="selectAllPerms">全选</el-button>
        <el-button size="small" @click="clearAllPerms">清空</el-button>
        <span class="perm-count">已选 {{ permForm.permissions.length }} / {{ pages.length }}</span>
      </div>
      <div class="perm-groups">
        <div v-for="group in pageGroups" :key="group.title" class="perm-group">
          <div class="group-title">{{ group.title }}</div>
          <el-checkbox-group v-model="permForm.permissions">
            <el-checkbox
              v-for="item in group.items"
              :key="item.path"
              :value="item.path"
              class="perm-check"
            >
              {{ item.label }}
            </el-checkbox>
          </el-checkbox-group>
        </div>
      </div>
      <template #footer>
        <el-button type="primary" :loading="saving" @click="savePermissions">保存权限</el-button>
        <el-button @click="permVisible = false">取消</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Key, Refresh, Plus } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'

const PAGE_GROUPS = [
  {
    title: '智能平台',
    items: [
      { path: '/pages/page_group.cgi', label: '智能体群' },
      { path: '/pages/page_agent.cgi', label: '智能体' },
      { path: '/pages/page_sandbox.cgi', label: '沙箱管理' },
      { path: '/pages/page_skills.cgi', label: 'Skills市场' },
      { path: '/pages/page_mcp.cgi', label: 'MCP市场' },
      { path: '/pages/page_llm.cgi', label: 'LLM市场' },
      { path: '/pages/page_files.cgi', label: '文件管理' },
      { path: '/pages/page_rag.cgi', label: 'RAG知识库' },
      { path: '/pages/page_httpmcp.cgi', label: 'HttpMCP' },
    ],
  },
  {
    title: '运维中台',
    items: [
      { path: '/pages/page_sql.cgi', label: 'SQL工具' },
      { path: '/pages/page_terminal.cgi', label: '远程终端' },
      { path: '/pages/page_docker.cgi', label: 'Docker' },
      { path: '/pages/page_monitor.cgi', label: '监控' },
    ],
  },
  {
    title: '系统管理',
    items: [
      { path: '/pages/page_site.cgi', label: '站点配置' },
      { path: '/pages/system_me.cgi', label: '个人中心' },
      { path: '/pages/system_user.cgi', label: '用户管理' },
      { path: '/pages/system_role.cgi', label: '角色管理' },
    ],
  },
]

const loading = ref(false)
const saving = ref(false)
const pages = ref([])
const definitions = ref([])
const roleVisible = ref(false)
const permVisible = ref(false)
const roleForm = reactive({ name: '', label: '', _edit: false })
const permForm = reactive({ name: '', label: '', permissions: [] })

const pageGroups = computed(() => {
  const set = new Set(pages.value)
  return PAGE_GROUPS.map((g) => ({
    title: g.title,
    items: g.items.filter((i) => set.has(i.path)),
  })).filter((g) => g.items.length)
})

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/system_role.cgi')
    pages.value = res.data?.pages || []
    definitions.value = res.data?.definitions || []
  } finally {
    loading.value = false
  }
}

function openRoleForm(row) {
  if (row) {
    Object.assign(roleForm, { name: row.name, label: row.label, _edit: true })
  } else {
    Object.assign(roleForm, { name: '', label: '', _edit: false })
  }
  roleVisible.value = true
}

function openPermForm(row) {
  Object.assign(permForm, {
    name: row.name,
    label: row.label,
    permissions: [...(row.permissions || [])],
  })
  permVisible.value = true
}

function selectAllPerms() {
  permForm.permissions = [...pages.value]
}

function clearAllPerms() {
  permForm.permissions = ['/pages/system_me.cgi']
}

async function saveRole() {
  const name = roleForm.name?.trim()
  const label = roleForm.label?.trim()
  if (!name) {
    ElMessage.warning('请填写角色标识')
    return
  }
  if (!label) {
    ElMessage.warning('请填写显示名称')
    return
  }
  saving.value = true
  try {
    const existing = definitions.value.find((r) => r.name === name)
    const res = await postCgi('/pages/system_role.cgi', {
      action: 'save_role',
      name,
      label,
      permissions: existing?.permissions || ['/pages/system_me.cgi'],
    })
    definitions.value = res.data || []
    ElMessage.success(res.msg || '保存成功')
    roleVisible.value = false
  } finally {
    saving.value = false
  }
}

async function savePermissions() {
  if (!permForm.permissions.length) {
    ElMessage.warning('请至少保留「个人中心」权限')
    permForm.permissions = ['/pages/system_me.cgi']
  }
  saving.value = true
  try {
    const res = await postCgi('/pages/system_role.cgi', {
      action: 'save_role',
      name: permForm.name,
      label: permForm.label,
      permissions: permForm.permissions,
    })
    definitions.value = res.data || []
    ElMessage.success(res.msg || '权限已保存')
    permVisible.value = false
  } finally {
    saving.value = false
  }
}

async function deleteRole(row) {
  await ElMessageBox.confirm(`确定删除角色「${row.label || row.name}」？`, '提示', { type: 'warning' })
  const res = await postCgi('/pages/system_role.cgi', { action: 'delete_role', name: row.name })
  definitions.value = res.data || []
  ElMessage.success(res.msg || '已删除')
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
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  flex-wrap: wrap;
  gap: 12px;
}
.header-left { display: flex; align-items: center; gap: 10px; }
.header-actions { display: flex; gap: 8px; }
.page-icon { font-size: 22px; color: #409eff; }
.page-title { font-size: 18px; font-weight: 600; color: #303133; }
.intro {
  font-size: 13px;
  color: #909399;
  margin: 0 0 16px;
}
.role-table { width: 100%; }
.role-table :deep(.el-table__header th) {
  background: #f5f7fa;
  color: #606266;
  font-weight: 600;
}
.role-name { font-weight: 500; color: #303133; }
.perm-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.perm-count {
  margin-left: auto;
  font-size: 13px;
  color: #909399;
}
.perm-groups { max-height: 52vh; overflow: auto; }
.perm-group { margin-bottom: 18px; }
.group-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 10px;
}
.perm-check {
  display: inline-flex;
  width: 48%;
  margin-right: 0;
  margin-bottom: 8px;
}
</style>
