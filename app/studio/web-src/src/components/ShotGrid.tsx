/** 中栏：镜头网格 + 批量工具栏 + 状态筛选 + 列表模式（查看/审核/成片）。 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { setDragShot } from '../lib/drag'
import { t } from '../lib/i18n'
import {
  renderOne, selectShot, setCheckedAll,
  setCheckedList, setListMode, toggleChecked, useStore,
} from '../lib/store'
import type { Shot, ShotState, ValidationReport } from '../lib/types'
import { BulkBar } from './BulkBar'
import { TakesModal } from './TakesModal'
import { ShotCard } from './ShotCard'
import './ShotGrid.css'

/** 每镜的问题数（错误 + 警告），用于卡片角标 */
export function problemsByShot(v: ValidationReport | null): Map<string, number> {
  const map = new Map<string, number>()
  for (const issue of [...(v?.errors ?? []), ...(v?.warnings ?? [])]) {
    if (!issue.shot_id) continue
    map.set(issue.shot_id, (map.get(issue.shot_id) ?? 0) + 1)
  }
  return map
}

type ListMode = 'view' | 'review' | 'compose' | 'settings'
const STATE_ORDER: ShotState[] = ['empty', 'queued', 'running', 'review', 'passed', 'rejected', 'stale', 'failed']

/** 该镜在指定模式下是否可被勾选 */
function eligibleFor(shot: Shot, mode: ListMode): boolean {
  if (mode === 'settings') return true
  if (!shot.take) return false
  if (mode === 'review') return shot.state !== 'passed'
  if (mode === 'compose') return true
  return false
}

interface Props {
  onPlan: (ids: string[]) => void
  onPlay: (sid: string) => void
  onImport: () => void
}

export function ShotGrid({ onPlan, onPlay, onImport }: Props) {
  const { detail, validation, checked, currentShotId, pid, listMode } = useStore()
  const shots = detail?.shots ?? []
  const problems = useMemo(() => problemsByShot(validation), [validation])
  const [versionsFor, setVersionsFor] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<ShotState | 'all'>('all')

  // 切项目时重置筛选
  useEffect(() => { setStatusFilter('all') }, [pid])

  // ESC 取消选中（避免不小心选中后角色/场景被自动指派）
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape' && currentShotId) {
        selectShot(null)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [currentShotId])

  // 注意：hook 必须全部在条件 return 之前调用，否则会在 pid 变化时触发 React #310
  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const s of shots) m.set(s.state, (m.get(s.state) ?? 0) + 1)
    return m
  }, [shots])

  // 非「查看」模式常驻复选框，先勾选再批量操作
  const showCheckboxes = listMode !== 'view'
  const eligibleIds = useMemo(
    () => shots.filter((s) => eligibleFor(s, listMode)).map((s) => s.shot_id),
    [shots, listMode],
  )

  if (!pid) {
    return <div className="mid"><div className="empty">{t('shots.pick-project')}</div></div>
  }

  const filtered = statusFilter === 'all' ? shots : shots.filter((s) => s.state === statusFilter)

  function selectEligible() {
    setCheckedList(showCheckboxes ? eligibleIds : [])
  }

  function onGridClick(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget && currentShotId) selectShot(null)
  }
  function onSelect(sid: string) {
    if (currentShotId === sid) selectShot(null)
    else selectShot(sid)
  }

  const modes: Array<{ key: ListMode; label: string }> = [
    { key: 'view', label: t('shots.mode.view') },
    { key: 'review', label: t('shots.mode.review') },
    { key: 'compose', label: t('shots.mode.compose') },
    { key: 'settings', label: t('shots.batch') },
  ]

  return (
    <div className="mid">
      <div className="grid-toolbar">
        <div className="mode-switch" role="tablist">
          {modes.map((m) => (
            <button
              key={m.key}
              className={`mode-btn mode-${m.key}${listMode === m.key ? ' is-active' : ''}`}
              onClick={() => setListMode(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>
        {showCheckboxes && (
          <>
            <button className="ghost" onClick={selectEligible} disabled={!eligibleIds.length}>
              {t('shots.select-all')}
            </button>
            <button className="ghost" onClick={() => setCheckedAll(false)} disabled={!checked.size}>
              {t('shots.clear')}
            </button>
          </>
        )}
        <div className="spacer" />
        <button className="primary" onClick={() => onPlan(checked.size ? [...checked] : [])} disabled={!shots.length}>
          {t('shots.plan')}
        </button>
      </div>

      <div className="status-filters">
        <button
          className={`status-chip${statusFilter === 'all' ? ' is-active' : ''}`}
          onClick={() => setStatusFilter('all')}
        >
          {t('shots.filter.all')} <b>{shots.length}</b>
        </button>
        {STATE_ORDER.filter((st) => (counts.get(st) ?? 0) > 0).map((st) => (
          <button
            key={st}
            className={`status-chip state-${st}${statusFilter === st ? ' is-active' : ''}`}
            onClick={() => setStatusFilter(st)}
          >
            <span className={`chip-dot state-${st}`} />
            {t(`state.${st}`)} <b>{counts.get(st)}</b>
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="empty" style={{ flex: 1 }}>
          <div>{shots.length === 0 ? t('shots.empty') : t('shots.filter-empty')}</div>
          {shots.length === 0 && (
            <>
              <div className="muted">{t('shots.empty-hint')}</div>
              <div className="row">
                <button className="primary" onClick={onImport}>{t('header.import-script')}</button>
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="shot-grid scroll" onClick={onGridClick}>
          {filtered.map((shot) => {
            const take = shot.take
            const videoUrl = take && take.version
              ? api.mediaUrl(pid, shot.shot_id, take.version)
              : undefined
            const previewUrl = shot.preview
              ? `/assets/previews/${encodeURIComponent(pid)}/${encodeURIComponent(shot.shot_id)}`
              : undefined
            const from = shot.chain_from
            const chainThumbUrl = shot.chain_ref && from?.shot_id && from.ready
              ? `/assets/lastframe/${encodeURIComponent(pid)}/${encodeURIComponent(from.shot_id)}`
              : undefined
            return (
              <ShotCard
                key={shot.shot_id}
                shot={shot}
                selected={currentShotId === shot.shot_id}
                checked={checked.has(shot.shot_id)}
                forceCheck={showCheckboxes && eligibleFor(shot, listMode)}
                videoUrl={videoUrl}
                previewUrl={previewUrl}
                chainThumbUrl={chainThumbUrl}
                problemCount={problems.get(shot.shot_id) ?? 0}
                onSelect={onSelect}
                onToggleCheck={(sid) => toggleChecked(sid)}
                onDragStart={setDragShot}
                onRender={(sid) => void renderOne(sid)}
                onPlay={onPlay}
                onShowVersions={(sid) => setVersionsFor(sid)}
              />
            )
          })}
        </div>
      )}

      {versionsFor && pid && (
        <TakesModal
          pid={pid}
          shotId={versionsFor}
          onClose={() => setVersionsFor(null)}
          onPlay={onPlay}
          onChanged={() => { /* reload() 已由 store 触发 */ }}
        />
      )}

      {showCheckboxes && <BulkBar />}
    </div>
  )
}
