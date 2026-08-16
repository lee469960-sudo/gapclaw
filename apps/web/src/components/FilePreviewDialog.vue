<template>
  <el-dialog
    v-model="visible"
    :title="title"
    width="80%"
    top="5vh"
    destroy-on-close
    class="file-preview-dialog"
    @closed="onClosed"
  >
    <div v-loading="loading" class="preview-wrap">
      <el-alert
        v-if="tip"
        type="info"
        :closable="false"
        show-icon
        class="tip"
        :title="tip"
      />
      <el-alert
        v-if="errorMsg"
        type="warning"
        :closable="false"
        show-icon
        class="tip"
        :title="errorMsg"
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
                    <td v-for="(cell, colIndex) in row" :key="colIndex">{{ cell }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>

      <div v-else-if="mode === 'docx'" class="docx-body" v-html="docxHtml" />

      <div v-else-if="mode === 'pptx'" class="pptx-wrap">
        <div v-for="slide in pptSlides" :key="slide.index" class="ppt-slide">
          <div class="ppt-slide-title">幻灯片 {{ slide.index }} · {{ slide.title }}</div>
          <ul>
            <li v-for="(t, i) in slide.texts" :key="i">{{ t }}</li>
          </ul>
        </div>
        <el-empty v-if="!pptSlides.length" description="未提取到幻灯片文字" />
      </div>

      <div v-else-if="mode === 'markdown'" class="md-body" v-html="markdownHtml" />

      <iframe
        v-else-if="mode === 'pdf'"
        :src="blobUrl"
        class="pdf-frame"
        title="PDF 预览"
      />

      <div v-else-if="mode === 'image'" class="img-wrap">
        <img :src="blobUrl" :alt="title" />
      </div>

      <pre v-else-if="mode === 'text'" class="text-preview">{{ textContent }}</pre>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { marked } from 'marked'
import api from '../api'
import { getCgi } from '../api'

marked.setOptions({ breaks: true, gfm: true })

const props = defineProps({
  modelValue: Boolean,
  title: { type: String, default: '' },
  path: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const loading = ref(false)
const mode = ref('text')
const tip = ref('')
const errorMsg = ref('')
const textContent = ref('')
const docxHtml = ref('')
const blobUrl = ref('')
const excelSheets = ref([])
const pptSlides = ref([])
const activeSheet = ref('0')

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const markdownHtml = computed(() => marked.parse(textContent.value || ''))

function revokeBlob() {
  if (blobUrl.value) {
    URL.revokeObjectURL(blobUrl.value)
    blobUrl.value = ''
  }
}

function resetState() {
  revokeBlob()
  mode.value = 'text'
  tip.value = ''
  errorMsg.value = ''
  textContent.value = ''
  docxHtml.value = ''
  excelSheets.value = []
  pptSlides.value = []
  activeSheet.value = '0'
}

async function fetchBlob(mime) {
  const res = await api.get('/pages/page_files.cgi', {
    params: { action: 'download', path: props.path },
    responseType: 'blob',
  })
  blobUrl.value = URL.createObjectURL(new Blob([res.data], { type: mime }))
}

async function loadContent() {
  if (!props.path) return
  loading.value = true
  resetState()
  try {
    const res = await getCgi('/pages/page_files.cgi', {
      action: 'preview',
      path: props.path,
    })
    const payload = res.data || {}
    if (payload.ok === false || payload.type === 'unsupported') {
      errorMsg.value = payload.msg || '无法预览'
      mode.value = 'text'
      return
    }
    const type = payload.type || 'text'
    mode.value = type
    if (payload.truncated) {
      tip.value =
        type === 'pptx'
          ? '幻灯片较多，仅显示前若干页文字摘要'
          : '内容较大，仅显示部分行/列'
    }
    if (type === 'pdf') {
      await fetchBlob('application/pdf')
      return
    }
    if (type === 'image') {
      await fetchBlob(payload.mime || 'application/octet-stream')
      return
    }
    if (type === 'excel') {
      excelSheets.value = (payload.sheets || []).map((sheet) => ({
        name: sheet.name || 'Sheet1',
        rows: sheet.rows || [],
      }))
      return
    }
    if (type === 'docx') {
      docxHtml.value = payload.html || '<p>（空文档）</p>'
      return
    }
    if (type === 'pptx') {
      pptSlides.value = payload.slides || []
      return
    }
    if (type === 'markdown') {
      textContent.value = payload.content || ''
      return
    }
    textContent.value = payload.content || payload.msg || ''
  } catch {
    errorMsg.value = '预览加载失败'
  } finally {
    loading.value = false
  }
}

function onClosed() {
  resetState()
}

watch(
  () => [props.modelValue, props.path],
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
.tip { margin-bottom: 12px; }
.excel-tabs :deep(.el-tabs__content) { padding-top: 8px; }
.excel-table-wrap {
  overflow: auto;
  max-height: calc(100vh - 280px);
  border: 1px solid #ebeef5;
  border-radius: 6px;
}
.excel-table {
  border-collapse: collapse;
  min-width: 100%;
  font-size: 13px;
}
.excel-table td {
  border: 1px solid #ebeef5;
  padding: 4px 8px;
  min-width: 100px;
  vertical-align: top;
}
.excel-table .row-index {
  min-width: 48px;
  width: 48px;
  text-align: center;
  color: #909399;
  background: #f5f7fa;
  position: sticky;
  left: 0;
}
.pdf-frame {
  width: 100%;
  height: calc(100vh - 220px);
  min-height: 480px;
  border: none;
  background: #525659;
}
.img-wrap {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 320px;
  background: #f5f7fa;
  border-radius: 8px;
  padding: 16px;
}
.img-wrap img {
  max-width: 100%;
  max-height: calc(100vh - 260px);
  object-fit: contain;
}
.text-preview {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  padding: 12px;
  background: #fafafa;
  border-radius: 6px;
  font-size: 14px;
  line-height: 1.6;
}
.docx-body,
.md-body {
  padding: 4px 8px;
  line-height: 1.7;
  color: #303133;
}
.docx-body :deep(h1),
.docx-body :deep(h2),
.docx-body :deep(h3),
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3) {
  margin: 1em 0 0.5em;
  line-height: 1.3;
}
.docx-body :deep(p),
.md-body :deep(p) {
  margin: 0.5em 0;
}
.docx-body :deep(table),
.md-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.8em 0;
}
.docx-body :deep(th),
.docx-body :deep(td),
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 8px 12px;
}
.docx-body :deep(th),
.md-body :deep(th) {
  background: #f5f7fa;
}
.pptx-wrap { display: flex; flex-direction: column; gap: 12px; }
.ppt-slide {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px 16px;
  background: #fafafa;
}
.ppt-slide-title {
  font-weight: 600;
  margin-bottom: 8px;
  color: #303133;
}
.ppt-slide ul {
  margin: 0;
  padding-left: 1.2em;
  color: #606266;
  line-height: 1.6;
}
</style>
