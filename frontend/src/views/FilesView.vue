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
      <!-- File count info -->
      <p class="text-sm text-gray-400">
        共 {{ totalFiles }} 个文件，当前第 {{ offset / limit + 1 }} 页
      </p>

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
                class="w-full h-full object-cover"
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
              class="flex-1 px-2 py-1.5 text-xs bg-green-50 dark:bg-green-900/20
                     text-green-600 dark:text-green-400 rounded hover:bg-green-100 dark:hover:bg-green-900/30
                     transition-colors"
            >
              缓存
            </button>
            <button
              @click="handleDeleteCache(file)"
              v-if="file.is_cached"
              class="flex-1 px-2 py-1.5 text-xs bg-red-50 dark:bg-red-900/20
                     text-red-600 dark:text-red-400 rounded hover:bg-red-100 dark:hover:bg-red-900/30
                     transition-colors"
            >
              清缓存
            </button>
            <button
              @click="handleGenerateThumb(file)"
              v-if="!file.thumb_path"
              class="flex-1 px-2 py-1.5 text-xs bg-amber-50 dark:bg-amber-900/20
                     text-amber-600 dark:text-amber-400 rounded hover:bg-amber-100 dark:hover:bg-amber-900/30
                     transition-colors"
            >
              缩略图
            </button>
          </div>
        </div>
      </div>

      <!-- Pagination -->
      <div v-if="totalFiles > limit" class="flex items-center justify-center gap-2 py-4">
        <button
          @click="changePage(-1)"
          :disabled="offset === 0"
          class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600
                 text-gray-600 dark:text-gray-300 disabled:opacity-30 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          上一页
        </button>
        <span class="text-sm text-gray-500 dark:text-gray-400">
          {{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(totalFiles / limit) }}
        </span>
        <button
          @click="changePage(1)"
          :disabled="offset + limit >= totalFiles"
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
import { ref, onMounted } from 'vue'
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
    photo: '\u{1F5BC}',    // 🖼
    video: '\u{1F3AC}',    // 🎬
    document: '\u{1F4C4}', // 📄
    audio: '\u{1F3B5}',    // 🎵
    sticker: '\u{1F3A8}',  // 🎨
    gif: '\u{1F3A5}',      // 🎥
  }
  return icons[type] || '\u{1F4CE}' // 📎
}

async function loadChannels() {
  try {
    const { data } = await channelsApi.list()
    channels.value = data
    // Auto-select from query param
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
  await loadFiles()
}

async function loadFiles() {
  if (!selectedChannelId.value) return
  filesLoading.value = true
  try {
    const { data } = await filesApi.list(selectedChannelId.value, {
      offset: offset.value,
      limit: limit.value,
    })
    files.value = data.files
    totalFiles.value = data.total
  } catch {
    files.value = []
  } finally {
    filesLoading.value = false
  }
}

function changePage(dir) {
  const newOffset = offset.value + dir * limit.value
  if (newOffset < 0 || newOffset >= totalFiles.value) return
  offset.value = newOffset
  loadFiles()
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
  try {
    await filesApi.cache(file.id)
    file.is_cached = true
  } catch {
    // handled by interceptor
  }
}

async function handleDeleteCache(file) {
  try {
    await filesApi.deleteCache(file.id)
    file.is_cached = false
  } catch {
    // handled by interceptor
  }
}

async function handleGenerateThumb(file) {
  try {
    await thumbnailsApi.generateSingle(file.id)
    // Show a brief toast
    window.dispatchEvent(new CustomEvent('app-toast', {
      detail: { type: 'success', message: '缩略图任务已创建: ' + file.file_name },
    }))
  } catch {
    // handled by interceptor
  }
}

onMounted(loadChannels)
</script>
