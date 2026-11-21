/** 底部状态栏：已选/预计耗时 + GPU/执行器/队列。状态信息只在这里出现。 */

import { t, tpl } from '../lib/i18n'
import { stopRendering, useStore } from '../lib/store'
import type { Shot } from '../lib/types'

/** 单镜耗时估算：草稿约 5 分钟，正式约 8.5 分钟 */
export function minutesPerShot(draft: boolean): number {
  return draft ? 5 : 8.5
}

function doneCount(shots: Shot[]): number {
  return shots.filter((s) => s.state === 'passed' || s.state === 'review').length
}

export function StatusBar() {
  const { detail, checked, queue, health, stats, opts } = useStore()
  const shots = detail?.shots ?? []
  const total = shots.length
  const done = doneCount(shots)
  const pct = total ? Math.round((done / total) * 100) : 0
  const busy = (queue?.count ?? 0) > 0

  return (
    <footer className="statusbar">
      <span>
        {tpl('status.selected', { n: checked.size })}
        {busy ? <> · {tpl('status.estimate', { m: Math.round(checked.size * minutesPerShot(opts.draft)) })}{opts.draft ? t('status.draft') : ''}</> : null}
      </span>
      <div className="progress" aria-hidden><div style={{ width: `${pct}%` }} /></div>
      <span>{done} / {total}（{pct}%）</span>
      {stats?.avg_shot_seconds ? <span className="statusbar-optional">{tpl('status.avg', { n: stats.avg_shot_seconds })}</span> : null}
      <div className="spacer" />
      {busy && (
        <button className="danger" onClick={() => void stopRendering()}>■ {t('status.stop')}</button>
      )}
      <span>{busy ? tpl('status.gpu-busy', { n: queue?.count ?? 0 }) : t('status.gpu-idle')}</span>
      <span className="statusbar-optional">{t('status.executor')} {health?.executor ? t('status.executor.on') : t('status.executor.off')}</span>
      <span className="statusbar-optional">{tpl('status.queue', { n: queue?.count ?? 0 })}</span>
    </footer>
  )
}
