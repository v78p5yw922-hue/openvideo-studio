/**
 * 计划弹窗：两段式。
 *  第一段 = /plan 出计划（多少镜、多久、几点完、阻断问题）
 *  第二段 = /confirm 才真正入队
 */

import { useEffect, useState } from 'react'
import { t, tpl } from '../lib/i18n'
import { confirmPlan, makePlan, nightRun, setOpts, toast, useStore } from '../lib/store'
import type { PlanResult } from '../lib/types'
import { Modal } from './Modal'
import './PlanModal.css'

interface Props {
  /** 空数组 = 全部镜头 */
  shotIds: string[]
  onClose: () => void
}

export function PlanModal({ shotIds, onClose }: Props) {
  const { opts, detail } = useStore()
  const [plan, setPlan] = useState<PlanResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  async function build(force = opts.force) {
    setLoading(true)
    const p = await makePlan(shotIds, { force })
    setPlan(p)
    setLoading(false)
  }

  useEffect(() => {
    void build()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const total = detail?.shots.length ?? 0
  const target = shotIds.length || total

  async function confirm() {
    if (!plan) return
    setBusy(true)
    const r = await confirmPlan(plan)
    setBusy(false)
    if (r) onClose()
  }

  async function night() {
    setBusy(true)
    const ok = await nightRun()
    setBusy(false)
    if (ok) onClose()
    else toast(t('plan.night.failed'), 'err')
  }

  return (
    <Modal
      title={tpl('plan.title', { n: target })}
      onClose={onClose}
      footer={(
        <>
          <button className="ghost" disabled={busy} onClick={() => void night()} title={t('plan.night.tip')}>
            {t('plan.night')}
          </button>
          <div className="spacer" />
          <button onClick={onClose}>{t('plan.cancel')}</button>
          <button
            className="primary"
            disabled={loading || busy || !plan || plan.count === 0 || plan.blocking.length > 0}
            onClick={() => void confirm()}
          >
            {busy ? t('plan.queuing')
              : plan ? tpl('plan.queue-count', { n: plan.count }) : t('plan.queue')}
          </button>
        </>
      )}
    >
      <div className="plan-options">
        <label className="check-row">
          <input type="checkbox" checked={opts.draft}
            onChange={(e) => setOpts({ draft: e.target.checked })} />
          {t('plan.draft')}
        </label>
        <label className="check-row">
          <input type="checkbox" checked={opts.force}
            onChange={(e) => setOpts({ force: e.target.checked })} />
          {t('plan.force')}
        </label>
        <label className="check-row">
          <input type="checkbox" checked={opts.pauseAfterOne}
            onChange={(e) => setOpts({ pauseAfterOne: e.target.checked })} />
          {t('plan.pause')}
        </label>
        <div className="opt-row">
          <label>
            {t('plan.prompt')}
            <select value={opts.promptMode} onChange={(e) => setOpts({ promptMode: e.target.value as typeof opts.promptMode })}>
              <option value="compile">{t('plan.prompt.compile')}</option>
              <option value="concat">{t('plan.prompt.concat')}</option>
              <option value="">{t('plan.prompt.default')}</option>
            </select>
          </label>
          <label>
            {t('plan.model')}
            <select value={opts.unet} onChange={(e) => setOpts({ unet: e.target.value as typeof opts.unet })}>
              <option value="">{t('plan.model.default')}</option>
              <option value="ref2va">{t('plan.model.ref2va')}</option>
            </select>
          </label>
          <button className="ghost" disabled={loading} onClick={() => void build()}>{t('plan.rebuild')}</button>
        </div>
      </div>

      {loading && <div className="muted">{t('plan.computing')}</div>}

      {plan && !loading && (
        <>
          <div className="plan-summary">
            <div><span className="muted">{t('plan.will')}</span><b>{plan.count}</b> {t('plan.shots')}</div>
            <div><span className="muted">{t('plan.est')}</span><b>{plan.est_minutes}</b> {t('plan.minutes')}</div>
            <div><span className="muted">{t('plan.worst')}</span><b>{plan.worst_minutes}</b> {t('plan.minutes')}</div>
            <div><span className="muted">{t('plan.finish')}</span><b>{plan.est_finish}</b></div>
          </div>

          {plan.count === 0 && (
            <div className="notice warn">
              {t('plan.nothing')}
              <button className="link" onClick={() => { setOpts({ force: true }); void build(true) }}>
                {t('plan.force-rebuild')}
              </button>
            </div>
          )}

          {plan.blocking.length > 0 && (
            <div className="notice bad">
              <b>{t('plan.blocking')}</b>
              <ul>
                {plan.blocking.map((i, n) => (
                  <li key={n}>{i.shot_id ? `${i.shot_id}：` : ''}{i.msg}</li>
                ))}
              </ul>
            </div>
          )}

          {plan.warnings.length > 0 && (
            <div className="notice warn">
              <b>{tpl('plan.warnings', { n: plan.warnings.length })}</b>
              <ul>
                {plan.warnings.slice(0, 8).map((i, n) => (
                  <li key={n}>{i.shot_id ? `${i.shot_id}：` : ''}{i.msg}</li>
                ))}
              </ul>
            </div>
          )}

          <details className="plan-list">
            <summary>{tpl('plan.list', { n: plan.shots.length })}</summary>
            <ul>
              {plan.shots.map((s) => (
                <li key={s.shot_id}>
                  <span className="mono">{s.shot_id}</span>
                  <span className="muted">{s.scene_id} · {s.duration}s · {s.transition}</span>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </Modal>
  )
}
