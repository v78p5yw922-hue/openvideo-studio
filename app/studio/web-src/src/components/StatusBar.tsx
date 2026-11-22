/** 底部状态栏：已选/预计耗时 + GPU/执行器/队列。状态信息只在这里出现。 */

import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { stopRendering, useStore } from '../lib/store'
import type { Shot } from '../lib/types'

/** 单镜耗时估算（RTX 4090 实测）：10s≈27 分钟、8s≈17 分钟、5s≈9 分钟；草稿打七折 */
export function minutesPerShot(draft: boolean, seconds = 10): number {
  const full = seconds >= 10 ? 27 : seconds >= 8 ? 17 : 9
  return draft ? Math.round(full * 0.7) : full
}

function shotSeconds(s: { duration?: number; duration_sec?: number }): number {
  return Number(s.duration_sec ?? s.duration ?? 10)
}

function doneCount(shots: Shot[]): number {
  return shots.filter((s) => s.state === 'passed' || s.state === 'review').length
}

interface ActiveJob {
  id: string; kind: string; state: string; project_id: string
  target: string; page: string; pct?: number | null; detail?: string; started_at?: string
}

export function StatusBar() {
  const { detail, checked, queue, health, stats, opts } = useStore()
  const { pathname } = useLocation()
  const onRenderPage = pathname.startsWith('/create/video/rendering')
  const [active, setActive] = useState<ActiveJob[]>([])

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const r = await api.activeJobs()
        if (alive) setActive(r.jobs || [])
      } catch { /* 忽略 */ }
    }
    void load()
    const timer = window.setInterval(() => void load(), 12000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [])

  const running = active.find((j) => j.state === 'running') || active[0]
  const kindLabel: Record<string, string> = {
    render: t('status.kind.render'), asset: t('status.kind.asset'),
    preview: t('status.kind.asset'), music: t('status.kind.music'),
    assemble: t('status.kind.compose'), merge: t('status.kind.compose'),
  }
  const ago = (iso?: string) => {
    if (!iso) return ''
    const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
    return tpl('status.running-for', { m })
  }
  const shots = detail?.shots ?? []
  const total = shots.length
  const done = doneCount(shots)
  const pct = total ? Math.round((done / total) * 100) : 0
  const busy = (queue?.count ?? 0) > 0

  return (
    <footer className="statusbar">
      <span>
        {onRenderPage ? (
          <>
            {tpl('status.selected-shots', { n: checked.size })}
            {checked.size > 0 && (
              <> · {tpl('status.estimate-short', {
                m: [...checked].reduce((sum, sid) => {
                  const s = shots.find((x) => x.shot_id === sid)
                  return sum + minutesPerShot(opts.draft, s ? shotSeconds(s) : 10)
                }, 0),
              })}{opts.draft ? t('status.draft') : ''}</>
            )}
          </>
        ) : (
          <span>{t('status.gpu-idle')}</span>
        )}
      </span>
      {onRenderPage && (
        <>
          <div className="progress" aria-hidden><div style={{ width: `${pct}%` }} /></div>
          <span>{done} / {total}（{pct}%）</span>
          {stats?.avg_shot_seconds
            ? <span className="statusbar-optional">{tpl('status.avg', { n: Math.round(stats.avg_shot_seconds) })}</span>
            : null}
        </>
      )}
      <div className="spacer" />
      {running && (
        <span className="statusbar-task">
          {t('status.running')}：{kindLabel[running.kind] || running.kind} · {running.target}
          {running.pct != null ? ` · ${Math.round(running.pct)}%` : ''}
          {running.started_at ? ` · ${ago(running.started_at)}` : ''}
          {' '}<Link to={running.page}>{t('status.jump')}</Link>
        </span>
      )}
      {busy && (
        <button className="danger" onClick={() => void stopRendering()}>■ {t('status.stop')}</button>
      )}
      <span>{busy ? tpl('status.gpu-busy', { n: queue?.count ?? 0 }) : t('status.gpu-idle')}</span>
      <span className="statusbar-optional">{t('status.executor')} {health?.executor ? t('status.executor.on') : t('status.executor.off')}</span>
      <span className="statusbar-optional">{tpl('status.queue', { n: queue?.count ?? 0 })}</span>
    </footer>
  )
}
