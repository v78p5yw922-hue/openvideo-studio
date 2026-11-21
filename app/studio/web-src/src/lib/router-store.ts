/**
 * Zustand 轻量 store —— 只管"UI 层"的瞬时 / 跨路由状态。
 * 服务端数据（projects/detail/queue/...）继续走 store.ts（用 TanStack Query 补缓存）。
 */

import { create } from 'zustand'

/** 资产管理页的筛选条件（进 URL 也存 Zustand，刷新后双保险） */
interface AssetFilterState {
  projectId: string
  groupId: string
  set: (patch: Partial<Pick<AssetFilterState, 'projectId' | 'groupId'>>) => void
  reset: () => void
}

export const useAssetFilters = create<AssetFilterState>((set) => ({
  projectId: '',
  groupId: '',
  set: (patch) => set(patch),
  reset: () => set({ projectId: '', groupId: '' }),
}))

/** 文生图画廊的筛选条件 */
interface StudioFilterState {
  projectId: string
  groupId: string
  kind: string
  set: (patch: Partial<Omit<StudioFilterState, 'set' | 'reset'>>) => void
  reset: () => void
}

export const useStudioFilters = create<StudioFilterState>((set) => ({
  projectId: '',
  groupId: '',
  kind: '',
  set: (patch) => set(patch),
  reset: () => set({ projectId: '', groupId: '', kind: '' }),
}))
