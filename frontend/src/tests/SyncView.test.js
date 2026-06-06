import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SyncView from '../views/SyncView.vue'

const mockChannelsList = vi.hoisted(() => vi.fn())
const mockSyncTrigger = vi.hoisted(() => vi.fn())
const mockSyncListTasks = vi.hoisted(() => vi.fn())
const mockSyncGetTask = vi.hoisted(() => vi.fn())
const mockSyncCancel = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  channelsApi: { list: mockChannelsList },
  syncApi: {
    trigger: mockSyncTrigger,
    listTasks: mockSyncListTasks,
    getTask: mockSyncGetTask,
    cancel: mockSyncCancel,
  },
}))

describe('SyncView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载频道列表', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    const wrapper = mount(SyncView)
    await flushPromises()
    expect(wrapper.text()).toContain('Ch1')
  })

  it('选择频道后触发同步', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockSyncListTasks.mockResolvedValue({ data: [] })
    mockSyncTrigger.mockResolvedValue({
      data: { id: 'task-1', status: 'running', phase: 'connecting', progress: 0 },
    })
    const wrapper = mount(SyncView)
    await flushPromises()

    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()

    const syncBtn = wrapper.findAll('button').filter(b => b.text().includes('开始同步'))
    await syncBtn[0].trigger('click')
    await flushPromises()

    expect(mockSyncTrigger).toHaveBeenCalledWith(1)
  })

  it('显示同步进行中的阶段指示', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockSyncListTasks.mockResolvedValue({ data: [] })
    mockSyncTrigger.mockResolvedValue({
      data: { id: 'task-1', status: 'running', phase: 'connecting', progress: 10 },
    })
    const wrapper = mount(SyncView)
    await flushPromises()

    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()

    const syncBtn = wrapper.findAll('button').filter(b => b.text().includes('开始同步'))
    await syncBtn[0].trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('同步进行中')
  })

  it('显示同步任务历史', async () => {
    mockChannelsList.mockResolvedValue({ data: [{ id: 1, title: 'Ch1', file_count: 10 }] })
    mockSyncListTasks.mockResolvedValue({
      data: [
        { id: 'task-1', status: 'completed', synced_files: 5, total_files: 10, skipped_files: 2, errors: [], started_at: '2026-01-01T00:00:00Z' },
      ],
    })
    const wrapper = mount(SyncView)
    await flushPromises()

    const chBtn = wrapper.findAll('button').filter(b => b.text().includes('Ch1'))
    await chBtn[0].trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('task-1')
    expect(wrapper.text()).toContain('completed')
  })
})
