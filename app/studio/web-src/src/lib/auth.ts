/**
 * 登录状态 —— 只存 token 和当前用户。
 *
 * 约定：
 *  - token 在 localStorage（长期登录）；请求头由 api.ts 自动带上
 *  - 任何接口返回 401（login 除外）→ 清 token 回登录页
 *  - 启动时用已有 token 调 /api/auth/me 恢复会话
 */

import { create } from 'zustand'
import { api, getToken, setToken } from './api'
import type { AuthUser } from './types'

interface AuthState {
  user: AuthUser | null
  /** 是否已尝试恢复会话（false 时先别闪登录页） */
  ready: boolean
  busy: boolean
  error: string
}

export const useAuth = create<AuthState>(() => ({
  user: null,
  ready: false,
  busy: false,
  error: '',
}))

function patch(p: Partial<AuthState>) {
  useAuth.setState(p)
}

/** 启动时恢复会话；没有 token 或 token 失效都直接回登录页 */
export async function bootstrapAuth(): Promise<void> {
  if (!getToken()) {
    patch({ ready: true })
    return
  }
  try {
    const r = await api.me()
    patch({ user: r.user, ready: true, error: '' })
  } catch {
    setToken('')
    patch({ user: null, ready: true })
  }
}

export async function login(username: string, password: string, remember: boolean): Promise<boolean> {
  patch({ busy: true, error: '' })
  try {
    const r = await api.login(username.trim(), password, remember)
    setToken(r.token)
    patch({ user: r.user, busy: false, ready: true })
    return true
  } catch (e) {
    patch({ busy: false, error: e instanceof Error ? e.message : String(e) })
    return false
  }
}

export async function logout(): Promise<void> {
  try { await api.logout() } catch { /* 网络失败也要本地登出 */ }
  setToken('')
  patch({ user: null })
}

/** 更新个人资料后刷新本地用户（改名立即生效） */
export function setAuthUser(user: AuthUser) {
  patch({ user })
}

// 任一接口 401 → 立刻回登录页（token 已失效）
if (typeof window !== 'undefined') {
  window.addEventListener('ov-unauthorized', () => {
    if (getToken()) setToken('')
    if (useAuth.getState().user) patch({ user: null })
  })
}
