import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import DashboardView from '../views/DashboardView.vue'

const mockChannelsList = vi.hoisted(() => vi.fn())
const mockCacheStats = vi.hoisted(() => vi.fn())
const mockThumbStats = vi.hoisted(() => vi.fn())
const mockSyncListTasks = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  channelsApi: { list: mockChannelsList },
  cacheApi: { stats: mockCacheStats },
  thumbnailsApi: { stats: mockThumbStats },
  syncApi: { listTasks: mockSyncListTasks },
}))

describe('DashboardView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载中显示 loading 文字', () => {
    mockChannelsList.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(DashboardView)
    expect(wrapper.text()).toContain('加载中')
  })

  it('加载完成后显示统计卡片', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Test Channel', file_count: 10 }] })
    mockCacheStats.mockResolvedValue({ data: { usage_percent: 45.5 } })
    mockThumbStats.mockResolvedValue({ data: { pending: 3, processing: 2 } })
    mockSyncListTasks.mockResolvedValue({ data: [] })
    const wrapper = mount(DashboardView)
    await flushPromises()

    expect(wrapper.text()).toContain('频道数')
    expect(wrapper.text()).toContain('文件数')
    expect(wrapper.text()).toContain('缓存使用')
    expect(wrapper.text()).toContain('缩略图任务')
  })

  it('显示最近同步任务', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Test Channel', file_count: 5 }] })
    mockCacheStats.mockResolvedValue({ data: { usage_percent: 10 } })
    mockThumbStats.mockResolvedValue({ data: { pending: 0, processing: 0 } })
    mockSyncListTasks.mockResolvedValue({
      data: [
        { id: 'abc-123', status: 'completed', synced_files: 5, total_files: 10, created_at: '2026-01-01T00:00:00Z' },
      ],
    })
    const wrapper = mount(DashboardView)
    await flushPromises()

    expect(wrapper.text()).toContain('Test Channel')
    expect(wrapper.text()).toContain('completed')
    expect(wrapper.text()).toContain('5/10')
  })

  it('无同步记录时显示提示', async () => {
    mockChannelsList.mockResolvedValue({ data: [] })
    mockCacheStats.mockResolvedValue({ data: { usage_percent: 0 } })
    mockThumbStats.mockResolvedValue({ data: { pending: 0, processing: 0 } })
    const wrapper = mount(DashboardView)
    await flushPromises()

    expect(wrapper.text()).toContain('暂无同步记录')
  })
})
