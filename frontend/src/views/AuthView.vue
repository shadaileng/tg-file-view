<template>
  <div class="max-w-lg mx-auto space-y-6">
    <h2 class="text-xl font-bold text-gray-800 dark:text-gray-200">Telegram 登录</h2>

    <!-- Status display -->
    <div
      v-if="authInfo.status === 'authorized'"
      class="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-xl p-5"
    >
      <div class="flex items-center gap-3">
        <span class="text-2xl">&#9989;</span>
        <div>
          <p class="font-semibold text-green-700 dark:text-green-300">已授权</p>
          <p class="text-sm text-green-600 dark:text-green-400">Telegram 客户端已成功连接</p>
        </div>
      </div>
      <button
        @click="handleLogout"
        :disabled="loading"
        class="mt-3 px-4 py-2 text-sm bg-red-500 hover:bg-red-600 text-white rounded-lg transition-colors disabled:opacity-50"
      >
        退出登录
      </button>
    </div>

    <!-- Not configured -->
    <div
      v-else-if="authInfo.status === 'not_configured'"
      class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl p-5"
    >
      <p class="font-semibold text-amber-700 dark:text-amber-300">未配置</p>
      <p class="text-sm text-amber-600 dark:text-amber-400 mt-1">
        请在环境变量中设置 TG_API_ID 和 TG_API_HASH
      </p>
    </div>

    <!-- Login flow -->
    <div
      v-else
      class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-6 space-y-4"
    >
      <!-- Step 1: Send code -->
      <div v-if="step === 'send'" class="space-y-4">
        <p class="text-sm text-gray-500 dark:text-gray-400">
          点击发送验证码到您的 Telegram 账号
        </p>
        <button
          @click="handleSendCode"
          :disabled="loading"
          class="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors disabled:opacity-50 text-sm font-medium"
        >
          {{ loading ? '发送中...' : '发送验证码' }}
        </button>
      </div>

      <!-- Step 2: Verify code -->
      <div v-else-if="step === 'verify'" class="space-y-4">
        <p class="text-sm text-gray-500 dark:text-gray-400">
          请输入 Telegram 发送的验证码
        </p>
        <input
          v-model="code"
          type="text"
          placeholder="验证码（5位数字）"
          class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600
                 bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200
                 focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
          @keyup.enter="handleVerifyCode"
        />
        <div class="flex gap-2">
          <button
            @click="handleVerifyCode"
            :disabled="loading"
            class="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors disabled:opacity-50 text-sm font-medium"
          >
            {{ loading ? '验证中...' : '验证' }}
          </button>
          <button
            @click="step = 'send'; code = ''"
            class="px-4 py-2.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
          >
            返回
          </button>
        </div>
      </div>

      <!-- Step 3: 2FA -->
      <div v-else-if="step === '2fa'" class="space-y-4">
        <p class="text-sm text-gray-500 dark:text-gray-400">
          您的账号开启了二次验证，请输入密码
        </p>
        <input
          v-model="password2fa"
          type="password"
          placeholder="2FA 密码"
          class="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600
                 bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200
                 focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
          @keyup.enter="handleVerify2FA"
        />
        <div class="flex gap-2">
          <button
            @click="handleVerify2FA"
            :disabled="loading"
            class="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors disabled:opacity-50 text-sm font-medium"
          >
            {{ loading ? '验证中...' : '确认' }}
          </button>
          <button
            @click="step = 'send'; password2fa = ''"
            class="px-4 py-2.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
          >
            返回
          </button>
        </div>
      </div>
    </div>

    <!-- Error display -->
    <div
      v-if="error"
      class="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3 text-sm text-red-700 dark:text-red-300"
    >
      {{ error }}
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { authApi } from '../api/index'

const loading = ref(false)
const error = ref('')
const step = ref('send') // send | verify | 2fa
const code = ref('')
const password2fa = ref('')
const authInfo = ref({ status: 'unknown', is_authorized: false })

async function refreshStatus() {
  try {
    const { data } = await authApi.status()
    authInfo.value = data
    if (data.is_authorized) {
      step.value = 'done'
    }
  } catch {
    authInfo.value = { status: 'error', is_authorized: false }
  }
}

async function handleSendCode() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await authApi.sendCode()
    authInfo.value.status = data.auth_state
    step.value = 'verify'
    code.value = ''
    password2fa.value = ''
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '发送失败'
  } finally {
    loading.value = false
  }
}

async function handleVerifyCode() {
  if (!code.value.trim()) {
    error.value = '请输入验证码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const { data } = await authApi.verifyCode(code.value)
    if (data.status === 'authorized') {
      authInfo.value = { status: 'authorized', is_authorized: true }
      step.value = 'done'
    } else if (data.status === '2fa_required') {
      step.value = '2fa'
      password2fa.value = ''
    }
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '验证失败'
  } finally {
    loading.value = false
  }
}

async function handleVerify2FA() {
  if (!password2fa.value.trim()) {
    error.value = '请输入 2FA 密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await authApi.verify2FA(password2fa.value)
    authInfo.value = { status: 'authorized', is_authorized: true }
    step.value = 'done'
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '2FA 验证失败'
  } finally {
    loading.value = false
  }
}

async function handleLogout() {
  loading.value = true
  try {
    await authApi.logout()
    authInfo.value = { status: 'disconnected', is_authorized: false }
    step.value = 'send'
    code.value = ''
    password2fa.value = ''
    error.value = ''
  } catch (e) {
    error.value = e.response?.data?.detail || e.message || '退出失败'
  } finally {
    loading.value = false
  }
}

onMounted(refreshStatus)
</script>
