/**
 * 分辨率预设 —— H3-Image 有效范围 4–20 MP，低于 4 MP 会糊。
 * 成片 1344×768（2.47 MP）不要直接用来生成图。
 */

import { tpl } from './i18n'

export interface ResolutionPreset {
  id: string
  /** i18n key，渲染时用 t(label) */
  label: string
  width: number
  height: number
  aspect: string        // 比例，便于 UI 显示
  mp: number            // 像素（以 MP 为单位，1e6）
  /** i18n key，渲染时用 t(useCase) */
  useCase: string       // 用途描述
}

export const RESOLUTION_PRESETS: ResolutionPreset[] = [
  { id: 'char_portrait', label: 'res.char_portrait', width: 2048, height: 2731, aspect: '3:4', mp: 5.6, useCase: 'res.char_portrait.use' },
  { id: 'char_full',     label: 'res.char_full',     width: 2048, height: 3584, aspect: '4:7', mp: 7.3, useCase: 'res.char_full.use' },
  { id: 'scene_wide',    label: 'res.scene_wide',    width: 3072, height: 1728, aspect: '16:9', mp: 5.3, useCase: 'res.scene_wide.use' },
  { id: 'scene_wide_hd', label: 'res.scene_wide_hd', width: 2560, height: 1440, aspect: '16:9', mp: 3.7, useCase: 'res.scene_wide_hd.use' },
  { id: 'mobile_story',  label: 'res.mobile_story',  width: 2160, height: 3840, aspect: '9:16', mp: 8.3, useCase: 'res.mobile_story.use' },
  { id: 'pc_4k',         label: 'res.pc_4k',         width: 3840, height: 2160, aspect: '16:9', mp: 8.3, useCase: 'res.pc_4k.use' },
  { id: 'poster_2k3',    label: 'res.poster_2k3',    width: 2560, height: 3840, aspect: '2:3', mp: 9.8, useCase: 'res.poster_2k3.use' },
  { id: 'avatar',        label: 'res.avatar',        width: 2048, height: 2048, aspect: '1:1',  mp: 4.2, useCase: 'res.avatar.use' },
  { id: 'prop_card',     label: 'res.prop_card',     width: 2048, height: 2048, aspect: '1:1',  mp: 4.2, useCase: 'res.prop_card.use' },
  { id: 'anim_char',     label: 'res.anim_char',     width: 2731, height: 2048, aspect: '4:3',  mp: 5.6, useCase: 'res.anim_char.use' },
]

/** 计算像素（MP）。 */
export function megapixels(w: number, h: number): number {
  return Math.round((w * h) / 1_000_000 * 10) / 10
}

/** 查找预设；找不到返回 null。 */
export function findPreset(id: string): ResolutionPreset | null {
  return RESOLUTION_PRESETS.find((p) => p.id === id) ?? null
}

/**
 * 选规格时的警告。预设本身已是 H3 可用档位，
 * 只有 MP 低于 4 才提示（H3-Image 低于 4 MP 会糊）。
 */
export function presetWarnings(p: ResolutionPreset | null): string[] {
  if (!p) return []
  const msgs: string[] = []
  if (p.mp < 4) msgs.push(tpl('res.low-mp', { mp: p.mp }))
  return msgs
}
