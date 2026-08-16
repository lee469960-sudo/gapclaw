<template>
  <div class="page">
    <!-- 用户信息 -->
    <div class="profile-card">
      <div class="avatar">
        <el-icon><UserFilled /></el-icon>
      </div>
      <div class="profile-body">
        <div class="profile-name">{{ profile.username || '-' }}</div>
        <div class="profile-roles">
          <el-tag
            v-for="r in profile.roles || []"
            :key="r"
            size="small"
            :type="roleTagType(r)"
            effect="dark"
            round
          >
            {{ r }}
          </el-tag>
        </div>
      </div>
    </div>

    <div class="panel">
      <el-tabs v-model="activeTab" class="me-tabs">
        <!-- 密码管理 -->
        <el-tab-pane label="密码管理" name="password">
          <div class="tab-content">
            <div class="section-desc">修改登录密码，请确保新密码足够安全。</div>
            <el-form label-width="90px" class="pwd-form">
              <el-form-item label="旧密码">
                <el-input
                  v-model="pwd.old"
                  type="password"
                  show-password
                  placeholder="请输入旧密码"
                  style="max-width:360px"
                />
              </el-form-item>
              <el-form-item label="新密码">
                <el-input
                  v-model="pwd.new"
                  type="password"
                  show-password
                  placeholder="请输入新密码"
                  style="max-width:360px"
                />
              </el-form-item>
              <el-form-item label="确认密码">
                <el-input
                  v-model="pwd.confirm"
                  type="password"
                  show-password
                  placeholder="请再次输入新密码"
                  style="max-width:360px"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="pwdLoading" @click="changePwd">
                  修改密码
                </el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <!-- TOKEN 管理 -->
        <el-tab-pane label="TOKEN管理" name="token">
          <div class="tab-content">
            <el-collapse v-model="guideExpanded" class="token-guide">
              <el-collapse-item name="guide" title="使用说明">
                <div class="guide-body">
                  <p>
                    个人 Token 是<strong> API 访问令牌</strong>，用于脚本、外部系统或自动化任务在<strong>无浏览器登录</strong>时，以你的账号身份调用平台接口。权限与当前账号的角色一致。
                  </p>
                  <h4>适用场景</h4>
                  <ul>
                    <li>curl / Python / Postman 调用 <code>/pages/*.cgi</code> 接口</li>
                    <li>外部系统触发 Agent 对话（<code>api_agent_cgi.cgi</code>）</li>
                    <li>WebSocket 连接时在首条消息传入 <code>token</code> 字段</li>
                    <li>CI、定时任务、内部服务对接 GAP 平台</li>
                  </ul>
                  <h4>请求方式</h4>
                  <p>在 HTTP 请求头添加：</p>
                  <pre class="code-block">Authorization: Bearer &lt;你的 Token&gt;</pre>
                  <h4>示例：调用 Agent 外部接口</h4>
                  <pre class="code-block">curl -G "{{ apiBase }}/pages/api_agent_cgi.cgi" \
  --data-urlencode "agent_id=&lt;AgentID&gt;" \
  --data-urlencode "message=你好" \
  -H "Authorization: Bearer &lt;你的 Token&gt;"</pre>
                  <h4>在 VS Code 中使用</h4>
                  <p>
                    个人 Token <strong>可以</strong>在 VS Code 里调 GAP API / 触发 Agent；
                    <strong>不能</strong>当作 GitHub Copilot、Cursor、Continue 的「模型 API Key」做行内补全
                    （那是 LLM 市场的 Key，如 MiniMax，与个人 Token 无关）。
                  </p>
                  <ol class="guide-steps">
                    <li>安装扩展 <strong>REST Client</strong>（<code>humao.rest-client</code>）或 Thunder Client</li>
                    <li>
                      工作区创建 <code>.vscode/http.env</code>（勿提交仓库），例如：
                      <pre class="code-block">gapHost={{ apiBase }}
gapToken=你的个人Token</pre>
                    </li>
                    <li>
                      新建 <code>gap.http</code>：
                      <pre class="code-block">### 触发 Agent
GET {{gapHost}}/pages/api_agent_cgi.cgi?agent_id=&lt;AgentID&gt;&amp;message=你好
Authorization: Bearer {{gapToken}}</pre>
                      在请求上方点 <strong>Send Request</strong> 即可。
                    </li>
                    <li>
                      或用 VS Code 内置终端：
                      <pre class="code-block">export GAP_TOKEN='你的Token'
curl -G "{{ apiBase }}/pages/api_agent_cgi.cgi" \
  --data-urlencode "agent_id=&lt;AgentID&gt;" \
  --data-urlencode "message=写一个函数" \
  -H "Authorization: Bearer $GAP_TOKEN"</pre>
                    </li>
                  </ol>
                  <p>
                    若目标是「VS Code 里 AI 写代码」，请在 Continue / Cline 等扩展中配置
                    <strong>LLM 市场的 API Key</strong> 与厂商 Base URL，不要填本页个人 Token。
                  </p>
                  <h4>注意</h4>
                  <ul class="guide-notes">
                    <li>浏览器内操作使用登录 Session，不依赖此 Token</li>
                    <li>Token 长期有效，删除后立即失效；请勿泄露，建议按用途分 Token 并填写备注</li>
                    <li>与 LLM 市场的 API Key、MCP 的 Headers 鉴权无关，是两套独立凭证</li>
                    <li>本地 <code>.vscode/http.env</code>、<code>.env.vscode</code> 勿提交 Git（仓库已忽略）</li>
                  </ul>
                </div>
              </el-collapse-item>
            </el-collapse>

            <div class="token-toolbar">
              <el-input
                v-model="tokenKeyword"
                placeholder="Token / 备注"
                clearable
                style="width:240px"
                @keyup.enter="applyTokenFilter"
                @clear="resetTokenFilter"
              >
                <template #prefix>
                  <el-icon><Search /></el-icon>
                </template>
              </el-input>
              <el-button type="primary" @click="applyTokenFilter">
                <el-icon><Search /></el-icon>
                筛选
              </el-button>
              <el-button @click="resetTokenFilter">重置</el-button>
              <div class="spacer" />
              <el-button type="primary" :loading="genLoading" @click="genToken">
                <el-icon><Plus /></el-icon>
                生成 Token
              </el-button>
            </div>

            <div class="token-summary">共 {{ tokens.length }} 个 Token，显示 {{ filteredTokens.length }} 个</div>

            <el-table
              :data="filteredTokens"
              stripe
              border
              empty-text="暂无 Token，点击右上角生成"
              class="token-table"
            >
              <el-table-column label="Token" min-width="280">
                <template #default="{ row }">
                  <div class="token-cell">
                    <span class="token-text mono">{{ maskToken(row.token) }}</span>
                    <el-button link type="primary" size="small" @click="copyToken(row.token)">
                      复制
                    </el-button>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="创建时间" width="180" />
              <el-table-column label="备注" width="160">
                <template #default="{ row }">
                  <span class="remark-text">{{ row.remark || '-' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="160" align="center" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" plain @click="openRemark(row)">
                    备注
                  </el-button>
                  <el-button size="small" type="danger" plain @click="delToken(row)">
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 编辑备注 -->
    <el-dialog v-model="remarkVisible" title="编辑备注" width="440px" destroy-on-close>
      <el-input
        v-model="remarkForm.remark"
        type="textarea"
        :rows="3"
        placeholder="请输入备注"
        maxlength="200"
        show-word-limit
      />
      <template #footer>
        <el-button @click="remarkVisible = false">取消</el-button>
        <el-button type="primary" :loading="remarkLoading" @click="saveRemark">保存</el-button>
      </template>
    </el-dialog>

    <!-- HTTP / 非安全上下文：手动复制 Token -->
    <el-dialog
      v-model="copyVisible"
      title="复制 Token"
      width="520px"
      destroy-on-close
      @opened="focusCopyInput"
    >
      <p class="copy-hint">
        当前为 HTTP 访问时，浏览器常禁止自动写入剪贴板。请<strong>全选</strong>下方内容后按
        <kbd>Ctrl</kbd>+<kbd>C</kbd>（Mac：<kbd>Cmd</kbd>+<kbd>C</kbd>）。
      </p>
      <el-input
        ref="copyInputRef"
        v-model="copyText"
        type="textarea"
        :rows="3"
        readonly
        class="copy-token-input"
      />
      <template #footer>
        <el-button @click="copyVisible = false">关闭</el-button>
        <el-button type="primary" @click="retryCopyFromDialog">再试自动复制</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { UserFilled, Search, Plus } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'

const activeTab = ref('password')
const guideExpanded = ref(['guide'])
const profile = ref({ username: '', roles: [] })
const tokens = ref([])
const copyVisible = ref(false)
const copyText = ref('')
const copyInputRef = ref(null)
const tokenKeyword = ref('')
const appliedKeyword = ref('')

const pwd = reactive({ old: '', new: '', confirm: '' })
const pwdLoading = ref(false)
const genLoading = ref(false)

const remarkVisible = ref(false)
const remarkLoading = ref(false)
const remarkForm = reactive({ index: -1, remark: '' })

const filteredTokens = computed(() => {
  const kw = appliedKeyword.value.trim().toLowerCase()
  if (!kw) return tokens.value
  return tokens.value.filter(
    (t) =>
      t.token?.toLowerCase().includes(kw) ||
      (t.remark || '').toLowerCase().includes(kw)
  )
})

const apiBase = computed(() => {
  if (import.meta.env.VITE_API_BASE) return import.meta.env.VITE_API_BASE.replace(/\/$/, '')
  return window.location.origin
})

function roleTagType(role) {
  if (role === 'master') return 'primary'
  if (role === 'admin') return 'warning'
  return 'info'
}

function maskToken(token) {
  if (!token) return '-'
  if (token.length <= 20) return token
  return `${token.slice(0, 16)}...${token.slice(-8)}`
}

function legacyExecCopy(text) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  ta.setSelectionRange(0, text.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(ta)
  return ok
}

function openManualCopy(text) {
  copyText.value = text
  copyVisible.value = true
}

function focusCopyInput() {
  nextTick(() => {
    const inst = copyInputRef.value
    const el = inst?.textarea || inst?.$el?.querySelector?.('textarea')
    if (el) {
      el.focus()
      el.select()
    }
  })
}

async function retryCopyFromDialog() {
  const text = copyText.value || ''
  if (!text) return
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      ElMessage.success('已复制到剪贴板')
      copyVisible.value = false
      return
    }
  } catch {
    // continue
  }
  if (legacyExecCopy(text)) {
    ElMessage.success('已复制到剪贴板')
    copyVisible.value = false
    return
  }
  focusCopyInput()
  ElMessage.warning('自动复制仍不可用，请手动 Ctrl/Cmd+C')
}

async function copyToken(token) {
  const text = token || ''
  if (!text) {
    ElMessage.warning('无可复制内容')
    return
  }
  // 非安全上下文（如 http://公网IP）优先弹窗手动复制，避免静默失败
  if (!window.isSecureContext) {
    openManualCopy(text)
    return
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      ElMessage.success('已复制到剪贴板')
      return
    }
  } catch {
    // fall through
  }
  if (legacyExecCopy(text)) {
    ElMessage.success('已复制到剪贴板')
    return
  }
  openManualCopy(text)
}

function applyTokenFilter() {
  appliedKeyword.value = tokenKeyword.value
}

function resetTokenFilter() {
  tokenKeyword.value = ''
  appliedKeyword.value = ''
}

async function load() {
  const res = await getCgi('/pages/system_me.cgi')
  profile.value = {
    username: res.data?.username || '',
    roles: res.data?.roles || [],
  }
  tokens.value = (res.data?.tokens || []).map((t, i) => ({ ...t, _index: i }))
}

async function changePwd() {
  if (!pwd.old) {
    ElMessage.warning('请输入旧密码')
    return
  }
  if (!pwd.new) {
    ElMessage.warning('请输入新密码')
    return
  }
  if (pwd.new !== pwd.confirm) {
    ElMessage.error('两次密码不一致')
    return
  }
  pwdLoading.value = true
  try {
    const res = await postCgi('/pages/system_me.cgi', {
      action: 'change_password',
      old_password: pwd.old,
      new_password: pwd.new,
    })
    ElMessage.success(res.msg || '密码修改成功')
    pwd.old = ''
    pwd.new = ''
    pwd.confirm = ''
  } finally {
    pwdLoading.value = false
  }
}

async function genToken() {
  genLoading.value = true
  try {
    const res = await postCgi('/pages/system_me.cgi', { action: 'generate_token' })
    tokens.value = (res.data || []).map((t, i) => ({ ...t, _index: i }))
    ElMessage.success(res.msg || 'Token 生成成功')
  } finally {
    genLoading.value = false
  }
}

function openRemark(row) {
  remarkForm.index = row._index
  remarkForm.remark = row.remark || ''
  remarkVisible.value = true
}

async function saveRemark() {
  remarkLoading.value = true
  try {
    const res = await postCgi('/pages/system_me.cgi', {
      action: 'update_remark',
      index: remarkForm.index,
      remark: remarkForm.remark.trim(),
    })
    tokens.value = (res.data || []).map((t, i) => ({ ...t, _index: i }))
    ElMessage.success('备注已保存')
    remarkVisible.value = false
  } finally {
    remarkLoading.value = false
  }
}

async function delToken(row) {
  await ElMessageBox.confirm('确定删除该 Token？删除后无法恢复。', '提示', { type: 'warning' })
  const res = await postCgi('/pages/system_me.cgi', {
    action: 'delete_token',
    index: row._index,
  })
  tokens.value = (res.data || []).map((t, i) => ({ ...t, _index: i }))
  ElMessage.success('已删除')
}

onMounted(load)
</script>

<style scoped>
.page {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 16px 20px;
  min-height: 400px;
}
.profile-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px 24px;
  background: linear-gradient(135deg, #409eff 0%, #53a8ff 100%);
  border-radius: 12px;
  margin-bottom: 16px;
  color: #fff;
}
.avatar {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  flex-shrink: 0;
}
.profile-name {
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 8px;
}
.profile-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.panel {
  background: #fff;
  border-radius: 12px;
  padding: 4px 20px 20px;
  border: 1px solid #ebeef5;
}
.me-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}
.me-tabs :deep(.el-tabs__item) {
  font-size: 15px;
  height: 48px;
}
.tab-content {
  padding-top: 20px;
}
.section-desc {
  font-size: 13px;
  color: #909399;
  margin-bottom: 20px;
}
.pwd-form {
  max-width: 480px;
}
.token-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.spacer { flex: 1; }
.token-summary {
  font-size: 13px;
  color: #909399;
  margin-bottom: 12px;
}
.token-table {
  width: 100%;
}
.token-table :deep(.el-table__header th) {
  background: #f5f7fa;
  color: #606266;
  font-weight: 600;
}
.token-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.token-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mono {
  font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  color: #606266;
}
.remark-text {
  color: #606266;
  font-size: 13px;
}
.token-guide {
  margin-bottom: 16px;
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 8px;
  overflow: hidden;
  background: var(--gap-bg, #f5f7fa);
}
.token-guide :deep(.el-collapse-item__header) {
  padding: 0 16px;
  font-weight: 600;
  color: var(--gap-text, #303133);
  background: transparent;
  border-bottom: none;
}
.token-guide :deep(.el-collapse-item__wrap) {
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.token-guide :deep(.el-collapse-item__content) {
  padding: 0 16px 16px;
}
.guide-body {
  font-size: 13px;
  color: var(--gap-text-secondary, #606266);
  line-height: 1.7;
}
.guide-body p { margin: 0 0 10px; }
.guide-body h4 {
  margin: 14px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--gap-text, #303133);
}
.guide-body h4:first-of-type { margin-top: 4px; }
.guide-body ul {
  margin: 0;
  padding-left: 1.25em;
}
.guide-body ol.guide-steps {
  margin: 0;
  padding-left: 1.25em;
}
.guide-body ol.guide-steps > li { margin-bottom: 10px; }
.guide-body li { margin-bottom: 4px; }
.guide-body code {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(64, 158, 255, 0.08);
  color: #409eff;
  font-size: 12px;
}
.guide-notes li { color: #909399; }
.code-block {
  margin: 8px 0 0;
  padding: 12px 14px;
  border-radius: 6px;
  background: #282c34;
  color: #abb2bf;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
  white-space: pre-wrap;
  word-break: break-all;
}
.copy-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
}
.copy-hint kbd {
  display: inline-block;
  padding: 1px 6px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #f5f7fa;
  font-size: 12px;
  font-family: Menlo, Monaco, Consolas, monospace;
}
.copy-token-input :deep(textarea) {
  font-family: Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}
</style>
