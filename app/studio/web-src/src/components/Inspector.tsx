/**
 * 右栏检查器：机位 / 台词 / 画面 / 转场与接续 / 提示词 / 版本。
 *
 * 保存语义：
 *  - 机位、台词、提示词、锚定、时长 —— 改动后点「保存修改」（一次 PUT 全项目镜头）
 *  - 场景、角色、转场、chain_ref、首帧 —— 点了立刻生效（即时反馈，避免和轮询打架）
 *  - 轮询不会重置正在编辑的输入框（只有切换镜头才重置）
 */

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { confirmDialog } from '../lib/confirm'
import { t, tpl } from '../lib/i18n'
import {
  assignShot, deleteShot, errText, reload, reviewShot, saveShotFields, selectShot,
  toast, toggleCast, toggleChainRef, useStore,
} from '../lib/store'
import type { Shot } from '../lib/types'
import { Accordion } from './Accordion'
import { SearchSelect } from './SearchSelect'
import { stateLabel } from './ShotCard'
import './Inspector.css'

interface Draft {
  camera: string
  dialogue: string
  prompt: string
  anchor: Shot['anchor']
  duration: number
}

function toDraft(s: Shot): Draft {
  return {
    camera: s.camera,
    dialogue: s.dialogue,
    prompt: s.prompt,
    anchor: s.anchor,
    duration: s.duration,
  }
}

/** 中文语速 3.8 字/秒：提示 5 秒最多约 15–19 字 */
function speechHint(dialogue: string): string {
  const n = dialogue.replace(/<[^>]+>/g, '').replace(/\s/g, '').length
  if (!n) return t('insp.no-dialogue')
  const sec = (n / 3.8).toFixed(1)
  return tpl('insp.words', { n, sec })
}

interface Props {
  onOpenPrompt: (sid: string) => void
  onOpenTakes: (sid: string) => void
  onPlanAll: () => void
}

export function Inspector({ onOpenPrompt, onOpenTakes, onPlanAll }: Props) {
  const { detail, currentShotId, validation } = useStore()
  const shots = detail?.shots ?? []
  const shot = shots.find((s) => s.shot_id === currentShotId) ?? null
  const pid = detail?.project.id ?? ''

  const [draft, setDraft] = useState<Draft | null>(shot ? toDraft(shot) : null)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const prevId = useRef<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // 只在切换镜头时重置草稿；轮询刷新不覆盖正在编辑的内容
  useEffect(() => {
    if (!shot) { setDraft(null); prevId.current = null; return }
    if (prevId.current !== shot.shot_id) {
      prevId.current = shot.shot_id
      setDraft(toDraft(shot))
      setRejectOpen(false)
      setReason('')
    }
  }, [shot])

  if (!shot || !draft) {
    return (
      <div className="right scroll">
        <div className="empty">
          <div>{t('insp.shots-one')}</div>
          {shots.length > 0 && <div className="muted">{tpl('insp.shots-total', { n: shots.length })}</div>}
          {shots.length > 0 && <button onClick={onPlanAll}>{t('insp.plan')}</button>}
        </div>
      </div>
    )
  }

  const idx = shots.findIndex((s) => s.shot_id === shot.shot_id)
  const issues = [...(validation?.errors ?? []), ...(validation?.warnings ?? [])]
    .filter((i) => i.shot_id === shot.shot_id)
  const dirty = (Object.keys(draft) as Array<keyof Draft>).some((k) => draft[k] !== shot[k])
  const scene = detail?.scenes.find((s) => s.scene_id === shot.scene_id)

  async function save() {
    if (!draft) return
    setBusy(true)
    await saveShotFields(shot!.shot_id, draft)
    setBusy(false)
  }

  async function pinPrevious() {
    try {
      await api.firstFrameFromPrevious(pid, shot!.shot_id)
      await reload()
      toast(t('insp.firstframe.pinned'), 'ok')
    } catch (e) {
      toast(tpl('insp.firstframe.pin-fail', { err: errText(e) }), 'err')
    }
  }

  async function clearFirstFrame() {
    try {
      await api.clearFirstFrame(pid, shot!.shot_id)
      await reload()
      toast(t('insp.firstframe.cleared'), 'ok')
    } catch (e) {
      toast(tpl('insp.firstframe.clear-fail', { err: errText(e) }), 'err')
    }
  }

  async function uploadFirstFrame(file: File) {
    try {
      await api.uploadFirstFrame(pid, shot!.shot_id, file)
      await reload()
      toast(t('insp.firstframe.updated'), 'ok')
    } catch (e) {
      toast(tpl('insp.firstframe.upload-fail', { err: errText(e) }), 'err')
    }
  }

  async function doReject() {
    setBusy(true)
    await reviewShot(shot!.shot_id, 'reject', reason)
    setBusy(false)
    setRejectOpen(false)
  }

  return (
    <div className="right scroll inspector">
      <div className="insp-head">
        <div>
          <div className="insp-title">
            <span className="mono">{shot.shot_id}</span>
            <span className={`state-tag state-${shot.state}`}>{stateLabel(shot.state)}</span>
          </div>
          <div className="muted insp-sub">
            {idx + 1} / {shots.length} · {shot.scene_id}
            {shot.take?.version ? ` · v${shot.take.version}` : ''}
          </div>
        </div>
        <div className="insp-nav">
          <button className="ghost" disabled={idx <= 0}
            onClick={() => selectShot(shots[idx - 1].shot_id)} title={t('insp.prev')} aria-label={t('insp.prev')}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M5 15 L12 8 L19 15" />
            </svg>
          </button>
          <button className="ghost" disabled={idx >= shots.length - 1}
            onClick={() => selectShot(shots[idx + 1].shot_id)} title={t('insp.next')} aria-label={t('insp.next')}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M5 9 L12 16 L19 9" />
            </svg>
          </button>
        </div>
      </div>

      {issues.length > 0 && (
        <ul className="issue-list">
          {issues.map((i, n) => <li key={n} className={i.level}>{i.msg}</li>)}
        </ul>
      )}

      {shot.state === 'review' && (
        <div className="review-box">
          <div className="muted">{t('insp.review-hint')}</div>
          {!rejectOpen ? (
            <div className="row">
              <button className="primary" disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  await reviewShot(shot.shot_id, 'pass')
                  setBusy(false)
                }}>{t('insp.pass')}</button>
              <button className="danger" onClick={() => setRejectOpen(true)}>{t('insp.reject')}</button>
            </div>
          ) : (
            <div className="reject">
              <input
                autoFocus
                placeholder={t('insp.reject-reason')}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void doReject() }}
              />
              <div className="row">
                <button className="danger" disabled={busy} onClick={() => void doReject()}>{t('insp.reject-confirm')}</button>
                <button onClick={() => setRejectOpen(false)}>{t('btn.cancel')}</button>
              </div>
            </div>
          )}
        </div>
      )}

      <Accordion title={t('insp.camera')} defaultOpen>
        <label className="field">
          <span>{t('insp.camera')}</span>
          <input value={draft.camera}
            onChange={(e) => setDraft({ ...draft, camera: e.target.value })}
            placeholder={t('insp.camera.placeholder')} />
        </label>
        <label className="field">
          <span>{t('insp.duration')}</span>
          <input type="number" min={1} max={20} step={1} value={draft.duration}
            onChange={(e) => setDraft({ ...draft, duration: Number(e.target.value) })} />
        </label>
      </Accordion>

      <Accordion title={t('insp.dialogue')} defaultOpen>
        <label className="field">
          <span>{t('insp.dialogue')}</span>
          <textarea rows={3} value={draft.dialogue}
            onChange={(e) => setDraft({ ...draft, dialogue: e.target.value })}
            placeholder="<d>角色→对象：台词</d>" />
        </label>
        <div className="hint-line muted">{speechHint(draft.dialogue)}</div>
      </Accordion>

      <Accordion title={t('insp.picture')} defaultOpen>
        <div className="field col">
          <span>{t('insp.scene')}</span>
          <SearchSelect
            placeholder={t('insp.scene.add')}
            options={(detail?.scenes ?? []).map((s) => ({
              value: s.scene_id,
              label: `${s.name}`,
              hint: s.scene_id,
            }))}
            onPick={(sid) => void assignShot(shot.shot_id, { scene_id: sid })}
          />
          {shot.scene_id && (
            <div className="muted hint-line">
              {t('insp.scene.current')}: {scene ? scene.name : shot.scene_id}
            </div>
          )}
        </div>
        <div className="field col">
          <span>{t('insp.cast')}</span>
          <div className="chips">
            {shot.cast.length === 0 && <span className="muted">{t('insp.no-cast')}</span>}
            {shot.cast.map((cid) => {
              const c = detail?.characters.find((x) => x.character_id === cid)
              return (
                <button key={cid} className="chip" title={tpl('left.cast.remove', { name: c?.name || cid, sid: shot.shot_id })}
                  onClick={() => void toggleCast(shot.shot_id, cid)}>
                  {c?.name || cid} ×
                </button>
              )
            })}
          </div>
          <SearchSelect
            placeholder={t('insp.cast.add')}
            options={(detail?.characters ?? [])
              .filter((c) => !shot.cast.includes(c.character_id))
              .map((c) => ({ value: c.character_id, label: c.name, hint: c.character_id }))}
            onPick={(cid) => void assignShot(shot.shot_id, { cast_add: [cid] })}
          />
        </div>
        <label className="field">
          <span>{t('insp.anchor')}</span>
          <select value={draft.anchor}
            onChange={(e) => setDraft({ ...draft, anchor: e.target.value as Shot['anchor'] })}>
            <option value="">{t('insp.anchor.auto')}</option>
            <option value="plate">{t('insp.anchor.plate')}</option>
          </select>
        </label>
      </Accordion>

      <Accordion title={t('insp.transition')}>
        <label className="field">
          <span>{t('insp.transition.label')}</span>
          <select value={shot.transition}
            onChange={(e) => void assignShot(shot.shot_id, {
              transition: e.target.value as Shot['transition'],
            })}>
            <option value="cut">{t('insp.transition.cut')}</option>
            <option value="chain">{t('insp.transition.chain')}</option>
          </select>
        </label>
        <label className="check-row">
          <input type="checkbox" checked={shot.chain_ref}
            onChange={(e) => void toggleChainRef(shot.shot_id, e.target.checked)} />
          {t('insp.chain-ref')}
        </label>

        {shot.chain_from?.shot_id && (
          <div className="chain-thumb">
            {shot.chain_from.ready ? (
              <img src={`/assets/lastframe/${encodeURIComponent(pid)}/${encodeURIComponent(shot.chain_from.shot_id)}`}
                alt={t('insp.lastframe-empty')} />
            ) : (
              <span className="muted">{t('insp.lastframe-empty')}</span>
            )}
          </div>
        )}

        <div className="field col">
          <span>{t('insp.firstframe')}</span>
          <div className="row wrap">
            <button className="ghost" onClick={() => void pinPrevious()}>{t('insp.firstframe.pin')}</button>
            <button className="ghost" onClick={() => fileRef.current?.click()}>{t('insp.firstframe.other')}</button>
            {shot.first_frame && <button className="ghost" onClick={() => void clearFirstFrame()}>{t('insp.firstframe.clear')}</button>}
            <input ref={fileRef} type="file" accept="image/*" hidden
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void uploadFirstFrame(f)
                e.target.value = ''
              }} />
          </div>
          {shot.first_frame && (
            <img className="first-frame"
              src={`/assets/firstframes/${encodeURIComponent(pid)}/${encodeURIComponent(shot.shot_id)}?v=${encodeURIComponent(shot.fingerprint)}`}
              alt={t('insp.firstframe')} />
          )}
        </div>
      </Accordion>

      <Accordion title={t('insp.prompt')}>
        <pre className="prompt-preview">{draft.prompt || t('insp.prompt.empty')}</pre>
        <button className="ghost" onClick={() => onOpenPrompt(shot.shot_id)}>{t('insp.prompt.expand')}</button>
      </Accordion>

      <Accordion title={t('insp.versions')}>
        <div className="row">
          <span className="muted">
            {shot.take
              ? tpl('insp.version.current', {
                  n: shot.take.version,
                  kind: shot.take.kind === 'draft' ? t('insp.version.draft') : t('insp.version.final'),
                })
              : t('insp.version.none')}
          </span>
          <button className="ghost" onClick={() => onOpenTakes(shot.shot_id)} disabled={!shot.take}>
            {t('insp.version.compare')}
          </button>
        </div>
      </Accordion>

      <footer className="insp-footer">
        <button className="primary" disabled={!dirty || busy} onClick={() => void save()}>
          {busy ? t('insp.saving') : dirty ? t('insp.save') : t('insp.saved')}
        </button>
        <button className="danger ghost" title={t('insp.delete')}
          onClick={async () => {
            const ok = await confirmDialog({
              title: t('insp.delete'),
              message: tpl('insp.delete.confirm', { sid: shot.shot_id }),
              confirmText: t('btn.delete'),
              danger: true,
            })
            if (ok) void deleteShot(shot.shot_id)
          }}>{t('insp.delete')}</button>
      </footer>
    </div>
  )
}
