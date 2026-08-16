<template>
  <el-dialog
    v-model="visible"
    :title="title"
    width="80%"
    top="5vh"
    destroy-on-close
    class="wp-preview-dialog"
    @closed="onClosed"
  >
    <div v-loading="loading" class="preview-wrap">
      <el-alert
        v-if="excelTruncated"
        type="info"
        :closable="false"
        show-icon
        class="excel-tip"
        title="表格较大，仅显示前 500 行 × 50 列"
      />
      <el-alert
        v-if="excelLegacyMsg"
        type="warning"
        :closable="false"
        show-icon
        class="excel-tip"
        :title="excelLegacyMsg"
      />
      <template v-if="mode === 'excel' && excelSheets.length">
        <el-tabs v-model="activeSheet" class="excel-tabs">
          <el-tab-pane
            v-for="(sheet, sheetIndex) in excelSheets"
            :key="`${sheet.name}-${sheetIndex}`"
            :label="sheet.name"
            :name="String(sheetIndex)"
          >
            <div class="excel-table-wrap">
              <table class="excel-table">
                <tbody>
                  <tr v-for="(row, rowIndex) in sheet.rows" :key="rowIndex">
                    <td class="row-index">{{ rowIndex + 1 }}</td>
                    <td v-for="(cell, colIndex) in row" :key="colIndex">
                      <el-input
                        v-if="editable"
                        v-model="excelSheets[sheetIndex].rows[rowIndex][colIndex]"
                        size="small"
                      />
                      <span v-else>{{ cell }}</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
      <div v-else-if="mode === 'markdown' && !editable" class="md-body" v-html="markdownHtml" />
      <iframe
        v-else-if="mode === 'pdf'"
        :src="pdfUrl"
        class="pdf-frame"
        title="PDF 预览"
      />
      <CodeEditorPanel
        v-else-if="mode === 'code'"
        v-model="textContent"
        :language="codeLanguage"
        :readonly="!editable"
      />
      <el-input
        v-else-if="editable"
        v-model="textContent"
        type="textarea"
        :rows="22"
      />
      <pre v-else class="text-preview">{{ textContent }}</pre>
    </div>
    <template v-if="editable && !excelLegacyMsg" #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { marked } from 'marked'
import api from '../api'
import { getCgi } from '../api'
import CodeEditorPanel from './CodeEditorPanel.vue'
import { languageIdForFilename } from '../utils/codeLanguage'

marked.setOptions({ breaks: true, gfm: true })

const props = defineProps({
  modelValue: Boolean,
  title: { type: String, default: '' },
  agentId: { type: String, required: true },
  path: { type: String, default: '' },
  mode: { type: String, default: 'text' },
  editable: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'save'])

const loading = ref(false)
const textContent = ref('')
const pdfUrl = ref('')
const excelSheets = ref([])
const excelTruncated = ref(false)
const excelLegacyMsg = ref('')
const activeSheet = ref('0')

const codeLanguage = computed(() => languageIdForFilename(props.title || props.path))

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const markdownHtml = computed(() => {
  if (props.mode !== 'markdown') return ''
  return marked.parse(textContent.value || '')
})

function revokePdfUrl() {
  if (pdfUrl.value) {
    URL.revokeObjectURL(pdfUrl.value)
    pdfUrl.value = ''
  }
}

function resetState() {
  revokePdfUrl()
  textContent.value = ''
  excelSheets.value = []
  excelTruncated.value = false
  excelLegacyMsg.value = ''
  activeSheet.value = '0'
}

async function loadContent() {
  if (!props.path || !props.agentId) return
  loading.value = true
  resetState()
  try {
    if (props.mode === 'pdf') {
      const res = await api.get('/pages/page_agent_chat.cgi', {
        params: {
          action: 'download_workplace',
          agent_id: props.agentId,
          path: props.path,
        },
        responseType: 'blob',
      })
      pdfUrl.value = URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }))
      return
    }
    const res = await getCgi('/pages/page_agent_chat.cgi', {
      action: 'view_workplace',
      agent_id: props.agentId,
      path: props.path,
    })
    const payload = res.data || {}
    if (payload.type === 'excel') {
      excelSheets.value = (payload.sheets || []).map((sheet) => ({
        name: sheet.name || 'Sheet1',
        rows: (sheet.rows || []).map((row) => [...row]),
      }))
      excelTruncated.value = !!payload.truncated
      return
    }
    if (payload.type === 'excel_legacy') {
      excelLegacyMsg.value = payload.msg || '请下载后查看'
      return
    }
    if (payload.ok === false) {
      textContent.value = payload.msg || '无法预览'
      return
    }
    textContent.value = payload.content ?? ''
  } finally {
    loading.value = false
  }
}

function onSave() {
  if (props.mode === 'excel') {
    emit('save', { type: 'excel', sheets: excelSheets.value })
    return
  }
  emit('save', textContent.value)
}

function onClosed() {
  resetState()
}

watch(
  () => [props.modelValue, props.path, props.mode],
  ([open]) => {
    if (open) loadContent()
  },
)
</script>

<style scoped>
.preview-wrap {
  min-height: 360px;
  max-height: calc(100vh - 220px);
  overflow: auto;
}
.excel-tip {
  margin-bottom: 12px;
}
.excel-tabs :deep(.el-tabs__content) {
  padding-top: 8px;
}
.excel-table-wrap {
  overflow: auto;
  max-height: calc(100vh - 280px);
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  border-radius: 6px;
}
.excel-table {
  border-collapse: collapse;
  min-width: 100%;
  font-size: 13px;
}
.excel-table td {
  border: 1px solid var(--el-border-color-lighter, #ebeef5);
  padding: 4px 8px;
  min-width: 100px;
  vertical-align: top;
  background: var(--gap-card-bg, #fff);
}
.excel-table .row-index {
  min-width: 48px;
  width: 48px;
  text-align: center;
  color: var(--gap-text-muted, #909399);
  background: var(--gap-hover-bg, #f5f7fa);
  position: sticky;
  left: 0;
}
.excel-table :deep(.el-input__wrapper) {
  box-shadow: none;
  padding: 0 4px;
}
.pdf-frame {
  width: 100%;
  height: calc(100vh - 220px);
  min-height: 480px;
  border: none;
  background: #525659;
}
.text-preview {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  padding: 12px;
  background: #fafafa;
  border-radius: 6px;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
}
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3) {
  margin: 1em 0 0.5em;
  line-height: 1.3;
}
.md-body :deep(h1) { font-size: 1.6em; border-bottom: 1px solid #eee; padding-bottom: 0.3em; }
.md-body :deep(h2) { font-size: 1.35em; }
.md-body :deep(h3) { font-size: 1.15em; }
.md-body :deep(p),
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 0.6em 0;
  line-height: 1.7;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 1.5em;
}
.md-body :deep(code) {
  background: #f5f7fa;
  padding: 0.15em 0.4em;
  border-radius: 4px;
  font-size: 0.9em;
}
.md-body :deep(pre) {
  background: #282c34;
  color: #abb2bf;
  padding: 12px 16px;
  border-radius: 6px;
  overflow: auto;
}
.md-body :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}
.md-body :deep(blockquote) {
  margin: 0.8em 0;
  padding: 0.4em 1em;
  border-left: 4px solid #409eff;
  background: #f0f7ff;
  color: #606266;
}
.md-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 8px 12px;
}
.md-body :deep(th) {
  background: #f5f7fa;
}
</style>
