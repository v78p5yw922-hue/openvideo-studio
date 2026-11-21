/** 合成成片 + 导出 + 统计。合成基于分镜列表里勾选的镜头。 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { assembleNow, chooseLatestAll, passAllShots, setCheckedList, setListMode, useStore } from '../lib/store'
import { AudioPanel } from './AudioPanel'
import './ExportPanel.css'

interface Props {
  onPlayOutput: (url: string, title: string) => void
}

export function ExportPanel({ onPlayOutput }: Props) {
  const { stats, detail, lastOutput, pid, validation, checked } = useStore()
  const nav = useNavigate()
  const [withSubs, setWithSubs] = useState(true)
  const [busy, setBusy] = useState(false)

  const shots = detail?.shots ?? []

  // 可合成 = 有 take；按项目顺序取勾选的
  const composable = shots.filter((s) => !!s.take)
  const selected = composable.filter((s) => checked.has(s.shot_id))
  const passed = shots.filter((s) => s.state === 'passed').length
  const shotIds = selected.map((s) => s.shot_id)

  async function assemble() {
    setBusy(true)
    const r = await assembleNow({ with_subs: withSubs, shot_ids: shotIds.length ? shotIds : undefined })
    setBusy(false)
    if (r) onPlayOutput(r.url, tpl('compose.output-title', { pid: pid ?? '' }))
  }

  function goSelect() {
    setListMode('compose')
    nav('/create/video/rendering')
  }
  function selectAllComposable() {
    setCheckedList(composable.map((s) => s.shot_id))
  }

  function posterUrl(sid: string, version?: number) {
    const v = version ? `?v=${version}` : ''
    return `/assets/poster/${encodeURIComponent(pid ?? '')}/${encodeURIComponent(sid)}${v}`
  }

  return (
    <div className="export-space">
      <section className="card compose-card">
        <div className="compose-head">
          <h2>{t('compose.title')}</h2>
          <p className="muted compose-counts">
            {tpl('compose.counts', { ready: composable.length, passed, chosen: selected.length })}
          </p>
        </div>

        {selected.length > 0 ? (
          <div className="compose-list-wrap">
            <p className="muted">{tpl('compose.selected', { n: selected.length })}</p>
            <div className="compose-list">
              {selected.map((s, i) => (
                <div key={s.shot_id} className="compose-item">
                  <span className="compose-idx">{i + 1}</span>
                  <div className="compose-thumb-box">
                    <img className="compose-thumb" src={posterUrl(s.shot_id, s.take?.version)} alt="" loading="lazy"
                         onError={(e) => { e.currentTarget.style.display = 'none' }} />
                  </div>
                  <div className="compose-meta">
                    <div>{tpl('shot.no', { n: s.ordinal + 1 })} <span className="muted">{s.shot_id}</span></div>
                    <div className="muted">{(s.camera || '').slice(0, 18)}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="compose-empty">
            <p className="muted">{t('compose.select-hint')}</p>
            <div className="row">
              <button className="primary" onClick={goSelect}>{t('compose.go-select')}</button>
              <button className="ghost" onClick={selectAllComposable} disabled={!composable.length}>
                {tpl('compose.select-all', { n: composable.length })}
              </button>
            </div>
          </div>
        )}

        <div className="pick-group">
          <span className="pick-label">{t('compose.pick')}</span>
          <button className="ghost" disabled={busy || !pid} title={t('compose.latest-all')}
                  onClick={() => void chooseLatestAll(pid ?? '')}>
            {t('compose.pick.latest')}
          </button>
          <button className="ghost" disabled={busy || !pid} title={t('compose.pass-all')}
                  onClick={() => void passAllShots(pid ?? '')}>
            {t('compose.pick.pass')}
          </button>
          <button className="ghost" disabled={!composable.length} onClick={selectAllComposable}>
            {t('compose.pick.choose')}
          </button>
        </div>

        <label className="check-row">
          <input type="checkbox" checked={withSubs} onChange={(e) => setWithSubs(e.target.checked)} />
          {t('compose.subs')}
        </label>

        <div className="row">
          <button className="primary" disabled={busy || selected.length === 0} onClick={() => void assemble()}>
            {busy ? t('compose.assembling') : t('compose.assemble')}
          </button>
          {lastOutput && (
            <>
              <button className="ghost" onClick={() => onPlayOutput(lastOutput.url, t('compose.recent'))}>{t('compose.play')}</button>
              <a className="ghost" href={lastOutput.url} download>{t('compose.download')}</a>
              <span className="muted">
                {lastOutput.subtitles
                  ? tpl('compose.result-subs', { n: lastOutput.shots })
                  : tpl('compose.result', { n: lastOutput.shots })}
              </span>
            </>
          )}
        </div>
        {validation?.errors.length ? (
          <p className="muted">{tpl('compose.errors', { n: validation.errors.length })}</p>
        ) : null}
        {shots.length === 0 && <div className="muted">{t('compose.no-shots')}</div>}
      </section>

      <div className="export-row">
        <AudioPanel />
        <section className="card">
          <h2>{t('compose.export')}</h2>
          <div className="row">
            <a className="ghost" href={api.exportMarkdownUrl(pid ?? '')} download>Markdown</a>
            <a className="ghost" href={api.exportEdlUrl(pid ?? '')} download>{t('export.edl')}</a>
            <a className="ghost" href={api.exportOtioUrl(pid ?? '')} download>{t('export.otio')}</a>
          </div>
        </section>
      </div>

      <section className="card">
        <h2>{t('compose.stats')}</h2>
        {stats ? (
          <div className="stat-grid">
            <Stat label={t('compose.stat.shots')} value={stats.shots} />
            <Stat label={t('compose.stat.takes')} value={stats.takes} />
            <Stat label={t('compose.stat.pass-rate')} value={`${Math.round(stats.pass_rate * 100)}%`} />
            <Stat label={t('compose.stat.first-take')} value={`${Math.round(stats.first_take_pass * 100)}%`} />
            <Stat label={t('compose.stat.rerender')} value={`${Math.round(stats.rerender_rate * 100)}%`} />
            <Stat label={t('compose.stat.avg')} value={`${stats.avg_shot_seconds}s`} />
            <Stat label={t('compose.stat.gpu')} value={Math.round(stats.total_gpu_minutes)} />
            <Stat label={t('compose.stat.vram')} value={`${Math.round(stats.peak_mib / 1024)} GB`} />
          </div>
        ) : (
          <div className="muted">{t('compose.no-stats')}</div>
        )}
        {stats && Object.keys(stats.reject_reasons).length > 0 && (
          <div className="reasons">
            <h3>{t('compose.reject-reasons')}</h3>
            {Object.entries(stats.reject_reasons).map(([k, v]) => (
              <div key={k} className="reason-row"><span>{k}</span><b>{v}</b></div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <span className="muted">{label}</span>
      <b>{value}</b>
    </div>
  )
}
