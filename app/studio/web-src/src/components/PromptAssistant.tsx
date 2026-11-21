/** 提示词助手：7 段结构化输入 + 词库插入 + 快速填充 + 终稿预览
 *
 *  设计：主体/状态/材质/光/镜头/动态 各自一格，右侧实时拼出英文终稿。
 *  新增：每段的说明、按用途一键填充、参考图提示。
 */

import { useMemo, useRef, useState } from 'react'
import { t, tpl } from '../lib/i18n'
import glossary from '../data/prompt-glossary.json'
import { compilePrompt, type PromptSegments } from '../lib/promptBuilder'

const SEG_KEYS: Array<keyof PromptSegments> = [
  'style', 'subject', 'state', 'material', 'light', 'lens', 'motion',
]

type Kind = 'character' | 'scene' | 'prop' | 'emotion'

interface Props {
  value: PromptSegments
  onChange: (v: PromptSegments) => void
  kind?: Kind
  /** 已选参考图的角色名（用于 <Picture n> 提示） */
  refNames?: string[]
}

export function PromptAssistant({ value, onChange, kind = 'character', refNames = [] }: Props) {
  const [activeCat, setActiveCat] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [focused, setFocused] = useState<keyof PromptSegments | null>(null)
  const inputsRef = useRef<Partial<Record<keyof PromptSegments, HTMLTextAreaElement>>>({})

  const compiled = useMemo(() => compilePrompt(value), [value])
  const filled = SEG_KEYS.filter((k) => (value[k] || '').trim()).length

  const cats = glossary.categories as Array<{ id: string; label: string; items: Array<{ zh: string; en: string }> }>
  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase()
    const out: Array<{ zh: string; en: string; catLabel: string }> = []
    for (const c of cats) {
      if (activeCat !== 'all' && c.id !== activeCat) continue
      for (const it of c.items) {
        const hay = `${it.zh} ${it.en}`.toLowerCase()
        if (!q || hay.includes(q)) out.push({ ...it, catLabel: c.label })
      }
    }
    return out
  }, [activeCat, search, cats])

  function insertInto(key: keyof PromptSegments, phrase: string) {
    const cur = value[key]
    const sep = cur.trim().length > 0 ? ', ' : ''
    onChange({ ...value, [key]: cur.trim() + sep + phrase.trim() })
    const el = inputsRef.current[key]
    if (el) { el.focus(); el.selectionStart = el.selectionEnd = el.value.length }
  }

  /** 按用途一键填充：只覆盖空段，避免抹掉用户已经写好的内容 */
  function quickFill(which: Kind) {
    const name = refNames[0] || ''
    const base: Partial<PromptSegments> = {}
    if (which === 'character') {
      base.subject = name ? `full body character sheet of ${name}` : 'full body character sheet'
      base.state = 'standing straight, neutral expression'
      base.lens = 'full body, centered, eye level'
      base.motion = 'static pose'
      base.material = 'clean even studio light on plain warm grey background'
    } else if (which === 'scene') {
      base.subject = 'empty rural interior, wide establishing view'
      base.lens = 'wide shot, eye level, 28mm'
      base.motion = 'empty room, no people'
      base.material = 'packed earth floor, mud-brick walls, wooden furniture'
    } else if (which === 'prop') {
      base.subject = 'a single prop on a plain surface'
      base.lens = 'close-up, centered, 50mm'
      base.motion = 'static product shot'
      base.material = 'soft studio light, subtle shadow'
    } else {
      base.subject = name ? `close-up portrait of ${name}` : 'close-up portrait'
      base.state = 'holding back tears, lips pressed'
      base.lens = 'close-up, eye level, 85mm'
      base.motion = 'frozen moment'
      base.material = 'warm rim light from the left'
    }
    const next = { ...value }
    for (const [k, v] of Object.entries(base)) {
      if (!(next[k as keyof PromptSegments] || '').trim()) next[k as keyof PromptSegments] = v as string
    }
    onChange(next)
  }

  async function copy() {
    if (!compiled.compiled) return
    try { await navigator.clipboard.writeText(compiled.compiled) } catch { /* ignore */ }
  }

  return (
    <div className="prompt-assistant">
      <header className="pa-head">
        <div>
          <strong>{t('pa.title')}</strong>
          <p className="muted">{t('pa.desc')}</p>
        </div>
        <span className="pa-count">{tpl('pa.filled', { n: filled, all: SEG_KEYS.length })}</span>
      </header>

      <div className="pa-quick">
        <span className="muted">{t('pa.quickfill')}</span>
        {(['character', 'scene', 'prop', 'emotion'] as Kind[]).map((k) => (
          <button key={k} type="button" className="pa-chip" onClick={() => quickFill(k)}>
            {t(`pa.quick.${k}`)}
          </button>
        ))}
      </div>

      {refNames.length > 0 && (
        <div className="pa-refs-hint">
          {tpl('pa.refs', { n: refNames.length, names: refNames.join('、') })}
        </div>
      )}

      <div className="pa-segments">
        {SEG_KEYS.map((key) => (
          <label key={key} className="pa-field">
            <span className="pa-label" title={t(`pa.seg.${key}.hint`)}>{t(`pa.seg.${key}`)}</span>
            <span className="pa-desc">{t(`pa.seg.${key}.desc`)}</span>
            <textarea
              ref={(el) => { if (el) inputsRef.current[key] = el }}
              rows={2}
              value={value[key]}
              onChange={(e) => onChange({ ...value, [key]: e.target.value })}
              onFocus={() => setFocused(key)}
              onBlur={() => setFocused((cur) => (cur === key ? null : cur))}
              placeholder={t(`pa.seg.${key}.hint`)}
              spellCheck={false}
            />
          </label>
        ))}
      </div>

      <div className="pa-glossary">
        <div className="pa-glossary-head">
          <select value={activeCat} onChange={(e) => setActiveCat(e.target.value)}>
            <option value="all">{t('pa.all-categories')}</option>
            {cats.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
          <input
            aria-label={t('pa.search-label')}
            placeholder={t('pa.search')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="pa-glossary-list">
          {filteredItems.slice(0, 80).map((item, idx) => (
            <div key={idx} className="pa-glossary-item">
              <div className="pa-glossary-zh">{item.zh}</div>
              <div className="pa-glossary-en" title={item.en}>{item.en}</div>
              <button
                type="button"
                className="pa-insert"
                onClick={() => insertInto(focused ?? 'subject', item.en)}
                title={focused
                  ? tpl('pa.insert-into', { label: t(`pa.seg.${focused}`) })
                  : t('pa.insert-default')}
              >{t('pa.insert')}</button>
            </div>
          ))}
          {filteredItems.length > 80 && <div className="muted">{t('pa.limit')}</div>}
        </div>
      </div>

      <div className="pa-preview">
        <div className="pa-preview-head">
          <strong>{t('pa.final')}</strong>
          <span className="muted">
            {tpl('pa.chars', { n: compiled.compiled.length })}
            {compiled.truncated ? t('pa.truncated') : ''}
          </span>
          <div className="spacer" />
          <button className="ghost" disabled={!compiled.compiled} onClick={() => void copy()}>
            {t('pa.copy')}
          </button>
        </div>
        <div className="pa-preview-text">
          {compiled.compiled || <span className="muted">{t('pa.empty')}</span>}
        </div>
        <div className="pa-rules muted">
          {t('pa.rule1')}<br />
          {t('pa.rule2')}<br />
          {t('pa.rule3')}
        </div>
      </div>
    </div>
  )
}
