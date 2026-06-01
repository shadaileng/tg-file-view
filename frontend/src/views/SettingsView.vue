<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">系统设置</h2>
      <button
        @click="loadConfigs"
        class="px-4 py-2 text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600
               text-gray-600 dark:text-gray-300 rounded-lg transition-colors"
      >
        刷新
      </button>
    </div>

    <div v-if="loading" class="text-center text-gray-400 py-8 text-sm">加载中...</div>

    <!-- Config list -->
    <div v-else class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div class="divide-y divide-gray-200 dark:divide-gray-700">
        <div
          v-for="(config, key) in configs"
          :key="key"
          class="flex items-center justify-between px-5 py-3 hover:bg-gray-50 dark:hover:bg-gray-750"
        >
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ key }}</p>
            <p class="text-xs text-gray-400 mt-0.5 truncate">{{ maskValue(config) }}</p>
          </div>
          <div class="flex items-center gap-2 ml-4">
            <span
              v-if="isReadonly(key)"
              class="text-xs text-gray-400 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded"
              title="此配置项不可通过 API 修改"
            >
              只读
            </span>
            <button
              v-else
              @click="openEdit(key, config)"
              class="px-3 py-1.5 text-xs bg-indigo-50 dark:bg-indigo-900/20
                     text-indigo-600 dark:text-indigo-400 rounded hover:bg-indigo-100 dark:hover:bg-indigo-900/30
                     transition-colors"
            >
              编辑
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Edit modal -->
    <div
      v-if="editTarget"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="editTarget = null"
    >
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-md w-full mx-4">
        <h3 class="text-lg font-semibold text-gray-800 dark:text-gray-200">编辑配置</h3>
        <p class="text-xs text-gray-400 mt-1">{{ editTarget.key }}</p>

        <input
          v-model="editValue"
          type="text"
          class="mt-3 w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600
                 bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-sm
                 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          @keyup.enter="handleSave"
        />

        <div v-if="editTarget.key === 'admin_password'" class="mt-2">
          <label class="text-xs text-gray-400 mb-1 block">管理员密码（必填）</label>
          <input
            v-model="adminPassword"
            type="password"
            placeholder="输入管理员密码"
            class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600
                   bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-sm
                   focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>

        <p v-if="editError" class="mt-2 text-xs text-red-500">{{ editError }}</p>

        <div class="mt-4 flex justify-end gap-2">
          <button
            @click="editTarget = null; editError = ''"
            class="px-4 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
          >
            取消
          </button>
          <button
            @click="handleSave"
            :disabled="editLoading"
            class="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg disabled:opacity-50"
          >
            {{ editLoading ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { configApi } from '../api/index'

const loading = ref(true)
const configs = ref({})
const editTarget = ref(null)
const editValue = ref('')
const editLoading = ref(false)
const editError = ref('')
const adminPassword = ref('')

// Keys that need admin password for all mutations
const READONLY_KEYS = ['api_id', 'api_hash', 'app_version', 'device_model', 'system_version']

function isReadonly(key) {
  return READONLY_KEYS.includes(key)
}

function maskValue(config) {
  if (!config || !config.value) return '-'
  // Mask sensitive values
  const sensitiveKeys = ['api_hash', 'admin_password', 'tg_admin_password']
  if (sensitiveKeys.includes(config.key)) return '***'
  return config.value
}

async function loadConfigs() {
  try {
    const { data } = await configApi.list()
    // The API returns a list of { key, value, editable }
    configs.value = Array.isArray(data) ? data : []
  } catch {
    configs.value = []
  } finally {
    loading.value = false
  }
}

function openEdit(key, config) {
  editTarget.value = { key, ...config }
  editValue.value = config.value || ''
  adminPassword.value = ''
  editError.value = ''
}

async function handleSave() {
  if (!editTarget.value) return
  editLoading.value = true
  editError.value = ''
  try {
    // Always require admin password for editing
    const pw = adminPassword.value
    await configApi.update(editTarget.value.key, editValue.value, pw || '')
    editTarget.value = null
    await loadConfigs()
    window.dispatchEvent(new CustomEvent('app-toast', {
      detail: { type: 'success', message: `配置 ${editTarget.value?.key || ''} 已更新` },
    }))
  } catch (e) {
    editError.value = e.response?.data?.detail || e.message || '保存失败'
  } finally {
    editLoading.value = false
  }
}

onMounted(loadConfigs)
</script>
