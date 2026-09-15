<template>
  <div class="login-page">
    <div class="login-theme">
      <ThemeSwitch />
    </div>
    <div class="login-card">
      <img :src="siteLogo" class="login-logo" alt="" />
      <h1>{{ siteTitle }}</h1>
      <p class="sub">{{ siteSub }}</p>
      <el-form @submit.prevent="onLogin">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="form.password" type="password" placeholder="密码" show-password />
        </el-form-item>
        <el-form-item>
          <div class="captcha-row">
            <el-input
              v-model="form.captcha"
              placeholder="验证码"
              maxlength="4"
              class="captcha-input"
            />
            <button type="button" class="captcha-box" :title="'点击刷新'" @click="loadCaptcha">
              {{ captchaCode || '····' }}
            </button>
          </div>
        </el-form-item>
        <el-button type="primary" native-type="submit" style="width:100%" :loading="loading">登 录</el-button>
      </el-form>
      <p v-if="footer" class="footer">{{ footer }}</p>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { postCgi } from '../api'
import { clearShell, setLoginExpiry } from '../session'
import { applySiteBrand, loadPublicBrand, logoUrl } from '../branding'
import ThemeSwitch from '../components/ThemeSwitch.vue'

const router = useRouter()
const loading = ref(false)
const captchaCode = ref('')
const form = reactive({ username: 'admin', password: 'admin123', captcha: '' })
const siteTitle = ref('GAP')
const siteSub = ref('智能工作台')
const siteLogo = ref(logoUrl(''))
const footer = ref('')

onMounted(async () => {
  await loadCaptcha()
  const data = await loadPublicBrand()
  if (data) {
    const name = data.site_name || 'GAP — 智能工作台'
    siteLogo.value = logoUrl(data.site_logo)
    footer.value = data.footer || ''
    const parts = name.split(/[—\-–]/)
    siteTitle.value = parts[0]?.trim() || name
    siteSub.value = parts.slice(1).join(' ').trim() || '智能工作台'
  }
})

async function loadCaptcha() {
  try {
    const res = await fetch('/captcha.cgi', { credentials: 'include' })
    const data = await res.json()
    if (data.code === 0 && data.data?.code) {
      captchaCode.value = data.data.code
      form.captcha = ''
    }
  } catch {
    captchaCode.value = ''
  }
}

async function onLogin() {
  if (loading.value) return
  if (!form.captcha?.trim()) {
    ElMessage.warning('请输入验证码')
    return
  }
  loading.value = true
  try {
    clearShell()
    await postCgi('/login.cgi', form)
    setLoginExpiry(Date.now() + 24 * 3600 * 1000)
    router.push('/')
  } catch {
    await loadCaptcha()
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a2332 0%, #2d4a3e 50%, #1a2332 100%);
  position: relative;
}
.login-theme {
  position: absolute;
  top: 20px;
  right: 24px;
}
.login-card {
  width: 380px;
  padding: 32px;
  background: var(--gap-card-bg);
  border-radius: 12px;
  box-shadow: 0 8px 32px var(--gap-shadow);
}
.login-logo {
  display: block;
  width: 64px;
  height: 64px;
  object-fit: contain;
  margin: 0 auto 12px;
}
h1 { margin: 0; text-align: center; font-size: 28px; color: var(--gap-text); }
.sub { text-align: center; color: #999; margin: 8px 0 24px; }
.footer { text-align: center; color: #bbb; font-size: 12px; margin-top: 16px; }
.captcha-row { display: flex; gap: 10px; width: 100%; }
.captcha-input { flex: 1; }
.captcha-box {
  width: 108px;
  height: 40px;
  border: 1px solid var(--el-border-color);
  border-radius: 4px;
  background: linear-gradient(135deg, #eef3ff 0%, #f8f9fc 100%);
  color: #1a5fb4;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 6px;
  cursor: pointer;
  user-select: none;
  font-family: 'Courier New', monospace;
}
.captcha-box:hover { border-color: var(--el-color-primary); }
</style>
