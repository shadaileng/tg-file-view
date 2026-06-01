import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// Response interceptor: unified error toasting
api.interceptors.response.use(
  (res) => res,
  (err) => {
    const detail = err.response?.data?.detail || err.message || 'Unknown error'
    const event = new CustomEvent('app-toast', {
      detail: { type: 'error', message: detail },
    })
    window.dispatchEvent(event)
    return Promise.reject(err)
  },
)

// --- Auth ---
export const authApi = {
  sendCode: () => api.post('/auth/send-code'),
  verifyCode: (code) => api.post('/auth/verify-code', { code }),
  verify2FA: (password) => api.post('/auth/verify-2fa', { password }),
  status: () => api.get('/auth/status'),
  logout: () => api.post('/auth/logout'),
}

// --- Channels ---
export const channelsApi = {
  list: () => api.get('/channels'),
  get: (id) => api.get(`/channels/${id}`),
  create: (data) => api.post('/channels', data),
  delete: (id) => api.delete(`/channels/${id}`),
  discover: () => api.get('/channels/discover'),
}

// --- Files ---
export const filesApi = {
  list: (channelId, { offset = 0, limit = 50 } = {}) =>
    api.get(`/channels/${channelId}/files`, { params: { offset, limit } }),
  get: (id) => api.get(`/files/${id}`),
  download: (id) => api.get(`/files/${id}/download`, { responseType: 'blob' }),
  cache: (id) => api.post(`/files/${id}/cache`),
  deleteCache: (id) => api.delete(`/files/${id}/cache`),
}

// --- Sync ---
export const syncApi = {
  trigger: (channelId) => api.post(`/channels/${channelId}/sync`),
  listTasks: (channelId) => api.get(`/channels/${channelId}/sync/tasks`),
  getTask: (taskId) => api.get(`/sync/tasks/${taskId}`),
  cancel: (taskId) => api.post(`/sync/tasks/${taskId}/cancel`),
}

// --- Thumbnails ---
export const thumbnailsApi = {
  generateSingle: (fileId) => api.post(`/files/${fileId}/thumbnail`),
  generateBatch: (fileIds) => api.post('/thumbnails/generate-batch', { file_ids: fileIds }),
  listJobs: (params = {}) => api.get('/thumbnails/jobs', { params }),
  getJob: (jobId) => api.get(`/thumbnails/jobs/${jobId}`),
  stats: () => api.get('/thumbnails/stats'),
  cancel: (jobId) => api.post(`/thumbnails/jobs/${jobId}/cancel`),
}

// --- Cache ---
export const cacheApi = {
  stats: () => api.get('/cache/stats'),
  evict: () => api.post('/cache/evict'),
}

// --- Config ---
export const configApi = {
  list: () => api.get('/config'),
  get: (key) => api.get(`/config/${key}`),
  update: (key, value, adminPassword) =>
    api.put(`/config/${key}`, { value }, { headers: { 'x-admin-password': adminPassword } }),
}

// --- Health ---
export const healthApi = {
  check: () => api.get('/health'),
}

export default api
