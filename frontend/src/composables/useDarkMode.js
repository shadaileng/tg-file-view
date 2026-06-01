import { ref, watch } from 'vue'

const STORAGE_KEY = 'tg-file-viewer-theme'

const isDark = ref(false)

function applyTheme(dark) {
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light')
}

// Init on load
const saved = localStorage.getItem(STORAGE_KEY)
if (saved === 'dark') {
  isDark.value = true
  applyTheme(true)
} else if (saved === 'light') {
  isDark.value = false
  applyTheme(false)
} else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
  isDark.value = true
  applyTheme(true)
}

export function useDarkMode() {
  function toggleDark() {
    isDark.value = !isDark.value
    applyTheme(isDark.value)
  }

  return { isDark, toggleDark }
}
