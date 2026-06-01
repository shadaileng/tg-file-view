<template>
  <div class="flex h-screen overflow-hidden">
    <!-- Sidebar -->
    <aside
      class="w-56 flex-shrink-0 flex flex-col border-r border-gray-200 dark:border-gray-700
             bg-sidebar-light dark:bg-sidebar-dark transition-colors duration-200"
    >
      <!-- Logo -->
      <div class="h-14 flex items-center px-4 border-b border-gray-200 dark:border-gray-700">
        <span class="text-lg font-bold text-indigo-600 dark:text-indigo-400">TG File Viewer</span>
      </div>

      <!-- Nav -->
      <nav class="flex-1 overflow-y-auto py-2 px-2 space-y-1">
        <router-link
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium
                 text-gray-600 dark:text-gray-400
                 hover:bg-gray-200 dark:hover:bg-gray-700
                 transition-colors"
          active-class="bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300"
        >
          <span class="w-5 text-center">{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>

      <!-- Footer -->
      <div class="p-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-400">
        v0.1.0
      </div>
    </aside>

    <!-- Main -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <!-- Header -->
      <header
        class="h-14 flex items-center justify-between px-6 border-b border-gray-200 dark:border-gray-700
               bg-white dark:bg-gray-800 transition-colors duration-200 flex-shrink-0"
      >
        <h1 class="text-sm font-semibold text-gray-500 dark:text-gray-400">
          {{ currentPageTitle }}
        </h1>

        <div class="flex items-center gap-3">
          <!-- Health indicator -->
          <span
            class="flex items-center gap-1.5 text-xs"
            :class="healthOk ? 'text-green-600 dark:text-green-400' : 'text-red-500'"
          >
            <span
              class="w-2 h-2 rounded-full"
              :class="healthOk ? 'bg-green-500' : 'bg-red-500'"
            ></span>
            {{ healthOk ? 'API 正常' : 'API 离线' }}
          </span>

          <!-- Auth status -->
          <span
            v-if="authStatus === 'authorized'"
            class="text-xs text-green-600 dark:text-green-400"
          >TG 已授权</span>
          <span
            v-else-if="authStatus !== 'authorized'"
            class="text-xs text-amber-500 cursor-pointer hover:underline"
            @click="$router.push('/auth')"
          >TG 未授权</span>

          <!-- Dark mode toggle -->
          <button
            @click="toggleDark"
            class="p-1.5 rounded-lg text-gray-500 dark:text-gray-400
                   hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title="切换夜间模式"
          >
            <svg v-if="isDark" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/>
            </svg>
            <svg v-else class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                    d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/>
            </svg>
          </button>
        </div>
      </header>

      <!-- Content -->
      <main class="flex-1 overflow-auto p-6 bg-gray-50 dark:bg-gray-900 transition-colors duration-200">
        <router-view />
      </main>
    </div>

    <!-- Toast -->
    <Transition name="toast">
      <div
        v-if="toast.show"
        :class="[
          'fixed top-4 right-4 z-50 px-4 py-3 rounded-lg shadow-lg text-sm font-medium max-w-sm',
          toast.type === 'error'
            ? 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200'
            : 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
        ]"
      >
        {{ toast.message }}
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, reactive } from 'vue'
import { useRoute } from 'vue-router'
import { useDarkMode } from './composables/useDarkMode'
import { healthApi, authApi } from './api/index'

const route = useRoute()
const { isDark, toggleDark } = useDarkMode()

const navItems = [
  { to: '/', icon: '\u{1F4CA}', label: 'Dashboard' },
  { to: '/channels', icon: '\u{1F4E1}', label: '频道管理' },
  { to: '/files', icon: '\u{1F4C1}', label: '文件浏览' },
  { to: '/sync', icon: '\u{1F504}', label: '同步管理' },
  { to: '/thumbnails', icon: '\u{1F5BC}', label: '缩略图' },
  { to: '/cache', icon: '\u{1F4BE}', label: '缓存管理' },
  { to: '/settings', icon: '\u{2699}', label: '系统设置' },
  { to: '/auth', icon: '\u{1F511}', label: 'Telegram 登录' },
]

const pageTitles = {
  Dashboard: '概览',
  Channels: '频道管理',
  Files: '文件浏览',
  Sync: '同步管理',
  Thumbnails: '缩略图',
  Cache: '缓存管理',
  Settings: '系统设置',
  Auth: 'Telegram 登录',
}

const currentPageTitle = computed(() => {
  return pageTitles[route.name] || route.name || 'TG File Viewer'
})

const healthOk = ref(false)
const authStatus = ref('unknown')

// Toast system
const toast = reactive({ show: false, type: 'error', message: '' })
let toastTimer = null

function showToast(type, message) {
  toast.show = true
  toast.type = type
  toast.message = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.show = false }, 4000)
}

function handleToast(e) {
  showToast(e.detail.type, e.detail.message)
}

function handleAuthChanged() {
  checkAuth()
}

async function checkHealth() {
  try {
    await healthApi.check()
    healthOk.value = true
  } catch {
    healthOk.value = false
  }
}

async function checkAuth() {
  try {
    const { data } = await authApi.status()
    authStatus.value = data.is_authorized ? 'authorized' : data.status
  } catch {
    authStatus.value = 'error'
  }
}

let healthTimer = null
onMounted(() => {
  checkHealth()
  checkAuth()
  healthTimer = setInterval(checkHealth, 30000)
  window.addEventListener('app-toast', handleToast)
  window.addEventListener('app-auth-changed', handleAuthChanged)
})

onUnmounted(() => {
  clearInterval(healthTimer)
  window.removeEventListener('app-toast', handleToast)
  window.removeEventListener('app-auth-changed', handleAuthChanged)
})
</script>
