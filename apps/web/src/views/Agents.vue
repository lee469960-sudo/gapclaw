<template>
  <div class="page">
    <div class="header">
      <el-radio-group v-model="scope" @change="load">
        <el-radio-button value="all">所有</el-radio-button>
        <el-radio-button value="mine">我的</el-radio-button>
      </el-radio-group>
      <div class="header-actions">
        <el-input v-model="keyword" placeholder="搜索 Agent..." clearable style="width:220px" @keyup.enter="doSearch" />
        <el-button @click="doSearch">搜索</el-button>
        <el-button @click="resetSearch">重置</el-button>
        <el-button @click="showTicks">定时列表</el-button>
        <el-button type="primary" @click="openForm()">+ 创建 Agent</el-button>
      </div>
    </div>

    <div v-loading="loading" class="card-grid">
      <div v-for="row in filtered" :key="row.id" class="card">
        <div class="card-head" @click="openSessionDialog(row)">
          <span class="card-title">
            <el-icon v-if="row.visibility === 'private'" class="vis-icon"><Lock /></el-icon>
            <el-icon v-else class="vis-icon pub"><Unlock /></el-icon>
            {{ row.name }}
          </span>
        </div>
        <div class="card-meta">
          <div><span class="label">ID</span> {{ row.id }}</div>
          <div><span class="label">创建人</span> {{ row.creator }}</div>
          <div><span class="label">时间</span> {{ row.created_at || '-' }}</div>
        </div>
        <div class="card-tags">
          <el-tag size="small" :type="row.profile === 'code' ? 'primary' : 'info'">
            {{ row.profile === 'code' ? 'Code Agent' : 'Standard' }}
          </el-tag>
          <template v-if="row.profile === 'code'">
            <el-tag size="small" effect="plain">{{ row.code_project_name || '项目不可访问' }}</el-tag>
            <el-tag
              size="small"
              :type="row.code_project_availability?.ready ? 'success' : 'danger'"
            >
              Manifest {{ projectStatusLabel(row.code_project_availability) }}
            </el-tag>
            <router-link
              v-if="!row.code_project_availability?.ready"
              class="card-project-link"
              to="/code-projects"
              @click.stop
            >
              修复项目配置
            </router-link>
          </template>
          <el-tag v-if="row.sandbox_name" size="small" effect="plain">{{ row.sandbox_name }}</el-tag>
          <el-tag v-if="row.llm_name" size="small" type="success" effect="plain">{{ row.llm_name }}</el-tag>
          <el-tag size="small" type="info">Skill {{ (row.skills || []).length }}</el-tag>
          <el-tag size="small" type="warning">MCP {{ (row.mcps || []).length }}</el-tag>
          <el-tag size="small" type="success">RAG {{ (row.rags || []).length }}</el-tag>
        </div>
        <div class="card-actions">
          <el-button size="small" @click.stop="openMemory(row)">记忆</el-button>
          <el-button size="small" type="primary" plain @click.stop="openForm(row)">编辑</el-button>
          <el-button size="small" type="danger" link @click.stop="onDelete(row)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="!loading && !filtered.length" description="暂无 Agent" />
    </div>

    <!-- 创建/编辑双栏弹窗 -->
    <el-dialog
      v-model="visible"
      :title="form.id ? '编辑Agent' : '创建Agent'"
      width="960px"
      top="4vh"
      class="agent-form-dialog"
      destroy-on-close
    >
      <div class="form-columns">
        <div class="col-left">
          <section class="cfg-block">
            <h3 class="cfg-title">基础配置</h3>
            <el-form :model="form" label-position="top" class="cfg-form">
              <el-form-item label="名称" required>
                <el-input v-model="form.name" placeholder="Agent 名称" />
              </el-form-item>
              <el-form-item label="描述" required>
                <el-input v-model="form.description" type="textarea" :rows="2" placeholder="简要描述" />
              </el-form-item>
              <el-form-item label="运行 Profile">
                <el-radio-group v-model="form.profile">
                  <el-radio value="standard">Standard</el-radio>
                  <el-radio value="code">Code</el-radio>
                </el-radio-group>
              </el-form-item>
              <template v-if="form.profile === 'code'">
                <el-form-item label="Code Project" required>
                  <el-select v-model="form.code_project_id" placeholder="选择已就绪项目" style="width: 100%">
                    <el-option
                      v-for="project in codeProjectOptions"
                      :key="project.id"
                      :label="`${project.name} · ${projectStatusLabel(project.availability)}`"
                      :value="project.id"
                      :disabled="!project.availability?.ready"
                    />
                  </el-select>
                </el-form-item>
                <el-alert
                  :title="selectedCodeProject?.availability?.ready
                    ? '项目已就绪，将使用受管 Workspace、Code Tools 与 Verifier'
                    : projectStatusLabel(selectedCodeProject?.availability)"
                  :type="selectedCodeProject?.availability?.ready ? 'success' : 'warning'"
                  show-icon
                  :closable="false"
                />
                <router-link class="project-link" to="/code-projects">管理 Code Projects</router-link>
              </template>
            </el-form>
          </section>
          <section class="cfg-block cfg-prompt">
            <h3 class="cfg-title">提示词</h3>
            <el-input
              v-model="form.prompt"
              type="textarea"
              :rows="16"
              placeholder="系统提示词"
              class="prompt-input"
            />
          </section>
        </div>

        <div class="col-right">
          <section class="cfg-block">
            <h3 class="cfg-title">
              <el-icon><Grid /></el-icon>
              资源配置
            </h3>
            <div class="res-list">
              <!-- 动作权限 -->
              <div class="res-item" :class="{ open: resourcePanel === 'actions' }">
                <button type="button" class="res-head" @click="toggleResourcePanel('actions')">
                  <el-icon class="res-ico"><Setting /></el-icon>
                  <span class="res-label">动作权限</span>
                  <span class="res-meta">已选{{ selectedActionCount }}个</span>
                  <el-icon class="res-chevron"><ArrowDown /></el-icon>
                </button>
                <div v-show="resourcePanel === 'actions'" class="res-body">
                  <div v-for="group in ACTION_GROUPS" :key="group.title" class="action-group">
                    <div class="action-group-title">{{ group.title }}</div>
                    <el-checkbox-group v-model="form.allowed_actions" class="action-checkboxes">
                      <el-checkbox
                        v-for="item in group.items"
                        :key="item.id"
                        :value="item.id"
                      >
                        {{ item.label }}
                      </el-checkbox>
                    </el-checkbox-group>
                  </div>
                  <div class="action-group">
                    <div class="action-group-title">最终回复 (必选, 始终启用)</div>
                    <el-checkbox :model-value="true" disabled>输出最终回复</el-checkbox>
                  </div>
                  <div class="soft-circuit-row">
                    <span class="soft-circuit-label">软熔断</span>
                    <el-input-number
                      v-model="form.mcp_soft_circuit"
                      :min="1"
                      :max="50"
                      controls-position="right"
                      size="small"
                    />
                    <span class="unit">次</span>
                  </div>
                  <div class="field-hint">MCP 同工具失败达该次数后纠偏提示、不中止任务；默认 5</div>
                  <div class="soft-circuit-row">
                    <span class="soft-circuit-label">工具结果截断</span>
                    <el-input-number
                      v-model="form.tool_result_clip"
                      :min="1"
                      :max="100000"
                      controls-position="right"
                      size="small"
                    />
                    <span class="unit">字符</span>
                  </div>
                  <div class="field-hint">单个工具结果入上下文的截断字符数，同时决定超大 MCP 结果落盘阈值；默认 6000</div>
                </div>
              </div>

              <!-- 沙箱 -->
              <div class="res-item">
                <div class="res-head res-head-static">
                  <el-icon class="res-ico"><Box /></el-icon>
                  <span class="res-label">沙箱</span>
                  <el-select
                    v-model="form.sandbox"
                    placeholder="选择沙箱"
                    filterable
                    class="res-inline-select"
                  >
                    <el-option v-for="s in sandboxes" :key="s.id" :label="s.name" :value="s.id" />
                  </el-select>
                  <el-icon class="res-chevron muted"><ArrowRight /></el-icon>
                </div>
              </div>

              <!-- LLM -->
              <div class="res-item">
                <div class="res-head res-head-static">
                  <el-icon class="res-ico"><Cpu /></el-icon>
                  <span class="res-label">LLM</span>
                  <span class="res-meta">共{{ llms.length }}个</span>
                  <el-select
                    v-model="form.llm"
                    placeholder="选择 LLM"
                    filterable
                    class="res-inline-select"
                  >
                    <el-option-group label="单模型">
                      <el-option v-for="l in singleLlms" :key="l.id" :label="l.name" :value="l.id" />
                    </el-option-group>
                    <el-option-group label="模型组">
                      <el-option v-for="l in groupLlms" :key="l.id" :label="l.name" :value="l.id" />
                    </el-option-group>
                  </el-select>
                  <el-icon class="res-chevron muted"><ArrowRight /></el-icon>
                </div>
              </div>

              <!-- MCP -->
              <div class="res-item" :class="{ open: resourcePanel === 'mcp' }">
                <button type="button" class="res-head" @click="toggleResourcePanel('mcp')">
                  <el-icon class="res-ico"><Connection /></el-icon>
                  <span class="res-label">MCP</span>
                  <span class="res-meta">共{{ mcps.length }}个</span>
                  <span class="res-meta accent">已选{{ (form.mcps || []).length }}个</span>
                  <el-icon class="res-chevron"><ArrowDown /></el-icon>
                </button>
                <div v-show="resourcePanel === 'mcp'" class="res-body">
                  <div class="tag-row">
                    <el-tag
                      v-for="id in form.mcps"
                      :key="id"
                      closable
                      effect="plain"
                      type="primary"
                      @close="removeMcp(id)"
                    >
                      {{ mcpName(id) }}
                    </el-tag>
                    <button type="button" class="add-link" @click="openMcpPicker">+ 添加</button>
                  </div>
                </div>
              </div>

              <!-- HttpMcp -->
              <div class="res-item" :class="{ open: resourcePanel === 'httpmcp' }">
                <button type="button" class="res-head" @click="toggleResourcePanel('httpmcp')">
                  <el-icon class="res-ico"><Connection /></el-icon>
                  <span class="res-label">HTTP 请求代理</span>
                  <span class="res-meta">共{{ httpmcps.length }}个</span>
                  <span class="res-meta accent">已选{{ (form.httpmcps || []).length }}个</span>
                  <el-icon class="res-chevron"><ArrowDown /></el-icon>
                </button>
                <div v-show="resourcePanel === 'httpmcp'" class="res-body">
                  <div class="tag-row">
                    <el-tag
                      v-for="id in form.httpmcps"
                      :key="id"
                      closable
                      effect="plain"
                      type="warning"
                      @close="removeHttpmcp(id)"
                    >
                      {{ httpmcpName(id) }}
                    </el-tag>
                    <button type="button" class="add-link" @click="openHttpmcpPicker">+ 添加</button>
                  </div>
                </div>
              </div>

              <!-- Skills -->
              <div class="res-item" :class="{ open: resourcePanel === 'skills' }">
                <button type="button" class="res-head" @click="toggleResourcePanel('skills')">
                  <el-icon class="res-ico"><Collection /></el-icon>
                  <span class="res-label">Skills</span>
                  <span class="res-meta">共{{ skills.length }}个</span>
                  <span class="res-meta accent">已选{{ (form.skills || []).length }}个</span>
                  <span class="add-link head-add" @click.stop="openSkillPicker">+ 添加</span>
                  <el-icon class="res-chevron"><ArrowDown /></el-icon>
                </button>
                <div v-show="resourcePanel === 'skills'" class="res-body">
                  <div class="tag-row">
                    <el-tag
                      v-for="id in form.skills"
                      :key="id"
                      closable
                      effect="plain"
                      @close="removeSkill(id)"
                    >
                      {{ skillName(id) }}
                    </el-tag>
                    <span v-if="!(form.skills || []).length" class="empty-hint">暂未绑定 Skill</span>
                  </div>
                </div>
              </div>

              <!-- 知识库 -->
              <div class="res-item" :class="{ open: resourcePanel === 'rag' }">
                <button type="button" class="res-head" @click="toggleResourcePanel('rag')">
                  <el-icon class="res-ico"><Notebook /></el-icon>
                  <span class="res-label">知识库</span>
                  <span class="res-meta">共{{ rags.length }}个</span>
                  <span class="res-meta accent">已选{{ (form.rags || []).length }}个</span>
                  <span class="add-link head-add" @click.stop="openRagPicker">+ 添加</span>
                  <el-icon class="res-chevron"><ArrowDown /></el-icon>
                </button>
                <div v-show="resourcePanel === 'rag'" class="res-body">
                  <div class="tag-row">
                    <el-tag
                      v-for="id in form.rags"
                      :key="id"
                      closable
                      effect="plain"
                      type="success"
                      @close="removeRag(id)"
                    >
                      {{ ragName(id) }}
                    </el-tag>
                    <span v-if="!(form.rags || []).length" class="empty-hint">暂未绑定知识库</span>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section class="cfg-block">
            <h3 class="cfg-title">
              <el-icon><User /></el-icon>
              主动等级
            </h3>
            <div class="proactivity-row">
              <el-select v-model="form.proactivity" class="proactivity-select">
                <el-option :value="1" label="1 · 保守" />
                <el-option :value="2" label="2 · 平衡" />
                <el-option :value="3" label="3 · 完全自主" />
              </el-select>
              <p class="proactivity-hint">等级越高，遇到模糊/有风险时越少反问、越倾向直接执行</p>
            </div>
          </section>

          <section class="cfg-block">
            <h3 class="cfg-title">
              <el-icon><Lock /></el-icon>
              权限管理
            </h3>
            <el-radio-group v-model="form.visibility" class="vis-radios">
              <el-radio value="public">公共</el-radio>
              <el-radio value="private">私有</el-radio>
            </el-radio-group>
            <template v-if="form.visibility === 'private'">
              <div class="field-hint">输入允许访问的用户名，多个用逗号分隔，不填写仅本人可见</div>
              <el-input v-model="form.allowedUsersStr" placeholder="如：user1, user2" />
            </template>
          </section>

          <section class="cfg-block cfg-adv">
            <button type="button" class="cfg-title adv-toggle" @click="toggleAdv">
              <el-icon><Setting /></el-icon>
              高级设置
              <el-icon class="res-chevron" :class="{ open: advOpen }"><ArrowDown /></el-icon>
            </button>
            <div v-show="advOpen" class="adv-fields">
              <div class="adv-field">
                <span class="adv-label">最大循环次数</span>
                <div class="adv-input-row">
                  <el-input-number v-model="form.max_iterations" :min="1" :max="500" controls-position="right" size="small" />
                  <span class="unit">次</span>
                </div>
              </div>
              <div class="adv-field">
                <span class="adv-label">历史长度</span>
                <div class="adv-input-row">
                  <el-input-number v-model="form.history_length" :min="1" :max="200" controls-position="right" size="small" />
                  <span class="unit">轮</span>
                </div>
              </div>
              <div class="adv-field">
                <span class="adv-label">会话总结</span>
                <div class="adv-input-row">
                  <el-input-number v-model="form.summary_max_words" :min="100" :max="50000" :step="100" controls-position="right" size="small" />
                  <span class="unit">字</span>
                </div>
              </div>
              <div class="adv-field">
                <span class="adv-label">LLM 超时</span>
                <div class="adv-input-row">
                  <el-input-number v-model="form.llm_timeout" :min="30" :max="7200" :step="60" controls-position="right" size="small" />
                  <span class="unit">秒</span>
                </div>
              </div>
              <div class="adv-field">
                <span class="adv-label">Skill 超时</span>
                <div class="adv-input-row">
                  <el-input-number v-model="form.skill_timeout" :min="30" :max="7200" :step="60" controls-position="right" size="small" />
                  <span class="unit">秒</span>
                </div>
              </div>
              <div class="adv-field">
                <span class="adv-label">Shell 超时</span>
                <div class="adv-input-row">
                  <el-input-number v-model="form.shell_timeout" :min="30" :max="7200" :step="60" controls-position="right" size="small" />
                  <span class="unit">秒</span>
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
      <template #footer>
        <div class="dialog-footer">
          <el-button v-if="form.id" type="danger" plain @click="deleteFromForm">删除</el-button>
          <span class="footer-spacer" />
          <el-button @click="visible = false">取消</el-button>
          <el-button type="primary" @click="save">保存</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 记忆 -->
    <el-dialog v-model="memoryVisible" title="Agent 记忆" width="560px">
      <el-input v-model="memoryContent" type="textarea" :rows="12" placeholder="长期记忆内容" />
      <template #footer>
        <el-button @click="memoryVisible = false">取消</el-button>
        <el-button type="primary" @click="saveMemory">保存</el-button>
      </template>
    </el-dialog>

    <!-- 定时列表 -->
    <el-dialog v-model="ticksVisible" title="定时任务列表" width="720px">
      <el-table :data="ticks" size="small" stripe>
        <el-table-column prop="agent_id" label="Agent ID" width="120" />
        <el-table-column prop="cron" label="Cron" />
        <el-table-column prop="message" label="消息" show-overflow-tooltip />
        <el-table-column prop="enabled" label="启用" width="70">
          <template #default="{ row }">{{ row.enabled ? '是' : '否' }}</template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- Skill 选择 -->
    <el-dialog v-model="skillPickerVisible" title="选择 Skill" width="480px">
      <el-checkbox-group v-model="pickerSkills">
        <div v-for="s in skills" :key="s.id" class="picker-item">
          <el-checkbox :value="s.id">{{ s.name }} ({{ s.id }})</el-checkbox>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="skillPickerVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmSkills">确定</el-button>
      </template>
    </el-dialog>

    <AgentSessionDialog
      v-model="sessionDialogVisible"
      :agent-id="sessionAgent?.id"
      :agent="sessionAgent"
    />

    <!-- MCP 选择 -->
    <el-dialog v-model="mcpPickerVisible" title="选择 MCP" width="480px">
      <el-checkbox-group v-model="pickerMcps">
        <div v-for="m in mcps" :key="m.id" class="picker-item">
          <el-checkbox :value="m.id">
            {{ m.name }} ({{ m.id }})
            <span v-if="m.routing_eligible === false" class="muted">— 不参与自动路由，请补充描述或标签</span>
          </el-checkbox>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="mcpPickerVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmMcps">确定</el-button>
      </template>
    </el-dialog>

    <!-- RAG 知识库选择 -->
    <el-dialog v-model="ragPickerVisible" title="选择知识库" width="480px">
      <el-checkbox-group v-model="pickerRags">
        <div v-for="r in rags" :key="r.id" class="picker-item">
          <el-checkbox :value="r.id">{{ r.name }} ({{ r.id }})</el-checkbox>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="ragPickerVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmRags">确定</el-button>
      </template>
    </el-dialog>

    <!-- HttpMcp 选择 -->
    <el-dialog v-model="httpmcpPickerVisible" title="选择 HTTP 请求代理" width="480px">
      <el-checkbox-group v-model="pickerHttpmcps">
        <div v-for="h in httpmcps" :key="h.id" class="picker-item">
          <el-checkbox :value="h.id">{{ h.name }} ({{ h.id }})</el-checkbox>
        </div>
      </el-checkbox-group>
      <template #footer>
        <el-button @click="httpmcpPickerVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmHttpmcps">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onActivated } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Lock, Unlock, Setting, Grid, Box, Cpu, Connection, Collection, Notebook, ArrowDown, ArrowRight, User } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { cachedGetCgi, invalidateListCache } from '../listCache'
import AgentSessionDialog from '../components/AgentSessionDialog.vue'

defineOptions({ name: 'Agents' })

const DEFAULT_PROMPT = `你是一名专业的XX，收到用户问题后，认真思考，并通过简单的工具调用组合解决/回答用户问题。每次执行任务，在工作区 task/ 下创建以毫秒时间戳为任务ID的文件夹，把过程产物（临时脚本、分片数据、checkpoint 等）全部放在该任务文件夹内；仅把对话需要交付的最终文件写到当前目录（工作区根，或用户选中的文件夹）。路径已相对工作区根，禁止再创建或写入 workplace/ 子目录。`

/** Toggleable actions shown in UI (9 items). `done` is always on, not stored. */
const ACTION_GROUPS = [
  {
    title: 'Skills (通过沙箱执行)',
    items: [
      { id: 'skill_read_md', label: '读取 Skill 说明' },
      { id: 'skill_run_script', label: '执行 Skill 脚本' },
    ],
  },
  {
    title: 'MCP (远程工具)',
    items: [{ id: 'mcp_tool_call', label: '调用 MCP 工具' }],
  },
  {
    title: 'HTTP 请求代理',
    items: [{ id: 'httpmcp_call', label: 'HTTP 请求代理' }],
  },
  {
    title: 'Shell (系统命令)',
    items: [{ id: 'shell', label: '执行 Shell 命令' }],
  },
  {
    title: '文件操作',
    items: [
      { id: 'file_read', label: '读取文件' },
      { id: 'file_write', label: '写入文件' },
      { id: 'file_search', label: '搜索文件内容' },
      { id: 'file_search_replace', label: '精准编辑文件' },
    ],
  },
]

const ALL_TOGGLEABLE_ACTIONS = ACTION_GROUPS.flatMap((g) => g.items.map((i) => i.id))
const KNOWN_ACTION_KEYS = new Set([...ALL_TOGGLEABLE_ACTIONS, 'rag_query', 'done'])

const list = ref([])
const loading = ref(false)
const scope = ref('all')
const keyword = ref('')
const searchKw = ref('')
const visible = ref(false)
const memoryVisible = ref(false)
const ticksVisible = ref(false)
const skillPickerVisible = ref(false)
const mcpPickerVisible = ref(false)
const ragPickerVisible = ref(false)
const httpmcpPickerVisible = ref(false)
const memoryContent = ref('')
const memoryAgentId = ref('')
const ticks = ref([])
const sandboxes = ref([])
const llms = ref([])
const skills = ref([])
const mcps = ref([])
const rags = ref([])
const httpmcps = ref([])
const codeProjects = ref([])
const pickerSkills = ref([])
const pickerMcps = ref([])
const pickerRags = ref([])
const pickerHttpmcps = ref([])
const advOpen = ref(false)
const resourcePanel = ref('')
const sessionDialogVisible = ref(false)
const sessionAgent = ref(null)
const resourcesLoaded = ref(false)
const form = reactive({})

const ADV_DEFAULTS = {
  max_iterations: 150,
  history_length: 30,
  summary_max_words: 5000,
  llm_timeout: 1800,
  skill_timeout: 1800,
  shell_timeout: 1800,
  mcp_soft_circuit: 5,
  tool_result_clip: 6000,
}

const selectedActionCount = computed(() => {
  const selected = form.allowed_actions || []
  return selected.filter((a) => ALL_TOGGLEABLE_ACTIONS.includes(a)).length
})

// react-engine-v14 V12: 模型下拉框同时列出单模型与模型组，按 l.type 拆分。
const singleLlms = computed(() => llms.value.filter((l) => l.type === 'llm'))
const groupLlms = computed(() => llms.value.filter((l) => l.type === 'group'))
const codeProjectOptions = computed(() => {
  const options = [...codeProjects.value]
  if (
    form.profile === 'code'
    && form.code_project_id
    && !options.some((project) => project.id === form.code_project_id)
  ) {
    options.push({
      id: form.code_project_id,
      name: form.code_project_name || '当前绑定',
      availability: form.code_project_availability || {
        ready: false,
        reason: 'code_project_unauthorized',
      },
    })
  }
  return options
})
const selectedCodeProject = computed(() =>
  codeProjectOptions.value.find((project) => project.id === form.code_project_id),
)

function projectStatusLabel(availability) {
  const labels = {
    ready: '已就绪',
    project_disabled: '项目已停用',
    project_environment_not_allowed: '环境不支持',
    manifest_missing: '尚未发布 Manifest',
    manifest_invalid: 'Manifest 无效',
    code_project_unauthorized: '项目不可访问',
  }
  return labels[availability?.reason] || availability?.reason || '请选择项目'
}

function sanitizeAllowedActions(actions) {
  const list = Array.isArray(actions) ? actions : []
  const cleaned = [...new Set(list.filter((a) => KNOWN_ACTION_KEYS.has(a) && a !== 'done'))]
  return cleaned.length ? cleaned : [...ALL_TOGGLEABLE_ACTIONS]
}

const filtered = computed(() => {
  const kw = searchKw.value.trim().toLowerCase()
  if (!kw) return list.value
  return list.value.filter((r) =>
    r.name?.toLowerCase().includes(kw) ||
    r.description?.toLowerCase().includes(kw) ||
    r.id?.toLowerCase().includes(kw)
  )
})

function toggleResourcePanel(name) {
  resourcePanel.value = resourcePanel.value === name ? '' : name
}

function toggleAdv() {
  advOpen.value = !advOpen.value
}

function skillName(id) {
  return skills.value.find((s) => s.id === id)?.name || id
}

function mcpName(id) {
  return mcps.value.find((m) => m.id === id)?.name || id
}

function ragName(id) {
  return rags.value.find((r) => r.id === id)?.name || id
}

function httpmcpName(id) {
  return httpmcps.value.find((h) => h.id === id)?.name || id
}

async function loadResources({ force = false } = {}) {
  if (force) {
    invalidateListCache('/pages/page_agent.cgi')
  }
  const res = await cachedGetCgi('/pages/page_agent.cgi', { action: 'refs' }, force ? 0 : 60000)
  const data = res.data || {}
  sandboxes.value = data.sandboxes || []
  llms.value = data.llms || []
  skills.value = data.skills || []
  mcps.value = data.mcps || []
  rags.value = data.rags || []
  httpmcps.value = data.httpmcps || []
  codeProjects.value = data.code_projects || []
  resourcesLoaded.value = true
}

async function ensureResources() {
  if (resourcesLoaded.value) return
  await loadResources()
}

async function load({ background = false } = {}) {
  if (!background) loading.value = true
  try {
    const res = await cachedGetCgi(
      '/pages/page_agent.cgi',
      { action: 'list', scope: scope.value },
      15000,
    )
    list.value = res.data || []
  } finally {
    if (!background) loading.value = false
  }
}

function doSearch() {
  searchKw.value = keyword.value
}

function resetSearch() {
  keyword.value = ''
  searchKw.value = ''
}

function defaultLlmId() {
  // 默认优先单模型：先 MinMax，再首个单模型；不默认选中模型组。
  const minmax = singleLlms.value.find((l) => l.name === 'MinMax')
  return minmax?.id || singleLlms.value[0]?.id || ''
}

function defaultSandboxId() {
  const dba = sandboxes.value.find((s) => s.name === 'dba-sandbox')
  return dba?.id || sandboxes.value[0]?.id || ''
}

function openSessionDialog(row) {
  sessionAgent.value = row
  sessionDialogVisible.value = true
}

async function openForm(row) {
  // Always refresh refs so newly created sandbox/MCP/skill appear immediately
  await loadResources({ force: true })
  if (row) {
    const detail = await getCgi('/pages/page_agent.cgi', { action: 'get', id: row.id })
    const currentRow = detail.data || row
    Object.assign(form, {
      ...ADV_DEFAULTS,
      ...currentRow,
      skills: [...(currentRow.skills || [])],
      mcps: [...(currentRow.mcps || [])],
      rags: [...(currentRow.rags || [])],
      httpmcps: [...(currentRow.httpmcps || [])],
      allowed_actions: sanitizeAllowedActions(currentRow.allowed_actions),
      profile: currentRow.profile || 'standard',
      code_project_id: currentRow.code_project_id || '',
      allowedUsersStr: (currentRow.allowed_users || []).join(','),
      max_iterations: currentRow.max_iterations ?? ADV_DEFAULTS.max_iterations,
      history_length: currentRow.history_length ?? ADV_DEFAULTS.history_length,
      summary_max_words: currentRow.summary_max_words ?? ADV_DEFAULTS.summary_max_words,
      llm_timeout: currentRow.llm_timeout ?? ADV_DEFAULTS.llm_timeout,
      skill_timeout: currentRow.skill_timeout ?? ADV_DEFAULTS.skill_timeout,
      shell_timeout: currentRow.shell_timeout ?? ADV_DEFAULTS.shell_timeout,
      mcp_soft_circuit: currentRow.mcp_soft_circuit ?? ADV_DEFAULTS.mcp_soft_circuit,
      tool_result_clip: currentRow.tool_result_clip ?? ADV_DEFAULTS.tool_result_clip,
    })
  } else {
    Object.assign(form, {
      id: '',
      name: '',
      description: '',
      prompt: DEFAULT_PROMPT,
      llm: defaultLlmId(),
      sandbox: defaultSandboxId(),
      skills: [],
      mcps: [],
      rags: [],
      httpmcps: [],
      allowed_actions: [...ALL_TOGGLEABLE_ACTIONS],
      profile: 'standard',
      code_project_id: '',
      proactivity: 2,
      visibility: 'private',
      allowedUsersStr: '',
      ...ADV_DEFAULTS,
    })
  }
  advOpen.value = false
  resourcePanel.value = ''
  visible.value = true
}

async function save() {
  if (!form.name?.trim()) {
    ElMessage.warning('请填写名称')
    return
  }
  if (!form.description?.trim()) {
    ElMessage.warning('请填写描述')
    return
  }
  if (form.profile === 'code' && !selectedCodeProject.value?.availability?.ready) {
    ElMessage.warning(projectStatusLabel(selectedCodeProject.value?.availability))
    return
  }
  const allowed_users = form.allowedUsersStr
    ? form.allowedUsersStr.split(',').map((s) => s.trim()).filter(Boolean)
    : []
  await postCgi('/pages/page_agent.cgi', {
    action: form.id ? 'update' : 'create',
    id: form.id || undefined,
    name: form.name,
    description: form.description,
    profile: form.profile || 'standard',
    code_project_id: form.profile === 'code' ? form.code_project_id : '',
    prompt: form.prompt,
    llm: form.llm || undefined,
    sandbox: form.sandbox || undefined,
    skills: form.skills,
    mcps: form.mcps,
    rags: form.rags,
    httpmcps: form.httpmcps,
    proactivity: form.proactivity,
    visibility: form.visibility,
    allowed_users,
    max_iterations: form.max_iterations,
    history_length: form.history_length,
    summary_max_words: form.summary_max_words,
    llm_timeout: form.llm_timeout,
    skill_timeout: form.skill_timeout,
    shell_timeout: form.shell_timeout,
    mcp_soft_circuit: form.mcp_soft_circuit,
    tool_result_clip: form.tool_result_clip,
    allowed_actions: sanitizeAllowedActions(form.allowed_actions),
  })
  ElMessage.success('保存成功')
  visible.value = false
  invalidateListCache('/pages/page_agent.cgi')
  load()
}

async function onDelete(row) {
  await ElMessageBox.confirm(`确定删除 ${row.name}？`)
  await postCgi('/pages/page_agent.cgi', { action: 'delete', id: row.id })
  invalidateListCache('/pages/page_agent.cgi')
  load()
}

async function deleteFromForm() {
  if (!form.id) return
  await ElMessageBox.confirm(`确定删除 ${form.name || form.id}？`)
  await postCgi('/pages/page_agent.cgi', { action: 'delete', id: form.id })
  ElMessage.success('已删除')
  visible.value = false
  invalidateListCache('/pages/page_agent.cgi')
  load()
}

function openMemory(row) {
  memoryAgentId.value = row.id
  memoryContent.value = row.memory || ''
  memoryVisible.value = true
  if (!memoryContent.value) {
    postCgi('/pages/page_agent.cgi', { action: 'get_memory', id: row.id }).then((res) => {
      memoryContent.value = res.data?.content || ''
    })
  }
}

async function saveMemory() {
  await postCgi('/pages/page_agent.cgi', {
    action: 'save_memory',
    id: memoryAgentId.value,
    content: memoryContent.value,
  })
  ElMessage.success('记忆已保存')
  memoryVisible.value = false
}

async function showTicks() {
  const res = await postCgi('/pages/page_agent.cgi', { action: 'list_all_ticks' })
  ticks.value = res.data || []
  ticksVisible.value = true
}

function removeSkill(id) {
  form.skills = form.skills.filter((s) => s !== id)
}

function removeMcp(id) {
  form.mcps = form.mcps.filter((m) => m !== id)
}

function removeRag(id) {
  form.rags = form.rags.filter((r) => r !== id)
}

function removeHttpmcp(id) {
  form.httpmcps = form.httpmcps.filter((h) => h !== id)
}

function openSkillPicker() {
  ensureResources().then(() => {
    pickerSkills.value = [...(form.skills || [])]
    resourcePanel.value = 'skills'
    skillPickerVisible.value = true
  })
}

function openMcpPicker() {
  ensureResources().then(() => {
    pickerMcps.value = [...(form.mcps || [])]
    resourcePanel.value = 'mcp'
    mcpPickerVisible.value = true
  })
}

function openRagPicker() {
  ensureResources().then(() => {
    pickerRags.value = [...(form.rags || [])]
    resourcePanel.value = 'rag'
    ragPickerVisible.value = true
  })
}

function openHttpmcpPicker() {
  ensureResources().then(() => {
    pickerHttpmcps.value = [...(form.httpmcps || [])]
    resourcePanel.value = 'httpmcp'
    httpmcpPickerVisible.value = true
  })
}

function confirmSkills() {
  form.skills = [...pickerSkills.value]
  skillPickerVisible.value = false
}

function confirmMcps() {
  form.mcps = [...pickerMcps.value]
  mcpPickerVisible.value = false
}

function confirmRags() {
  form.rags = [...pickerRags.value]
  ragPickerVisible.value = false
}

function confirmHttpmcps() {
  form.httpmcps = [...pickerHttpmcps.value]
  httpmcpPickerVisible.value = false
}

onMounted(() => {
  load()
  loadResources().catch(() => {})
})

onActivated(() => {
  load({ background: true })
  resourcesLoaded.value = false
})
</script>

<style scoped>
.page {
  background: var(--gap-card-bg);
  border-radius: 8px;
  padding: 16px;
  min-height: 400px;
}
.header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
.header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.card-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
@media (max-width: 1200px) { .card-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 768px) { .card-grid { grid-template-columns: 1fr; } }
.card {
  border: 1px solid var(--gap-card-border);
  border-radius: 8px;
  padding: 16px;
  background: var(--gap-card-bg);
  transition: box-shadow 0.2s, background 0.2s, border-color 0.2s;
}
.card:hover { box-shadow: 0 2px 12px var(--gap-shadow); }
.card-head { cursor: pointer; margin-bottom: 10px; }
.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--gap-text);
  display: flex;
  align-items: center;
  gap: 6px;
}
.vis-icon { color: var(--gap-text-muted); }
.vis-icon.pub { color: var(--gap-accent); }
.card-meta { font-size: 13px; color: var(--gap-text-secondary); line-height: 1.8; margin-bottom: 10px; }
.card-meta .label { color: var(--gap-text-muted); margin-right: 6px; }
.card-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.card-project-link { font-size: 12px; line-height: 24px; }
.card-actions {
  display: flex;
  gap: 8px;
  border-top: 1px solid var(--gap-card-border);
  padding-top: 12px;
}

.form-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 28px;
  max-height: min(72vh, 780px);
  overflow: auto;
  padding-right: 4px;
}
.col-left, .col-right { min-width: 0; display: flex; flex-direction: column; gap: 18px; }

.cfg-block { margin: 0; }
.cfg-title {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--gap-text);
  display: flex;
  align-items: center;
  gap: 6px;
}
.cfg-title .el-icon { color: var(--gap-text-secondary); }
.cfg-form :deep(.el-form-item) { margin-bottom: 12px; }
.cfg-form :deep(.el-form-item__label) {
  font-weight: 500;
  color: var(--gap-text-secondary);
}
.project-link { display: inline-block; margin-top: 8px; }
.prompt-input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.55;
}

.res-list {
  border: 1px solid var(--gap-card-border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--gap-input-bg);
}
.res-item {
  border-bottom: 1px solid var(--gap-card-border);
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.res-item:last-child { border-bottom: none; }
.res-item.open {
  background: color-mix(in srgb, var(--gap-primary) 12%, var(--gap-input-bg));
  box-shadow: inset 3px 0 0 var(--gap-primary);
}
.res-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 11px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  color: var(--gap-text);
  font-size: 13px;
  transition: background 0.15s ease;
}
.res-head:hover { background: var(--gap-hover-bg); }
.res-item.open .res-head:hover { background: transparent; }
.res-head-static { cursor: default; }
.res-head-static:hover { background: transparent; }
.res-ico { color: var(--gap-text-muted); flex-shrink: 0; }
.res-label { font-weight: 500; flex-shrink: 0; min-width: 56px; }
.res-meta { color: var(--gap-text-muted); font-size: 12px; white-space: nowrap; }
.res-meta.accent { color: var(--gap-primary); }
.res-chevron {
  margin-left: auto;
  color: var(--gap-text-muted);
  transition: transform 0.2s ease;
  flex-shrink: 0;
}
.res-chevron.muted { margin-left: 0; }
.res-item.open > .res-head .res-chevron {
  transform: rotate(180deg);
  color: var(--gap-primary);
}
.res-inline-select {
  flex: 1;
  min-width: 0;
}
.res-inline-select :deep(.el-select__wrapper) {
  box-shadow: none !important;
  background: transparent !important;
  padding-left: 4px;
  padding-right: 4px;
  min-height: 28px;
}
.res-inline-select :deep(.el-select__selected-item),
.res-inline-select :deep(.el-select__placeholder) {
  color: var(--gap-text);
}
.res-inline-select :deep(.el-select__caret) { display: none; }
.res-body {
  padding: 0 12px 12px 36px;
  animation: res-body-in 0.18s ease;
  color: var(--gap-text);
}
@keyframes res-body-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

.action-group { margin-bottom: 10px; }
.action-group-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--gap-text-muted);
  margin-bottom: 4px;
}
.action-checkboxes {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}
.soft-circuit-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.soft-circuit-label {
  font-size: 13px;
  color: var(--gap-text-secondary);
  min-width: 48px;
}

.tag-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.add-link {
  border: none;
  background: none;
  color: var(--gap-primary);
  cursor: pointer;
  font-size: 13px;
  padding: 0 4px;
  line-height: 1.4;
}
.add-link:hover { opacity: 0.85; }
.head-add { margin-left: auto; margin-right: 4px; }
.empty-hint { font-size: 12px; color: var(--gap-text-muted); }

.proactivity-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.proactivity-select { width: 160px; flex-shrink: 0; }
.proactivity-hint {
  margin: 0;
  flex: 1;
  min-width: 160px;
  font-size: 12px;
  color: var(--gap-text-muted);
  line-height: 1.5;
}

.vis-radios { margin-bottom: 8px; }
.field-hint {
  font-size: 12px;
  color: var(--gap-text-muted);
  line-height: 1.5;
  margin: 0 0 8px;
}

.cfg-adv .adv-toggle {
  width: 100%;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 0;
  margin-bottom: 0;
  color: inherit;
}
.cfg-adv .adv-toggle .res-chevron { margin-left: auto; }
.cfg-adv .adv-toggle .res-chevron.open {
  transform: rotate(180deg);
  color: var(--gap-primary);
}
.adv-fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 0 0;
  animation: res-body-in 0.18s ease;
}
.adv-field {
  display: flex;
  align-items: center;
  gap: 12px;
}
.adv-label {
  width: 96px;
  flex-shrink: 0;
  font-size: 13px;
  color: var(--gap-text-secondary);
}
.adv-input-row { display: flex; align-items: center; gap: 8px; flex: 1; }
.adv-input-row .el-input-number { flex: 1; width: auto; }
.unit { color: var(--gap-text-muted); font-size: 13px; white-space: nowrap; }

.dialog-footer {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 8px;
}
.footer-spacer { flex: 1; }
.picker-item { padding: 6px 0; }

@media (max-width: 768px) {
  .form-columns { grid-template-columns: 1fr; max-height: none; }
}
</style>
