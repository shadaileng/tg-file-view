<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">缩略图管理</h2>
      <div class="flex gap-2">
        <button
          @click="showBatch = true"
          class="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors"
        >
          批量生成
        </button>
      </div>
    </div>

    <!-- Stats -->
    <div class="grid grid-cols-2 sm:grid-cols-5 gap-3">
      <div
        v-for="item in statCards"
        :key="item.key"
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4 text-center"
      >
        <p class="text-2xl font-bold" :class="item.color">{{ stats[item.key] || 0 }}</p>
        <p class="text-xs text-gray-400 mt-1">{{ item.label }}</p>
      </div>
    </div>

    <!-- Batch dialog -->
    <div
      v-if="showBatch"
      class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
    >
      <h3 class="text-sm font-semibold text-gray-500 dark:text-gray-400 mb-3">批量生成缩略图</h3>
      <p class="text-xs text-gray-400 mb-3">输入文件 ID 列表（逗号或换行分隔，最多 100 个）</p>
      <textarea
        v-model="batchInput"
        rows="4"
        placeholder="1, 2, 3, 4, 5"
        class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600
               bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-sm
               focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
      ></textarea>
      <div class="mt-3 flex gap-2">
        <button
          @click="handleBatchGenerate"
          :disabled="batchLoading"
          class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm transition-colors disabled:opacity-50"
        >
          {{ batchLoading ? '提交中...' : '提交' }}
        </button>
        <button
          @click="showBatch = false; batchInput = ''; batchResult = null"
          class="px-4 py-2 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        >
          取消
        </button>
      </div>
      <div v-if="batchResult" class="mt-3 p-3 rounded-lg bg-green-50 dark:bg-green-900/20 text-sm">
        <p class="text-green-700 dark:text-green-300">
          已创建 {{ batchResult.total_created }} 个任务，
          跳过 {{ batchResult.skipped_file_ids?.length || 0 }} 个，
          未找到 {{ batchResult.not_found_file_ids?.length || 0 }} 个
        </p>
      </div>
    </div>

    <!-- Status filter -->
    <div class="flex gap-2">
      <button
        v-for="s in statusOptions"
        :key="s.value"
        @click="statusFilter = s.value"
        class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
        :class="statusFilter === s.value
          ? 'bg-indigo-600 text-white'
          : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
      >
        {{ s.label }} ({{ stats[s.value] || 0 }})
      </button>
    </div>

    <!-- Job list -->
    <div v-if="jobsLoading" class="text-center text-gray-400 py-8 text-sm">加载中...</div>

    <div v-else-if="jobs.length === 0" class="text-center py-12">
      <p class="text-gray-400 dark:text-gray-500 text-sm">暂无缩略图任务</p>
    </div>

    <!-- Auto-refresh indicator -->
    <div v-if="isPolling" class="flex items-center gap-1.5 text-xs text-gray-400">
      <span class="inline-block w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse"></span>
      自动刷新中
    </div>

    <div v-else class="space-y-2">
      <div
        v-for="job in jobs"
        :key="job.id"
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4"
      >
        <div class="flex items-start justify-between">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <p class="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
                {{ job.file_name }}
              </p>
              <span
                class="px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0"
                :class="jobStatusClass(job.status)"
              >{{ job.status }}</span>
            </div>
            <div class="mt-1 flex gap-3 text-xs text-gray-400">
              <span>{{ job.mime_type }}</span>
              <span>优先级: {{ job.priority }}</span>
              <span>尝试: {{ job.attempt }}/{{ job.max_retries }}</span>
            </div>
            <!-- Phase progress bar (only for processing jobs) -->
            <div v-if="job.status === 'processing'" class="mt-2">
              <div class="flex justify-between text-xs mb-1">
                <span class="text-gray-500">{{ phaseLabel(job) }}</span>
                <span class="text-gray-500">{{ job.progress }}%</span>
              </div>
              <div class="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5">
                <div
                  class="h-1.5 rounded-full transition-all duration-500"
                  :class="progressColor(job)"
                  :style="{ width: job.progress + '%' }"
                />
              </div>
            </div>
            <div v-if="job.thumb_url" class="mt-2">
              <img :src="job.thumb_url" class="w-16 h-16 object-cover rounded border border-gray-200 dark:border-gray-600" />
            </div>
            <p v-if="job.error_msg" class="mt-1 text-xs text-red-500">{{ job.error_msg }}</p>
          </div>
          <div class="flex flex-col items-end gap-1 text-xs text-gray-400">
            <span>{{ job.created_at ? new Date(job.created_at).toLocaleString() : '-' }}</span>
            <button
              v-if="job.status === 'pending' || job.status === 'processing'"
              @click="handleCancel(job.id)"
              class="text-red-500 hover:underline"
            >
              取消
            </button>
          </div>
        </div>
      </div>

      <!-- Pagination -->
      <div class="flex justify-center gap-2 py-4">
        <button
          @click="page--; loadJobs()"
          :disabled="page <= 1"
          class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                 text-gray-600 dark:text-gray-300 disabled:opacity-30 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          上一页
        </button>
        <span class="px-3 py-1.5 text-sm text-gray-500">第 {{ page }} 页</span>
        <button
          @click="page++; loadJobs()"
          :disabled="jobs.length < pageSize"
          class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                 text-gray-600 dark:text-gray-300 disabled:opacity-30 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          下一页
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { thumbnailsApi } from '../api/index'

const stats = ref({})
const jobs = ref([])
const jobsLoading = ref(false)
const statusFilter = ref('pending')
const page = ref(1)
const pageSize = 20
const isPolling = ref(false)

let pollTimer = null

const showBatch = ref(false)
const batchInput = ref('')
const batchLoading = ref(false)
const batchResult = ref(null)

const statCards = [
  { key: 'pending', label: '待处理', color: 'text-amber-500' },
  { key: 'processing', label: '处理中', color: 'text-blue-500' },
  { key: 'completed', label: '已完成', color: 'text-green-500' },
  { key: 'failed', label: '失败', color: 'text-red-500' },
  { key: 'total', label: '总计', color: 'text-gray-600 dark:text-gray-300' },
]

const statusOptions = [
  { value: 'pending', label: '待处理' },
  { value: 'processing', label: '处理中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

function jobStatusClass(status) {
  const map = {
    pending: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
    processing: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
    completed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
    failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
    cancelled: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
  }
  return map[status] || ''
}

function phaseLabel(job) {
  const map = {
    pending: '⏳ 等待中',
    processing: '🔄 准备中',
    downloading: '📥 下载中',
    generating: '🎨 生成中',
    completed: '✅ 完成',
    failed: '❌ 失败',
    cancelled: '⛔ 已取消',
  }
  return map[job.phase] || job.status || ''
}

function progressColor(job) {
  const map = {
    processing: 'bg-blue-400',
    downloading: 'bg-indigo-400',
    generating: 'bg-purple-400',
  }
  return map[job.phase] || 'bg-blue-500'
}

// Check if there are any active jobs needing polling
const hasActive = computed(() =>
  jobs.value.some(
    (j) => j.status === 'pending' || j.status === 'processing'
  )
)

function startPolling() {
  if (pollTimer) return
  isPolling.value = true
  pollTimer = setInterval(() => {
    loadJobs()
    loadStats()
  }, 2000)
}

function stopPolling() {
  isPolling.value = false
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// Watch hasActive: if active jobs appear, start polling; if all done, stop
watch(hasActive, (active) => {
  active ? startPolling() : stopPolling()
})

async function loadStats() {
  try {
    const { data } = await thumbnailsApi.stats()
    stats.value = data
  } catch {
    // handled by interceptor
  }
}

async function loadJobs() {
  jobsLoading.value = true
  try {
    const { data } = await thumbnailsApi.listJobs({
      status: statusFilter.value,
      offset: (page.value - 1) * pageSize,
      limit: pageSize,
    })
    jobs.value = data.jobs || []
  } catch {
    jobs.value = []
  } finally {
    jobsLoading.value = false
  }
}

async function handleCancel(jobId) {
  try {
    await thumbnailsApi.cancel(jobId)
    await Promise.all([loadJobs(), loadStats()])
  } catch {
    // handled by interceptor
  }
}

async function handleBatchGenerate() {
  const ids = batchInput.value
    .split(/[,\n]+/)
    .map(s => parseInt(s.trim(), 10))
    .filter(n => !isNaN(n) && n > 0)
  if (ids.length === 0) return
  batchLoading.value = true
  try {
    const { data } = await thumbnailsApi.generateBatch(ids.slice(0, 100))
    batchResult.value = data
    await Promise.all([loadJobs(), loadStats()])
  } catch {
    // handled by interceptor
  } finally {
    batchLoading.value = false
  }
}

watch(statusFilter, () => {
  page.value = 1
  loadJobs()
})

onMounted(async () => {
  await Promise.all([loadStats(), loadJobs()])
  // Start polling if there are active jobs on mount
  if (stats.value?.pending > 0 || stats.value?.processing > 0) {
    startPolling()
  }
})

onUnmounted(stopPolling)
</script>
