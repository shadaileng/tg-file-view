<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">同步管理</h2>
    </div>

    <!-- Channel selector -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <label class="block text-xs text-gray-400 mb-2">选择频道触发同步</label>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="ch in channels"
          :key="ch.id"
          @click="selectedChannelId = ch.id"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          :class="selectedChannelId === ch.id
            ? 'bg-indigo-600 text-white'
            : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
        >
          {{ ch.title }}
        </button>
      </div>

      <div v-if="selectedChannelId" class="mt-4 flex items-center gap-3">
        <button
          @click="handleTriggerSync"
          :disabled="syncLoading"
          class="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
        >
          {{ syncLoading ? '启动中...' : '开始同步' }}
        </button>
        <span class="text-xs text-gray-400">
          最后同步: {{ lastSyncTime || '从未同步' }}
        </span>
      </div>
    </div>

    <!-- Current sync progress -->
    <div
      v-if="activeSync"
      class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
    >
      <h3 class="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">
        同步进行中 — 任务 {{ activeSync.id?.slice(0, 8) }}...
      </h3>
      <div class="space-y-2">
        <div class="flex justify-between text-sm">
          <span class="text-gray-500 dark:text-gray-400">状态</span>
          <span
            class="px-2 py-0.5 rounded-full text-xs font-medium"
            :class="statusClass(activeSync.status)"
          >{{ activeSync.status }}</span>
        </div>
        <div class="flex justify-between text-sm">
          <span class="text-gray-500 dark:text-gray-400">进度</span>
          <span class="text-gray-800 dark:text-gray-200">
            {{ activeSync.synced_files || 0 }} / {{ activeSync.total_files || '?' }}
          </span>
        </div>
        <div v-if="activeSync.errors && activeSync.errors.length" class="text-xs text-red-500">
          错误: {{ activeSync.errors.join(', ') }}
        </div>
        <button
          v-if="activeSync.status === 'running'"
          @click="cancelActive"
          class="mt-2 px-3 py-1 text-xs bg-red-500 hover:bg-red-600 text-white rounded transition-colors"
        >
          取消同步
        </button>
      </div>
    </div>

    <!-- Task history -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
      <h3 class="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">同步任务历史</h3>

      <div v-if="tasksLoading" class="text-center text-gray-400 py-4 text-sm">加载中...</div>

      <div v-else-if="tasks.length === 0" class="text-center text-gray-400 py-8 text-sm">
        {{ selectedChannelId ? '该频道暂无同步记录' : '请选择频道查看历史' }}
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
              <th class="pb-2 pr-3">任务ID</th>
              <th class="pb-2 pr-3">状态</th>
              <th class="pb-2 pr-3">文件数</th>
              <th class="pb-2 pr-3">跳过</th>
              <th class="pb-2 pr-3">错误</th>
              <th class="pb-2 pr-3">开始时间</th>
              <th class="pb-2">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="task in tasks"
              :key="task.id"
              class="border-b border-gray-100 dark:border-gray-700/50"
            >
              <td class="py-2 pr-3 font-mono text-xs text-gray-400">{{ task.id.slice(0, 8) }}</td>
              <td class="py-2 pr-3">
                <span
                  class="px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="statusClass(task.status)"
                >{{ task.status }}</span>
              </td>
              <td class="py-2 pr-3 text-gray-700 dark:text-gray-300">
                {{ task.synced_files || 0 }}/{{ task.total_files || '?' }}
              </td>
              <td class="py-2 pr-3 text-gray-500">{{ task.skipped_files || 0 }}</td>
              <td class="py-2 pr-3">
                <span v-if="task.errors && task.errors.length" class="text-red-500 text-xs">
                  {{ task.errors.length }}
                </span>
                <span v-else class="text-gray-300">-</span>
              </td>
              <td class="py-2 pr-3 text-xs text-gray-400">
                {{ task.started_at ? new Date(task.started_at).toLocaleString() : '-' }}
              </td>
              <td class="py-2">
                <button
                  v-if="task.status === 'running'"
                  @click="handleCancelTask(task.id)"
                  class="px-2 py-0.5 text-xs text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded"
                >
                  取消
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { channelsApi, syncApi } from '../api/index'

const channels = ref([])
const selectedChannelId = ref(null)
const syncLoading = ref(false)
const tasks = ref([])
const tasksLoading = ref(false)
const activeSync = ref(null)
const lastSyncTime = ref(null)

let pollTimer = null

function statusClass(status) {
  const map = {
    running: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
    completed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
    failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
    cancelled: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
    pending: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  }
  return map[status] || 'bg-gray-100 text-gray-600'
}

async function loadChannels() {
  try {
    const { data } = await channelsApi.list()
    channels.value = data
  } catch {
    // handled by interceptor
  }
}

async function loadTasks() {
  if (!selectedChannelId.value) {
    tasks.value = []
    return
  }
  tasksLoading.value = true
  try {
    const { data } = await syncApi.listTasks(selectedChannelId.value)
    tasks.value = data
    // Update last sync time
    if (data.length > 0 && data[0].status === 'completed') {
      lastSyncTime.value = data[0].completed_at
        ? new Date(data[0].completed_at).toLocaleString()
        : null
    }
  } catch {
    tasks.value = []
  } finally {
    tasksLoading.value = false
  }
}

async function handleTriggerSync() {
  if (!selectedChannelId.value) return
  syncLoading.value = true
  try {
    const { data } = await syncApi.trigger(selectedChannelId.value)
    activeSync.value = data
    // Start polling
    startPolling()
  } catch (e) {
    // 409 conflict is common — don't show as error
    if (e.response?.status !== 409) {
      // handled by interceptor
    }
  } finally {
    syncLoading.value = false
  }
}

async function pollActiveSync() {
  if (!activeSync.value?.id) {
    stopPolling()
    return
  }
  try {
    const { data } = await syncApi.getTask(activeSync.value.id)
    activeSync.value = data
    if (data.status !== 'running' && data.status !== 'pending') {
      stopPolling()
      await loadTasks()
    }
  } catch {
    stopPolling()
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(pollActiveSync, 2000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function handleCancelTask(taskId) {
  try {
    await syncApi.cancel(taskId)
    await loadTasks()
    if (activeSync.value?.id === taskId) {
      activeSync.value = null
      stopPolling()
    }
  } catch {
    // handled by interceptor
  }
}

async function cancelActive() {
  if (activeSync.value) {
    await handleCancelTask(activeSync.value.id)
  }
}

// Watch channel selection
watch(selectedChannelId, async (newVal) => {
  if (newVal) {
    await loadTasks()
    const ch = channels.value.find(c => c.id === newVal)
    if (ch?.last_sync) {
      lastSyncTime.value = new Date(ch.last_sync).toLocaleString()
    } else {
      lastSyncTime.value = null
    }
  }
})

onMounted(loadChannels)
onUnmounted(stopPolling)
</script>
