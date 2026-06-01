<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">频道管理</h2>
      <div class="flex gap-2">
        <button
          @click="toggleDiscover"
          class="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors"
        >
          {{ showDiscover ? '取消' : '发现频道' }}
        </button>
        <button
          @click="showAdd = true"
          class="px-4 py-2 text-sm bg-green-600 hover:bg-green-700 text-white rounded-lg transition-colors"
        >
          添加频道
        </button>
      </div>
    </div>

    <!-- Add dialog -->
    <div
      v-if="showAdd"
      class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
    >
      <h3 class="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">添加频道</h3>
      <div class="flex gap-3 items-end">
        <div class="flex-1">
          <label class="block text-xs text-gray-400 mb-1">频道 username 或 tg_id</label>
          <input
            v-model="addInput"
            type="text"
            placeholder="例如: test_channel 或 -1001234567890"
            class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600
                   bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-sm
                   focus:outline-none focus:ring-2 focus:ring-indigo-500"
            @keyup.enter="handleAddChannel"
          />
        </div>
        <button
          @click="handleAddChannel"
          :disabled="addLoading"
          class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm transition-colors disabled:opacity-50"
        >
          {{ addLoading ? '添加中...' : '添加' }}
        </button>
        <button
          @click="showAdd = false; addError = ''"
          class="px-4 py-2 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        >
          取消
        </button>
      </div>
      <p v-if="addError" class="mt-2 text-sm text-red-500">{{ addError }}</p>
    </div>

    <!-- Discover -->
    <div
      v-if="showDiscover"
      class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
    >
      <h3 class="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">
        发现频道
        <span v-if="discoverLoading" class="ml-2 text-xs text-gray-400">加载中...</span>
      </h3>
      <div v-if="discoverChannels.length === 0 && !discoverLoading" class="text-sm text-gray-400 text-center py-4">
        未发现频道，请确认 Telegram 已授权
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div
          v-for="ch in discoverChannels"
          :key="ch.tg_id"
          class="flex items-center justify-between px-3 py-2 rounded-lg
                 bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600"
        >
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">{{ ch.title }}</p>
            <p class="text-xs text-gray-400 truncate">{{ ch.username || 'tg_id: ' + ch.tg_id }}</p>
          </div>
          <button
            v-if="!ch.already_tracked"
            @click="handleAddFromDiscover(ch)"
            class="ml-2 px-3 py-1 text-xs bg-green-500 hover:bg-green-600 text-white rounded transition-colors"
          >
            添加
          </button>
          <span v-else class="ml-2 text-xs text-gray-400">已添加</span>
        </div>
      </div>
    </div>

    <!-- Channel list -->
    <div v-if="loading" class="text-center text-gray-400 py-8">加载中...</div>

    <div v-else-if="channels.length === 0" class="text-center py-12">
      <p class="text-gray-400 dark:text-gray-500 text-sm">还没有频道，点击上方添加第一个频道</p>
    </div>

    <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      <div
        v-for="ch in channels"
        :key="ch.id"
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4
               hover:shadow-md transition-shadow"
      >
        <div class="flex items-start justify-between">
          <div class="min-w-0 flex-1">
            <h3 class="font-semibold text-gray-800 dark:text-gray-200 truncate">{{ ch.title }}</h3>
            <p class="text-xs text-gray-400 mt-1">{{ ch.username || 'tg_id: ' + ch.tg_id }}</p>
          </div>
          <button
            @click="confirmDelete(ch)"
            class="ml-2 p-1 text-gray-400 hover:text-red-500 transition-colors text-sm"
            title="删除频道"
          >
            &times;
          </button>
        </div>
        <div class="mt-3 flex gap-4 text-xs text-gray-500 dark:text-gray-400">
          <span>{{ ch.file_count || 0 }} 文件</span>
          <span>{{ formatSize(ch.total_size) }}</span>
        </div>
        <div class="mt-2 text-xs text-gray-400">
          {{ ch.last_sync ? '最后同步: ' + new Date(ch.last_sync).toLocaleString() : '未同步' }}
        </div>
        <router-link
          :to="'/files?channel=' + ch.id"
          class="mt-3 block text-center px-3 py-1.5 text-xs bg-indigo-50 dark:bg-indigo-900/20
                 text-indigo-600 dark:text-indigo-400 rounded-lg hover:bg-indigo-100 dark:hover:bg-indigo-900/30 transition-colors"
        >
          查看文件
        </router-link>
      </div>
    </div>

    <!-- Delete confirm -->
    <div
      v-if="deleteTarget"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      @click.self="deleteTarget = null"
    >
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 max-w-sm w-full mx-4">
        <h3 class="text-lg font-semibold text-gray-800 dark:text-gray-200">确认删除</h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-2">
          确定要删除频道「{{ deleteTarget.title }}」吗？所有关联文件也将被删除。
        </p>
        <div class="mt-4 flex justify-end gap-2">
          <button
            @click="deleteTarget = null"
            class="px-4 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
          >
            取消
          </button>
          <button
            @click="handleDelete"
            :disabled="deleteLoading"
            class="px-4 py-2 text-sm bg-red-500 hover:bg-red-600 text-white rounded-lg disabled:opacity-50"
          >
            {{ deleteLoading ? '删除中...' : '删除' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { channelsApi } from '../api/index'

const loading = ref(true)
const channels = ref([])
const showAdd = ref(false)
const showDiscover = ref(false)
const addInput = ref('')
const addLoading = ref(false)
const addError = ref('')
const discoverChannels = ref([])
const discoverLoading = ref(false)
const deleteTarget = ref(null)
const deleteLoading = ref(false)

function formatSize(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return size.toFixed(1) + ' ' + units[i]
}

async function loadChannels() {
  try {
    const { data } = await channelsApi.list()
    channels.value = data
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

async function handleAddChannel() {
  const val = addInput.value.trim()
  if (!val) {
    addError.value = '请填写频道 username 或 tg_id'
    return
  }
  addLoading.value = true
  addError.value = ''
  try {
    const isNumber = /^-?\d+$/.test(val)
    const body = isNumber ? { tg_id: parseInt(val, 10) } : { username: val }
    await channelsApi.create(body)
    addInput.value = ''
    showAdd.value = false
    await loadChannels()
  } catch (e) {
    addError.value = e.response?.data?.detail || e.message
  } finally {
    addLoading.value = false
  }
}

async function handleAddFromDiscover(ch) {
  try {
    const body = ch.username ? { username: ch.username } : { tg_id: ch.tg_id }
    await channelsApi.create(body)
    await loadChannels()
    // Refresh discover to reflect new state
    await loadDiscover()
  } catch {
    // handled by interceptor
  }
}

async function loadDiscover() {
  discoverLoading.value = true
  try {
    const { data } = await channelsApi.discover()
    discoverChannels.value = data
  } catch {
    discoverChannels.value = []
  } finally {
    discoverLoading.value = false
  }
}

function confirmDelete(ch) {
  deleteTarget.value = ch
}

async function handleDelete() {
  if (!deleteTarget.value) return
  deleteLoading.value = true
  try {
    await channelsApi.delete(deleteTarget.value.id)
    deleteTarget.value = null
    await loadChannels()
  } catch {
    // handled by interceptor
  } finally {
    deleteLoading.value = false
  }
}

async function toggleDiscover() {
  showDiscover.value = !showDiscover.value
  if (showDiscover.value) {
    await loadDiscover()
  }
}

onMounted(loadChannels)
</script>
