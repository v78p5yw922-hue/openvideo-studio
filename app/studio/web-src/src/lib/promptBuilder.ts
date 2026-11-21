/**
 * 提示词拼接器 —— 从 7 段输入拼出英文终稿，供 H3-Image 使用。
 *
 * 规则（写进代码 = 写进提示词）：
 *   ① 一个概念 ≤3 个形容词，多了互相抵消
 *   ② 抽象词换成可见证据（不是拼这里的事，是用户在输入框里要遵守的）
 *   ③ 不要写否定（H3 没有负面分支）：不写"没有胡须"，写"clean-shaven"
 *
 * 设计决定：
 *   - 顺序固定：风格 → 主体 → 状态 → 材质 → 光 → 镜头 → 动态
 *   - 逗号分隔、去重、限长 800 字符（截断到最后一个完整逗号）
 *   - 纯函数，返回 { compiled, segments, truncated }
 */

export interface PromptSegments {
  style: string
  subject: string
  state: string
  material: string
  light: string
  lens: string
  motion: string
}

export interface CompiledPrompt {
  /** 最终拼接后的英文终稿 */
  compiled: string
  /** 每段清洗后的内容（空段会被过滤掉） */
  segments: Array<{ key: keyof PromptSegments; text: string }>
  /** 是否被 800 字符限制截断 */
  truncated: boolean
}

const MAX_LEN = 800
const SEG_ORDER: Array<keyof PromptSegments> = ['style', 'subject', 'state', 'material', 'light', 'lens', 'motion']

/** 清洗单段：去首尾空白、合并多空行为单空格、去掉句尾标点（会在拼接时加逗号）。 */
function clean(s: string): string {
  if (!s) return ''
  let out = s.trim().replace(/\s+/g, ' ')
  // 去掉句尾的逗号、句点、分号 —— 统一在拼接层加逗号
  out = out.replace(/[,.;:，。；：]+$/, '').trim()
  return out
}

/** 去掉重复片段：把文本按逗号拆成短语，去重后再拼回去。 */
function dedupe(parts: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const part of parts) {
    const key = part.toLowerCase()
    if (key && !seen.has(key)) {
      seen.add(key)
      out.push(part)
    }
  }
  return out
}

export function compilePrompt(segs: PromptSegments): CompiledPrompt {
  const cleaned = SEG_ORDER
    .map((k) => ({ key: k, text: clean(segs[k]) }))
    .filter((s) => s.text.length > 0)

  if (cleaned.length === 0) {
    return { compiled: '', segments: [], truncated: false }
  }

  // 去重：防止用户在风格段写了 "cel-shaded"、在镜头段又写一次
  const phrases: string[] = []
  for (const s of cleaned) {
    phrases.push(...s.text.split(',').map((p) => p.trim()).filter(Boolean))
  }
  const deduped = dedupe(phrases)

  let compiled = deduped.join(', ')
  let truncated = false

  if (compiled.length > MAX_LEN) {
    compiled = compiled.slice(0, MAX_LEN)
    const lastComma = compiled.lastIndexOf(', ')
    if (lastComma > MAX_LEN * 0.6) compiled = compiled.slice(0, lastComma) // 尽量截在逗号处
    compiled = compiled.replace(/[,\s]+$/, '')
    truncated = true
  }

  return { compiled, segments: cleaned, truncated }
}
