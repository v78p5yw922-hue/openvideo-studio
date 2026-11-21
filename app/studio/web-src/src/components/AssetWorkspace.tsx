/** 资产管理 —— 角色和场景（可按项目 / 分组筛选）。 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useAppModals } from '../lib/app-modals'
import { t, tpl } from '../lib/i18n'
import { verifyCharacter, useStore } from '../lib/store'
import type { AssetCandidate, Character, OutputVideo, Scene } from '../lib/types'
import './AssetWorkspace.css'

interface Props {
  onOpenAsset: (kind: 'character' | 'scene', id: string, projectId?: string) => void
}

export function AssetWorkspace({ onOpenAsset }: Props) {
  const { detail, pid, projects, groups } = useStore()
  const modals = useAppModals()
  const [projectId, setProjectId] = useState(pid ?? '')
  const [groupId, setGroupId] = useState('')
  const [characters, setCharacters] = useState<Character[]>([])
  const [scenes, setScenes] = useState<Scene[]>([])
  const [outputs, setOutputs] = useState<OutputVideo[]>([])
  const [generated, setGenerated] = useState<AssetCandidate[]>([])
  const [error, setError] = useState('')

  useEffect(() => { setProjectId(pid ?? ''); setGroupId('') }, [pid])

  useEffect(() => {
    let alive = true
    api.assetsCatalog({ projectId, groupId })
      .then((r) => {
        if (!alive) return
        setCharacters(r.characters)
        setScenes(r.scenes)
        setOutputs(r.outputs ?? [])
        setGenerated(r.generated ?? [])
        setError('')
      })
      .catch((e) => {
        if (!alive) return
        setCharacters([])
        setScenes([])
        setOutputs([])
        setGenerated([])
        setError(e instanceof Error ? e.message : t('assets.read-error'))
      })
    return () => { alive = false }
  }, [projectId, groupId, detail])

  const showProject = !projectId

  return (
    <div className="asset-space">
      <div className="asset-filters">
        <label>
          <span>{t('assets.filter.project')}</span>
          <select
            value={projectId}
            onChange={(e) => { setProjectId(e.target.value); setGroupId('') }}
          >
            <option value="">{t('assets.filter.all')}</option>
            {groups.filter((g) => g.id).map((g) => {
              const list = projects.filter((p) => (p.group_id || '') === g.id && !p.archived)
              if (list.length === 0) return null
              return (
                <optgroup key={g.id} label={g.name}>
                  {list.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
                </optgroup>
              )
            })}
            {projects.some((p) => !p.group_id && !p.archived) && (
              <optgroup label={t('project.ungrouped')}>
                {projects.filter((p) => !p.group_id && !p.archived).map((p) => (
                  <option key={p.id} value={p.id}>{p.title}</option>
                ))}
              </optgroup>
            )}
          </select>
        </label>

        <label>
          <span>{t('assets.filter.group')}</span>
          <select
            value={groupId}
            onChange={(e) => { setGroupId(e.target.value); setProjectId('') }}
          >
            <option value="">{t('assets.filter.all-groups')}</option>
            {groups.filter((g) => g.id).map((g) => (
              <option key={g.id} value={g.id}>{g.name}（{g.count}）</option>
            ))}
          </select>
        </label>

        <span className="muted asset-filter-count">
          {tpl('assets.filter.stats', { chars: characters.length, scenes: scenes.length })}
          {' · '}
          {tpl('assets.filter.stats-media', { outputs: outputs.length, generated: generated.length })}
        </span>
      </div>

      {error && <div className="asset-error">⚠ {error}</div>}

      <section className="assets-section">
        <h2>{t('assets.chars')} <span className="muted">{characters.length}</span></h2>
        {characters.length === 0 ? (
          <div className="empty">
            <div>{t('assets.char-empty')}</div>
            <div className="muted">{t('assets.char-empty-sub')}</div>
          </div>
        ) : (
          <div className="char-grid">
            {characters.map((c) => (
              <CharacterCard key={c.character_id} c={c}
                onOpen={() => onOpenAsset('character', c.character_id)} />
            ))}
          </div>
        )}
      </section>

      <section className="assets-section">
        <h2>{t('assets.scenes')} <span className="muted">{scenes.length}</span></h2>
        {scenes.length === 0 ? (
          <div className="empty">
            <div>{t('assets.scene-empty')}</div>
            <div className="muted">{t('assets.scene-empty-sub')}</div>
          </div>
        ) : (
          <div className="scene-grid">
            {scenes.map((s) => (
              <SceneCard
                key={`${s.project_id}/${s.scene_id}`}
                scene={s}
                showProject={showProject}
                onOpen={() => onOpenAsset('scene', s.scene_id, s.project_id)}
              />
            ))}
          </div>
        )}
      </section>

      <section className="assets-section">
        <h2>{t('assets.outputs')} <span className="muted">{outputs.length}</span></h2>
        {outputs.length === 0 ? (
          <div className="empty">
            <div>{t('assets.outputs-empty')}</div>
            <div className="muted">{t('assets.outputs-empty-sub')}</div>
          </div>
        ) : (
          <div className="output-grid">
            {outputs.map((o) => (
              <OutputCard
                key={`${o.project_id}/${o.name}`}
                o={o}
                showProject={showProject}
                onPlay={() => modals.playUrl(o.url, o.project_title || o.name)}
              />
            ))}
          </div>
        )}
      </section>

      <section className="assets-section">
        <h2>{t('assets.generated')} <span className="muted">{generated.length}</span></h2>
        {generated.length === 0 ? (
          <div className="empty">
            <div>{t('assets.generated-empty')}</div>
            <div className="muted">{t('assets.generated-empty-sub')}</div>
          </div>
        ) : (
          <div className="t2i-grid">
            {generated.map((c) => <GeneratedCard key={c.id} c={c} />)}
          </div>
        )}
      </section>
    </div>
  )
}

function CharacterCard({ c, onOpen }: { c: Character; onOpen: () => void }) {
  return (
    <div className="char-card">
      <div className="char-portrait">
        <img
          src={`/assets/characters/${encodeURIComponent(c.character_id)}?v=${encodeURIComponent((c.primary_ref || '').split('/').pop() || '0')}`}
          alt={c.name}
          loading="lazy"
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden' }}
        />
        {c.locked && <span className="badge">{t('assets.lock')}</span>}
      </div>
      <div className="char-info">
        <div className="asset-name">{c.name} {c.refs_verified && <span title="verified">✓</span>}</div>
        <div className="asset-sub">
          {tpl('assets.used-in', { n: c.used_in?.length ?? 0 })} · {tpl('assets.refs', { n: c.refs.length })}
          {c.overridden ? ` · ${t('assets.overridden')}` : ''}
          {(c.projects?.length ?? 0) > 1 ? ` · ${tpl('assets.projects-count', { n: c.projects!.length })}` : ''}
        </div>
        <p className="desc">{c.description || t('assets.no-desc')}</p>
        <div className="row">
          <button className="ghost" onClick={onOpen}>{t('assets.gallery')}</button>
          <button className="ghost" onClick={() => void verifyCharacter(c.character_id, !c.refs_verified)}>
            {c.refs_verified ? t('assets.unverify') : t('assets.verify')}
          </button>
        </div>
      </div>
    </div>
  )
}

function SceneCard({ scene, showProject, onOpen }: {
  scene: Scene; showProject: boolean; onOpen: () => void
}) {
  return (
    <div className="scene-card">
      <div className="scene-plate">
        {scene.plate_sha ? (
          <img
            src={`/assets/scenes/${encodeURIComponent(scene.project_id)}/${encodeURIComponent(scene.scene_id)}?v=${encodeURIComponent(scene.plate_sha)}`}
            alt={scene.name}
            loading="lazy"
          />
        ) : (
          <span className="placeholder">{t('assets.no-plate')}</span>
        )}
      </div>
      <div className="scene-info">
        <div className="asset-name">{scene.name || scene.scene_id}</div>
        <div className="asset-sub">
          {scene.scene_id} · {tpl('assets.angles', { n: scene.extra_plates?.length ?? 0 })}
          {scene.locked ? ` · ${t('assets.locked')}` : ''}
        </div>
        {showProject && scene.project_title && (
          <div className="asset-sub">{tpl('assets.scene-project', { name: scene.project_title })}</div>
        )}
        {scene.extra_plates?.length ? (
          <div className="mini-plates">
            {scene.extra_plates.map((p, i) => (
              <span key={p} className="mini-plate" title={p}>{tpl('asset.angle', { n: i + 1 })}</span>
            ))}
          </div>
        ) : null}
        <div className="row">
          <button className="ghost" onClick={onOpen}>{t('assets.plate-manage')}</button>
        </div>
      </div>
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function formatDate(sec: number): string {
  const d = new Date(sec * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function OutputCard({ o, showProject, onPlay }: {
  o: OutputVideo; showProject: boolean; onPlay: () => void
}) {
  const [copied, setCopied] = useState(false)

  async function copyPath(e: React.MouseEvent) {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(o.path)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  function download(e: React.MouseEvent) {
    e.stopPropagation()
    const a = document.createElement('a')
    a.href = o.url
    a.download = o.name
    a.click()
  }

  async function reveal(e: React.MouseEvent) {
    e.stopPropagation()
    try {
      await api.revealOutput(o.project_id, o.name)
    } catch {
      try { await navigator.clipboard.writeText(o.path) } catch { /* 忽略 */ }
    }
  }

  return (
    <div className="output-card">
      <div className="output-post" onClick={onPlay}>
        <img
          src={`${o.poster}?v=${o.mtime}`}
          alt={o.name}
          loading="lazy"
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden' }}
        />
        <span className="output-play" title={t('assets.play')}>▶</span>
        <button className="output-dl" title={t('assets.download')} onClick={download}>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none"
               stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 4v11" />
            <path d="M7 10l5 5 5-5" />
            <path d="M5 20h14" />
          </svg>
        </button>
      </div>
      <div className="output-info">
        <div className="asset-name" title={o.name}>{o.name}</div>
        {showProject && o.project_title && (
          <div className="asset-sub">{tpl('assets.scene-project', { name: o.project_title })}</div>
        )}
        <div className="asset-sub">{formatDate(o.mtime)} · {formatSize(o.size)}</div>
        <div className="output-actions">
          <button className="mini-btn" onClick={reveal} title={t('assets.open-dir')}>
            {t('assets.open-dir')}
          </button>
          <button className="mini-btn" onClick={copyPath} title={o.path}>
            {copied ? t('assets.copied') : t('assets.copy-path')}
          </button>
        </div>
      </div>
    </div>
  )
}

function GeneratedCard({ c }: { c: AssetCandidate }) {
  return (
    <div className="t2i-card">
      <div className="t2i-img">
        {c.url ? (
          <img src={c.url} alt={c.prompt} loading="lazy" />
        ) : (
          <span className="placeholder">{t('assets.no-image')}</span>
        )}
      </div>
      <div className="t2i-info">
        <div className="asset-sub">{t(`t2i.kind.${c.kind}`)} · {c.width}×{c.height}</div>
        <p className="desc">{c.prompt || t('assets.no-desc')}</p>
      </div>
    </div>
  )
}
