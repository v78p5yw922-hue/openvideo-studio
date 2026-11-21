/**
 * ShotCard —— 镜头卡：上方是视频区，下方一条说明区（复选框 + 第几镜 + 描述）。
 *
 * 行为：
 *  - 鼠标移入自动播放，移出暂停并回到第一帧
 *  - 顶部状态胶囊只显示状态（不显示进度数字）
 *  - 不显示台词（台词在右栏检查器里编辑）
 *  - 复选框在底部说明区最前；选择模式下强制显示
 *  - 放大播放为画面中央悬浮按钮；点击卡片本身只选中
 */

import { memo } from 'react'
import { t, tpl } from '../lib/i18n'
import type { Shot, ShotState } from '../lib/types'
import { VideoPlayer } from './VideoPlayer'
import './ShotCard.css'

export function stateLabel(state: ShotState): string {
  return t(`state.${state}`)
}

interface Props {
  shot: Shot
  selected: boolean
  checked: boolean
  /** 成片视频（有 take 时） */
  videoUrl?: string
  /** 首帧预览图（还没有视频时） */
  previewUrl?: string
  /** chain 且上游就绪时，显示"将从这里起步"的末帧 */
  chainThumbUrl?: string
  /** 该镜的校验问题数，显示在右上角 */
  problemCount?: number
  /** 选择模式下强制显示复选框 */
  forceCheck?: boolean
  onSelect: (shotId: string) => void
  onToggleCheck: (shotId: string, checked: boolean) => void
  onDragStart: (shotId: string) => void
  onRender: (shotId: string) => void
  onPlay: (shotId: string) => void
  onShowVersions: (shotId: string) => void
}

export function progressText(shot: Shot): string {
  const p = shot.progress
  if (!p) return stateLabel(shot.state)
  if (p.stage === 'starting') return t('progress.starting')
  if (p.stage === 'loading') return t('progress.loading')
  if (p.stage === 'encoding') return t('progress.encoding')
  if (p.stage === 'decoding') return t('progress.decoding')
  if (p.stage === 'stalled') return tpl('progress.stalled', { detail: p.detail || '' })
  if (typeof p.pct === 'number' && p.pct >= 100 && p.stage !== 'done') return t('progress.finishing')
  if (typeof p.pct === 'number' && p.pct > 0) {
    const step = p.detail?.match(/([\d.]+)s\/it/)?.[1]
    return step ? tpl('progress.seconds-per-step', { pct: p.pct, step }) : `${p.pct}%`
  }
  return stateLabel(shot.state)
}

function shotDesc(shot: Shot): string {
  const d = (shot.camera || '').replace(/\s+/g, ' ').trim()
  if (d) return d
  return shot.shot_id
}

export const ShotCard = memo(function ShotCard({
  shot, selected, checked, videoUrl, previewUrl, chainThumbUrl, problemCount = 0, forceCheck,
  onSelect, onToggleCheck, onDragStart, onRender, onPlay, onShowVersions,
}: Props) {
  const running = shot.state === 'running'
  const busy = running || shot.state === 'queued'
  const pct = shot.progress?.pct ?? 0
  const seq = String(shot.ordinal + 1).padStart(2, '0')
  const desc = shotDesc(shot)

  return (
    <div
      className={`shot-card state-${shot.state}${selected ? ' is-selected' : ''}${forceCheck ? ' force-check' : ''}`}
      draggable
      onDragStart={() => onDragStart(shot.shot_id)}
      onClick={() => onSelect(shot.shot_id)}
      role="button"
      tabIndex={0}
      title={t('shot.select')}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(shot.shot_id) }}
    >
      <div className="shot-frame">
        {videoUrl ? (
          <VideoPlayer src={videoUrl} hoverPlay className="shot-video" />
        ) : previewUrl ? (
          <img className="shot-preview" src={previewUrl} alt="" loading="lazy" title={t('shot.preview')} />
        ) : chainThumbUrl ? (
          <img className="shot-preview" src={chainThumbUrl} alt="" loading="lazy" title={t('shot.chain-hint')} />
        ) : (
          <div className="shot-empty">
            <span className="shot-empty-text">{running ? t('shot.rendering') : t('shot.pending')}</span>
          </div>
        )}

        {/* 顶部状态：只显示状态名 */}
        <div className="shot-top">
          <span className="shot-state">
            <span className={`shot-dot state-${shot.state}`} />
            <span className="shot-state-text">{stateLabel(shot.state)}</span>
          </span>
          {problemCount > 0 && (
            <span className="shot-problems" title={tpl('shots.problems', { n: problemCount })}>⚠{problemCount}</span>
          )}
        </div>

        {/* 悬停出现：放大播放（点击卡片本身只做选中） */}
        {videoUrl && !busy && (
          <button
            className="shot-play"
            title={t('shot.play')}
            aria-label={t('shot.play')}
            onClick={(e) => { e.stopPropagation(); onPlay(shot.shot_id) }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <path d="M8 5.5v13l10-6.5-10-6.5z" />
            </svg>
          </button>
        )}

        {/* 版本号 */}
        {shot.take?.version ? (
          <button
            className="shot-version"
            title={t('shot.more-versions')}
            onClick={(e) => { e.stopPropagation(); onShowVersions(shot.shot_id) }}
          >
            v{shot.take.version}
          </button>
        ) : null}

        {/* 细进度条（只在渲染中） */}
        {busy && <div className="shot-progress"><div style={{ width: `${pct}%` }} /></div>}

        {/* hover 操作 */}
        <div className="shot-actions" onClick={(e) => e.stopPropagation()}>
          <button className="shot-btn primary" disabled={busy} onClick={() => onRender(shot.shot_id)} title={t('header.generate-tip')}>
            {busy ? progressText(shot) : t('shot.generate')}
          </button>
          {shot.take?.version ? (
            <button className="shot-btn" onClick={() => onShowVersions(shot.shot_id)} title={t('shot.more-versions')}>
              {t('shot.versions')}
            </button>
          ) : null}
        </div>
      </div>

      {/* 底部说明区：复选框 + 第几镜 + 描述；点击白区也会选中该镜（复选框自己拦掉冒泡） */}
      <div className="shot-meta">
        <label className="shot-check" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => onToggleCheck(shot.shot_id, e.target.checked)}
            aria-label={tpl('shot.check', { sid: shot.shot_id })}
          />
        </label>
        <span className="shot-meta-name">
          {tpl('shot.no', { n: shot.ordinal + 1 })}
          <span className="shot-seq">{tpl('shot.seq', { seq })}</span>
        </span>
        <span className="shot-meta-desc" title={desc}>{desc}</span>
        {shot.state === 'stale' && <span className="shot-stale-tag">{t('state.stale')}</span>}
        {shot.duration ? <span className="shot-meta-dur">{shot.duration}s</span> : null}
      </div>
    </div>
  )
})
