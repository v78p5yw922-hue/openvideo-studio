/**
 * 左栏：本剧角色 / 本剧场景 / 其他项目角色。
 * 交互：先选镜头，再点角色 = 加入/移出；点场景 = 指派。
 * 图片本身点击 = 放大预览（不触发指派），其余区域才执行操作。
 */

import { useState } from 'react'
import { confirmDialog } from '../lib/confirm'
import { getDragShot, setDragShot } from '../lib/drag'
import { t, tpl } from '../lib/i18n'
import { assignShot, toast, toggleCast, useStore } from '../lib/store'
import { Lightbox } from './Lightbox'
import './LeftPanel.css'

interface Props {
  onOpenAsset: (kind: 'character' | 'scene', id: string) => void
  onImport: () => void
}

function bust(path: string | undefined): string {
  return encodeURIComponent((path || '').split('/').pop() || '0')
}

export function LeftPanel({ onOpenAsset, onImport }: Props) {
  const { detail, currentShotId } = useStore()
  const characters = detail?.characters ?? []
  const scenes = detail?.scenes ?? []
  const shot = detail?.shots.find((s) => s.shot_id === currentShotId) ?? null

  const inProject = characters.filter((c) => (c.used_in?.length ?? 0) > 0)
  const others = characters.filter((c) => (c.used_in?.length ?? 0) === 0)

  const [othersOpen, setOthersOpen] = useState(false)
  const [zoom, setZoom] = useState<{ src: string; alt: string } | null>(null)

  function dropOnCharacter(cid: string) {
    const sid = getDragShot()
    if (!sid) return
    setDragShot(null)
    void toggleCast(sid, cid)
  }

  function dropOnScene(sid: string) {
    const shotId = getDragShot()
    if (!shotId) return
    setDragShot(null)
    void assignShot(shotId, { scene_id: sid })
  }

  async function pickCharacter(cid: string) {
    const char = characters.find((c) => c.character_id === cid)
    if (!shot) {
      toast(t('left.pick-shot'), 'info')
      return
    }
    const inShot = shot.cast.includes(cid)
    const name = char?.name ?? cid
    const ok = await confirmDialog({
      title: inShot
        ? tpl('left.cast.remove', { name, sid: shot.shot_id })
        : tpl('left.cast.add', { name, sid: shot.shot_id }),
      confirmText: inShot ? t('btn.delete') : t('btn.confirm'),
      danger: inShot,
    })
    if (!ok) return
    void toggleCast(shot.shot_id, cid)
  }

  async function pickScene(sid: string) {
    if (!shot) {
      toast(t('left.pick-shot-scene'), 'info')
      return
    }
    if (shot.scene_id === sid) {
      toast(t('left.scene.already'), 'info')
      return
    }
    const ok = await confirmDialog({
      title: t('insp.scene'),
      message: tpl('left.scene.set', { sid: shot.shot_id }),
      confirmText: t('btn.confirm'),
    })
    if (!ok) return
    void assignShot(shot.shot_id, { scene_id: sid })
  }

  function CharacterRow({ c }: { c: (typeof characters)[number] }) {
    const used = shot ? shot.cast.includes(c.character_id) : false
    const thumb = c.primary_ref
      ? `/assets/characters/${encodeURIComponent(c.character_id)}?v=${bust(c.primary_ref)}`
      : ''
    return (
      <div
        className={`asset-row${used ? ' is-in-shot' : ''}`}
        onClick={() => void pickCharacter(c.character_id)}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); dropOnCharacter(c.character_id) }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') void pickCharacter(c.character_id) }}
        title={shot ? tpl('left.cast.add', { name: c.name, sid: shot.shot_id }) : t('left.pick-shot')}
      >
        {thumb ? (
          <img
            className="char-thumb"
            src={thumb}
            alt={c.name}
            loading="lazy"
            onClick={(e) => { e.stopPropagation(); setZoom({ src: thumb, alt: c.name }) }}
          />
        ) : (
          <div className="char-thumb placeholder">{c.name.slice(0, 1)}</div>
        )}
        <div className="asset-main">
          <div className="asset-name">
            {c.name}{c.locked ? ' 🔒' : ''}
            {c.refs_verified ? <span className="ok-mark" title="✓">✓</span> : null}
          </div>
          <div className="asset-sub">
            {tpl('left.shots-used', { n: c.used_in?.length ?? 0, m: c.refs.length })}
            {c.overridden ? t('left.overridden') : ''}
          </div>
        </div>
        <button
          className="row-action"
          onClick={(e) => { e.stopPropagation(); onOpenAsset('character', c.character_id) }}
          title={t('left.gallery')}
        >⋯</button>
      </div>
    )
  }

  return (
    <div className="left scroll">
      {shot && (
        <div className="left-current muted">
          {tpl('left.current', { sid: shot.shot_id })}
        </div>
      )}

      <div className="col-head">
        <span>{t('left.chars')}</span>
        <b className="col-count">{inProject.length}</b>
      </div>
      {inProject.length === 0 && (
        <div className="empty">
          <div>{t('left.no-chars')}</div>
          <div className="muted">{t('left.no-chars-hint')}</div>
        </div>
      )}
      {inProject.map((c) => <CharacterRow key={c.character_id} c={c} />)}

      <div className="col-head">
        <span>{t('left.scenes')}</span>
        <b className="col-count">{scenes.length}</b>
      </div>
      {scenes.length === 0 && (
        <div className="empty">
          <div>{t('left.no-scenes')}</div>
          <button className="link" onClick={onImport}>{t('header.import-script')}</button>
        </div>
      )}
      {scenes.map((s) => {
        const src = s.plate_sha
          ? `/assets/scenes/${encodeURIComponent(detail?.project.id ?? '')}/${encodeURIComponent(s.scene_id)}?v=${encodeURIComponent(s.plate_sha)}`
          : ''
        return (
          <div
            key={s.scene_id}
            className={`scene-row${shot?.scene_id === s.scene_id ? ' is-in-shot' : ''}`}
            onClick={() => void pickScene(s.scene_id)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); dropOnScene(s.scene_id) }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter') void pickScene(s.scene_id) }}
            title={shot ? t('left.pick-shot-scene') : t('left.pick-shot-scene')}
          >
            {src ? (
              <img
                className="scene-thumb"
                src={src}
                alt={s.name || s.scene_id}
                loading="lazy"
                onClick={(e) => { e.stopPropagation(); setZoom({ src, alt: s.name || s.scene_id }) }}
              />
            ) : (
              <div className="scene-thumb placeholder">{t('assets.no-plate')}</div>
            )}
            <div className="asset-main">
              <div className="asset-name">{s.name || s.scene_id}{s.locked ? ' 🔒' : ''}</div>
              <div className="asset-sub">
                {s.scene_id}
                {s.extra_plates?.length ? ` · ${tpl('assets.angles', { n: s.extra_plates.length })}` : ''}
              </div>
            </div>
            <button
              className="row-action"
              onClick={(e) => { e.stopPropagation(); onOpenAsset('scene', s.scene_id) }}
              title={t('left.plate')}
            >⋯</button>
          </div>
        )
      })}

      <div
        className={`col-head collapsible${othersOpen ? ' open' : ''}`}
        onClick={() => setOthersOpen((v) => !v)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') setOthersOpen((v) => !v) }}
      >
        <span>{t('left.others')} <b className="col-count">{others.length}</b></span>
        <span className="chevron" aria-hidden>▸</span>
      </div>
      {othersOpen && (
        <>
          {others.length === 0 && (
            <div className="empty">
              <div>{characters.length === 0 ? t('left.no-assets') : t('left.all-in-use')}</div>
              {characters.length === 0 && <button className="link" onClick={onImport}>{t('header.import-script')}</button>}
            </div>
          )}
          {others.map((c) => <CharacterRow key={c.character_id} c={c} />)}
        </>
      )}

      {zoom && <Lightbox src={zoom.src} alt={zoom.alt} onClose={() => setZoom(null)} />}
    </div>
  )
}
