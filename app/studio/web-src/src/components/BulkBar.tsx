/**
 * 批量操作条：非「查看」模式常驻底部。
 * 各模式显示不同的批量动作，均要求先勾选（选中数 > 0 才可点）：
 *  - 审核：批量通过 / 批量打回
 *  - 成片：去合成（保留勾选，导航到合成页）
 *  - 批量设置：按角色智能 / 全部硬切 / 全部接上一帧 / 批量修改…
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { t, tpl } from '../lib/i18n'
import {
  autoTransitionsSelected, batchEdit, batchReviewShots, chainRefAll, cutAll,
  setCheckedAll, useStore,
} from '../lib/store'
import type { Transition } from '../lib/types'

export function BulkBar() {
  const { checked, detail, listMode } = useStore()
  const nav = useNavigate()
  const [editOpen, setEditOpen] = useState(false)
  const [scene, setScene] = useState('')
  const [transition, setTransition] = useState('')
  const [duration, setDuration] = useState('')

  const ids = [...checked]
  const none = ids.length === 0
  const scenes = detail?.scenes ?? []

  function resetFields() {
    setScene('')
    setTransition('')
    setDuration('')
  }
  function closeEdit() {
    setEditOpen(false)
    resetFields()
  }

  async function applyEdit() {
    await batchEdit(ids, {
      scene_id: scene || undefined,
      transition: (transition || undefined) as Transition | undefined,
      duration: duration ? Number(duration) : undefined,
    })
    setCheckedAll(false)
    closeEdit()
  }

  // batchReviewShots 内部已清空 checked
  function doReview(verdict: 'pass' | 'reject') {
    void batchReviewShots(verdict)
  }

  function goCompose() {
    // 保留 checked 给合成页用，只导航
    nav('/create/video/compose')
  }

  async function doAuto() { await autoTransitionsSelected(ids); setCheckedAll(false) }
  async function doCut() { await cutAll(ids); setCheckedAll(false) }
  async function doChain() { await chainRefAll(true, ids); setCheckedAll(false) }

  const modeLabel = listMode === 'review'
    ? t('shots.mode.review')
    : listMode === 'compose'
      ? t('shots.mode.compose')
      : t('shots.batch')

  return (
    <div className={`bulk-bar bulk-${listMode}`}>
      <span className="bulk-mode-label">{modeLabel}</span>
      <span>{tpl('bulk.selected', { n: checked.size })}</span>

      {listMode === 'settings' && editOpen && (
        <div className="bulk-panel">
          <label>{t('bulk.scene')}
            <select value={scene} onChange={(e) => setScene(e.target.value)}>
              <option value="">{t('bulk.no-change')}</option>
              {scenes.map((s) => (
                <option key={s.scene_id} value={s.scene_id}>{s.scene_id} {s.name}</option>
              ))}
            </select>
          </label>
          <label>{t('bulk.transition')}
            <select value={transition} onChange={(e) => setTransition(e.target.value)}>
              <option value="">{t('bulk.no-change')}</option>
              <option value="cut">{t('bulk.cut')}</option>
              <option value="chain">{t('bulk.chain')}</option>
            </select>
          </label>
          <label>{t('bulk.duration')}
            <input type="number" min={1} max={20} value={duration}
              onChange={(e) => setDuration(e.target.value)} placeholder={t('bulk.no-change')} />
          </label>
          <div className="row">
            <button className="primary"
              disabled={none || (!scene && !transition && !duration)}
              onClick={() => void applyEdit()}>{t('bulk.apply')}</button>
            <button className="ghost" onClick={closeEdit}>{t('bulk.cancel')}</button>
          </div>
        </div>
      )}

      <div className="spacer" />

      {listMode === 'review' && (
        <>
          <button className="primary" title={t('bulk.pass.tip')} disabled={none}
            onClick={() => doReview('pass')}>✓ {t('bulk.pass')}</button>
          <button className="ghost" title={t('bulk.reject.tip')} disabled={none}
            onClick={() => doReview('reject')}>{t('bulk.reject')}</button>
        </>
      )}

      {listMode === 'compose' && (
        <button className="primary" onClick={goCompose} title={t('bulk.next.tip')} disabled={none}>
          {t('bulk.compose')} →
        </button>
      )}

      {listMode === 'settings' && (
        <>
          <button className="ghost" disabled={none} onClick={() => void doAuto()}>
            {t('shots.batch.auto')}
          </button>
          <button className="ghost" disabled={none} onClick={() => void doCut()}>
            {t('shots.batch.cut')}
          </button>
          <button className="ghost" disabled={none} onClick={() => void doChain()}>
            {t('shots.batch.chain-ref')}
          </button>
          <button className={editOpen ? 'primary' : 'ghost'}
            onClick={() => setEditOpen((v) => !v)}>
            {t('bulk.edit')} {editOpen ? '︿' : '﹀'}
          </button>
        </>
      )}
    </div>
  )
}
