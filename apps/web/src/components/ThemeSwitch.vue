<template>
  <div class="theme-switch" @mouseleave="showTip = false">
    <button
      type="button"
      class="theme-btn"
      :class="{ active: theme === 'light' }"
      title="浅色模式"
      @click="setTheme('light')"
      @mouseenter="showTipFor('light')"
    >
      <el-icon><Sunny /></el-icon>
    </button>
    <button
      type="button"
      class="theme-btn"
      :class="{ active: theme === 'dark' }"
      title="深色模式"
      @click="setTheme('dark')"
      @mouseenter="showTipFor('dark')"
    >
      <el-icon><Moon /></el-icon>
    </button>
    <div v-if="showTip" class="theme-tip">{{ tipText }}</div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Sunny, Moon } from '@element-plus/icons-vue'
import { theme, setTheme } from '../theme'

const showTip = ref(false)
const tipMode = ref('light')

const tipText = computed(() =>
  tipMode.value === 'dark' ? '颜色模式：深色' : '颜色模式：浅色'
)

function showTipFor(mode) {
  tipMode.value = mode
  showTip.value = true
}
</script>

<style scoped>
.theme-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.theme-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #c2c2c2;
  cursor: pointer;
  transition: color 0.2s, box-shadow 0.2s, background 0.2s;
}

.theme-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}

.theme-btn.active {
  color: var(--gap-primary);
  box-shadow: 0 0 0 2px var(--gap-primary);
  background: rgba(64, 158, 255, 0.12);
}

.theme-btn .el-icon {
  font-size: 16px;
}

.theme-tip {
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
  padding: 6px 12px;
  border-radius: 8px;
  background: #2a2a2a;
  color: #f0f0f0;
  font-size: 12px;
  white-space: nowrap;
  pointer-events: none;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
  z-index: 100;
}

html:not(.dark) .theme-switch {
  background: rgba(0, 0, 0, 0.04);
  border-color: rgba(0, 0, 0, 0.08);
}

html:not(.dark) .theme-btn {
  color: #606266;
}

html:not(.dark) .theme-btn:hover {
  color: #303133;
  background: rgba(0, 0, 0, 0.06);
}

html:not(.dark) .theme-btn.active {
  color: var(--gap-primary);
  box-shadow: 0 0 0 2px var(--gap-primary);
  background: rgba(64, 158, 255, 0.08);
}

html:not(.dark) .theme-tip {
  background: #303133;
  color: #fff;
}
</style>
