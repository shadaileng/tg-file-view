import { describe, it, expect, vi, beforeEach } from 'vitest'

describe('useDarkMode', () => {
  beforeEach(async () => {
    vi.resetModules()
    document.documentElement.classList.remove('dark')
  })

  it('默认主题为 light (localStorage 无值, prefers-color-scheme 无)', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })))
    localStorage.getItem = vi.fn(() => null)

    const { useDarkMode } = await import('../composables/useDarkMode')
    const { isDark } = useDarkMode()
    expect(isDark.value).toBe(false)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('toggleDark 切换主题', async () => {
    localStorage.getItem = vi.fn(() => null)

    const { useDarkMode } = await import('../composables/useDarkMode')
    const { isDark, toggleDark } = useDarkMode()
    expect(isDark.value).toBe(false)

    toggleDark()
    expect(isDark.value).toBe(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.setItem).toHaveBeenCalledWith('tg-file-viewer-theme', 'dark')

    toggleDark()
    expect(isDark.value).toBe(false)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(localStorage.setItem).toHaveBeenCalledWith('tg-file-viewer-theme', 'light')
  })

  it('localStorage 值为 dark 时自动应用暗色主题', async () => {
    localStorage.getItem = vi.fn((key) => {
      if (key === 'tg-file-viewer-theme') return 'dark'
      return null
    })

    const { useDarkMode } = await import('../composables/useDarkMode')
    const { isDark } = useDarkMode()
    expect(isDark.value).toBe(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('prefers-color-scheme: dark 时自动应用暗色主题', async () => {
    localStorage.getItem = vi.fn(() => null)
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))

    const { useDarkMode } = await import('../composables/useDarkMode')
    const { isDark } = useDarkMode()
    expect(isDark.value).toBe(true)
  })
})
