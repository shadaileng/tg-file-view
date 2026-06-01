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

    <!-- Grouped config cards -->
    <div v-else class="space-y-4">
      <section
        v-for="(items, groupName) in groupedConfigs"
        :key="groupName"
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden"
      >
        <!-- Group header -->
        <div class="px-4 py-3 bg-gray-50 dark:bg-gray-750 border-b border-gray-200 dark:border-gray-700">
          <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300">
            {{ CONFIG_GROUPS[groupName].label }}
          </h3>
          <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
            {{ CONFIG_GROUPS[groupName].description }}
          </p>
        </div>

        <!-- Config rows within group -->
        <div class="divide-y divide-gray-200 dark:divide-gray-700">
          <div
            v-for="config in items"
            :key="config.key"
            class="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-750 text-sm"
          >
            <!-- Key name -->
            <span
              class="shrink-0 w-48 font-mono text-xs font-medium text-indigo-600 dark:text-indigo-400 truncate"
              :title="config.key"
            >
              {{ config.key }}
            </span>
            <!-- Current value -->
            <span
              class="flex-1 min-w-0 text-xs text-gray-500 dark:text-gray-400 truncate"
              :title="maskValue(config)"
            >
              {{ maskValue(config) }}
            </span>
            <!-- Actions -->
            <span class="shrink-0 flex items-center gap-2">
              <span
                v-if="isReadonly(config.key)"
                class="text-xs text-gray-400 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded"
                title="此配置项不可通过 API 修改"
              >
                只读
              </span>
              <button
                v-else
                @click="openEdit(config.key, config)"
                class="px-3 py-1 text-xs bg-indigo-50 dark:bg-indigo-900/20
                       text-indigo-600 dark:text-indigo-400 rounded hover:bg-indigo-100 dark:hover:bg-indigo-900/30
                       transition-colors"
              >
                编辑
              </button>
            </span>
          </div>
        </div>
      </section>
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
import { ref, computed, onMounted } from 'vue'
import { configApi } from '../api/index'

const loading = ref(true)
const configs = ref([])
const editTarget = ref(null)
const editValue = ref('')
const editLoading = ref(false)
const editError = ref('')
const adminPassword = ref('')

// ---- Config grouping ----
const CONFIG_GROUPS = {
  telegram: {
    label: 'Telegram 配置',
    description: '与 Telegram 服务器通信的凭据，不可通过界面修改',
    keys: ['api_id', 'api_hash', 'phone', 'bot_token', 'proxy_url'],
  },
  sync: {
    label: '同步配置',
    description: '控制频道同步任务的批次行为与限流策略',
    keys: ['sync_batch_size', 'sync_bulk_api_limit', 'sync_delay_seconds'],
  },
  thumbnail: {
    label: '缩略图配置',
    description: '缩略图生成的尺寸、并发数和视频预览参数',
    keys: ['thumb_max_width', 'thumb_max_height', 'thumb_video_chunk_preview_mb', 'thumb_workers'],
  },
  cache: {
    label: '缓存配置',
    description: '文件缓存策略相关参数',
    keys: ['cache_max_size_mb'],
  },
  system: {
    label: '系统配置',
    description: '服务主机、端口、管理员密码及调试模式等运行参数',
    keys: ['host', 'port', 'admin_password', 'debug'],
  },
}

const groupedConfigs = computed(() => {
  const arr = Array.isArray(configs.value) ? configs.value : []
  const map = {}
  for (const c of arr) {
    map[c.key] = c
  }
  const result = {}
  for (const [groupName, groupDef] of Object.entries(CONFIG_GROUPS)) {
    result[groupName] = groupDef.keys
      .map(key => map[key])
      .filter(Boolean)
  }
  return result
})

// ---- Readonly / sensitive ----
// Matches backend config.READONLY_CONFIG_KEYS
const READONLY_KEYS = ['api_id', 'api_hash', 'phone', 'bot_token', 'proxy_url', 'admin_password']

function isReadonly(key) {
  return READONLY_KEYS.includes(key)
}

function maskValue(config) {
  if (!config || config.value == null || config.value === '') return '-'
  const sensitiveKeys = ['api_hash', 'admin_password', 'tg_admin_password']
  if (sensitiveKeys.includes(config.key)) return '***'
  return config.value
}

// ---- Data loading ----
async function loadConfigs() {
  try {
    const { data } = await configApi.list()
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
    const pw = adminPassword.value
    await configApi.update(editTarget.value.key, editValue.value, pw || '')
    const savedKey = editTarget.value.key
    editTarget.value = null
    await loadConfigs()
    window.dispatchEvent(new CustomEvent('app-toast', {
      detail: { type: 'success', message: `配置 ${savedKey} 已更新` },
    }))
  } catch (e) {
    editError.value = e.response?.data?.detail || e.message || '保存失败'
  } finally {
    editLoading.value = false
  }
}

onMounted(loadConfigs)
</script>
