import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SettingsView from '../views/SettingsView.vue'

const mockList = vi.hoisted(() => vi.fn())
const mockUpdate = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  configApi: {
    list: mockList,
    update: mockUpdate,
  },
}))

describe('SettingsView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载中显示 loading', () => {
    mockList.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(SettingsView)
    expect(wrapper.text()).toContain('加载中')
  })

  it('加载后按组分组建', async () => {
    mockList.mockResolvedValue({
      data: [
        { key: 'api_id', value: '12345' },
        { key: 'api_hash', value: 'abcdef' },
        { key: 'phone', value: '+8613800138000' },
        { key: 'sync_batch_size', value: '500' },
        { key: 'thumb_workers', value: '2' },
        { key: 'cache_max_size_mb', value: '0' },
        { key: 'admin_password', value: 'secret' },
      ],
    })
    const wrapper = mount(SettingsView)
    await flushPromises()

    expect(wrapper.text()).toContain('Telegram 配置')
    expect(wrapper.text()).toContain('同步配置')
    expect(wrapper.text()).toContain('缩略图配置')
    expect(wrapper.text()).toContain('缓存配置')
    expect(wrapper.text()).toContain('系统配置')
  })

  it('敏感信息脱敏显示', async () => {
    mockList.mockResolvedValue({
      data: [
        { key: 'api_hash', value: 'supersecret' },
        { key: 'admin_password', value: 'mypassword' },
      ],
    })
    const wrapper = mount(SettingsView)
    await flushPromises()

    expect(wrapper.text()).toContain('***')
    expect(wrapper.text()).not.toContain('supersecret')
    expect(wrapper.text()).not.toContain('mypassword')
  })

  it('只读配置项显示只读标签', async () => {
    mockList.mockResolvedValue({
      data: [
        { key: 'api_id', value: '12345' },
        { key: 'api_hash', value: '***' },
      ],
    })
    const wrapper = mount(SettingsView)
    await flushPromises()

    const readonlyBadges = wrapper.findAll('span').filter(s => s.text().includes('只读'))
    expect(readonlyBadges.length).toBeGreaterThanOrEqual(2)
  })

  it('编辑弹窗', async () => {
    mockList.mockResolvedValue({
      data: [
        { key: 'phone', value: '+8613800138000' },
        { key: 'sync_batch_size', value: '500' },
      ],
    })
    mockUpdate.mockResolvedValue({})
    const wrapper = mount(SettingsView)
    await flushPromises()

    const editBtns = wrapper.findAll('button').filter(b => b.text().includes('编辑'))
    await editBtns[0].trigger('click')
    await flushPromises()

    expect(wrapper.find('input').exists()).toBe(true)
  })
})
