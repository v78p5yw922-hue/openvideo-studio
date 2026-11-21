/**
 * 资产弹窗（角色 / 场景统一风格）：
 *  - 固定尺寸网格；空的时候可以把图片直接拖进框里
 *  - 角色：参考图排序（第 1 张 = 主图）/ 单张替换 / 删除 / 本剧覆盖
 *  - 场景：第 1 张 = 母版（模板），后面是其他角度
 */

import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../lib/api'
import { confirmDialog } from '../lib/confirm'
import { t, tpl } from '../lib/i18n'
import { errText, reload, saveOverride, saveRefOrder, toast, useStore } from '../lib/store'
import type { Scene } from '../lib/types'
import { Lightbox } from './Lightbox'
import { Modal } from './Modal'
import { InpaintEditor } from './InpaintEditor'
import './AssetManagerModal.css'

interface Props {
  kind: 'character' | 'scene'
  id: string
  /** 从资产管理页跨项目打开场景时，指定所属项目 */
  pid?: string
  onClose: () => void
}

export function AssetManagerModal({ kind, id, pid, onClose }: Props) {
  const { detail } = useStore()
  if (!detail) return null
  return kind === 'character'
    ? <CharacterManager cid={id} onClose={onClose} />
    : <SceneManager sid={id} projectId={pid} onClose={onClose} />
}

function baseName(path: string): string {
  return path.split('/').pop() || path
}

function imagesFrom(dt: DataTransfer): File[] {
  return Array.from(dt.files).filter((f) => f.type.startsWith('image/'))
}

/** 待去水印的文件 + 处理完成后的上传回调 */
type PendingInpaint = { file: File; done: (f: File) => void }

// ------------------------------------------------------------------ 角色

function CharacterManager({ cid, onClose }: { cid: string; onClose: () => void }) {
  const { detail } = useStore()
  const ch = detail?.characters.find((c) => c.character_id === cid)
  const [refs, setRefs] = useState<string[]>(ch?.refs ?? [])
  const [desc, setDesc] = useState('')
  const [busy, setBusy] = useState(false)
  const [viewing, setViewing] = useState<string | null>(null)
  const [dragFrom, setDragFrom] = useState<number | null>(null)
  const [dragOver, setDragOver] = useState<{ i: number; before: boolean } | null>(null)
  const [fileOver, setFileOver] = useState(false)
  const [inpaint, setInpaint] = useState<PendingInpaint | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const replaceRef = useRef<HTMLInputElement>(null)
  const replaceIdx = useRef<number>(-1)

  // 服务端数据一变（上传 / 删除 / 换图），本地列表跟着同步。
  const sig = (ch?.refs ?? []).join('|')
  useEffect(() => {
    setRefs(ch?.refs ?? [])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig])

  if (!ch) return null
  const overridden = Boolean(ch.overridden)

  async function upload(files: File[]) {
    if (!files.length) return
    setBusy(true)
    try {
      const r = await api.uploadCharacterRefs(cid, files)
      setRefs(r.refs)
      await reload()
      toast(tpl('asset.uploaded', { n: files.length }), 'ok')
    } catch (e) {
      toast(tpl('asset.upload-failed', { err: errText(e) }), 'err')
    } finally {
      setBusy(false)
    }
  }

  async function replaceAt(idx: number, file: File) {
    setBusy(true)
    try {
      const r = await api.replaceCharacterRef(cid, idx, file)
      setRefs(r.refs)
      await reload()
      toast(t('asset.replaced'), 'ok')
    } catch (e) {
      toast(tpl('asset.replace-failed', { err: errText(e) }), 'err')
    } finally {
      setBusy(false)
    }
  }

  async function remove(path: string) {
    if (overridden) {
      toast(t('asset.override-locked'), 'err')
      return
    }
    const ok = await confirmDialog({
      title: t('btn.delete'),
      message: tpl('asset.delete.confirm', { name: baseName(path) }),
      confirmText: t('btn.delete'),
      danger: true,
    })
    if (!ok) return
    try {
      const r = await api.deleteCharacterRef(cid, path)
      setRefs(r.refs)
      await reload()
      toast(t('asset.deleted'), 'ok')
    } catch (e) {
      toast(tpl('asset.delete-failed', { err: errText(e) }), 'err')
    }
  }

  /** 拖动排序：松开即保存 */
  async function dropTo(target: number, before: boolean) {
    const from = dragFrom
    setDragFrom(null)
    setDragOver(null)
    if (from === null) return
    const next = [...refs]
    const [item] = next.splice(from, 1)
    let to = target - (from < target ? 1 : 0)
    if (!before) to += 1
    to = Math.max(0, Math.min(to, next.length))
    if (from === to) return
    next.splice(to, 0, item)
    setRefs(next)
    setBusy(true)
    try {
      await saveRefOrder(cid, next)
    } finally {
      setBusy(false)
    }
  }

  function thumb(i: number): string {
    return `/assets/characters/${encodeURIComponent(cid)}/refs/`
      + encodeURIComponent(baseName(refs[i]))
  }

  return (
    <Modal
      title={tpl('asset.char-title', { name: ch.name })}
      onClose={onClose}
      className="gallery-modal"
      footer={(
        <>
          <span className="muted">{t('asset.drag-hint')}</span>
          <div className="spacer" />
          <button onClick={onClose}>{t('asset.close')}</button>
        </>
      )}
    >
      {overridden && <div className="notice">{t('asset.overridden')}</div>}

      <div
        className={`gallery-grid${fileOver ? ' is-file-over' : ''}`}
        onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setFileOver(true) } }}
        onDragLeave={() => setFileOver(false)}
        onDrop={(e) => {
          if (!e.dataTransfer.types.includes('Files')) return
          e.preventDefault()
          setFileOver(false)
          const files = imagesFrom(e.dataTransfer)
          if (files.length === 1) setInpaint({ file: files[0], done: (f) => upload([f]) })
          else if (files.length) void upload(files)
        }}
      >
        {refs.map((path, i) => (
          <div
            key={path}
            className={`gallery-card${i === 0 ? ' is-primary' : ''}`
              + `${dragFrom === i ? ' is-dragging' : ''}`
              + `${dragOver?.i === i && dragFrom !== null && dragFrom !== i
                ? (dragOver.before ? ' is-drop-before' : ' is-drop-after') : ''}`}
            draggable={!busy}
            onDragStart={() => setDragFrom(i)}
            onDragEnd={() => { setDragFrom(null); setDragOver(null) }}
            onDragOver={(e) => {
              if (e.dataTransfer.types.includes('Files')) return
              e.preventDefault()
              const r = e.currentTarget.getBoundingClientRect()
              setDragOver({ i, before: e.clientX < r.left + r.width / 2 })
            }}
            onDragLeave={() => setDragOver((v) => (v?.i === i ? null : v))}
            onDrop={(e) => {
              if (e.dataTransfer.types.includes('Files')) return
              e.preventDefault()
              const r = e.currentTarget.getBoundingClientRect()
              void dropTo(i, e.clientX < r.left + r.width / 2)
            }}
          >
            <div className="gallery-img" onClick={() => setViewing(thumb(i))} role="presentation">
              <img src={thumb(i)} alt={`${t('asset.primary')} ${i + 1}`} loading="lazy" draggable={false} />
              {i === 0 && <span className="primary-badge">{t('asset.primary')}</span>}
              <span className="gallery-idx">{i + 1}</span>
            </div>
            <div className="gallery-actions">
              <button className="link" onClick={() => setViewing(thumb(i))}>{t('asset.view')}</button>
              <button className="link" disabled={busy}
                onClick={() => { replaceIdx.current = i; replaceRef.current?.click() }}>{t('asset.replace')}</button>
              <button className="link danger" disabled={busy}
                onClick={() => void remove(path)}>{t('asset.delete')}</button>
            </div>
          </div>
        ))}

        <button className="gallery-add" disabled={busy} onClick={() => fileRef.current?.click()}>
          <span className="plus">＋</span>
          <span>{refs.length === 0 ? t('asset.drop-hint') : t('asset.upload')}</span>
        </button>

        <input ref={fileRef} type="file" accept="image/*" multiple hidden
          onChange={(e) => {
            const files = Array.from(e.target.files ?? [])
            e.target.value = ''
            if (!files.length) return
            // 单张走「去水印」预览；多张直接上传（可后续逐张替换去水印）
            if (files.length === 1) setInpaint({ file: files[0], done: (f) => upload([f]) })
            else void upload(files)
          }} />
        <input ref={replaceRef} type="file" accept="image/*" hidden
          onChange={(e) => {
            const f = e.target.files?.[0]
            const idx = replaceIdx.current
            e.target.value = ''
            if (f && idx >= 0) setInpaint({ file: f, done: (cf) => replaceAt(idx, cf) })
          }} />

        {inpaint && (
          <InpaintEditor
            file={inpaint.file}
            onCancel={() => setInpaint(null)}
            onConfirm={(f) => { const done = inpaint.done; setInpaint(null); void done(f) }}
          />
        )}
      </div>

      <section className="sub-section">
        <h4>{t('asset.override')}</h4>
        <p className="muted">{t('asset.override-hint')}</p>
        <div className="row">
          <input value={desc} onChange={(e) => setDesc(e.target.value)}
            placeholder={t('asset.override-placeholder')} />
          <button onClick={() => void saveOverride(cid, desc)}>{t('asset.override-save')}</button>
        </div>
      </section>

      <section className="sub-section">
        <div className="row">
          <span className="muted">{tpl('asset.refs-count', { n: refs.length })}</span>
          <div className="spacer" />
          <label className="check-row">
            <input type="checkbox" checked={ch.refs_verified}
              onChange={(e) => void api.verifyCharacter(cid, e.target.checked).then(reload)} />
            {t('asset.verified')}
          </label>
        </div>
      </section>

      {viewing && <Lightbox src={viewing} alt={ch.name} onClose={() => setViewing(null)} />}
    </Modal>
  )
}

// ------------------------------------------------------------------ 场景

function SceneManager({ sid, projectId, onClose }: {
  sid: string; projectId?: string; onClose: () => void
}) {
  const { detail, pid } = useStore()
  const activePid = projectId || pid || ''
  const fromStore = activePid === pid
    ? detail?.scenes.find((s) => s.scene_id === sid)
    : undefined
  const [scene, setScene] = useState<Scene | undefined>(fromStore)
  const [busy, setBusy] = useState(false)
  const [viewing, setViewing] = useState<string | null>(null)
  const [plateOver, setPlateOver] = useState(false)
  const [altOver, setAltOver] = useState(false)
  const [inpaint, setInpaint] = useState<PendingInpaint | null>(null)
  const motherRef = useRef<HTMLInputElement>(null)
  const altRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (fromStore) { setScene(fromStore); return }
    if (!activePid) { setScene(undefined); return }
    let alive = true
    void api.getProject(activePid)
      .then((d) => { if (alive) setScene(d.scenes.find((s) => s.scene_id === sid)) })
      .catch(() => { if (alive) setScene(undefined) })
    return () => { alive = false }
  }, [fromStore, activePid, sid])

  async function refresh() {
    if (activePid === pid) { await reload(); return }
    const d = await api.getProject(activePid)
    setScene(d.scenes.find((s) => s.scene_id === sid))
  }

  if (!scene || !activePid) return null
  const plateUrl = scene.plate_sha
    ? `/assets/scenes/${encodeURIComponent(activePid)}/${encodeURIComponent(sid)}?v=${encodeURIComponent(scene.plate_sha)}`
    : ''

  async function replaceMother(file: File, force = false) {
    setBusy(true)
    try {
      await api.uploadScenePlate(activePid, sid, file, force)
      await refresh()
      toast(t('asset.plate-updated'), 'ok')
    } catch (e) {
      if (!force && e instanceof ApiError && e.status === 409) {
        setBusy(false)
        const ok = await confirmDialog({
          title: t('asset.plate'),
          message: tpl('asset.scene-locked.confirm', { err: errText(e) }),
          confirmText: t('btn.confirm'),
          danger: true,
        })
        if (ok) await replaceMother(file, true)
        return
      }
      toast(tpl('asset.upload-failed', { err: errText(e) }), 'err')
    } finally {
      setBusy(false)
    }
  }

  async function addAlts(files: File[]) {
    if (!files.length) return
    setBusy(true)
    try {
      const r = await api.addScenePlates(activePid, sid, files)
      await refresh()
      toast(tpl('asset.scene-added', { n: r.added }), 'ok')
    } catch (e) {
      toast(tpl('asset.upload-failed', { err: errText(e) }), 'err')
    } finally {
      setBusy(false)
    }
  }

  async function removeAlt(index: number) {
    const ok = await confirmDialog({
      title: t('btn.delete'),
      message: tpl('asset.scene-delete.confirm', { n: index + 1 }),
      confirmText: t('btn.delete'),
      danger: true,
    })
    if (!ok) return
    try {
      await api.removeScenePlate(activePid, sid, index)
      await refresh()
      toast(t('asset.deleted'), 'ok')
    } catch (e) {
      toast(tpl('asset.delete-failed', { err: errText(e) }), 'err')
    }
  }

  return (
    <Modal
      title={tpl('asset.scene-title', { name: scene.name || sid })}
      onClose={onClose}
      className="gallery-modal"
      footer={(
        <>
          <label className="check-row">
            <input type="checkbox" checked={Boolean(scene.locked)}
              onChange={async (e) => {
                try {
                  await api.setSceneLock(activePid, sid, e.target.checked)
                  await refresh()
                } catch (err) {
                  toast(tpl('asset.lock-failed', { err: errText(err) }), 'err')
                }
              }} />
            {t('asset.lock')}
          </label>
          <div className="spacer" />
          <button className="primary" onClick={onClose}>{t('asset.done')}</button>
        </>
      )}
    >
      <section className="sub-section">
        <h4>{t('asset.plate')}</h4>
        <div
          className={`gallery-single${plateOver ? ' is-file-over' : ''}`}
          onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setPlateOver(true) } }}
          onDragLeave={() => setPlateOver(false)}
          onDrop={(e) => {
            if (!e.dataTransfer.types.includes('Files')) return
            e.preventDefault()
            setPlateOver(false)
            const f = imagesFrom(e.dataTransfer)[0]
            if (f) setInpaint({ file: f, done: (cf) => replaceMother(cf) })
          }}
        >
          {plateUrl ? (
            <img src={plateUrl} alt={scene.name || sid} onClick={() => setViewing(plateUrl)} />
          ) : (
            <button className="gallery-add tall" disabled={busy} onClick={() => motherRef.current?.click()}>
              <span className="plus">＋</span>
              <span>{t('asset.drop-hint')}</span>
            </button>
          )}
        </div>
        <div className="row">
          <button className="ghost" disabled={busy} onClick={() => motherRef.current?.click()}>
            {scene.plate_sha ? t('asset.plate-replace') : t('asset.plate-upload')}
          </button>
          <input ref={motherRef} type="file" accept="image/*" hidden
            onChange={(e) => {
              const f = e.target.files?.[0]
              e.target.value = ''
              if (f) setInpaint({ file: f, done: (cf) => replaceMother(cf) })
            }} />
        </div>
      </section>

      <section className="sub-section">
        <h4>{t('asset.angles')} <span className="muted">{scene.extra_plates.length}</span></h4>
        <p className="muted">{t('asset.angles-hint')}</p>
        <div
          className={`gallery-grid scenes${altOver ? ' is-file-over' : ''}`}
          onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setAltOver(true) } }}
          onDragLeave={() => setAltOver(false)}
          onDrop={(e) => {
            if (!e.dataTransfer.types.includes('Files')) return
            e.preventDefault()
            setAltOver(false)
            const files = imagesFrom(e.dataTransfer)
            if (files.length === 1) setInpaint({ file: files[0], done: (f) => addAlts([f]) })
            else if (files.length) void addAlts(files)
          }}
        >
          {scene.extra_plates.map((p, i) => (
            <div key={p} className="gallery-card">
              <div className="gallery-img is-file">
                <span className="gallery-file">{baseName(p)}</span>
                <span className="gallery-idx">{i + 1}</span>
              </div>
              <div className="gallery-actions">
                <button className="link danger" disabled={busy} onClick={() => void removeAlt(i)}>
                  {t('asset.delete')}
                </button>
              </div>
            </div>
          ))}
          <button className="gallery-add" disabled={busy} onClick={() => altRef.current?.click()}>
            <span className="plus">＋</span>
            <span>{scene.extra_plates.length === 0 ? t('asset.drop-hint') : t('asset.angle-add')}</span>
          </button>
          <input ref={altRef} type="file" accept="image/*" multiple hidden
            onChange={(e) => {
              const files = Array.from(e.target.files ?? [])
              e.target.value = ''
              if (!files.length) return
              if (files.length === 1) setInpaint({ file: files[0], done: (f) => addAlts([f]) })
              else void addAlts(files)
            }} />

          {inpaint && (
            <InpaintEditor
              file={inpaint.file}
              onCancel={() => setInpaint(null)}
              onConfirm={(f) => { const done = inpaint.done; setInpaint(null); void done(f) }}
            />
          )}
        </div>
      </section>

      {viewing && <Lightbox src={viewing} alt={scene.name || sid} onClose={() => setViewing(null)} />}
    </Modal>
  )
}
