/**
 * 主题切换 —— 只负责 data-theme 与 localStorage 的读写，
 * 不碰样式；样式全在 tokens.css 的 :root[data-theme="xxx"] 里。
 */

import { useCallback, useEffect, useState } from 'react'

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'ov-theme'

/** 从 localStorage 读，没有就跟随系统。只在首屏和初始化时调用一次。 */
export function detectInitialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'dark' || saved === 'light') return saved
  } catch {
    /* 隐私模式下 localStorage 可能抛 */
  }
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  return media.matches ? 'dark' : 'light'
}

/** 设置主题（写 DOM + localStorage）。 */
export function setTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch { /* ignore */ }
}

/** 读取当前主题（优先 DOM 属性，否则重新 detect）。 */
export function getCurrentTheme(): Theme {
  const attr = document.documentElement.getAttribute('data-theme')
  if (attr === 'dark' || attr === 'light') return attr
  return detectInitialTheme()
}

/** React hook：返回当前主题 + 切换函数。首帧就有值，不需要再等。 */
export function useTheme(): [Theme, () => void] {
  const [theme, setThemeState] = useState<Theme>(() => getCurrentTheme())

  useEffect(() => {
    // 跟随系统变化（只在用户没手动选过时才自动切）
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    function onChange(e: MediaQueryListEvent) {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved === 'dark' || saved === 'light') return
      const next: Theme = e.matches ? 'dark' : 'light'
      setTheme(next)
      setThemeState(next)
    }
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const toggle = useCallback(() => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    setThemeState(next)
  }, [theme])

  return [theme, toggle]
}
