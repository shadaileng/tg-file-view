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

async function handleEvict() {
  evictLoading.value = true
  evictResult.value = null
  try {
    const { data } = await cacheApi.evict()
    evictResult.value = data
    await loadStats()
  } catch {
    // handled by interceptor
  } finally {
    evictLoading.value = false
  }
}

onMounted(loadStats)
</script>
