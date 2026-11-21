/**
 * 画面风格预设 —— 决定生成图"长什么样"（渲染方式、线条、材质、光照）。
 *
 * 只影响资产工坊这一次生成；分镜渲染的画风仍由剧本的 style 字段决定。
 * 每条英文都遵守 H3 规则：一个概念 ≤3 个形容词，不写否定。
 */

export type StyleFamily = '3d' | '2d' | 'photo' | 'paint'

export interface StylePreset {
  id: string
  family: StyleFamily
  /** i18n key，渲染时用 t(label) */
  label: string
  en: string
  /** i18n key，渲染时用 t(desc) */
  desc: string
}

export const STYLE_FAMILIES: Array<{ key: StyleFamily; label: string; hint: string }> = [
  { key: '3d',    label: 'style.fam.3d',    hint: 'style.fam.3d.hint' },
  { key: '2d',    label: 'style.fam.2d',    hint: 'style.fam.2d.hint' },
  { key: 'photo', label: 'style.fam.photo', hint: 'style.fam.photo.hint' },
  { key: 'paint', label: 'style.fam.paint', hint: 'style.fam.paint.hint' },
]

export const STYLE_PRESETS: StylePreset[] = [
  // ---- 3D ----
  { id: '3d_pbr', family: '3d', label: 'style.3d_pbr',
    en: '3D render, PBR materials, soft global illumination',
    desc: 'style.3d_pbr.desc' },
  { id: '3d_toon', family: '3d', label: 'style.3d_toon',
    en: '3D animation, stylized proportions, soft subsurface skin',
    desc: 'style.3d_toon.desc' },
  { id: '3d_clay', family: '3d', label: 'style.3d_clay',
    en: 'clay stop-motion, fingerprint surface, miniature set lighting',
    desc: 'style.3d_clay.desc' },

  // ---- 2D ----
  { id: '2d_cel', family: '2d', label: 'style.2d_cel',
    en: 'flat cel-shaded color, clean line art, bold outlines',
    desc: 'style.2d_cel.desc' },
  { id: '2d_anime', family: '2d', label: 'style.2d_anime',
    en: 'Japanese animation style, expressive eyes, cel shading',
    desc: 'style.2d_anime.desc' },
  { id: '2d_guoman', family: '2d', label: 'style.2d_guoman',
    en: 'Chinese animation style, ink-inspired backgrounds, saturated color',
    desc: 'style.2d_guoman.desc' },
  { id: '2d_comic', family: '2d', label: 'style.2d_comic',
    en: 'comic panel art, halftone shading, heavy ink lines',
    desc: 'style.2d_comic.desc' },

  // ---- 写实 ----
  { id: 'photo_real', family: 'photo', label: 'style.photo_real',
    en: 'photorealistic, natural skin texture, realistic lighting',
    desc: 'style.photo_real.desc' },
  { id: 'photo_cinema', family: 'photo', label: 'style.photo_cinema',
    en: 'cinematic film still, anamorphic lens, shallow depth of field',
    desc: 'style.photo_cinema.desc' },
  { id: 'photo_film', family: 'photo', label: 'style.photo_film',
    en: 'vintage film look, slight grain, halation, muted colors',
    desc: 'style.photo_film.desc' },
  { id: 'photo_doc', family: 'photo', label: 'style.photo_doc',
    en: 'documentary photography, available light, candid framing',
    desc: 'style.photo_doc.desc' },

  // ---- 绘画 ----
  { id: 'paint_water', family: 'paint', label: 'style.paint_water',
    en: 'watercolor wash, soft bleeding edges, paper texture',
    desc: 'style.paint_water.desc' },
  { id: 'paint_gongbi', family: 'paint', label: 'style.paint_gongbi',
    en: 'Chinese gongbi style, fine outlines, mineral pigments',
    desc: 'style.paint_gongbi.desc' },
  { id: 'paint_impasto', family: 'paint', label: 'style.paint_impasto',
    en: 'thick painterly brushwork, impasto, visible strokes',
    desc: 'style.paint_impasto.desc' },
  { id: 'paint_ink', family: 'paint', label: 'style.paint_ink',
    en: 'Chinese ink wash painting, wet brush strokes, negative space',
    desc: 'style.paint_ink.desc' },
  { id: 'paint_sketch', family: 'paint', label: 'style.paint_sketch',
    en: 'black-and-white pencil sketch, cross-hatching',
    desc: 'style.paint_sketch.desc' },
]

export function stylesOf(family: StyleFamily): StylePreset[] {
  return STYLE_PRESETS.filter((s) => s.family === family)
}

export function findStyle(id: string): StylePreset | null {
  return STYLE_PRESETS.find((s) => s.id === id) ?? null
}
