/**
 * usePopover —— 点击外部自动关闭的轻量 hook。
 * 用法：const { open, toggle, ref } = usePopover()
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export function usePopover() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)

  const toggle = useCallback(() => setOpen((v) => !v), [])
  const close = useCallback(() => setOpen(false), [])

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (!ref.current) return
      // 点击 popover 内部（含按钮本身）→ 不关；点击外部 → 关
      if (!ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  return { open, toggle, close, ref, setOpen }
}
