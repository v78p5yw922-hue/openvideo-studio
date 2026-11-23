/**
 * /create/mv —— 音乐 MV：选一首音乐 + 一个地域/舞蹈预设 → 自动展开分镜项目
 *
 * MV 不是新的渲染管线：它生成一份 V3 分镜并导入成项目，
 * 之后和普通分镜一样校验、排队渲染、合成（音乐已自动挂成 BGM）。
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { toast, useStore } from '../lib/store'
import { AudioBar } from '../lib/audioPlayer'
import './MvPage.css'

interface Music { id: string; prompt: string; url?: string; params?: Record<string, unknown> }
interface Preset {
  id: string; kind: 'scenery' | 'dance'; region: string
  label: string; zh: string; en: string; style: string
}

export function MvPage() {
  const nav = useNavigate()
  const [music, setMusic] = useState<Music[]>([])
  const [picked, setPicked] = useState('')
  const [presets, setPresets] = useState<Preset[]>([])
  const [preset, setPreset] = useState('')
  const [kind, setKind] = useState<'all' | 'scenery' | 'dance'>('all')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ project_id: string; title: string; shots: number } | null>(null)
  const { projects } = useStore()
  const history = projects.filter((p) => p.id.startsWith('p_mv_')).slice(0, 8)

  useEffect(() => {
    api.assetCandidates()
      .then((r) => {
        const all = (r.candidates || []) as unknown as Music[]
        setMusic(all.filter((c) => (c as unknown as { kind: string }).kind === 'music'))
      })
      .catch(() => setMusic([]))
    api.mvPresets().then((r) => setPresets(r.presets || [])).catch(() => setPresets([]))
  }, [])

  const shown = presets.filter((p) => kind === 'all' || p.kind === kind)
  const pickedMusic = music.find((m) => m.id === picked)

  async function plan() {
    if (!picked) { toast(t('mv.need-music'), 'err'); return }
    if (!preset) { toast(t('mv.need-preset'), 'err'); return }
    setBusy(true)
    try {
      const r = await api.mvPlan({ music_id: picked, preset_id: preset })
      setResult(r)
      toast(tpl('mv.done', { n: r.shots }), 'ok')
    } catch (e) {
      toast(String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page mv-page">
      <div className="page-head">
        <div>
          <h1>{t('mv.title')}</h1>
          <p className="muted">{t('mv.subtitle')}</p>
        </div>
      </div>

      <div className="page-body mv-body">
        <section className="card">
          <header className="card-head"><h2>{t('mv.step1')}</h2>
            <span className="muted sm">{music.length} {t('mv.music-count')}</span></header>
          {music.length === 0 && <div className="muted">{t('mv.no-music')}</div>}
          <div className="mv-music">
            {music.map((m) => {
              const pr = m.params || {}
              return (
                <label key={m.id} className={`mv-music-item${picked === m.id ? ' is-active' : ''}`}>
                  <input type="radio" name="mv-music" checked={picked === m.id}
                         onChange={() => setPicked(m.id)} />
                  <span className="mv-music-text">
                    <b>{(m.prompt || m.id).slice(0, 46)}</b>
                    {pr.caption_zh ? <span className="mv-music-zh">{String(pr.caption_zh)}</span> : null}
                    <span className="muted sm">
                      {pr.seconds ? `${pr.seconds}s` : ''} {pr.lyrics ? t('assets.music-vocal') : t('assets.music-instrumental')}
                    </span>
                  </span>
                  {m.url && <AudioBar url={m.url} compact />}
                </label>
              )
            })}
          </div>
        </section>

        <section className="card">
          <header className="card-head"><h2>{t('mv.step2')}</h2>
            <div className="mv-kinds">
              {(['all', 'scenery', 'dance'] as const).map((k) => (
                <button key={k} className={`sv2-chip${kind === k ? ' is-active' : ''}`}
                        onClick={() => setKind(k)}>
                  {k === 'all' ? t('mv.kind.all') : k === 'scenery' ? t('mv.kind.scenery') : t('mv.kind.dance')}
                </button>
              ))}
            </div>
          </header>

          <div className="mv-presets">
            {shown.map((p) => (
              <button key={p.id} className={`mv-preset${preset === p.id ? ' is-active' : ''}`}
                      onClick={() => setPreset(p.id)}>
                <span className="mv-preset-head">
                  <b>{p.label}</b>
                  <span className="muted sm">{p.region}</span>
                </span>
                <span className="mv-preset-zh">{p.zh}</span>
                <span className="mv-preset-en">{p.en}</span>
              </button>
            ))}
          </div>

          {pickedMusic && (
            <p className="muted sm">
              {t('mv.will-use')}：{pickedMusic.prompt.slice(0, 50)} ·
              {' '}{String((pickedMusic.params || {}).seconds || '—')}s
            </p>
          )}

          <button className="btn" disabled={busy} onClick={() => void plan()}>
            {busy ? t('mv.planning') : t('mv.plan')}
          </button>

          {result && (
            <div className="mv-result">
              <b>{result.title}</b>
              <span className="muted">{tpl('mv.result', { n: result.shots })}</span>
              <div className="mv-result-actions">
                <button className="ghost sm"
                        onClick={() => nav(`/create/video/rendering?project=${result.project_id}`)}>
                  {t('mv.open-rendering')}
                </button>
                <button className="ghost sm"
                        onClick={() => nav(`/create/video/compose?project=${result.project_id}`)}>
                  {t('mv.open-compose')}
                </button>
              </div>
            </div>
          )}
        </section>

        {history.length > 0 && (
          <section className="card mv-history">
            <header className="card-head"><h2>{t('mv.history')}</h2>
              <span className="muted sm">{history.length}</span></header>
            <div className="mv-history-list">
              {history.map((h) => (
                <div key={h.id} className="mv-history-item">
                  <b>{h.title}</b>
                  <div className="mv-result-actions">
                    <button className="ghost sm"
                            onClick={() => nav(`/create/video/rendering?project=${h.id}`)}>
                      {t('mv.open-rendering')}
                    </button>
                    <button className="ghost sm"
                            onClick={() => nav(`/create/video/compose?project=${h.id}`)}>
                      {t('mv.open-compose')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
