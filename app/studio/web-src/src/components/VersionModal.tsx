/**
 * 多版本对比：一个镜头所有成片并排看，选一个设为当前。
 * 「设为当前」只有最新一版可用（后端 adopt 只接受最新 take）。
 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { confirmDialog } from '../lib/confirm'
import { t, tpl } from '../lib/i18n'
import { adoptTake, deleteTake, errText, toast, useStore } from '../lib/store'
import { Modal } from './Modal'
import { VideoPlayer } from './VideoPlayer'
import './VersionModal.css'

interface Props {
  sid: string
  onClose: () => void
}

export function VersionModal({ sid, onClose }: Props) {
  const { detail, pid } = useStore()
  const shot = detail?.shots.find((s) => s.shot_id === sid)
  const [takes, setTakes] = useState<Array<{ version: number; path: string }>>([])
  const [busy, setBusy] = useState(false)

  async function load() {
    if (!pid) return
    try {
      const r = await api.listTakes(pid, sid)
      setTakes([...r.takes].sort((a, b) => b.version - a.version))
    } catch (e) {
      toast(tpl('ver.read-failed', { err: errText(e) }), 'err')
    }
  }

  useEffect(() => { void load() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [pid, sid])

  if (!pid || !shot) return null
  const latest = takes[0]?.version
  const current = shot.take?.version

  return (
    <Modal
      title={tpl('ver.title', { sid })}
      onClose={onClose}
      wide
      footer={(
        <>
          <span className="muted">{tpl('ver.summary', { cur: current ?? '—', n: takes.length })}</span>
          <div className="spacer" />
          <button className="primary" onClick={onClose}>{t('ver.close')}</button>
        </>
      )}
    >
      {takes.length === 0 && <div className="muted">{t('ver.empty')}</div>}
      <div className="ver-grid">
        {takes.map((tk) => {
          const isCurrent = tk.version === current
          return (
            <div key={tk.version} className={`ver-card${isCurrent ? ' is-current' : ''}`}>
              <VideoPlayer src={api.mediaUrl(pid, sid, tk.version)} controls muted={false} />
              <div className="ver-meta">
                <b>v{tk.version}</b>
                {isCurrent && <span className="tag">{t('ver.current')}</span>}
                {tk.version === latest && !isCurrent && <span className="tag muted-tag">{t('ver.latest')}</span>}
                <div className="spacer" />
                {tk.version === latest && !isCurrent && (
                  <button className="ghost" disabled={busy} onClick={async () => {
                    setBusy(true)
                    await adoptTake(sid)
                    await load()
                    setBusy(false)
                  }}>{t('ver.set-current')}</button>
                )}
                {!isCurrent && (
                  <button className="ghost danger" disabled={busy} onClick={async () => {
                    const ok = await confirmDialog({
                      title: t('ver.delete'),
                      message: tpl('ver.delete.confirm', { v: tk.version }),
                      confirmText: t('btn.delete'),
                      danger: true,
                    })
                    if (!ok) return
                    setBusy(true)
                    await deleteTake(sid, tk.version)
                    await load()
                    setBusy(false)
                  }}>{t('ver.delete')}</button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </Modal>
  )
}
