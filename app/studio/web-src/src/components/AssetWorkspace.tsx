/** 资产管理 —— 顶部 tab（角色/场景/视频/图片/音频）+ 项目筛选 + 分页 30/页 */

import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { useAppModals } from '../lib/app-modals'
import { t, tpl } from '../lib/i18n'
import { confirmDialog } from '../lib/confirm'
import { toast, useStore } from '../lib/store'
import type { AssetCandidate, Character, OutputVideo, Scene } from '../lib/types'
import { AudioBar } from '../lib/audioPlayer'
import { AudioDetail } from './AudioDetail'
import { SmartImg } from './MediaFallback'
import './AssetWorkspace.css'

interface Props {
  onOpenAsset: (kind: 'character' | 'scene', id: string, projectId?: string) => void
}

type Tab = 'character' | 'scene' | 'video' | 'image' | 'audio' | 'mv'
const PAGE_SIZE = 30

const TABS: Array<{ key: Tab; label: string }> = [
  { key: 'character', label: 'assets.chars' },
  { key: 'scene', label: 'assets.scenes' },
  { key: 'video', label: 'assets.videos' },
  { key: 'image', label: 'assets.images' },
  { key: 'audio', label: 'assets.audio' },
  { key: 'mv', label: 'assets.mv' },
]

function timeLabel(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso || ''
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 项目名固定 8 字宽，超出省略，悬浮看全名 */
function ProjectTag({ name }: { name: string }) {
  if (!name) return null
  const short = name.length > 8 ? name.slice(0, 8) + '…' : name
  return <span className="aw-proj" title={name}>{short}</span>
}

function CopyId({ value, label }: { value: string; label: string }) {
  return (
    <button className="aw-copy" title={`${label}: ${value}`}
            onClick={(e) => {
              e.stopPropagation()
              void navigator.clipboard?.writeText(value)
              toast(t('assets.copied'), 'ok')
            }}>⧉</button>
  )
}

export function AssetWorkspace({ onOpenAsset }: Props) {
  const { projects } = useStore()
  const modals = useAppModals()
  const [tab, setTab] = useState<Tab>((new URLSearchParams(window.location.search)
    .get('tab') as Tab) || 'character')
  const [page, setPage] = useState(0)
  const [sp] = useSearchParams()
  const [projectFilter, setProjectFilter] = useState(sp.get('project') || '')
  const [characters, setCharacters] = useState<Character[]>([])
  const [scenes, setScenes] = useState<Scene[]>([])
  const [outputs, setOutputs] = useState<OutputVideo[]>([])
  const [cands, setCands] = useState<AssetCandidate[]>([])
  const [error, setError] = useState('')
  const [zoom, setZoom] = useState<AssetCandidate | null>(null)
  const [play, setPlay] = useState<OutputVideo | null>(null)
  const [audioDetail, setAudioDetail] = useState<AssetCandidate | null>(null)

  const projectName = (id?: string) => {
    if (!id) return ''
    return projects.find((p) => p.id === id)?.title || id
  }

  const load = () => {
    api.assetsCatalog()
      .then((r) => {
        setCharacters(r.characters || [])
        setScenes(r.scenes || [])
        setOutputs(r.outputs || [])
        setError('')
      })
      .catch((e) => setError(e instanceof Error ? e.message : t('assets.read-error')))
    api.assetCandidates()
      .then((r) => {
        const seen = new Map<string, AssetCandidate>()
        for (const c of r.candidates || []) seen.set(c.id, c)
        setCands([...seen.values()])
      })
      .catch(() => undefined)
  }

  useEffect(load, [])
  useEffect(() => setPage(0), [tab, projectFilter])

  const hasProjectFilter = tab === 'character' || tab === 'scene' || tab === 'video'

  const chars = useMemo(() => {
    if (!projectFilter) return characters
    return characters.filter((c) => (c.projects || []).some((p) => p.id === projectFilter))
  }, [characters, projectFilter])

  const scenesAll = useMemo(() => {
    if (!projectFilter) return scenes
    return scenes.filter((s) => s.project_id === projectFilter)
  }, [scenes, projectFilter])

  const videos = useMemo(() => {
    if (!projectFilter) return outputs
    return outputs.filter((o) => (o as unknown as { project_id?: string }).project_id === projectFilter)
  }, [outputs, projectFilter])

  const images = useMemo(
    () => cands.filter((c) => c.kind !== 'music' && !(c as unknown as { broken?: boolean }).broken),
    [cands])
  const audios = useMemo(() => cands.filter((c) => c.kind === 'music'), [cands])

  const mvProjects = (projects || []).filter((p) => p.id.startsWith('p_mv_'))
  const counts: Record<Tab, number> = {
    character: chars.length,
    scene: scenesAll.length,
    video: videos.length,
    image: images.length,
    audio: audios.length,
    mv: mvProjects.length,
  }

  const total = counts[tab]
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const slice = <T,>(arr: T[]) => arr.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)

  async function removeCharacter(c: Character) {
    const ok = await confirmDialog({
      title: t('assets.delete-character'),
      message: tpl('assets.delete-character-confirm', { name: c.name }),
      confirmText: t('assets.delete'),
      danger: true,
    })
    if (!ok) return
    await api.deleteCharacter(c.character_id, true)
    toast(t('assets.deleted'), 'ok')
    load()
  }

  async function removeCandidate(c: AssetCandidate) {
    const ok = await confirmDialog({
      title: t('assets.delete'),
      message: t('assets.delete-confirm'),
      confirmText: t('assets.delete'),
      danger: true,
    })
    if (!ok) return
    await api.deleteAssetCandidate(c.id)
    toast(t('assets.deleted'), 'ok')
    setZoom(null)
    load()
  }

  return (
    <div className="aw-root">
      <div className="aw-tabs" role="tablist">
        {TABS.map((x) => (
          <button key={x.key} role="tab" aria-selected={tab === x.key}
                  className={`aw-tab${tab === x.key ? ' is-active' : ''}`}
                  onClick={() => setTab(x.key)}>
            {t(x.label)} <span className="aw-tab-n">{counts[x.key]}</span>
          </button>
        ))}
        <div className="aw-tabs-spacer" />
        {hasProjectFilter && (
          <select className="aw-proj-filter" value={projectFilter}
                  onChange={(e) => setProjectFilter(e.target.value)}>
            <option value="">{t('assets.filter.all-projects')}</option>
            {projects.filter((p) => !p.archived).map((p) => (
              <option key={p.id} value={p.id}>{p.title}</option>
            ))}
          </select>
        )}
        {pages > 1 && (
          <div className="aw-pager">
            <button className="ghost sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>‹</button>
            <span className="muted">{page + 1} / {pages}</span>
            <button className="ghost sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>›</button>
          </div>
        )}
      </div>

      {error && <div className="aw-error">⚠ {error}</div>}

      <div className="aw-body">
        {tab === 'character' && (
          <div className="aw-grid aw-chars">
            {slice(chars).map((c) => (
              <div key={c.character_id} className="aw-card aw-char"
                   onClick={() => onOpenAsset('character', c.character_id)}>
                <div className="aw-thumb aw-thumb-contain">
                  <SmartImg
                    src={`/assets/characters/${encodeURIComponent(c.character_id)}?v=${encodeURIComponent((c.primary_ref || '').split('/').pop() || '0')}`}
                    alt={c.name} />
                </div>
                <div className="aw-meta">
                  <b title={c.name}>{c.name}</b>
                  <span className="aw-meta-right">
                    <ProjectTag name={projectName((c.projects || [])[0]?.id)} />
                  </span>
                </div>
                <div className="aw-ids">
                  <span className="aw-id" title={c.character_id}>{c.character_id}</span>
                  <CopyId value={c.character_id} label="character_id" />
                </div>
              </div>
            ))}
            {chars.length === 0 && <div className="muted">{t('assets.char-empty')}</div>}
          </div>
        )}

        {tab === 'scene' && (
          <div className="aw-grid aw-scenes">
            {slice(scenesAll).map((s) => (
              <div key={`${s.project_id}/${s.scene_id}`} className="aw-card aw-scene"
                   onClick={() => onOpenAsset('scene', s.scene_id, s.project_id)}>
                <div className="aw-thumb aw-thumb-cover">
                  <SmartImg
                    src={`/assets/scenes/${encodeURIComponent(s.project_id)}/${encodeURIComponent(s.scene_id)}?v=${s.updated_at || ''}`}
                    alt={s.name} />
                </div>
                <div className="aw-meta">
                  <b title={s.name}>{s.name || s.scene_id}</b>
                  <span className="aw-meta-right">
                    <ProjectTag name={projectName(s.project_id)} />
                  </span>
                </div>
                <div className="aw-ids">
                  <span className="aw-id" title={`${s.scene_id} · ${s.project_id}`}>
                    {s.scene_id}
                  </span>
                  <CopyId value={s.scene_id} label="scene_id" />
                  <CopyId value={s.project_id} label="project_id" />
                </div>
              </div>
            ))}
            {scenesAll.length === 0 && <div className="muted">{t('assets.scene-empty')}</div>}
          </div>
        )}

        {tab === 'video' && (
          <div className="aw-grid aw-videos">
            {slice(videos).map((o) => (
              <div key={o.url} className="aw-card aw-video">
                <div className="aw-thumb aw-thumb-cover aw-video-thumb">
                  <SmartImg src={o.poster} alt={o.name} />
                  <button className="aw-play" onClick={() => setPlay(o)} title={t('assets.play')}>▶</button>
                  <a className="aw-dl" href={o.url} download onClick={(e) => e.stopPropagation()}
                     title={t('assets.download')}>⬇</a>
                </div>
                <div className="aw-meta">
                  <b title={o.name}>{o.name}</b>
                  <span className="aw-meta-right">
                    <ProjectTag name={projectName((o as unknown as { project_id?: string }).project_id)} />
                    <span className="muted">{(o.size / 1048576).toFixed(1)}M</span>
                  </span>
                </div>
                <div className="aw-ids">
                  <span className="aw-id" title={(o as unknown as { project_id?: string }).project_id}>
                    {(o as unknown as { project_id?: string }).project_id || '—'}
                  </span>
                  {(o as unknown as { project_id?: string }).project_id && (
                    <CopyId value={(o as unknown as { project_id?: string }).project_id as string}
                            label="project_id" />
                  )}
                </div>
              </div>
            ))}
            {videos.length === 0 && <div className="muted">{t('assets.video-empty')}</div>}
          </div>
        )}

        {tab === 'image' && (
          <div className="aw-grid aw-images">
            {slice(images).map((c) => (
              <figure key={c.id} className="aw-card aw-image" onClick={() => setZoom(c)}>
                <div className="aw-thumb aw-thumb-contain">
                  <SmartImg src={c.url} alt={c.prompt} />
                </div>
                <div className="aw-meta">
                  <b className="muted">{timeLabel(c.created_at)}</b>
                  <span className="aw-meta-right muted">{c.width}×{c.height}</span>
                </div>
                <figcaption className="aw-prompt" title={c.prompt}>{c.prompt}</figcaption>
                <div className="aw-ids">
                  <span className="aw-id" title={c.id}>{c.id}</span>
                  <CopyId value={c.id} label="asset_id" />
                </div>
              </figure>
            ))}
            {images.length === 0 && <div className="muted">{t('assets.generated-empty')}</div>}
          </div>
        )}

        {tab === 'mv' && (
          <div className="aw-music-list">
            {slice(mvProjects).map((p) => (
              <div key={p.id} className="aw-music">
                <div className="aw-music-head">
                  <b>{p.title}</b>
                  <span className="aw-meta-right muted">{p.id}</span>
                </div>
                <div className="aw-music-actions">
                  <span className="muted">{p.updated_at ? timeLabel(p.updated_at) : ''}</span>
                  <div className="aw-tabs-spacer" />
                  <button className="ghost sm" onClick={() => onOpenAsset('scene', '', p.id)}>
                    {t('mv.open-rendering')}
                  </button>
                </div>
              </div>
            ))}
            {mvProjects.length === 0 && <div className="muted">{t('assets.mv-empty')}</div>}
          </div>
        )}

        {tab === 'audio' && (
          <div className="aw-music-list">
            {slice(audios).map((m) => {
              const pr = (m as unknown as { params?: Record<string, unknown> }).params || {}
              return (
                <div key={m.id} className="aw-music">
                  <div className="aw-music-head">
                    <b title={m.prompt}>{m.prompt}</b>
                    <span className="aw-meta-right">
                      <ProjectTag name={projectName(m.project_id)} />
                      <span className="muted">
                        {pr.seconds ? `${pr.seconds}s` : '—'} · {pr.lyrics ? t('assets.music-vocal') : t('assets.music-instrumental')}
                      </span>
                    </span>
                  </div>
                  <AudioBar url={m.url || ''} />
                  <div className="aw-ids">
                    <span className="aw-id" title={m.id}>{m.id}</span>
                    <CopyId value={m.id} label="asset_id" />
                  </div>
                  <div className="aw-music-actions">
                    <span className="muted">{timeLabel(m.created_at)}</span>
                    <div className="aw-tabs-spacer" />
                    <button className="ghost sm" onClick={() => setAudioDetail(m)}>{t('audio.detail')}</button>
                    <button className="ghost sm danger" onClick={() => void removeCandidate(m)}>
                      {t('assets.delete')}
                    </button>
                  </div>
                </div>
              )
            })}
            {audios.length === 0 && <div className="muted">{t('assets.music-empty')}</div>}
          </div>
        )}
      </div>

      {zoom && (
        <div className="aw-zoom-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setZoom(null) }}>
          <div className="aw-zoom">
            <header>
              <span className="muted">{timeLabel(zoom.created_at)} · {zoom.width}×{zoom.height} · seed {zoom.seed}</span>
              <div className="aw-zoom-actions">
                <button className="ghost sm" onClick={() => void removeCandidate(zoom)}>{t('assets.delete')}</button>
                <button className="ghost sm" onClick={() => setZoom(null)}>✕</button>
              </div>
            </header>
            <div className="aw-zoom-stage"><img src={zoom.url} alt={zoom.prompt} /></div>
            <p className="aw-zoom-prompt">{zoom.prompt}</p>
          </div>
        </div>
      )}

      {audioDetail && <AudioDetail cand={audioDetail as never} onClose={() => setAudioDetail(null)} />}

      {play && (
        <div className="aw-zoom-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setPlay(null) }}>
          <div className="aw-zoom">
            <header>
              <span className="muted">{play.name}</span>
              <div className="aw-zoom-actions">
                <a className="ghost sm" href={play.url} download>{t('assets.download')}</a>
                <button className="ghost sm" onClick={() => setPlay(null)}>✕</button>
              </div>
            </header>
            <video src={play.url} controls autoPlay style={{ maxWidth: '100%', maxHeight: '70vh' }} />
          </div>
        </div>
      )}
    </div>
  )
}
