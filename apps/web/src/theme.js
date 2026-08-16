import { ref } from 'vue'

const STORAGE_KEY = 'gap_theme'

export const theme = ref('light')

export function initTheme() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'dark' || saved === 'light') {
    theme.value = saved
  }
  applyTheme(theme.value)
}

export function setTheme(mode) {
  if (mode !== 'light' && mode !== 'dark') return
  theme.value = mode
  localStorage.setItem(STORAGE_KEY, mode)
  applyTheme(mode)
}

function applyTheme(mode) {
  const root = document.documentElement
  root.classList.toggle('dark', mode === 'dark')
  root.setAttribute('data-theme', mode)
}
