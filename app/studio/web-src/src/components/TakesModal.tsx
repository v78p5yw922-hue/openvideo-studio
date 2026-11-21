/** TakesModal —— 一个镜头的全部版本：都能播放，选哪版用于合成。 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { confirmDialog } from '../lib/confirm'
import { t, tpl } from '../lib/i18n'
import { adoptVersion, chooseTakeVersion, toast } from '../lib/store'
import { Modal } from './Modal'
import { VideoPlayer } from './VideoPlayer'
import './TakesModal.css'

interface Take {
  version: number
  path: string
  elapsed_s?: number
  chosen?: boolean
  matches_script?: boolean
  metrics?: { duration?: number; frames?: number }
  created_at?: string
}

interface Props {
  pid: string
  shotId: string
  onClose: () => void
  onPlay: (url: string) => void
  onChanged: () => void
}

export function TakesModal({ pid, shotId, onClose, onPlay, onChanged }: Props) {
  const [takes, setTakes] = useState<Take[]>([])
  const [busy, setBusy] = useState(0)

  async function load() {
    try {
      const r = await api.takesAll(pid, shotId)
      setTakes(r.takes ?? [])
    } catch (e) {
      toast(tpl('ver.read-failed', { err: String(e) }), 'err')
    }
  }

  useEffect(() => { void load() }, [pid, shotId])

  async function choose(v: number) {
    setBusy(v)
    const ok = await chooseTakeVersion(pid, shotId, v)
    setBusy(0)
    if (ok) { await load(); onChanged() }
  }

  async function adopt(v: number) {
    setBusy(v)
    const ok = await adoptVersion(pid, shotId, v)
    setBusy(0)
    if (ok) { await load(); onChanged() }
  }

  async function remove(v: number) {
    const ok = await confirmDialog({
      title: t('ver.delete'),
      message: tpl('ver.delete.confirm', { v }),
      confirmText: t('btn.delete'),
      danger: true,
    })
    if (!ok) return
    setBusy(v)
    try {
      await api.deleteTakes(pid, shotId, v)
      toast(`v${v} · ${t('asset.deleted')}`, 'ok')
      await load()
      onChanged()
    } catch (e) {
      toast(tpl('asset.delete-failed', { err: String(e) }), 'err')
    } finally {
      setBusy(0)
    }
  }

  return (
    <Modal
      title={tpl('takes.title', { sid: shotId })}
      onClose={onClose}
      wide
      footer={<><span className="spacer" /><button onClick={onClose}>{t('btn.close')}</button></>}
    >
      {takes.length === 0 && <p className="muted">{t('takes.empty')}</p>}
      <div className="takes-grid">
        {takes.map((tk) => {
          const url = api.mediaUrl(pid, shotId, tk.version)
          const secs = tk.metrics?.duration
          return (
            <div key={tk.version} className={`takes-card${tk.chosen ? ' chosen' : ''}`}>
              <div className="takes-video">
                <VideoPlayer
                  src={url}
                  controls
                  muted={false}
                  onClick={() => onPlay(url)}
                />
              </div>
              <div className="takes-info">
                <div className="takes-line">
                  <b>v{tk.version}</b>
                  {tk.chosen && <span className="tag">{t('takes.chosen')}</span>}
                  {!tk.matches_script && <span className="tag warn">{t('takes.stale')}</span>}
                </div>
                <div className="muted small">
                  {secs ? `${tpl('takes.seconds', { n: secs.toFixed(1) })} · ` : ''}
                  {tk.elapsed_s ? tpl('takes.rendered', { n: Math.round(tk.elapsed_s / 60) }) : ''}
                </div>
              </div>
              <div className="takes-acts">
                <button disabled={!!busy || tk.chosen} onClick={() => void choose(tk.version)}>
                  {tk.chosen ? t('takes.chosen-btn') : t('takes.choose')}
                </button>
                <button disabled={!!busy || tk.matches_script}
                  title={t('takes.adopt.tip')}
                  onClick={() => void adopt(tk.version)}>
                  {tk.matches_script ? t('takes.adopted') : t('takes.adopt')}
                </button>
                <button className="ghost danger" disabled={!!busy}
                  onClick={() => void remove(tk.version)}>{t('btn.delete')}</button>
              </div>
            </div>
          )
        })}
      </div>
    </Modal>
  )
}
