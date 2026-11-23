/** 没有 caption_zh 时的中文兜底：按关键词给一句能看懂的风格说明。 */
export function musicZh(prompt: string, captionZh?: string): string {
  if (captionZh && captionZh.trim()) return captionZh
  const p = (prompt || '').toLowerCase()
  const hit = (words: string[], zh: string) => words.some((w) => p.includes(w)) ? zh : ''
  return (
    hit(['piano', 'cello'], '钢琴与弦乐，抒情、克制') ||
    hit(['guitar', 'harmonica'], '木吉他与口琴，民谣、怀旧') ||
    hit(['ukulele', 'percussion'], '尤克里里与轻打击，明亮轻快') ||
    hit(['folk ballad'], '中式民谣，叙事感') ||
    hit(['jazz'], '爵士三重奏，夜晚酒吧感') ||
    hit(['orchestral', 'strings'], '管弦，开阔有推进') ||
    hit(['synth', 'electronic'], '合成器与电鼓，都市夜色') ||
    hit(['rain'], '雨声氛围，安静') ||
    '器乐 / 环境氛围'
  )
}
