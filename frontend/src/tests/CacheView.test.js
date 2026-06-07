import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import CacheView from '../views/CacheView.vue'

const mockStats = vi.hoisted(() => vi.fn())
const mockEvict = vi.hoisted(() => vi.fn())
const mockRecords = vi.hoisted(() => vi.fn())
const mockDeleteRecord = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  cacheApi: {
    stats: mockStats,
    evict: mockEvict,
    records: mockRecords,
    deleteRecord: mockDeleteRecord,
  },
}))

describe('CacheView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载中显示 loading', () => {
    mockStats.mockReturnValue(new Promise(() => {}))
    mockRecords.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(CacheView)
    expect(wrapper.text()).toContain('加载中')
  })

  it('加载完成后显示缓存统计', async () => {
    mockStats.mockResolvedValue({
      data: { file_count: 50, total_size_mb: 123.4, max_size_mb: 500, usage_percent: 24.68 },
    })
    mockRecords.mockResolvedValue({ data: { records: [], total: 0 } })
    const wrapper = mount(CacheView)
    await flushPromises()

    expect(wrapper.text()).toContain('50')
    expect(wrapper.text()).toContain('123.4 MB')
    expect(wrapper.text()).toContain('500 MB')
    expect(wrapper.text()).toContain('24.7')
  })

  it('无限缓存上限显示', async () => {
    mockStats.mockResolvedValue({
      data: { file_count: 10, total_size_mb: 5.0, max_size_mb: 0, usage_percent: 0 },
    })
    mockRecords.mockResolvedValue({ data: { records: [], total: 0 } })
    const wrapper = mount(CacheView)
    await flushPromises()

    expect(wrapper.text()).toContain('无限')
  })

  it('手动淘汰缓存', async () => {
    mockStats.mockResolvedValue({
      data: { file_count: 50, total_size_mb: 200, max_size_mb: 200, usage_percent: 100 },
    })
    mockEvict.mockResolvedValue({
      data: { evicted_count: 30, freed_mb: 150, total_size_mb: 50 },
    })
    mockRecords.mockResolvedValue({ data: { records: [], total: 0 } })
    const wrapper = mount(CacheView)
    await flushPromises()

    const evictBtn = wrapper.findAll('button').filter(b => b.text().includes('手动淘汰'))
    await evictBtn[0].trigger('click')
    await flushPromises()

    expect(mockEvict).toHaveBeenCalled()
    expect(wrapper.text()).toContain('淘汰完成')
  })

  it('显示缓存文件列表', async () => {
    mockStats.mockResolvedValue({
      data: { file_count: 2, total_size_mb: 3.0, max_size_mb: 100, usage_percent: 3 },
    })
    mockRecords.mockResolvedValue({
      data: {
        records: [
          { id: 1, file_id: 1, file_name: 'a.jpg', channel_title: 'Ch1', file_size: 1024, status: 'cached', cached_at: '2026-01-01T00:00:00Z' },
          { id: 2, file_id: 2, file_name: 'b.pdf', channel_title: 'Ch2', file_size: 2048, status: 'caching', cached_at: null },
        ],
        total: 2,
      },
    })
    const wrapper = mount(CacheView)
    await flushPromises()

    expect(wrapper.text()).toContain('缓存文件列表')
    expect(wrapper.text()).toContain('a.jpg')
    expect(wrapper.text()).toContain('Ch1')
    expect(wrapper.text()).toContain('Ch2')
    expect(wrapper.text()).toContain('1.0 KB')
    expect(wrapper.text()).toContain('已缓存')
    expect(wrapper.text()).toContain('缓存中')
    expect(wrapper.text()).toContain('共 2 个文件')
  })

  it('删除缓存记录', async () => {
    mockStats.mockResolvedValue({
      data: { file_count: 1, total_size_mb: 1.0, max_size_mb: 100, usage_percent: 1 },
    })
    mockRecords.mockResolvedValue({
      data: {
        records: [{ id: 1, file_id: 1, file_name: 'a.jpg', channel_title: 'Ch1', file_size: 1024, status: 'cached', cached_at: '2026-01-01T00:00:00Z' }],
        total: 1,
      },
    })
    mockDeleteRecord.mockResolvedValue({ data: { status: 'ok' } })
    const wrapper = mount(CacheView)
    await flushPromises()

    const delBtn = wrapper.findAll('button').filter(b => b.text().trim() === '删除')
    expect(delBtn.length).toBeGreaterThan(0)
    await delBtn[0].trigger('click')
    await flushPromises()

    expect(mockDeleteRecord).toHaveBeenCalledWith(1)
  })

  it('空缓存记录显示提示', async () => {
    mockStats.mockResolvedValue({
      data: { file_count: 0, total_size_mb: 0, max_size_mb: 100, usage_percent: 0 },
    })
    mockRecords.mockResolvedValue({ data: { records: [], total: 0 } })
    const wrapper = mount(CacheView)
    await flushPromises()

    expect(wrapper.text()).toContain('暂无缓存记录')
  })
})
