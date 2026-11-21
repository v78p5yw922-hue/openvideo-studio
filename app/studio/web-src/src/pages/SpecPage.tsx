/** /spec —— 规范只读页：60 条规则 + 历史教训，可搜索 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import './SpecPage.css'

type Rule = { n: number; section: string; text: string }
type Lesson = { n: number; title: string; body: string }
type Payload = {
  rules: Rule[]
  lessons: Lesson[]
  sections: { name: string; count: number }[]
  sources: { rules: string; lessons: string; available: boolean; dir: string }
}

export function SpecPage() {
  const [data, setData] = useState<Payload | null>(null)
  const [q, setQ] = useState('')
  const [tab, setTab] = useState<'rules' | 'lessons'>('rules')
  const [section, setSection] = useState('')

  useEffect(() => {
    api.spec().then((d) => setData(d as unknown as Payload)).catch(() => setData(null))
  }, [])

  const rules = useMemo(() => {
    if (!data) return []
    const needle = q.trim()
    return data.rules.filter((r) =>
      (!section || r.section === section) &&
      (!needle || r.text.includes(needle) || String(r.n) === needle))
  }, [data, q, section])

  const lessons = useMemo(() => {
    if (!data) return []
    const needle = q.trim().toLowerCase()
    return data.lessons.filter((l) =>
      !needle || l.title.toLowerCase().includes(needle) || l.body.toLowerCase().includes(needle))
  }, [data, q])

  return (
    <div className="page spec-page">
      <div className="page-head">
        <div>
          <h1>{t('spec.title')}</h1>
          <p className="muted">
            {data ? t('spec.subtitle', { r: data.rules.length, l: data.lessons.length }) : t('settings.loading')}
          </p>
        </div>
        <div className="spec-tabs">
          <button className={tab === 'rules' ? 'is-active' : ''} onClick={() => setTab('rules')}>
            {t('spec.tab.rules')}
          </button>
          <button className={tab === 'lessons' ? 'is-active' : ''} onClick={() => setTab('lessons')}>
            {t('spec.tab.lessons')}
          </button>
        </div>
      </div>

      <div className="page-body spec-body">
        {tab === 'rules' && (
          <aside className="spec-side">
            <button className={!section ? 'is-active' : ''} onClick={() => setSection('')}>
              {t('glossary.all')} <span className="n">{data?.rules.length ?? 0}</span>
            </button>
            {(data?.sections || []).map((s) => (
              <button key={s.name} className={section === s.name ? 'is-active' : ''}
                      onClick={() => setSection(s.name)}>
                {s.name} <span className="n">{s.count}</span>
              </button>
            ))}
          </aside>
        )}

        <main className="spec-main">
          <input className="glossary-search" placeholder={t('spec.search')}
                 value={q} onChange={(e) => setQ(e.target.value)} />

          {data && !data.sources.available && (
            <div className="glossary-err">
              {t('spec.missing')} {data.sources.dir}
            </div>
          )}

          {tab === 'rules' && (
            <ol className="spec-list">
              {rules.map((r) => (
                <li key={`${r.n}-${r.section}`}>
                  <span className="spec-n">{r.n}</span>
                  <div>
                    <div className="spec-text" dangerouslySetInnerHTML={{ __html: inline(r.text) }} />
                    <div className="spec-section">{r.section}</div>
                  </div>
                </li>
              ))}
            </ol>
          )}

          {tab === 'lessons' && (
            <div className="spec-lessons">
              {lessons.map((l) => (
                <article key={l.n} className="spec-lesson">
                  <h3><span className="spec-n">{l.n}</span>{l.title}</h3>
                  <pre>{l.body}</pre>
                </article>
              ))}
            </div>
          )}

          {!data && <div className="muted">{t('settings.loading')}</div>}
        </main>
      </div>
    </div>
  )
}

/** 极简行内格式：`code` 与 **粗体** */
function inline(s: string): string {
  return s
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
}
