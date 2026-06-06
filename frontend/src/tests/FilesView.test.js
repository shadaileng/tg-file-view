import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHashHistory } from 'vue-router'
import FilesView from '../views/FilesView.vue'

const mockChannelsList = vi.hoisted(() => vi.fn())
const mockFilesList = vi.hoisted(() => vi.fn())
const mockFilesView = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  channelsApi: { list: mockChannelsList },
  filesApi: {
    list: mockFilesList,
    view: mockFilesView,
  },
  thumbnailsApi: { generateSingle: vi.fn() },
}))

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'Dashboard', component: { template: '<div></div>' } },
    { path: '/channels', name: 'Channels', component: { template: '<div>C</div>' } },
    { path: '/sync', name: 'Sync', component: { template: '<div>S</div>' } },
    { path: '/files', name: 'Files', component: { template: '<div>F</div>' } },
  ],
})

describe('FilesView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    URL.createObjectURL = vi.fn(() => 'blob:test')
    URL.revokeObjectURL = vi.fn()
  })

  it('加载频道列表', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockFilesList.mockResolvedValue({ data: { files: [], total: 0 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Ch1')
  })

  it('选择频道后加载文件列表', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockFilesList.mockResolvedValue({
      data: {
        files: [
          { id: 1, file_name: 'photo.jpg', file_type: 'photo', file_size: 1024, mime_type: 'image/jpeg', is_cached: false },
          { id: 2, file_name: 'doc.pdf', file_type: 'document', file_size: 2048, mime_type: 'application/pdf', is_cached: true, thumb_path: 'thumb.jpg' },
        ],
        total: 2,
      },
    })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()

    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('photo.jpg')
    expect(wrapper.text()).toContain('已缓存')
  })

  it('无频道时提示选择频道', async () => {
    mockChannelsList.mockResolvedValue({ data: [] })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('请先选择一个频道')
  })
})
