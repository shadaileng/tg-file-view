import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHashHistory } from 'vue-router'
import FilesView from '../views/FilesView.vue'

const mockChannelsList = vi.hoisted(() => vi.fn())
const mockFilesList = vi.hoisted(() => vi.fn())

const mockCache = vi.hoisted(() => vi.fn())
const mockDeleteCache = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  channelsApi: { list: mockChannelsList },
  filesApi: { list: mockFilesList, cache: mockCache, deleteCache: mockDeleteCache },
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

function makeFiles(count, startId = 1) {
  return Array.from({ length: count }, (_, i) => ({
    id: startId + i,
    file_name: `file_${i}.jpg`,
    file_type: 'photo',
    file_size: 1024,
    mime_type: 'image/jpeg',
    is_cached: false,
  }))
}

describe('FilesView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('文件少于等于一页时不显示分页控件', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 30 }] })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(30), total: 30 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('上一页')
    expect(wrapper.text()).not.toContain('下一页')
  })

  it('文件多于一页时显示分页控件', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 120 }] })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 120 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('上一页')
    expect(wrapper.text()).toContain('下一页')
    expect(wrapper.text()).toContain('/ 3')
  })

  it('输入页码按回车后跳转到指定页', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 200 }] })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 200 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()
    mockFilesList.mockClear()
    const input = wrapper.find('input[type="number"]')
    await input.setValue(3)
    await input.trigger('keyup.enter')
    await flushPromises()
    expect(mockFilesList).toHaveBeenCalledWith(1, { offset: 100, limit: 50 })
  })

  it('页码边界保护：小于1时归为1', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 200 }] })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 200 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()
    mockFilesList.mockClear()
    const input = wrapper.find('input[type="number"]')
    await input.setValue(0)
    await input.trigger('keyup.enter')
    await flushPromises()
    expect(mockFilesList).toHaveBeenCalledWith(1, { offset: 0, limit: 50 })
  })

  it('页码边界保护：大于最大页时归为最大页', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 200 }] })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 200 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()
    mockFilesList.mockClear()
    const input = wrapper.find('input[type="number"]')
    await input.setValue(999)
    await input.trigger('keyup.enter')
    await flushPromises()
    expect(mockFilesList).toHaveBeenCalledWith(1, { offset: 150, limit: 50 })
  })

  it('点击下一页按钮', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 200 }] })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 200 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()
    mockFilesList.mockClear()
    const nextBtn = wrapper.findAll('button').filter(b => b.text().trim() === '下一页')
    await nextBtn[0].trigger('click')
    await flushPromises()
    expect(mockFilesList).toHaveBeenCalledWith(1, { offset: 50, limit: 50 })
  })

  it('切换频道时重置页码和文件列表', async () => {
    mockChannelsList.mockResolvedValue({
      data: [
        { id: 1, title: 'Ch1', file_count: 200 },
        { id: 2, title: 'Ch2', file_count: 80 },
      ],
    })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 200 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtns = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtns[0].trigger('click')
    await flushPromises()
    mockFilesList.mockClear()
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 80 } })
    const ch2Btns = wrapper.findAll('button').filter(b => b.text().includes('Ch2'))
    await ch2Btns[0].trigger('click')
    await flushPromises()
    expect(mockFilesList).toHaveBeenCalledWith(2, { offset: 0, limit: 50 })
  })

  it('无限滚动追加加载更多文件', async () => {
    let observerCallback
    window.IntersectionObserver = class {
      constructor(cb) { observerCallback = cb }
      observe() {}
      disconnect() {}
    }
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 120 }] })
    mockFilesList.mockResolvedValueOnce({ data: { files: makeFiles(50), total: 120 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtns = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtns[0].trigger('click')
    await flushPromises()

    mockFilesList.mockResolvedValueOnce({ data: { files: makeFiles(50, 51), total: 120 } })
    if (observerCallback) {
      observerCallback([{ isIntersecting: true }])
    }
    await flushPromises()

    expect(mockFilesList).toHaveBeenCalledTimes(2)
    expect(mockFilesList).toHaveBeenLastCalledWith(1, { offset: 50, limit: 50 })
  })

  it('全部加载完显示已加载全部提示', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 50 }] })
    mockFilesList.mockResolvedValue({ data: { files: makeFiles(50), total: 50 } })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtns = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtns[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('已加载全部')
  })

  it('图片预览使用直接 /api/files/{id}/view URL', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockFilesList.mockResolvedValue({
      data: {
        files: [{ id: 42, file_name: 'test.jpg', file_type: 'photo', file_size: 1024, mime_type: 'image/jpeg', is_cached: false }],
        total: 1,
      },
    })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtns = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtns[0].trigger('click')
    await flushPromises()
    const viewBtn = wrapper.findAll('button').filter(b => b.text().trim() === '查看')
    await viewBtn[0].trigger('click')
    await flushPromises()
    // Teleport renders outside wrapper, check document.body
    expect(document.body.innerHTML).toContain('/api/files/42/view')
  })

  it('缓存按钮点击后显示缓存中并禁用', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockFilesList.mockResolvedValue({
      data: {
        files: [{ id: 5, file_name: 'test.jpg', file_type: 'photo', file_size: 1024, mime_type: 'image/jpeg', is_cached: false }],
        total: 1,
      },
    })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtns = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtns[0].trigger('click')
    await flushPromises()

    const cacheBtn = wrapper.findAll('button').filter(b => b.text().trim() === '缓存')
    expect(cacheBtn[0].attributes('disabled')).toBeUndefined()

    // Click cache, verify button is disabled and text changes
    let resolveCache
    mockCache.mockReturnValue(new Promise((r) => { resolveCache = r }))
    await cacheBtn[0].trigger('click')
    await flushPromises()

    const disabledBtn = wrapper.findAll('button').filter(b => b.text().trim() === '缓存中...')
    expect(disabledBtn.length).toBeGreaterThan(0)
    expect(disabledBtn[0].attributes('disabled')).toBeDefined()

    // Resolve and verify
    resolveCache()
    await flushPromises()
    expect(mockCache).toHaveBeenCalledWith(5)
  })

  it('清缓存按钮点击后显示清缓存中', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockFilesList.mockResolvedValue({
      data: {
        files: [{ id: 6, file_name: 'test2.jpg', file_type: 'photo', file_size: 1024, mime_type: 'image/jpeg', is_cached: true }],
        total: 1,
      },
    })
    const wrapper = mount(FilesView, { global: { plugins: [router] } })
    await flushPromises()
    const chBtns = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtns[0].trigger('click')
    await flushPromises()

    let resolveDelete
    mockDeleteCache.mockReturnValue(new Promise((r) => { resolveDelete = r }))
    const delBtn = wrapper.findAll('button').filter(b => b.text().trim() === '清缓存')
    await delBtn[0].trigger('click')
    await flushPromises()

    const disabledDel = wrapper.findAll('button').filter(b => b.text().trim() === '清缓存中...')
    expect(disabledDel.length).toBeGreaterThan(0)
    resolveDelete()
    await flushPromises()
    expect(mockDeleteCache).toHaveBeenCalledWith(6)
  })
})
