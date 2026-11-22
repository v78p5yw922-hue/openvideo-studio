/**
 * 音频详情弹窗：播放器 + 歌词（含进度高亮）+ 风格描述 + 参数。
 *
 * 歌词来自后端 .lrc（时间轴按行估算），所以高亮是"大致对得上"，
 * 没有词级对齐 —— 弹窗里也写明了这一点。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { t, tpl } from '../lib/i18n'
import { Modal } from './Modal'
import './AudioDetail.css'

export interface AudioCand {
  id: string
  prompt: string
  url?: string
  lyrics?: string
  lyrics_url?: string
  seed?: number
  created_at?: string
  params?: Record<string, unknown>
}

interface Line { at: number; text: string }

function parseLrc(raw: string): Line[] {
  const out: Line[] = []
  for (const ln of String(raw || '').split(/\r?\n/)) {
    const m = ln.match(/^\[(\d{2}):(\d{2}(?:\.\d+)?)\](.*)$/)
    if (m) {
      out.push({ at: Number(m[1]) * 60 + Number(m[2]), text: m[3].trim() })
    } else if (ln.trim()) {
      out.push({ at: -1, text: ln.trim() })   // 段落标签，不占时间
    }
  }
  return out
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export function AudioDetail({ cand, onClose }: { cand: AudioCand; onClose: () => void }) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [now, setNow] = useState(0)
  const [dur, setDur] = useState(0)
  const [lrc, setLrc] = useState('')

  useEffect(() => {
    if (typeof cand.lyrics === 'string' && cand.lyrics.trim()) setLrc(cand.lyrics)
    else if (cand.lyrics_url) {
      fetch(cand.lyrics_url).then((r) => (r.ok ? r.text() : '')).then(setLrc).catch(() => undefined)
    }
  }, [cand])

  const lines = useMemo(() => parseLrc(lrc), [lrc])
  const timed = lines.filter((l) => l.at >= 0)
  const activeIdx = timed.reduce((acc, l, i) => (now >= l.at ? i : acc), -1)
  const pct = dur > 0 ? Math.min(100, (now / dur) * 100) : 0

  const pr = cand.params || {}

  return (
    <Modal title={t('audio.detail')} onClose={onClose} wide>
      <div className="ad-root">
        <audio ref={audioRef} src={cand.url} controls preload="metadata"
               onTimeUpdate={(e) => setNow(e.currentTarget.currentTime)}
               onLoadedMetadata={(e) => setDur(e.currentTarget.duration || 0)}
               style={{ width: '100%' }} />

        <div className="ad-progress">
          <div className="ad-bar"><i style={{ width: `${pct}%` }} /></div>
          <span className="muted">{fmt(now)} / {fmt(dur)}</span>
        </div>

        <div className="ad-grid">
          <div className="ad-lyrics">
            <div className="ad-label">{t('audio.lyrics')}</div>
            {timed.length === 0 && !lrc && <div className="muted">{t('audio.no-lyrics')}</div>}
            {lines.map((l, i) => {
              const timedIdx = l.at >= 0 ? timed.indexOf(l) : -1
              return (
                <div key={`${i}-${l.text}`}
                     className={`ad-line${l.at < 0 ? ' ad-tag' : ''}${timedIdx === activeIdx ? ' is-active' : ''}`}
                     onClick={() => {
                       const a = audioRef.current
                       if (a && l.at >= 0) a.currentTime = l.at
                     }}>
                  {l.text}
                </div>
              )
            })}
            {timed.length > 0 && (
              <p className="muted sm">{t('audio.lrc-estimated')}</p>
            )}
          </div>

          <div className="ad-meta">
            <div className="ad-label">{t('audio.style')}</div>
            <p>{cand.prompt}</p>

            <div className="ad-label">{t('audio.params')}</div>
            <ul className="ad-list">
              {pr.seconds ? <li>{tpl('audio.p-len', { v: String(pr.seconds) })}</li> : null}
              {pr.cfg ? <li>cfg {String(pr.cfg)}</li> : null}
              {pr.top_k ? <li>top_k {String(pr.top_k)}</li> : null}
              {pr.steps ? <li>{String(pr.steps)} steps</li> : null}
              {pr.model ? <li>{String(pr.model)}</li> : null}
              {cand.seed != null ? <li>seed {cand.seed}</li> : null}
              <li>id <code>{cand.id}</code></li>
            </ul>

            {cand.lyrics_url && (
              <a className="ghost sm" href={cand.lyrics_url} download={`${cand.id}.lrc`}>
                {t('audio.download-lrc')}
              </a>
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}
