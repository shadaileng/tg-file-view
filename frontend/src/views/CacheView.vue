<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">缓存管理</h2>
      <button
        @click="handleEvict"
        :disabled="evictLoading"
        class="px-4 py-2 text-sm bg-amber-500 hover:bg-amber-600 text-white rounded-lg transition-colors disabled:opacity-50"
      >
        {{ evictLoading ? '淘汰中...' : '手动淘汰' }}
      </button>
    </div>

    <!-- Stats -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <p class="text-sm text-gray-400">缓存文件数</p>
        <p class="text-3xl font-bold text-gray-800 dark:text-gray-200 mt-1">{{ cacheStats.file_count || 0 }}</p>
      </div>

      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <p class="text-sm text-gray-400">缓存大小</p>
        <p class="text-3xl font-bold text-gray-800 dark:text-gray-200 mt-1">
          {{ cacheStats.total_size_mb?.toFixed(1) || 0 }} MB
        </p>
      </div>

      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <p class="text-sm text-gray-400">缓存上限</p>
        <p class="text-3xl font-bold text-gray-800 dark:text-gray-200 mt-1">
          {{ cacheStats.max_size_mb === 0 ? '无限' : cacheStats.max_size_mb + ' MB' }}
        </p>
      </div>
    </div>

    <!-- Usage bar -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
      <div class="flex justify-between text-sm mb-2">
        <span class="text-gray-500 dark:text-gray-400">使用率</span>
        <span class="font-semibold" :class="usageColor">
          {{ (cacheStats.usage_percent || 0).toFixed(1) }}%
        </span>
      </div>
      <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-3">
        <div
          class="h-3 rounded-full transition-all duration-500"
          :style="{ width: Math.min(cacheStats.usage_percent || 0, 100) + '%' }"
          :class="usageBarColor"
        ></div>
      </div>
    </div>

    <!-- Evict result -->
    <div
      v-if="evictResult"
      class="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-xl p-5"
    >
      <h3 class="text-sm font-semibold text-green-700 dark:text-green-300 mb-2">淘汰完成</h3>
      <div class="grid grid-cols-3 gap-4 text-sm">
        <div>
          <p class="text-xs text-green-500">淘汰文件数</p>
          <p class="font-bold text-green-700 dark:text-green-300">{{ evictResult.evicted_count }}</p>
        </div>
        <div>
          <p class="text-xs text-green-500">释放空间</p>
          <p class="font-bold text-green-700 dark:text-green-300">{{ evictResult.freed_mb?.toFixed(1) }} MB</p>
        </div>
        <div>
          <p class="text-xs text-green-500">当前大小</p>
          <p class="font-bold text-green-700 dark:text-green-300">{{ evictResult.total_size_mb?.toFixed(1) }} MB</p>
        </div>
      </div>
    </div>

    <!-- Cached files table -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
      <div class="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300">缓存文件列表</h3>
        <p class="text-xs text-gray-400">共 {{ recordsTotal }} 个文件</p>
      </div>

      <!-- Loading -->
      <div v-if="recordsLoading" class="text-center text-gray-400 py-8 text-sm">加载中...</div>

      <!-- Empty -->
      <div v-else-if="records.length === 0" class="text-center text-gray-400 py-8 text-sm">
        暂无缓存记录
      </div>

      <!-- Table -->
      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
              <th class="py-3 px-4 font-medium">文件名</th>
              <th class="py-3 px-4 font-medium">所属频道</th>
              <th class="py-3 px-4 font-medium">大小</th>
              <th class="py-3 px-4 font-medium">状态</th>
              <th class="py-3 px-4 font-medium">缓存时间</th>
              <th class="py-3 px-4 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="rec in records"
              :key="rec.id"
              class="border-b border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-gray-700/30"
            >
              <td class="py-3 px-4 text-gray-800 dark:text-gray-200 max-w-[200px] truncate" :title="rec.file_name">
                <router-link
                  :to="`/files?channel=${rec.file_id ? rec.channel_id || '' : ''}`"
                  class="hover:text-indigo-500 transition-colors"
                >
                  {{ rec.file_name }}
                </router-link>
              </td>
              <td class="py-3 px-4 text-gray-500 dark:text-gray-400">{{ rec.channel_title }}</td>
              <td class="py-3 px-4 text-gray-600 dark:text-gray-400">{{ formatSize(rec.file_size) }}</td>
              <td class="py-3 px-4">
                <span
                  class="inline-block px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="statusClass(rec.status)"
                >
                  {{ statusLabel(rec.status) }}
                </span>
              </td>
              <td class="py-3 px-4 text-gray-400 text-xs whitespace-nowrap">
                {{ rec.cached_at ? formatTime(rec.cached_at) : '-' }}
              </td>
              <td class="py-3 px-4">
                <button
                  @click="handleDeleteRecord(rec)"
                  :disabled="deletingRecords.has(rec.id)"
                  class="px-2 py-1 text-xs bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400
                         rounded hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors
                         disabled:opacity-50"
                >
                  {{ deletingRecords.has(rec.id) ? '删除中...' : '删除' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div v-if="recordsTotal > recordsLimit" class="px-4 py-3 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <button
          @click="goRecordsPage(recordsPage - 1)"
          :disabled="recordsPage <= 1"
          class="px-3 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600
                 text-gray-600 dark:text-gray-300 disabled:opacity-30 hover:bg-gray-100 dark:hover:bg-gray-700"
        >上一页</button>
        <span class="text-xs text-gray-500 dark:text-gray-400">
          第 {{ recordsPage }} / {{ recordsTotalPages }} 页
        </span>
        <button
          @click="goRecordsPage(recordsPage + 1)"
          :disabled="recordsPage >= recordsTotalPages"
          class="px-3 py-1.5 text-xs rounded-lg border border-gray-300 dark:border-gray-600
                 text-gray-600 dark:text-gray-300 disabled:opacity-30 hover:bg-gray-100 dark:hover:bg-gray-700"
        >下一页</button>
      </div>
    </div>

    <div v-if="loading" class="text-center text-gray-400 py-8 text-sm">加载中...</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { cacheApi } from '../api/index'

const loading = ref(true)
const cacheStats = ref({})
const evictLoading = ref(false)
const evictResult = ref(null)

// Records table
const records = ref([])
const recordsTotal = ref(0)
const recordsPage = ref(1)
const recordsLimit = ref(20)
const recordsLoading = ref(false)
const deletingRecords = ref(new Set())

const recordsTotalPages = computed(() => Math.ceil(recordsTotal.value / recordsLimit.value))

const usageColor = computed(() => {
  const pct = cacheStats.value.usage_percent || 0
  if (pct > 80) return 'text-red-500'
  if (pct > 60) return 'text-amber-500'
  return 'text-green-500'
})

const usageBarColor = computed(() => {
  const pct = cacheStats.value.usage_percent || 0
  if (pct > 80) return 'bg-red-500'
  if (pct > 60) return 'bg-amber-500'
  return 'bg-green-500'
})

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

function formatTime(iso) {
  if (!iso) return '-'
  return new Date(iso).toLocaleString()
}

function statusClass(status) {
  const map = {
    cached: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
    caching: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
    failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  }
  return map[status] || 'bg-gray-100 text-gray-600'
}

function statusLabel(status) {
  const map = {
    cached: '已缓存',
    caching: '缓存中',
    failed: '失败',
  }
  return map[status] || status
}

async function loadStats() {
  try {
    const { data } = await cacheApi.stats()
    cacheStats.value = data
  } catch {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}

async function loadRecords() {
  recordsLoading.value = true
  try {
    const offset = (recordsPage.value - 1) * recordsLimit.value
    const { data } = await cacheApi.records({ offset, limit: recordsLimit.value })
    records.value = data.records
    recordsTotal.value = data.total
  } catch {
    // handled by interceptor
  } finally {
    recordsLoading.value = false
  }
}

function goRecordsPage(page) {
  if (page < 1 || page > recordsTotalPages.value) return
  recordsPage.value = page
  loadRecords()
}

async function handleEvict() {
  evictLoading.value = true
  evictResult.value = null
  try {
    const { data } = await cacheApi.evict()
    evictResult.value = data
    await loadStats()
    await loadRecords()
  } catch {
    // handled by interceptor
  } finally {
    evictLoading.value = false
  }
}

async function handleDeleteRecord(rec) {
  if (deletingRecords.value.has(rec.id)) return
  deletingRecords.value.add(rec.id)
  try {
    await cacheApi.deleteRecord(rec.id)
    window.dispatchEvent(new CustomEvent('app-toast', {
      detail: { type: 'success', message: `已删除: ${rec.file_name}` },
    }))
    await loadRecords()
    await loadStats()
  } catch {
    // handled by interceptor
  } finally {
    deletingRecords.value.delete(rec.id)
  }
}

onMounted(() => {
  loadStats()
  loadRecords()
})
</script>
