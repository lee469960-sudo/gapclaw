<template>
  <div class="page" v-loading="loading">
    <div class="header">
      <div class="header-left">
        <el-icon class="page-icon"><Setting /></el-icon>
        <span class="page-title">站点配置</span>
      </div>
      <el-button type="primary" :loading="saving" @click="save">
        <el-icon><Check /></el-icon>
        保存配置
      </el-button>
    </div>

    <div class="content-grid">
      <!-- 基础设置 -->
      <div class="panel">
        <div class="panel-title">
          <el-icon><EditPen /></el-icon>
          基础信息
        </div>
        <el-form :model="form" label-width="100px" class="site-form">
          <el-form-item label="站点名称">
            <el-input v-model="form.site_name" placeholder="如：GAP 智能工作台" maxlength="64" show-word-limit />
          </el-form-item>
          <el-form-item label="Logo URL">
            <el-input v-model="form.site_logo" placeholder="Logo 图片地址，或上传下方" />
          </el-form-item>
          <el-form-item label="上传 Logo">
            <el-upload
              class="logo-uploader"
              :show-file-list="false"
              accept="image/png,image/jpeg,image/gif,image/webp"
              :auto-upload="false"
              :on-change="onLogoChange"
            >
              <div v-if="logoPreview" class="logo-preview">
                <img :src="logoPreview" alt="logo" />
                <div class="logo-mask">点击更换</div>
              </div>
              <div v-else class="logo-placeholder">
                <el-icon><Plus /></el-icon>
                <span>上传 Logo</span>
              </div>
            </el-upload>
            <div class="field-hint">支持 PNG / JPG / GIF / WebP，建议尺寸 128×128</div>
          </el-form-item>
          <el-form-item label="页脚文字">
            <el-input
              v-model="form.footer"
              type="textarea"
              :rows="3"
              :placeholder="`留空则显示版本 ${form.version || ''}`"
              maxlength="200"
              show-word-limit
            />
            <div class="field-hint">打包版本来自 deploy/gap.version，页脚会自动带上该版本号。</div>
          </el-form-item>
        </el-form>
      </div>

      <!-- 预览 -->
      <div class="panel preview-panel">
        <div class="panel-title">
          <el-icon><View /></el-icon>
          预览
        </div>
        <div class="preview-box">
          <div class="preview-header">
            <img v-if="logoPreview" :src="logoPreview" class="preview-logo" alt="" />
            <div v-else class="preview-logo placeholder">
              <el-icon><Picture /></el-icon>
            </div>
            <div class="preview-title">{{ form.site_name || 'GAP 智能工作台' }}</div>
          </div>
          <div class="preview-body">
            <p class="preview-desc">站点名称与 Logo 将应用于登录页与系统顶栏。</p>
          </div>
          <div class="preview-footer">
            {{ previewFooter }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Setting, Check, EditPen, View, Plus, Picture } from '@element-plus/icons-vue'
import { getCgi, postCgi } from '../api'
import { patchShellSite } from '../session'

const loading = ref(false)
const saving = ref(false)
const form = reactive({
  site_name: '',
  site_logo: '',
  footer: '',
  version: '',
})

function displayFooter(text, version) {
  const ver = String(version || '').trim()
  const t = String(text || '').trim()
  if (!t || /^[vV]?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(t)) return ver
  if (ver && t.includes(ver)) return t
  if (!ver) return t
  return `${t} · ${ver}`
}

function brandingFrom(data) {
  return {
    site_name: data.site_name,
    site_logo: data.site_logo,
    footer: data.footer_display || displayFooter(data.footer, data.version || form.version),
  }
}

const logoPreview = computed(() => {
  const url = form.site_logo
  if (!url) return ''
  if (url.startsWith('http') || url.startsWith('/')) return url
  return url
})

const previewFooter = computed(() => displayFooter(form.footer, form.version) || '© GAP — 智能工作台')

async function load() {
  loading.value = true
  try {
    const res = await getCgi('/pages/page_site.cgi')
    Object.assign(form, {
      site_name: res.data?.site_name ?? '',
      site_logo: res.data?.site_logo ?? '',
      footer: res.data?.footer ?? '',
      version: res.data?.version ?? '',
    })
  } finally {
    loading.value = false
  }
}

function applySiteBranding(data) {
  if (!data) return
  window.dispatchEvent(new CustomEvent('gap-site-updated', { detail: brandingFrom(data) }))
}

async function save() {
  saving.value = true
  try {
    const res = await postCgi('/pages/page_site.cgi', {
      action: 'save',
      site_name: form.site_name,
      site_logo: form.site_logo,
      footer: form.footer,
    })
    if (res.data) {
      Object.assign(form, {
        site_name: res.data.site_name ?? form.site_name,
        site_logo: res.data.site_logo ?? form.site_logo,
        footer: res.data.footer ?? form.footer,
        version: res.data.version ?? form.version,
      })
      const branding = brandingFrom(res.data)
      patchShellSite(branding)
      applySiteBranding(res.data)
    }
    ElMessage.success(res.msg || '保存成功')
  } finally {
    saving.value = false
  }
}

async function onLogoChange(file) {
  if (!file?.raw) return
  const fd = new FormData()
  fd.append('action', 'upload_logo')
  fd.append('file', file.raw)
  try {
    const res = await fetch('/pages/page_site.cgi', { method: 'POST', body: fd, credentials: 'include' })
    const data = await res.json()
    if (data.code !== 0) {
      ElMessage.error(data.msg || '上传失败')
      return
    }
    form.site_logo = data.data?.site_logo || form.site_logo
    const branding = brandingFrom({
      site_name: form.site_name,
      site_logo: form.site_logo,
      footer: form.footer,
      version: form.version,
    })
    patchShellSite(branding)
    applySiteBranding({ ...branding, footer: form.footer, version: form.version, footer_display: branding.footer })
    ElMessage.success('Logo 上传成功，已自动保存')
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  }
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
  margin-bottom: 20px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-icon {
  font-size: 22px;
  color: #409eff;
}
.page-title {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}
.content-grid {
  display: grid;
  grid-template-columns: 1fr 360px;
  gap: 16px;
  align-items: start;
}
@media (max-width: 960px) {
  .content-grid { grid-template-columns: 1fr; }
}
.panel {
  border: 1px solid #ebeef5;
  border-radius: 12px;
  padding: 18px 20px;
  background: #fff;
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 18px;
  padding-bottom: 12px;
  border-bottom: 1px solid #f0f0f0;
}
.site-form { max-width: 520px; }
.field-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 6px;
  line-height: 1.4;
}
.logo-uploader :deep(.el-upload) {
  border: 1px dashed #dcdfe6;
  border-radius: 8px;
  cursor: pointer;
  overflow: hidden;
  transition: border-color 0.2s;
}
.logo-uploader :deep(.el-upload:hover) {
  border-color: #409eff;
}
.logo-preview {
  width: 120px;
  height: 120px;
  position: relative;
}
.logo-preview img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  padding: 8px;
}
.logo-mask {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.2s;
}
.logo-preview:hover .logo-mask { opacity: 1; }
.logo-placeholder {
  width: 120px;
  height: 120px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #909399;
  font-size: 13px;
}
.logo-placeholder .el-icon { font-size: 28px; }
.preview-box {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  overflow: hidden;
  background: #fafbfc;
}
.preview-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  background: #303133;
  color: #fff;
}
.preview-logo {
  width: 36px;
  height: 36px;
  object-fit: contain;
  border-radius: 6px;
  background: #fff;
  padding: 4px;
}
.preview-logo.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
  font-size: 18px;
}
.preview-title {
  font-size: 16px;
  font-weight: 600;
}
.preview-body {
  padding: 20px 18px;
  min-height: 80px;
}
.preview-desc {
  font-size: 13px;
  color: #909399;
  margin: 0;
  line-height: 1.6;
}
.preview-footer {
  padding: 12px 18px;
  border-top: 1px solid #ebeef5;
  font-size: 12px;
  color: #909399;
  text-align: center;
}
</style>
