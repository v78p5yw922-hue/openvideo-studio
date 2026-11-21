/** /glossary —— 剧本词库（只读查看 + 搜索；编辑在下一步）
 *
 *  数据来自 /api/glossary（studio/data/script-glossary.json）。
 *  用途：写分镜时查词、看某个词在哪个分类、确认校验器会用哪些禁用词。
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { toast } from '../lib/store'
import './GlossaryPage.css'

type Cat = { id: string; label: string; count: number; items: string[]; _added?: string[]; _removed?: string[] }
type Glossary = {
  version: number; source: string; total: number
  categories: Cat[]
  rules: Record<string, unknown>
}

export function GlossaryPage() {
  const [data, setData] = useState<Glossary | null>(null)
  const [q, setQ] = useState('')
  const [active, setActive] = useState<string>('')
  const [err, setErr] = useState('')
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [target, setTarget] = useState<string>('')

  const reload = () => {
    api.glossary('')
      .then((d) => {
        const g = d as unknown as Glossary
        setData(g)
        if (!target && g.categories.length) setTarget(g.categories[0].id)
      })
      .catch((e) => setErr(String(e)))
  }

  useEffect(() => { reload() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const addWord = async (catId: string) => {
    const word = (draft[catId] || '').trim()
    if (!word) return
    setBusy(true)
    try {
      await api.glossaryAdd(catId, word)
      setDraft((d) => ({ ...d, [catId]: '' }))
      toast(t('glossary.added'), 'ok')
      reload()
    } catch (e) {
      toast(String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  const removeWord = async (catId: string, word: string) => {
    setBusy(true)
    try {
      await api.glossaryRemove(catId, word)
      toast(t('glossary.removed'), 'ok')
      reload()
    } catch (e) {
      toast(String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  const resetAll = async () => {
    if (!confirm(t('glossary.reset.confirm'))) return
    setBusy(true)
    try {
      await api.glossaryReset(target)
      toast(t('glossary.reset.done'), 'ok')
      reload()
    } catch (e) {
      toast(String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  const cats = useMemo(() => {
    if (!data) return []
    const needle = q.trim()
    return data.categories
      .filter((c) => !active || c.id === active)
      .map((c) => ({
        ...c,
        items: needle ? c.items.filter((i) => i.includes(needle)) : c.items,
      }))
      .filter((c) => !needle || c.items.length > 0)
  }, [data, q, active])

  const ruleWords = useMemo(() => {
    if (!data) return []
    const out: { label: string; words: string[] }[] = []
    const push = (label: string, v: unknown) => {
      if (Array.isArray(v) && v.length) out.push({ label, words: v.map(String) })
    }
    push(t('glossary.rules.beat'), data.rules.beat_forbidden_words)
    push(t('glossary.rules.restate'), data.rules.restate_words)
    push(t('glossary.rules.speech'), data.rules.speech_cue_words)
    return out
  }, [data])

  return (
    <div className="page glossary-page">
      <div className="page-head">
        <div>
          <h1>{t('glossary.title')}</h1>
          <p className="muted">
            {data ? `${t('glossary.count', { n: data.total })} · ${data.source}` : t('settings.loading')}
          </p>
        </div>
        <div className="glossary-actions">
          <span className="muted sm">{t('glossary.overlay-hint')}</span>
          <button className="ghost sm" onClick={() => void resetAll()} disabled={busy}>
            {target ? t('glossary.reset.cat') : t('glossary.reset.all')}
          </button>
        </div>
      </div>

      <div className="page-body glossary-body">
        <aside className="glossary-side">
          <button className={`glossary-cat${active === '' ? ' is-active' : ''}`} onClick={() => { setActive(''); setTarget('') }}>
            {t('glossary.all')} <span className="n">{data?.total ?? 0}</span>
          </button>
          {(data?.categories || []).map((c) => (
            <button
              key={c.id}
              className={`glossary-cat${active === c.id ? ' is-active' : ''}`}
              onClick={() => { setActive(c.id); setTarget(c.id) }}
            >
              {c.label} <span className="n">{c.count}</span>
            </button>
          ))}
        </aside>

        <main className="glossary-main">
          <input
            className="glossary-search"
            placeholder={t('glossary.search')}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />

          {err && <div className="glossary-err">{err}</div>}

          {cats.map((c) => (
            <section key={c.id} className="glossary-group">
              <header>
                <span>{c.label}<span className="n">{c.items.length}</span></span>
                <span className="glossary-add">
                  <input
                    value={draft[c.id] || ''}
                    placeholder={t('glossary.add-placeholder')}
                    onChange={(e) => setDraft((d) => ({ ...d, [c.id]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === 'Enter') void addWord(c.id) }}
                  />
                  <button className="btn sm" disabled={busy || !(draft[c.id] || '').trim()}
                          onClick={() => void addWord(c.id)}>{t('glossary.add')}</button>
                </span>
              </header>
              <div className="glossary-chips">
                {c.items.map((i) => {
                  const isAdded = (c._added || []).includes(i)
                  return (
                    <span key={i} className={`chip${isAdded ? ' is-new' : ''}`}>
                      {i}
                      <button className="chip-x" title={t('glossary.remove')}
                              onClick={() => void removeWord(c.id, i)}>×</button>
                    </span>
                  )
                })}
              </div>
            </section>
          ))}

          {!err && data && cats.length === 0 && (
            <div className="muted glossary-empty">{t('glossary.empty')}</div>
          )}

          {ruleWords.length > 0 && (
            <section className="glossary-group is-rule">
              <header>{t('glossary.rules.title')}</header>
              {ruleWords.map((r) => (
                <div key={r.label} className="rule-row">
                  <span className="rule-label">{r.label}</span>
                  <span className="glossary-chips">
                    {r.words.map((w) => <span key={w} className="chip is-warn">{w}</span>)}
                  </span>
                </div>
              ))}
            </section>
          )}
        </main>
      </div>
    </div>
  )
}
