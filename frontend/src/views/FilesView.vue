<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">文件浏览</h2>
    </div>

    <!-- Channel selector -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <label class="block text-xs text-gray-400 mb-2">选择频道</label>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="ch in channels"
          :key="ch.id"
          @click="selectChannel(ch)"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          :class="selectedChannelId === ch.id
            ? 'bg-indigo-600 text-white'
            : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
        >
          {{ ch.title }}
          <span class="ml-1 text-xs opacity-70">{{ ch.file_count || 0 }}</span>
        </button>
      </div>
    </div>

    <!-- File grid -->
    <div v-if="filesLoading" class="text-center text-gray-400 py-12">加载中...</div>

    <div v-else-if="!selectedChannelId" class="text-center py-16">
      <p class="text-gray-400 dark:text-gray-500 text-sm">请先选择一个频道查看文件</p>
      <router-link to="/channels" class="mt-2 inline-block text-sm text-indigo-500 hover:underline">
        前往频道管理
      </router-link>
    </div>

    <div v-else-if="files.length === 0 && !filesLoading" class="text-center py-16">
      <p class="text-gray-400 dark:text-gray-500 text-sm">该频道暂无文件，尝试触发同步</p>
      <button
        @click="$router.push('/sync')"
        class="mt-2 inline-block text-sm text-indigo-500 hover:underline"
      >
        前往同步管理
      </button>
    </div>

    <div v-else class="space-y-4">
      <!-- File count + pagination controls -->
      <div class="flex items-center justify-between">
        <p class="text-sm text-gray-400">共 {{ totalFiles }} 个文件</p>
        <div v-if="totalFiles > limit" class="flex items-center gap-2">
          <button
            @click="goToPage(currentPage - 1)"
            :disabled="currentPage <= 1"
            class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                   text-gray-600 dark:text-gray-300 disabled:opacity-30 hover:bg-gray-100 dark:hover:bg-gray-700"
          >上一页</button>
          <span class="text-sm text-gray-500 dark:text-gray-400">
            第 <input v-model.number="pageInput" type="number" :min="1" :max="totalPages"
              @keyup.enter="jumpToPage" @blur="jumpToPage"
              class="w-16 text-center border border-gray-300 dark:border-gray-600 rounded px-1 py-0.5
                     bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-sm" /> / {{ totalPages }} 页
          </span>
          <button
            @click="goToPage(currentPage + 1)"
            :disabled="currentPage >= totalPages"
            class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                   text-gray-600 dark:text-gray-300 disabled:opacity-30 hover:bg-gray-100 dark:hover:bg-gray-700"
          >下一页</button>
        </div>
      </div>

      <!-- Card grid -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        <div
          v-for="file in files"
          :key="file.id"
          class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700
                 overflow-hidden hover:shadow-md transition-shadow group"
        >
          <!-- Thumbnail -->
          <div class="relative">
            <div
              v-if="file.thumb_path"
              class="w-full h-40 bg-gray-100 dark:bg-gray-700 flex items-center justify-center overflow-hidden"
            >
              <img
                :src="'/thumbnails/' + file.thumb_path"
                :alt="file.file_name"
                class="w-full h-full object-contain"
                loading="lazy"
              />
            </div>
            <div
              v-else
              class="w-full h-40 bg-gray-100 dark:bg-gray-700 flex items-center justify-center"
            >
              <span class="text-3xl text-gray-400">{{ fileIcon(file.file_type) }}</span>
            </div>

            <!-- Cache badge -->
            <span
              v-if="file.is_cached"
              class="absolute top-2 right-2 px-2 py-0.5 text-xs bg-green-500 text-white rounded-full"
            >
              已缓存
            </span>
          </div>

          <!-- Info -->
          <div class="p-3 space-y-1">
            <p class="text-sm font-medium text-gray-800 dark:text-gray-200 truncate" :title="file.file_name">
              {{ file.file_name }}
            </p>
            <div class="flex justify-between text-xs text-gray-400">
              <span>{{ file.file_type }}</span>
              <span>{{ formatSize(file.file_size) }}</span>
            </div>
          </div>

          <!-- Actions -->
          <div class="px-3 pb-3 flex gap-1">
            <button
              @click="handleView(file)"
              class="flex-1 px-2 py-1.5 text-xs bg-blue-50 dark:bg-blue-900/20
                     text-blue-600 dark:text-blue-400 rounded hover:bg-blue-100 dark:hover:bg-blue-900/30
                     transition-colors"
            >
              查看
            </button>
            <button
              @click="handleDownload(file)"
              class="flex-1 px-2 py-1.5 text-xs bg-indigo-50 dark:bg-indigo-900/20
                     text-indigo-600 dark:text-indigo-400 rounded hover:bg-indigo-100 dark:hover:bg-indigo-900/30
                     transition-colors"
            >
              下载
            </button>
            <button
              @click="handleCache(file)"
              v-if="!file.is_cached"
              :disabled="processingFiles.has(file.id)"
              class="flex-1 px-2 py-1.5 text-xs bg-green-50 dark:bg-green-900/20
                     text-green-600 dark:text-green-400 rounded
                     transition-colors"
              :class="processingFiles.has(file.id) ? 'opacity-50 cursor-not-allowed' : 'hover:bg-green-100 dark:hover:bg-green-900/30'"
            >
              {{ processingFiles.has(file.id) ? '缓存中...' : '缓存' }}
            </button>
            <button
              @click="handleDeleteCache(file)"
              v-if="file.is_cached"
              :disabled="processingFiles.has(file.id)"
              class="flex-1 px-2 py-1.5 text-xs bg-red-50 dark:bg-red-900/20
                     text-red-600 dark:text-red-400 rounded
                     transition-colors"
              :class="processingFiles.has(file.id) ? 'opacity-50 cursor-not-allowed' : 'hover:bg-red-100 dark:hover:bg-red-900/30'"
            >
              {{ processingFiles.has(file.id) ? '清缓存中...' : '清缓存' }}
            </button>
            <button
              @click="handleGenerateThumb(file)"
              v-if="!file.thumb_path"
              :disabled="processingFiles.has(file.id)"
              class="flex-1 px-2 py-1.5 text-xs bg-amber-50 dark:bg-amber-900/20
                     text-amber-600 dark:text-amber-400 rounded
                     transition-colors"
              :class="processingFiles.has(file.id) ? 'opacity-50 cursor-not-allowed' : 'hover:bg-amber-100 dark:hover:bg-amber-900/30'"
            >
              {{ processingFiles.has(file.id) ? '生成中...' : '缩略图' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Infinite scroll sentinel -->
      <div ref="sentinelEl" class="h-4"></div>

      <!-- Loading more indicator -->
      <div v-if="loadingMore" class="text-center text-gray-400 py-4">
        <div class="inline-block w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
        <p class="text-sm mt-1">加载更多...</p>
      </div>

      <!-- All loaded -->
      <div v-else-if="files.length >= totalFiles && totalFiles > 0" class="text-center text-gray-400 text-sm py-4">
        已加载全部 {{ totalFiles }} 个文件
      </div>
    </div>

    <!-- Preview Modal -->
    <Teleport to="body">
      <div
        v-if="preview.visible"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
        @click.self="closePreview"
      >
        <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-4xl w-full mx-4 max-h-[90vh] flex flex-col overflow-hidden">
          <!-- Header -->
          <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
            <span class="text-sm font-medium text-gray-800 dark:text-gray-200 truncate max-w-[70%]">
              {{ preview.file?.file_name }}
            </span>
            <button
              @click="closePreview"
              class="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded transition-colors"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- Body -->
          <div class="flex-1 overflow-auto p-4 flex items-center justify-center min-h-[200px]">
            <!-- Error -->
            <div v-if="preview.error" class="text-center py-8">
              <p class="text-red-500 text-sm">{{ preview.error }}</p>
              <button
                @click="closePreview"
                class="mt-2 px-3 py-1 text-xs text-indigo-500 hover:underline"
              >关闭</button>
            </div>

            <!-- Image -->
            <img
              v-else-if="preview.type === 'image'"
              :src="preview.url"
              :alt="preview.file?.file_name"
              class="max-w-full max-h-[75vh] object-contain rounded"
              @error="preview.error = '图片加载失败'"
            />

            <!-- Video -->
            <video
              v-else-if="preview.type === 'video'"
              :src="preview.url"
              controls
              class="max-w-full max-h-[75vh] rounded"
              @error="preview.error = '视频加载失败'"
            ></video>

            <!-- Audio -->
            <audio
              v-else-if="preview.type === 'audio'"
              :src="preview.url"
              controls
              class="w-full"
              @error="preview.error = '音频加载失败'"
            ></audio>

            <!-- Unsupported fallback -->
            <div v-else class="text-center py-8 space-y-3">
              <p class="text-gray-400 dark:text-gray-500 text-sm">该文件类型不支持浏览器内预览</p>
              <div class="space-y-1 text-xs text-gray-400 dark:text-gray-500">
                <p>文件名：{{ preview.file?.file_name }}</p>
                <p>类型：{{ preview.file?.mime_type }}</p>
                <p>大小：{{ formatSize(preview.file?.file_size) }}</p>
              </div>
              <button
                @click="handleDownload(preview.file)"
                class="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
              >下载文件</button>
            </div>
          </div>

          <!-- Footer -->
          <div v-if="preview.file && !preview.error" class="px-4 py-2 border-t border-gray-200 dark:border-gray-700 flex justify-between items-center">
            <span class="text-xs text-gray-400">
              {{ formatSize(preview.file.file_size) }} · {{ preview.file.mime_type }}
            </span>
            <div class="flex gap-2">
              <button
                @click="handleDownload(preview.file)"
                class="px-3 py-1 text-xs bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400 rounded hover:bg-indigo-100 dark:hover:bg-indigo-900/30 transition-colors"
              >下载</button>
              <button
                @click="closePreview"
                class="px-3 py-1 text-xs bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 rounded hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
              >关闭</button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { channelsApi, filesApi, thumbnailsApi } from '../api/index'

const route = useRoute()
const channels = ref([])
const selectedChannelId = ref(null)
const files = ref([])
const totalFiles = ref(0)
const offset = ref(0)
const limit = ref(50)
const filesLoading = ref(false)
const loadingMore = ref(false)
const pageInput = ref(1)
const sentinelEl = ref(null)
const processingFiles = ref(new Set())

let observer = null

const currentPage = computed(() => Math.floor(offset.value / limit.value) + 1)
const totalPages = computed(() => Math.ceil(totalFiles.value / limit.value))

const preview = reactive({
  visible: false,
  error: '',
  type: '',
  url: '',
  file: null,
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

function fileIcon(type) {
  const icons = {
    photo: '\u{1F5BC}',
    video: '\u{1F3AC}',
    document: '\u{1F4C4}',
    audio: '\u{1F3B5}',
    sticker: '\u{1F3A8}',
    gif: '\u{1F3A5}',
  }
  return icons[type] || '\u{1F4CE}'
}

async function loadFiles(append = false) {
  if (!selectedChannelId.value) return
  if (append) {
    loadingMore.value = true
  } else {
    filesLoading.value = true
  }
  try {
    const { data } = await filesApi.list(selectedChannelId.value, {
      offset: offset.value,
      limit: limit.value,
    })
    if (append) {
      files.value = [...files.value, ...data.files]
    } else {
      files.value = data.files
      pageInput.value = currentPage.value
    }
    totalFiles.value = data.total
  } catch {
    if (!append) files.value = []
  } finally {
    filesLoading.value = false
    loadingMore.value = false
  }
}

function loadMore() {
  if (loadingMore.value || filesLoading.value) return
  if (files.value.length >= totalFiles.value) return
  offset.value += limit.value
  loadFiles(true)
}

function jumpToPage() {
  let p = pageInput.value
  if (isNaN(p) || p < 1) p = 1
  else if (p > totalPages.value) p = totalPages.value
  goToPage(p)
}

function goToPage(page) {
  if (page < 1 || page > totalPages.value) return
  if (!selectedChannelId.value) return
  offset.value = (page - 1) * limit.value
  pageInput.value = page
  window.scrollTo({ top: 0, behavior: 'smooth' })
  loadFiles(false)
}

async function loadChannels() {
  try {
    const { data } = await channelsApi.list()
    channels.value = data
    const chParam = route.query.channel
    if (chParam) {
      const id = parseInt(chParam, 10)
      if (channels.value.find(c => c.id === id)) {
        selectChannel(channels.value.find(c => c.id === id))
      }
    }
  } catch {
    // handled by interceptor
  }
}

async function selectChannel(ch) {
  selectedChannelId.value = ch.id
  offset.value = 0
  files.value = []
  pageInput.value = 1
  window.scrollTo({ top: 0 })
  await loadFiles(false)
}

async function handleDownload(file) {
  try {
    const response = await filesApi.download(file.id)
    const url = URL.createObjectURL(response.data)
    const a = document.createElement('a')
    a.href = url
    a.download = file.file_name
    document.body.appendChild(a)
    a.click()
    URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch {
    // handled by interceptor
  }
}

async function handleCache(file) {
  if (processingFiles.value.has(file.id)) return
  processingFiles.value.add(file.id)
  try {
    const { data } = await filesApi.cache(file.id)
    file.is_cached = data.is_cached
  } catch {
    // handled by interceptor
  } finally {
    processingFiles.value.delete(file.id)
  }
}

async function handleDeleteCache(file) {
  if (processingFiles.value.has(file.id)) return
  processingFiles.value.add(file.id)
  try {
    await filesApi.deleteCache(file.id)
    file.is_cached = false
  } catch {
    // handled by interceptor
  } finally {
    processingFiles.value.delete(file.id)
  }
}

async function handleGenerateThumb(file) {
  if (processingFiles.value.has(file.id)) return
  processingFiles.value.add(file.id)
  try {
    await thumbnailsApi.generateSingle(file.id)
    window.dispatchEvent(new CustomEvent('app-toast', {
      detail: { type: 'success', message: '缩略图任务已创建: ' + file.file_name },
    }))
  } catch {
    // handled by interceptor
  } finally {
    processingFiles.value.delete(file.id)
  }
}

function handleView(file) {
  preview.visible = true
  preview.error = ''
  preview.file = file

  const mime = file.mime_type || ''
  if (mime.startsWith('image/') || mime === 'application/pdf') {
    preview.type = 'image'
  } else if (mime.startsWith('video/')) {
    preview.type = 'video'
  } else if (mime.startsWith('audio/')) {
    preview.type = 'audio'
  } else {
    preview.type = 'unsupported'
    return
  }

  preview.url = `/api/files/${file.id}/view`
}

function closePreview() {
  preview.visible = false
  preview.error = ''
  preview.type = ''
  preview.url = ''
  preview.file = null
}

onMounted(() => {
  loadChannels()
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) {
        loadMore()
      }
    },
    { rootMargin: '200px' }
  )
})

watch(sentinelEl, (el) => {
  if (el && observer) {
    observer.observe(el)
  }
})

onUnmounted(() => {
  if (observer) observer.disconnect()
})
</script>
