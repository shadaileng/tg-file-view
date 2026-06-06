import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import AuthView from '../views/AuthView.vue'

const mockSendCode = vi.hoisted(() => vi.fn())
const mockVerifyCode = vi.hoisted(() => vi.fn())
const mockVerify2FA = vi.hoisted(() => vi.fn())
const mockStatus = vi.hoisted(() => vi.fn())
const mockLogout = vi.hoisted(() => vi.fn())

vi.mock('../api/index', () => ({
  authApi: {
    sendCode: mockSendCode,
    verifyCode: mockVerifyCode,
    verify2FA: mockVerify2FA,
    status: mockStatus,
    logout: mockLogout,
  },
}))

describe('AuthView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('加载完成后显示 authorized 状态', async () => {
    mockStatus.mockResolvedValue({ data: { status: 'authorized', is_authorized: true } })
    const wrapper = mount(AuthView)
    await flushPromises()
    expect(wrapper.text()).toContain('已授权')
    expect(wrapper.find('button').text()).toContain('退出登录')
  })

  it('显示 not_configured 提示', async () => {
    mockStatus.mockResolvedValue({ data: { status: 'not_configured', is_authorized: false } })
    const wrapper = mount(AuthView)
    await flushPromises()
    expect(wrapper.text()).toContain('未配置')
  })

  it('发送验证码流程 (send -> verify)', async () => {
    mockStatus.mockResolvedValue({ data: { status: 'disconnected', is_authorized: false } })
    mockSendCode.mockResolvedValue({ data: { auth_state: 'code_sent' } })
    const wrapper = mount(AuthView)
    await flushPromises()

    expect(wrapper.text()).toContain('发送验证码')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(mockSendCode).toHaveBeenCalled()
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('验证码输入验证', async () => {
    mockStatus.mockResolvedValue({ data: { status: 'disconnected', is_authorized: false } })
    mockSendCode.mockResolvedValue({ data: { auth_state: 'code_sent' } })
    const wrapper = mount(AuthView)
    await flushPromises()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    const input = wrapper.find('input')
    await input.setValue('12345')
    mockVerifyCode.mockResolvedValue({ data: { status: 'authorized' } })
    await wrapper.findAll('button')[0].trigger('click')
    await flushPromises()

    expect(mockVerifyCode).toHaveBeenCalledWith('12345')
  })

  it('2FA 流程', async () => {
    mockStatus.mockResolvedValue({ data: { status: 'disconnected', is_authorized: false } })
    mockSendCode.mockResolvedValue({ data: { auth_state: 'code_sent' } })
    const wrapper = mount(AuthView)
    await flushPromises()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    await wrapper.find('input').setValue('12345')
    mockVerifyCode.mockResolvedValue({ data: { status: '2fa_required', is_authorized: false } })
    await wrapper.findAll('button')[0].trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('二次验证')
  })

  it('退出登录', async () => {
    mockStatus.mockResolvedValue({ data: { status: 'authorized', is_authorized: true } })
    mockLogout.mockResolvedValue({})
    const wrapper = mount(AuthView)
    await flushPromises()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(mockLogout).toHaveBeenCalled()
  })

  it('API 错误时显示登录流程', async () => {
    mockStatus.mockRejectedValue(new Error('Network error'))
    const wrapper = mount(AuthView)
    await flushPromises()
    expect(wrapper.text()).toContain('发送验证码')
  })
})
