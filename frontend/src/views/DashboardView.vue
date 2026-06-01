<template>
  <div class="space-y-6">
    <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">Dashboard</h2>

    <!-- Stats Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <!-- Channels -->
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <div class="flex items-center justify-between">
          <div>
            <p class="text-sm text-gray-500 dark:text-gray-400">频道数</p>
            <p class="text-2xl font-bold text-gray-800 dark:text-gray-200">{{ stats.channels }}</p>
          </div>
          <div class="w-10 h-10 rounded-lg bg-indigo-100 dark:bg-indigo-900/30 flex items-center justify-center text-lg">
            📡
          </div>
        </div>
      </div>

      <!-- Files -->
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <div class="flex items-center justify-between">
          <div>
            <p class="text-sm text-gray-500 dark:text-gray-400">文件数</p>
            <p class="text-2xl font-bold text-gray-800 dark:text-gray-200">{{ stats.files }}</p>
          </div>
          <div class="w-10 h-10 rounded-lg bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center text-lg">
            📁
          </div>
        </div>
      </div>

      <!-- Cache -->
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <div class="flex items-center justify-between">
          <div>
            <p class="text-sm text-gray-500 dark:text-gray-400">缓存使用</p>
            <p class="text-2xl font-bold text-gray-800 dark:text-gray-200">
              {{ stats.cachePercent.toFixed(1) }}%
            </p>
          </div>
          <div class="w-10 h-10 rounded-lg bg-green-100 dark:bg-green-900/30 flex items-center justify-center text-lg">
            💾
          </div>
        </div>
        <div class="mt-3 w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
          <div
            class="h-2 rounded-full transition-all duration-500"
            :class="stats.cachePercent > 80 ? 'bg-red-500' : 'bg-green-500'"
            :style="{ width: Math.min(stats.cachePercent, 100) + '%' }"
          ></div>
        </div>
      </div>

      <!-- Thumb Jobs -->
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <div class="flex items-center justify-between">
          <div>
            <p class="text-sm text-gray-500 dark:text-gray-400">缩略图任务</p>
            <p class="text-2xl font-bold text-gray-800 dark:text-gray-200">
              {{ stats.pendingThumbJobs }}
              <span class="text-sm font-normal text-gray-400">活跃</span>
            </p>
          </div>
          <div class="w-10 h-10 rounded-lg bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center text-lg">
            🖼️
          </div>
        </div>
      </div>
    </div>

    <!-- Sync Tasks -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
      <h3 class="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">最近同步任务</h3>
      <div v-if="recentTasks.length === 0" class="text-sm text-gray-400 dark:text-gray-500 text-center py-8">
        暂无同步记录
      </div>
      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
              <th class="pb-2 pr-4">频道</th>
              <th class="pb-2 pr-4">状态</th>
              <th class="pb-2 pr-4">文件数</th>
              <th class="pb-2 pr-4">时间</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="task in recentTasks"
              :key="task.id"
              class="border-b border-gray-100 dark:border-gray-700/50"
            >
              <td class="py-2 pr-4 text-gray-800 dark:text-gray-200">{{ task.channel_title }}</td>
              <td class="py-2 pr-4">
                <span
                  class="px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="statusClass(task.status)"
                >{{ task.status }}</span>
              </td>
              <td class="py-2 pr-4 text-gray-600 dark:text-gray-400">
                {{ task.synced_files }}/{{ task.total_files }}
              </td>
              <td class="py-2 pr-4 text-gray-400 text-xs">
                {{ task.created_at ? new Date(task.created_at).toLocaleString() : '-' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="text-center text-gray-400 py-8">加载中...</div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { channelsApi, cacheApi, thumbnailsApi, syncApi } from '../api/index'

const loading = ref(true)
const stats = ref({
  channels: 0,
  files: 0,
  cachePercent: 0,
  pendingThumbJobs: 0,
})

const recentTasks = ref([])

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

onMounted(async () => {
  try {
    const [channels, cacheStats, thumbStats] = await Promise.all([
      channelsApi.list(),
      cacheApi.stats(),
      thumbnailsApi.stats(),
    ])

    stats.value.channels = channels.data.length
    stats.value.files = channels.data.reduce((sum, c) => sum + (c.file_count || 0), 0)
    stats.value.cachePercent = cacheStats.data.usage_percent || 0
    stats.value.pendingThumbJobs = (thumbStats.data.pending || 0) + (thumbStats.data.processing || 0)

    // Fetch recent sync tasks from each channel (last 3)
    const taskPromises = channels.data.slice(0, 10).map(async (c) => {
      try {
        const { data } = await syncApi.listTasks(c.id)
        return data.slice(0, 1).map((t) => ({ ...t, channel_title: c.title }))
      } catch {
        return []
      }
    })
    const allTasks = (await Promise.all(taskPromises)).flat()
    allTasks.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
    recentTasks.value = allTasks.slice(0, 10)
  } catch {
    // Silently handle — stats may not be available
  } finally {
    loading.value = false
  }
})
</script>
