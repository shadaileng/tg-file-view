import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createWebHashHistory } from 'vue-router'
import ChannelsView from '../views/ChannelsView.vue'

const mockList = vi.hoisted(() => vi.fn())
const mockCreate = vi.hoisted(() => vi.fn())
const mockDelete = vi.hoisted(() => vi.fn())
const mockDiscover = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  channelsApi: {
    list: mockList,
    create: mockCreate,
    delete: mockDelete,
    discover: mockDiscover,
  },
}))

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'Dashboard', component: { template: '<div></div>' } },
    { path: '/files', name: 'Files', component: { template: '<div>Files</div>' } },
  ],
})

describe('ChannelsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载中显示 loading', () => {
    mockList.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    expect(wrapper.text()).toContain('加载中')
  })

  it('空列表显示提示', async () => {
    mockList.mockResolvedValue({ data: [] })
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('还没有频道')
  })

  it('显示频道列表', async () => {
    mockList.mockResolvedValue({
      data: [
        { id: 1, title: 'Channel A', username: 'ch_a', file_count: 10, total_size: 1024, last_sync: '2026-06-05T00:00:00Z' },
        { id: 2, title: 'Channel B', tg_id: -100123, file_count: 5, total_size: 512 },
      ],
    })
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('Channel A')
    expect(wrapper.text()).toContain('Channel B')
    expect(wrapper.text()).toContain('ch_a')
    expect(wrapper.text()).toContain('10 文件')
  })

  it('显示添加频道弹窗', async () => {
    mockList.mockResolvedValue({ data: [] })
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.findAll('button')[1].trigger('click')
    expect(wrapper.text()).toContain('添加频道')
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('添加频道 - 空输入报错', async () => {
    mockList.mockResolvedValue({ data: [] })
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.findAll('button')[1].trigger('click')
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const addBtn = buttons.filter(b => b.text().includes('添加') && !b.text().includes('取消'))[1]
    await addBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('请填写频道 username 或 tg_id')
  })

  it('添加频道 - 数字输入按 tg_id 创建', async () => {
    mockList.mockResolvedValue({ data: [] })
    mockCreate.mockResolvedValue({ data: { id: 10 } })
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.findAll('button')[1].trigger('click')
    await flushPromises()
    await wrapper.find('input').setValue('-1001234567890')
    const buttons = wrapper.findAll('button')
    const addBtn = buttons.filter(b => b.text().includes('添加') && !b.text().includes('取消'))[1]
    await addBtn.trigger('click')
    await flushPromises()

    expect(mockCreate).toHaveBeenCalledWith({ tg_id: -1001234567890 })
  })

  it('添加频道 - 文本输入按 username 创建', async () => {
    mockList.mockResolvedValue({ data: [] })
    mockCreate.mockResolvedValue({ data: { id: 11 } })
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.findAll('button')[1].trigger('click')
    await flushPromises()
    await wrapper.find('input').setValue('test_channel')
    const buttons = wrapper.findAll('button')
    const addBtn = buttons.filter(b => b.text().includes('添加') && !b.text().includes('取消'))[1]
    await addBtn.trigger('click')
    await flushPromises()

    expect(mockCreate).toHaveBeenCalledWith({ username: 'test_channel' })
  })

  it('删除频道确认弹窗', async () => {
    mockList.mockResolvedValue({
      data: [{ id: 1, title: 'To Delete', username: 'del', file_count: 0 }],
    })
    mockDelete.mockResolvedValue({})
    const wrapper = mount(ChannelsView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.find('button[title="删除频道"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('确认删除')

    const delBtn = wrapper.findAll('button').filter(b => b.text() === '删除')[0]
    await delBtn.trigger('click')
    await flushPromises()

    expect(mockDelete).toHaveBeenCalledWith(1)
  })
})
