import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Dashboard',
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: '/auth',
    name: 'Auth',
    component: () => import('../views/AuthView.vue'),
  },
  {
    path: '/channels',
    name: 'Channels',
    component: () => import('../views/ChannelsView.vue'),
  },
  {
    path: '/files',
    name: 'Files',
    component: () => import('../views/FilesView.vue'),
  },
  {
    path: '/sync',
    name: 'Sync',
    component: () => import('../views/SyncView.vue'),
  },
  {
    path: '/thumbnails',
    name: 'Thumbnails',
    component: () => import('../views/ThumbnailsView.vue'),
  },
  {
    path: '/cache',
    name: 'Cache',
    component: () => import('../views/CacheView.vue'),
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/SettingsView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
