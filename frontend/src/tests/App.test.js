import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from '../App.vue'

const mockHealthCheck = vi.hoisted(() => vi.fn())
const mockAuthStatus = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  healthApi: { check: mockHealthCheck },
  authApi: { status: mockAuthStatus },
}))

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'Dashboard', component: { template: '<div>Dashboard Content</div>' } },
    { path: '/channels', name: 'Channels', component: { template: '<div></div>' } },
    { path: '/files', name: 'Files', component: { template: '<div></div>' } },
    { path: '/sync', name: 'Sync', component: { template: '<div></div>' } },
    { path: '/thumbnails', name: 'Thumbnails', component: { template: '<div></div>' } },
    { path: '/cache', name: 'Cache', component: { template: '<div></div>' } },
    { path: '/settings', name: 'Settings', component: { template: '<div></div>' } },
    { path: '/auth', name: 'Auth', component: { template: '<div>Auth Page</div>' } },
  ],
})

describe('App.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.documentElement.classList.remove('dark')
  })

  it('渲染导航菜单', async () => {
    mockHealthCheck.mockResolvedValue({})
    mockAuthStatus.mockResolvedValue({ data: { is_authorized: false, status: 'disconnected' } })
    const wrapper = mount(App, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Dashboard')
    expect(wrapper.text()).toContain('频道管理')
    expect(wrapper.text()).toContain('文件浏览')
    expect(wrapper.text()).toContain('同步管理')
    expect(wrapper.text()).toContain('缩略图')
    expect(wrapper.text()).toContain('缓存管理')
    expect(wrapper.text()).toContain('系统设置')
    expect(wrapper.text()).toContain('Telegram 登录')
  })

  it('Health 检查显示正常', async () => {
    mockHealthCheck.mockResolvedValue({})
    mockAuthStatus.mockResolvedValue({ data: { is_authorized: false, status: 'disconnected' } })
    const wrapper = mount(App, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('API 正常')
  })

  it('Health 检查失败显示离线', async () => {
    mockHealthCheck.mockRejectedValue(new Error('offline'))
    mockAuthStatus.mockResolvedValue({ data: { is_authorized: false, status: 'disconnected' } })
    const wrapper = mount(App, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('API 离线')
  })

  it('已授权状态显示', async () => {
    mockHealthCheck.mockResolvedValue({})
    mockAuthStatus.mockResolvedValue({ data: { is_authorized: true, status: 'authorized' } })
    const wrapper = mount(App, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('TG 已授权')
  })

  it('未授权状态可点击跳转', async () => {
    mockHealthCheck.mockResolvedValue({})
    mockAuthStatus.mockResolvedValue({ data: { is_authorized: false, status: 'disconnected' } })
    const wrapper = mount(App, { global: { plugins: [router] } })
    await flushPromises()

    const authEl = wrapper.findAll('span').filter(s => s.text().includes('TG 未授权'))
    expect(authEl.length).toBeGreaterThan(0)
  })

  it('暗色模式切换', async () => {
    mockHealthCheck.mockResolvedValue({})
    mockAuthStatus.mockResolvedValue({ data: { is_authorized: false, status: 'disconnected' } })
    const wrapper = mount(App, { global: { plugins: [router] } })
    await flushPromises()

    const toggleBtn = wrapper.find('button[title="切换夜间模式"]')
    expect(toggleBtn.exists()).toBe(true)
    await toggleBtn.trigger('click')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
