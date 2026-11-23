/**
 * 文生图 —— 独立图片工作台（不绑项目/分组）。
 *
 * 布局：
 *   上半部分（固定）：类型 / 参考图（上传）/ 风格 / 分辨率 / 数量 / 步数 / 种子 + 提示词
 *   下半部分：图片列表（按真实比例、限制高度、悬浮微放大、信息压在图片下半部）
 *
 * 每张图显示：生成时间、类型、提示词（过长点击弹窗看全文 + 参数）。
 */

import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { RESOLUTION_PRESETS, findPreset, presetWarnings, megapixels } from '../lib/resolutionPresets'
import { STYLE_FAMILIES, stylesOf, findStyle, type StyleFamily } from '../lib/stylePresets'
import { compilePrompt, type PromptSegments } from '../lib/promptBuilder'
import type { AssetCandidate } from '../lib/types'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { toast } from '../lib/store'
import { PromptAssistant } from './PromptAssistant'
import { ModelStatusPanel } from './ModelStatusPanel'
import { Modal } from './Modal'
import './AssetStudio.css'

type AssetKind = 'character' | 'scene' | 'prop' | 'emotion'

const KINDS: Array<{ key: AssetKind; labelKey: string }> = [
  { key: 'character', labelKey: 't2i.kind.character' },
  { key: 'scene', labelKey: 't2i.kind.scene' },
  { key: 'prop', labelKey: 't2i.kind.prop' },
  { key: 'emotion', labelKey: 't2i.kind.emotion' },
]

const EMPTY_SEGS: PromptSegments = {
  style: '', subject: '', state: '', material: '', light: '', lens: '', motion: '',
}

const DEFAULT_STYLE_ID = '3d_pbr'

interface UploadedRef { path: string; name: string; url: string }

export function AssetStudio() {
  const [kind, setKind] = useState<AssetKind>('character')
  const [presetId, setPresetId] = useState<string>('char_portrait')
  const [count, setCount] = useState<1 | 2 | 4>(2)
  const [steps, setSteps] = useState(6)
  const [seedMode, setSeedMode] = useState<'random' | 'fixed'>('random')
  const [fixedSeed, setFixedSeed] = useState<number>(12345)
  const [segs, setSegs] = useState<PromptSegments>(() => ({
    ...EMPTY_SEGS,
    style: findStyle(DEFAULT_STYLE_ID)?.en ?? '',
  }))
  const [styleFamily, setStyleFamily] = useState<StyleFamily>('3d')
  const [styleId, setStyleId] = useState<string>(DEFAULT_STYLE_ID)
  const [refs, setRefs] = useState<UploadedRef[]>([])
  const [refNote, setRefNote] = useState('')

  const [sp] = useSearchParams()
  const onlyProject = sp.get('project') || ''
  const [candidates, setCandidates] = useState<AssetCandidate[]>([])
  const [busyId, setBusyId] = useState('')
  const [broken, setBroken] = useState<Set<string>>(new Set())
  const [detail, setDetail] = useState<AssetCandidate | null>(null)
  const [zoomLevel, setZoomLevel] = useState(1)
  const [loadError, setLoadError] = useState('')
  const [modelsReady, setModelsReady] = useState(false)
  const [queueCount, setQueueCount] = useState(0)
  const [genJob, setGenJob] = useState<{ id: string; total: number } | null>(null)
  const [genInfo, setGenInfo] = useState<{ pct: number | null; detail: string } | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  async function reloadCandidates() {
    try {
      const r = await api.assetCandidates()
      // 只要图片：音乐走「创作 → 音乐」；没有 url 的（文件丢失）也不显示
      setCandidates((r.candidates || []).filter(
        (c) => c.kind !== 'music' && !!c.url
          && !(c as unknown as { broken?: boolean }).broken
          && (!onlyProject || c.project_id === onlyProject)))
      setLoadError('')
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('t2i.read-failed'))
    }
  }

  useEffect(() => {
    void reloadCandidates()
    let alive = true
    let timer = 0
    const tick = async () => {
      try {
        const [ms, q] = await Promise.all([api.modelsStatus(), api.globalQueue()])
        if (!alive) return
        setModelsReady(ms.ready)
        setQueueCount(q.count)
      } catch { /* 状态读失败不影响页面 */ }
      if (alive) timer = window.setTimeout(tick, 10000)
    }
    void tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [])

  // 生成任务轮询
  useEffect(() => {
    if (!genJob) return
    let alive = true
    let timer = 0
    const tick = async () => {
      try {
        const r = await api.jobProgress(genJob.id)
        if (!alive) return
        setGenInfo({ pct: r.progress?.pct ?? null, detail: r.progress?.detail || '' })
        if (r.job.state === 'done') {
          setGenJob(null); setGenInfo(null)
          await reloadCandidates()
          toast(tpl('t2i.gen.done-n', { n: genJob.total }), 'ok')
          return
        }
        if (r.job.state === 'failed' || r.job.state === 'cancelled') {
          setGenJob(null); setGenInfo(null)
          await reloadCandidates()
          toast(r.job.error || t('t2i.gen.failed'), 'err')
          return
        }
      } catch { /* 下一轮 */ }
      if (alive) timer = window.setTimeout(tick, 3000)
    }
    void tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [genJob?.id])

  // 提示词里必须有 <Picture n>，否则模型基本无视参考图
  function subjectClause(): string {
    if (!refs.length) return ''
    const lines = refs.map((_, i) => {
      const note = refNote.trim()
      return `<Picture ${i + 1}> is the uploaded reference image${note ? ': ' + note : ', keep its subject identity, hairstyle, clothing and colors consistent'}`
    })
    return lines.join('. ') + '.'
  }

  const STYLE_GUARD =
    '2D anime, cel-shaded, clean line art, flat color, hand-painted background.'

  async function generate() {
    const styleWords = compilePrompt(segs).compiled
    const subject = subjectClause()
    const hasStyle = /cel[- ]?shad|2D anime|line art/i.test(styleWords)
    const compiled = [subject, styleWords, hasStyle ? '' : STYLE_GUARD]
      .filter(Boolean).join(' ').trim()
    if (!compiled) { toast(t('t2i.need-prompt'), 'err'); return }
    const preset = findPreset(presetId)
    if (!preset) return
    setGenInfo({ pct: null, detail: t('t2i.gen.submitting') })
    try {
      const r = await api.generateAssets({
        kind, prompt: compiled, width: preset.width, height: preset.height,
        steps, seed: seedMode === 'fixed' ? fixedSeed : -1, count,
        ref_paths: refs.length ? refs.map((x) => x.path) : undefined,
      })
      setGenJob({ id: r.job_id, total: r.count })
      toast(tpl('t2i.gen.queued-n', { n: r.queue_position }), 'ok')
    } catch (e) {
      setGenInfo(null)
      toast(e instanceof Error ? e.message : t('t2i.gen.failed'), 'err')
    }
  }

  async function uploadRefs(files: FileList | null) {
    if (!files?.length) return
    for (const f of Array.from(files).slice(0, 4 - refs.length)) {
      try {
        const r = await api.uploadAssetRef(f)
        setRefs((prev) => [...prev, { path: r.path, name: f.name, url: URL.createObjectURL(f) }])
      } catch (e) {
        toast(e instanceof Error ? e.message : t('t2i.refs.upload-failed'), 'err')
      }
    }
    if (fileInput.current) fileInput.current.value = ''
  }

  async function removeCandidate(c: AssetCandidate) {
    setBusyId(c.id)
    try {
      await api.deleteAssetCandidate(c.id)
      await reloadCandidates()
      setDetail(null)
      toast(t('assets.deleted'), 'ok')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('t2i.delete.failed'), 'err')
    } finally {
      setBusyId('')
    }
  }

  const preset = findPreset(presetId)
  const warnings = preset ? presetWarnings(preset) : []
  const stylePreset = findStyle(styleId)
  const generating = !!genJob
  const genDisabled = !modelsReady || generating

  const kindLabel = (k: string) => {
    const found = KINDS.find((x) => x.key === k)
    return found ? t(found.labelKey) : k
  }
  const timeLabel = (iso: string) => {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }

  return (
    <div className="studio-v2">
      {/* ── 上半：参数 + 提示词（固定） */}
      <section className="sv2-top">
        <div className="sv2-row">
          <div className="sv2-field">
            <label>{t('t2i.type')}</label>
            <div className="sv2-chips">
              {KINDS.map((k) => (
                <button key={k.key} className={`sv2-chip${kind === k.key ? ' is-active' : ''}`}
                        onClick={() => setKind(k.key)}>{t(k.labelKey)}</button>
              ))}
            </div>
          </div>

          <div className="sv2-field sv2-refs">
            <label>{t('t2i.refs')} <span className="muted">{refs.length}/4</span></label>
            <div className="sv2-ref-row">
              {refs.map((r, i) => (
                <div key={r.path} className="sv2-ref" title={r.name}>
                  <img src={r.url} alt={r.name} />
                  <b>{i + 1}</b>
                  <button className="sv2-ref-del"
                          onClick={() => setRefs((p) => p.filter((x) => x.path !== r.path))}>×</button>
                </div>
              ))}
              {refs.length < 4 && (
                <button className="sv2-ref-add" onClick={() => fileInput.current?.click()}>+</button>
              )}
              <input ref={fileInput} type="file" accept="image/*" multiple hidden
                     onChange={(e) => void uploadRefs(e.target.files)} />
            </div>
          </div>

          <div className="sv2-field">
            <label>{t('t2i.resolution')}</label>
            <select value={presetId} onChange={(e) => setPresetId(e.target.value)}>
              {RESOLUTION_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>{p.width}×{p.height} · {p.aspect}</option>
              ))}
            </select>
            {preset && megapixels(preset.width, preset.height) < 4 && (
              <span className="sv2-warn">{t('t2i.low-res')}</span>
            )}
          </div>

          <div className="sv2-field sv2-narrow">
            <label>{t('t2i.count')}</label>
            <div className="sv2-chips">
              {[1, 2, 4].map((n) => (
                <button key={n} className={`sv2-chip${count === n ? ' is-active' : ''}`}
                        onClick={() => setCount(n as 1 | 2 | 4)}>{n}</button>
              ))}
            </div>
          </div>

          <div className="sv2-field sv2-narrow">
            <label>{t('t2i.steps')} {steps}</label>
            <input type="range" min={4} max={12} step={1} value={steps}
                   onChange={(e) => setSteps(Number(e.target.value))} />
          </div>

          <div className="sv2-field">
            <label>{t('t2i.seed')}</label>
            <div className="sv2-chips">
              <button className={`sv2-chip${seedMode === 'random' ? ' is-active' : ''}`}
                      onClick={() => setSeedMode('random')}>{t('t2i.seed.random')}</button>
              <button className={`sv2-chip${seedMode === 'fixed' ? ' is-active' : ''}`}
                      onClick={() => setSeedMode('fixed')}>{t('t2i.seed.fixed')}</button>
              {seedMode === 'fixed' && (
                <input className="sv2-seed" type="number" value={fixedSeed}
                       onChange={(e) => setFixedSeed(Number(e.target.value))} />
              )}
            </div>
          </div>

          <div className="sv2-field sv2-grow">
            <label>{t('t2i.style')}
              {stylePreset && <span className="muted"> {t(stylePreset.label)}</span>}
            </label>
            <div className="sv2-chips">
              {STYLE_FAMILIES.map((f) => (
                <button key={f.key} className={`sv2-chip${styleFamily === f.key ? ' is-active' : ''}`}
                        onClick={() => setStyleFamily(f.key)}>{t(f.label)}</button>
              ))}
            </div>
            <div className="sv2-chips sv2-styles">
              {stylesOf(styleFamily).map((s) => (
                <button key={s.id} className={`sv2-chip${styleId === s.id ? ' is-active' : ''}`}
                        title={s.en}
                        onClick={() => {
                          setStyleId(s.id); setStyleFamily(s.family)
                          setSegs((prev) => ({ ...prev, style: s.en }))
                        }}>{t(s.label)}</button>
              ))}
            </div>
          </div>
        </div>

        <div className="sv2-prompt">
          <PromptAssistant value={segs} onChange={setSegs} kind={kind}
                           refNames={refs.map((r) => r.name)} />
          {refs.length > 0 && (
            <label className="sv2-refnote">
              <span className="muted">{t('t2i.refs.note')}</span>
              <input value={refNote} onChange={(e) => setRefNote(e.target.value)}
                     placeholder={t('t2i.refs.note-ph')} />
            </label>
          )}
        </div>

        <div className="sv2-actions">
          <ModelStatusPanel />
          <span className="muted">{tpl('t2i.queue.count', { n: queueCount })}</span>
          {warnings.map((w, i) => <span key={i} className="sv2-warn">{w}</span>)}
          <div className="sv2-spacer" />
          {generating && (
            <>
              <div className="sv2-bar"><div style={{ width: `${genInfo?.pct ?? 6}%` }} /></div>
              <span className="muted">{genInfo?.detail || t('t2i.gen.running')}</span>
              <button className="ghost danger" onClick={() => void api.cancelJob(genJob!.id)}>
                {t('btn.cancel')}
              </button>
            </>
          )}
          <button className="sv2-generate" disabled={genDisabled} onClick={() => void generate()}>
            {generating ? t('t2i.generating') : !modelsReady ? '🔒 ' + t('t2i.locked') : t('t2i.generate')}
          </button>
        </div>
      </section>

      {/* ── 下半：图片列表 */}
      <section className="sv2-gallery">
        {loadError && <div className="sv2-error">{loadError}</div>}
        {candidates.length === 0 ? (
          <div className="sv2-empty muted">
            {modelsReady ? t('t2i.ph.ready') : t('t2i.ph.locked')}
          </div>
        ) : (
          <div className="sv2-grid">
            {candidates.filter((c) => !broken.has(c.id)).map((c) => (
              <figure key={c.id} className="sv2-card"
                      style={{ aspectRatio: `${c.width || 1} / ${c.height || 1}` }}
                      onClick={() => setDetail(c)}>
                {c.url
                  ? <img src={c.url} alt={c.prompt} loading="lazy"
                         onError={() => setBroken((prev) => new Set(prev).add(c.id))} />
                  : <span className="muted">{t('t2i.lost')}</span>}
                <figcaption className="sv2-cap">
                  <span className="sv2-cap-line">
                    {timeLabel(c.created_at)} · {kindLabel(c.kind)}
                  </span>
                  <span className="sv2-cap-prompt">{c.prompt}</span>
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </section>

      {detail && (
        <Modal title={`${timeLabel(detail.created_at)} · ${kindLabel(detail.kind)}`}
               onClose={() => setDetail(null)} wide>
          <div className="sv2-detail">
            {detail.url && (
              <div className="sv2-zoom-stage">
                <img src={detail.url} alt={detail.prompt}
                     style={{ transform: `scale(${zoomLevel})` }}
                     onClick={() => setZoomLevel((z) => (z >= 2 ? 1 : z + 0.5))} />
              </div>
            )}
            <div className="sv2-zoom-row">
              {[1, 1.5, 2].map((z) => (
                <button key={z} className={`sv2-chip${zoomLevel === z ? ' is-active' : ''}`}
                        onClick={() => setZoomLevel(z)}>{z}×</button>
              ))}
              <span className="muted">{t('t2i.zoom.hint')}</span>
            </div>
            <div className="sv2-detail-meta muted">
              {detail.width}×{detail.height} · seed {detail.seed} · {detail.steps} 步
              {detail.elapsed_s ? ` · ${detail.elapsed_s.toFixed(0)}s` : ''}
            </div>
            <p className="sv2-detail-prompt">{detail.prompt}</p>
            {detail.negative && <p className="muted">negative: {detail.negative}</p>}
          </div>
          <div className="sv2-detail-actions">
            <button className="ghost danger" disabled={busyId === detail.id}
                    onClick={() => void removeCandidate(detail)}>{t('assets.delete')}</button>
          </div>
        </Modal>
      )}
    </div>
  )
}
