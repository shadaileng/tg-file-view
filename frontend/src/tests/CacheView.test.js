import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import CacheView from '../views/CacheView.vue'

const mockStats = vi.hoisted(() => vi.fn())
const mockEvict = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  cacheApi: {
    stats: mockStats,
    evict: mockEvict,
  },
}))

describe('CacheView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载中显示 loading', () => {
    mockStats.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(CacheView)
    expect(wrapper.text()).toContain('加载中')
  })

  it('加载完成后显示缓存统计', async () => {
    mockStats.mockResolvedValue({
      data: { file_count: 50, total_size_mb: 123.4, max_size_mb: 500, usage_percent: 24.68 },
    })
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
    const wrapper = mount(CacheView)
    await flushPromises()

    const evictBtn = wrapper.findAll('button').filter(b => b.text().includes('手动淘汰'))
    await evictBtn[0].trigger('click')
    await flushPromises()

    expect(mockEvict).toHaveBeenCalled()
    expect(wrapper.text()).toContain('淘汰完成')
  })
})
