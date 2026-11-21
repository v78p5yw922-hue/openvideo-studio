/** /skills —— 写作技能：把 skill 文件直接交给任意大模型使用
 *
 *  用途：不读仓库里的规则文件，也能生成/审核剧本。
 *  页面做三件事：①列出 skill 与正文 ②预览 ③拼一段可直接粘贴的提示词并复制
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { toast } from '../lib/store'
import './SkillsPage.css'

type Skill = { name: string; description: string; path: string; body: string; chars: number }
type Payload = { skills: Skill[]; dir: string; available: boolean }

const MODES = ['write', 'review', 'both'] as const
type Mode = typeof MODES[number]

export function SkillsPage() {
  const [data, setData] = useState<Payload | null>(null)
  const [active, setActive] = useState('')
  const [mode, setMode] = useState<Mode>('write')
  const [story, setStory] = useState('')
  const [episode, setEpisode] = useState('第 2 集')
  const [withRules, setWithRules] = useState(true)
  const [rules, setRules] = useState('')

  useEffect(() => {
    api.skills().then((d) => {
      const p = d as unknown as Payload
      setData(p)
      if (p.skills.length) setActive(p.skills[0].name)
    }).catch(() => setData(null))
    api.spec().then((d) => {
      const p = d as unknown as { rules: { n: number; section: string; text: string }[] }
      setRules((p.rules || []).map((r) => `${r.n}. ${r.text}`).join('\n'))
    }).catch(() => setRules(''))
  }, [])

  const skill = useMemo(
    () => data?.skills.find((s) => s.name === active) || data?.skills[0],
    [data, active])

  const review = useMemo(
    () => data?.skills.find((s) => s.name === 'drama-review'),
    [data])

  const prompt = useMemo(() => {
    const w = data?.skills.find((s) => s.name === 'drama-write')
    const parts: string[] = []
    if (mode !== 'review' && w) {
      parts.push(`你是短剧编剧。严格按下面这份 skill 工作：\n\n<<<SKILL: ${w.name}>>>\n${w.body}\n<<<END SKILL>>>`)
    }
    if (mode !== 'write' && review) {
      parts.push(`你是短剧审稿人。严格按下面这份 skill 工作：\n\n<<<SKILL: ${review.name}>>>\n${review.body}\n<<<END SKILL>>>`)
    }
    if (withRules && rules) {
      parts.push(`【补充规则（分镜硬规则，写/审都要遵守）】\n${rules}`)
    }
    parts.push(`【剧集】${episode}\n【剧情】${story || '（在这里写剧情梗概）'}`)
    parts.push(mode === 'review'
      ? '【任务】审下面的分镜，按 skill 的 JSON 契约输出 blocking / warnings，每条给原文证据与改法。\n【分镜】（粘贴分镜全文）'
      : mode === 'both'
        ? '【任务】先按 drama-write 写剧本与分镜（镜头表 + 每镜七槽），再按 drama-review 自审一轮，最后给出修订版与校验清单。'
        : '【任务】写剧本与分镜：镜头表 10 列 + 每镜七格（机位/接续/服装状态/人物/语气/开口/节奏/场景），最后附自查表。')
    return parts.join('\n\n')
  }, [data, mode, story, episode, withRules, rules, review])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(prompt)
      toast(t('skills.copied'), 'ok')
    } catch {
      toast(t('settings.copy-failed'), 'err')
    }
  }

  return (
    <div className="page skills-page">
      <div className="page-head">
        <div>
          <h1>{t('skills.title')}</h1>
          <p className="muted">{t('skills.subtitle')}</p>
        </div>
        <button className="btn" onClick={() => void copy()}>{t('skills.copy')}</button>
      </div>

      <div className="page-body skills-body">
        <aside className="skills-side">
          {(data?.skills || []).map((s) => (
            <button key={s.name} className={active === s.name ? 'is-active' : ''}
                    onClick={() => setActive(s.name)}>
              <b>{s.name}</b>
              <span className="muted sm">{s.chars} 字</span>
            </button>
          ))}
          {data && !data.available && <div className="glossary-err">{t('skills.missing')}</div>}
        </aside>

        <main className="skills-main">
          {skill && (
            <section className="card">
              <header className="card-head">
                <h2>{skill.name}</h2>
                <span className="muted sm">{skill.path}</span>
              </header>
              <p className="muted">{skill.description}</p>
              <pre className="skill-body">{skill.body}</pre>
            </section>
          )}

          <section className="card">
            <header className="card-head"><h2>{t('skills.build')}</h2></header>

            <div className="skills-form">
              <label>
                <span>{t('skills.mode')}</span>
                <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
                  <option value="write">{t('skills.mode.write')}</option>
                  <option value="review">{t('skills.mode.review')}</option>
                  <option value="both">{t('skills.mode.both')}</option>
                </select>
              </label>
              <label>
                <span>{t('skills.episode')}</span>
                <input value={episode} onChange={(e) => setEpisode(e.target.value)} />
              </label>
              <label className="full">
                <span>{t('skills.story')}</span>
                <textarea rows={5} value={story} onChange={(e) => setStory(e.target.value)}
                          placeholder={t('skills.story.placeholder')} />
              </label>
              <label className="check">
                <input type="checkbox" checked={withRules} onChange={(e) => setWithRules(e.target.checked)} />
                <span>{t('skills.with-rules')}</span>
              </label>
            </div>

            <div className="skills-preview">
              <div className="skills-preview-head">
                <span>{t('skills.preview')}</span>
                <span className="muted sm">{prompt.length} 字</span>
              </div>
              <pre>{prompt}</pre>
            </div>
          </section>
        </main>
      </div>
    </div>
  )
}
