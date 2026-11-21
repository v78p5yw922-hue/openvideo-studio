/**
 * 全局确认框 —— 替代 window.confirm，统一视觉与按钮层级。
 *
 * 用法：if (!(await confirmDialog({ title, message, danger: true }))) return
 */

import { create } from 'zustand'

export interface ConfirmOptions {
  title: string
  message?: string
  confirmText?: string
  cancelText?: string
  /** 危险操作：确认按钮用红色 */
  danger?: boolean
}

interface ConfirmStore extends ConfirmOptions {
  open: boolean
  resolve: ((ok: boolean) => void) | null
  show: (o: ConfirmOptions) => Promise<boolean>
  settle: (ok: boolean) => void
}

export const useConfirm = create<ConfirmStore>((set, get) => ({
  open: false,
  title: '',
  resolve: null,
  show: (o) => new Promise<boolean>((resolve) => {
    set({ open: true, ...o, resolve })
  }),
  settle: (ok) => {
    const r = get().resolve
    set({ open: false, resolve: null })
    r?.(ok)
  },
}))

export function confirmDialog(o: ConfirmOptions): Promise<boolean> {
  return useConfirm.getState().show(o)
}

export function alertDialog(title: string, message?: string): Promise<boolean> {
  return useConfirm.getState().show({ title, message, cancelText: '', confirmText: 'OK' })
}
