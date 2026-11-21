/**
 * app-modals —— 弹窗 / 播放器的全局状态。
 * 从 App.tsx 里拆出来，让子页面（pages/*）直接 import 调用，不用传 props。
 */

import { create } from 'zustand'
import { api } from './api'

export type ModalKind =
  | { kind: 'asset'; target: 'character' | 'scene'; id: string; projectId?: string }
  | { kind: 'plan'; ids: string[] }
  | { kind: 'import' }
  | { kind: 'takes'; sid: string }
  | { kind: 'prompt'; sid: string }
  | null

export interface PlayerState {
  title: string
  src: string
  meta?: string
  download?: string
}

interface ModalsStore {
  modal: ModalKind
  player: PlayerState | null
  openAsset: (target: 'character' | 'scene', id: string, projectId?: string) => void
  openPlan: (ids: string[]) => void
  openImport: () => void
  openTakes: (sid: string) => void
  openPrompt: (sid: string) => void
  closeModal: () => void
  playUrl: (url: string, title: string) => void
  playShot: (pid: string, sid: string, version: number) => void
  closePlayer: () => void
  jump: (sid: string) => void
}

export const useAppModals = create<ModalsStore>((set) => ({
  modal: null,
  player: null,

  openAsset: (target, id, projectId) =>
    set({ modal: { kind: 'asset', target, id, projectId } }),
  openPlan: (ids) => set({ modal: { kind: 'plan', ids } }),
  openImport: () => set({ modal: { kind: 'import' } }),
  openTakes: (sid) => set({ modal: { kind: 'takes', sid } }),
  openPrompt: (sid) => set({ modal: { kind: 'prompt', sid } }),
  closeModal: () => set({ modal: null }),

  playUrl: (url, title) =>
    set({ player: { title, src: url, download: url } }),
  playShot: (pid, sid, version) =>
    set({
      player: {
        title: `${sid} · v${version}`,
        src: api.mediaUrl(pid, sid, version),
        download: api.mediaUrl(pid, sid, version),
      },
    }),
  closePlayer: () => set({ player: null }),

  // jump 是切镜头（LeftPanel/ShotGrid 会用到），走 store 里的 selectShot
  jump: (_sid) => {
    // 这个由 store.selectShot 处理，import 一下避免循环依赖
    import('./store').then(({ selectShot }) => selectShot(_sid))
  },
}))
