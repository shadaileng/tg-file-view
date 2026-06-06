import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ThumbnailsView from '../views/ThumbnailsView.vue'

const mockStats = vi.hoisted(() => vi.fn())
const mockListJobs = vi.hoisted(() => vi.fn())
const mockGenerateBatch = vi.hoisted(() => vi.fn())
const mockCancel = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  thumbnailsApi: {
    stats: mockStats,
    listJobs: mockListJobs,
    generateBatch: mockGenerateBatch,
    cancel: mockCancel,
  },
}))

describe('ThumbnailsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载统计卡片', async () => {
    mockStats.mockResolvedValue({ data: { pending: 5, processing: 2, completed: 10, failed: 1, total: 18 } })
    mockListJobs.mockResolvedValue({ data: { jobs: [] } })
    const wrapper = mount(ThumbnailsView)
    await flushPromises()

    expect(wrapper.text()).toContain('待处理')
    expect(wrapper.text()).toContain('处理中')
    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).toContain('失败')
  })

  it('空任务列表提示', async () => {
    mockStats.mockResolvedValue({ data: { pending: 0, processing: 0, completed: 0, failed: 0, total: 0 } })
    mockListJobs.mockResolvedValue({ data: { jobs: [] } })
    const wrapper = mount(ThumbnailsView)
    await flushPromises()

    expect(wrapper.text()).toContain('暂无缩略图任务')
  })

  it('显示任务列表', async () => {
    mockStats.mockResolvedValue({ data: { pending: 1, processing: 0, completed: 0, failed: 0, total: 1 } })
    mockListJobs.mockResolvedValue({
      data: {
        jobs: [
          { id: 'job-1', file_name: 'test.jpg', status: 'pending', priority: 5, attempt: 0, max_retries: 3, mime_type: 'image/jpeg', created_at: '2026-01-01T00:00:00Z' },
        ],
      },
    })
    const wrapper = mount(ThumbnailsView)
    await flushPromises()

    expect(wrapper.text()).toContain('test.jpg')
    expect(wrapper.text()).toContain('pending')
  })

  it('显示批量生成弹窗', async () => {
    mockStats.mockResolvedValue({ data: { pending: 0, processing: 0, completed: 0, failed: 0, total: 0 } })
    mockListJobs.mockResolvedValue({ data: { jobs: [] } })
    mockGenerateBatch.mockResolvedValue({
      data: { total_created: 3, skipped_file_ids: [1], not_found_file_ids: [] },
    })
    const wrapper = mount(ThumbnailsView)
    await flushPromises()

    const batchBtn = wrapper.findAll('button').filter(b => b.text().includes('批量生成'))
    await batchBtn[0].trigger('click')
    await flushPromises()

    expect(wrapper.find('textarea').exists()).toBe(true)
    await wrapper.find('textarea').setValue('1, 2, 3, 4')
    const submitBtn = wrapper.findAll('button').filter(b => b.text().includes('提交'))
    await submitBtn[0].trigger('click')
    await flushPromises()

    expect(mockGenerateBatch).toHaveBeenCalledWith([1, 2, 3, 4])
  })

  it('处理中的任务显示进度条', async () => {
    mockStats.mockResolvedValue({ data: { pending: 0, processing: 1, completed: 0, failed: 0, total: 1 } })
    mockListJobs.mockResolvedValue({
      data: {
        jobs: [
          { id: 'job-1', file_name: 'video.mp4', status: 'processing', phase: 'downloading', progress: 45, priority: 5, attempt: 0, max_retries: 3, mime_type: 'video/mp4', created_at: '2026-01-01T00:00:00Z' },
        ],
      },
    })
    const wrapper = mount(ThumbnailsView)
    await flushPromises()

    expect(wrapper.text()).toContain('45%')
  })

  it('状态筛选按钮', async () => {
    mockStats.mockResolvedValue({ data: { pending: 0, processing: 0, completed: 0, failed: 0, total: 0 } })
    mockListJobs.mockResolvedValue({ data: { jobs: [] } })
    const wrapper = mount(ThumbnailsView)
    await flushPromises()

    const filterBtns = wrapper.findAll('button').filter(b =>
      ['待处理 (0)', '处理中 (0)', '已完成 (0)', '失败 (0)', '已取消 (0)'].includes(b.text()),
    )
    expect(filterBtns.length).toBeGreaterThanOrEqual(4)
  })

  it('有活跃任务时显示自动刷新', async () => {
    mockStats.mockResolvedValue({ data: { pending: 1, processing: 0, completed: 0, failed: 0, total: 1 } })
    mockListJobs.mockResolvedValue({
      data: {
        jobs: [
          { id: 'job-1', file_name: 'x.png', status: 'pending', priority: 5, attempt: 0, max_retries: 3, mime_type: 'image/png', created_at: '2026-01-01T00:00:00Z' },
        ],
      },
    })
    const wrapper = mount(ThumbnailsView)
    await flushPromises()

    expect(wrapper.text()).toContain('自动刷新')
  })
})
