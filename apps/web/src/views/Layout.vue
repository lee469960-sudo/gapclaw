<template>
  <el-container class="layout">
    <el-header class="header">
      <div class="left">
        <el-icon class="toggle" @click="collapsed = !collapsed"><Fold v-if="!collapsed" /><Expand v-else /></el-icon>
        <img :src="siteLogo" class="brand-logo" alt="" />
        <span class="brand">{{ siteName }}</span>
      </div>
      <div class="right">
        <ThemeSwitch />
        <span class="time">{{ serverTime }}</span>
        <span class="user"><el-icon><User /></el-icon> {{ username }}</span>
        <span class="logout" @click="logout">退出</span>
      </div>
    </el-header>
    <el-container class="body">
      <el-aside :width="collapsed ? '0' : '220px'" class="aside">
        <el-menu
          class="menu"
          :default-active="route.path"
          router
          :background-color="menuBg"
          :text-color="menuText"
          :active-text-color="menuActive"
        >
          <el-sub-menu v-for="group in menuGroups" :key="group.title" :index="group.title">
            <template #title>{{ group.title }}</template>
            <el-menu-item v-for="item in group.items" :key="item.route" :index="item.route">{{ item.label }}</el-menu-item>
          </el-sub-menu>
        </el-menu>
        <div v-if="footerText" class="version">{{ footerText }}</div>
      </el-aside>
      <el-main class="main">
        <router-view v-slot="{ Component }">
          <keep-alive :include="['Agents', 'Sandboxes']">
            <component :is="Component" :key="route.path" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { fetchShell, clearShell } from '../session'
import { applySiteBrand, logoUrl } from '../branding'
import { theme } from '../theme'
import ThemeSwitch from '../components/ThemeSwitch.vue'

const route = useRoute()
const collapsed = ref(false)
const username = ref('')
const siteName = ref('GAP — 智能工作台')
const siteLogo = ref(logoUrl(''))
const footerText = ref('')
const serverTime = ref('')
const menuGroups = ref([])
const menuBg = computed(() => (theme.value === 'dark' ? '#1a1a1a' : '#ffffff'))
const menuText = computed(() => (theme.value === 'dark' ? '#b0b3b8' : '#606266'))
const menuActive = computed(() => (theme.value === 'dark' ? '#5fb878' : '#409eff'))
let timer

function tick() {
  serverTime.value = new Date().toLocaleString('zh-CN', { hour12: false })
}

function applySite(data) {
  if (!data) return
  if (data.site_name) siteName.value = data.site_name
  siteLogo.value = logoUrl(data.site_logo)
  footerText.value = data.footer || ''
  applySiteBrand({ site_name: siteName.value, site_logo: siteLogo.value })
}

function onSiteUpdated(e) {
  applySite(e.detail)
}

onMounted(async () => {
  tick()
  timer = setInterval(tick, 1000)
  window.addEventListener('gap-site-updated', onSiteUpdated)
  try {
    const data = await fetchShell()
    username.value = data.username || 'user'
    applySite(data)
    menuGroups.value = data.menu_groups || []
  } catch {
    window.location.href = '/login'
  }
})

onUnmounted(() => {
  clearInterval(timer)
  window.removeEventListener('gap-site-updated', onSiteUpdated)
})

async function logout() {
  clearShell()
  try {
    await fetch('/logout.cgi', { credentials: 'include' })
  } catch {
    /* 即使接口失败也继续跳转登录页 */
  }
  window.location.href = '/login'
}
</script>

<style scoped>
.layout {
  height: 100vh;
  overflow: hidden;
}
.header {
  background: var(--gap-header);
  color: var(--gap-header-text);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid var(--gap-aside-border);
  box-shadow: 0 1px 4px var(--gap-shadow);
  flex-shrink: 0;
  z-index: 10;
}
.left { display: flex; align-items: center; gap: 12px; }
.brand-logo { width: 28px; height: 28px; object-fit: contain; border-radius: 4px; background: #fff; padding: 2px; }
.brand { font-size: 18px; color: var(--gap-header-text-hover); font-weight: 600; }
.toggle { cursor: pointer; font-size: 20px; color: var(--gap-header-text); }
.right { display: flex; align-items: center; gap: 20px; }
.time, .user { color: var(--gap-header-text); }
.logout { cursor: pointer; color: var(--gap-header-text); }
.logout:hover { color: var(--gap-header-text-hover); }
.body {
  min-height: 0;
  overflow: hidden;
}
.aside {
  background: var(--gap-aside);
  transition: width 0.3s;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  border-right: none;
}
.version { text-align: center; color: var(--gap-text-muted); font-size: 12px; padding: 12px; border-top: 1px solid var(--gap-aside-border); }
.main {
  background: var(--gap-bg);
  padding: 16px;
  overflow: auto;
  min-width: 0;
}
</style>
