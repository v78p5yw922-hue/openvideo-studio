/**
 * 文生图 —— 用 H3-Image 生成角色/场景/道具图，直接入库到现有资产系统。
 * M1 阶段：页面骨架 + 提示词助手 + 模型状态 + 生成按钮置灰（"先下载模型"）。
 * M2 之后接上真实生成工作流（runner kind=asset）。
 */

import { useEffect, useState } from 'react'
import { RESOLUTION_PRESETS, findPreset, presetWarnings, megapixels } from '../lib/resolutionPresets'
import { STYLE_FAMILIES, stylesOf, findStyle, type StyleFamily } from '../lib/stylePresets'
import { compilePrompt, type PromptSegments } from '../lib/promptBuilder'
import type { AssetCandidate, AssetGroup } from '../lib/types'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { toast, useStore } from '../lib/store'
import { PromptAssistant } from './PromptAssistant'
import { ModelStatusPanel } from './ModelStatusPanel'
import { SearchSelect } from './SearchSelect'
import './AssetStudio.css'

type AssetKind = 'character' | 'scene' | 'prop' | 'emotion'
type GenMode = 't2i' | 'i2i' | 'variants'

const KINDS: Array<{ key: AssetKind; labelKey: string }> = [
  { key: 'character', labelKey: 't2i.kind.character' },
  { key: 'scene',     labelKey: 't2i.kind.scene' },
  { key: 'prop',      labelKey: 't2i.kind.prop' },
  { key: 'emotion',   labelKey: 't2i.kind.emotion' },
]

const MODES: Array<{ key: GenMode; labelKey: string }> = [
  { key: 't2i',      labelKey: 't2i.mode.t2i' },
  { key: 'i2i',      labelKey: 't2i.mode.i2i' },
  { key: 'variants', labelKey: 't2i.mode.variants' },
]

const EMPTY_SEGS: PromptSegments = {
  style: '', subject: '', state: '', material: '', light: '', lens: '', motion: '',
}

const DEFAULT_STYLE_ID = '3d_pbr'

export function AssetStudio() {
  const [kind, setKind] = useState<AssetKind>('character')
  const [presetId, setPresetId] = useState<string>('char_portrait')
  const [count, setCount] = useState<1 | 2 | 4>(4)
  const [steps, setSteps] = useState(6)
  const [seedMode, setSeedMode] = useState<'random' | 'fixed'>('random')
  const [fixedSeed, setFixedSeed] = useState<number>(12345)
  const [mode, setMode] = useState<GenMode>('t2i')
  // 风格默认先落到提示词里，避免"左侧显示 3D PBR、实际风格段是空的"
  const [segs, setSegs] = useState<PromptSegments>(() => ({
    ...EMPTY_SEGS,
    style: findStyle(DEFAULT_STYLE_ID)?.en ?? '',
  }))
  const [styleFamily, setStyleFamily] = useState<StyleFamily>('3d')
  const [styleId, setStyleId] = useState<string>(DEFAULT_STYLE_ID)
  // 参考图：本剧角色的主图，按选择顺序对应 <Picture 1..n>
  const [refIds, setRefIds] = useState<string[]>([])

  const { pid, detail, projects } = useStore()
  const characters = detail?.characters ?? []
  const scenes = detail?.scenes ?? []
  // 参考图优先只列本剧出过场的角色；没有 cast 信息时退回整个角色库
  const castChars = characters.filter((c) => (c.used_in?.length ?? 0) > 0)
  const refPool = castChars.length ? castChars : characters

  const [candidates, setCandidates] = useState<AssetCandidate[]>([])
  const [assetGroups, setAssetGroups] = useState<AssetGroup[]>([])
  const [loadError, setLoadError] = useState('')
  const [busyId, setBusyId] = useState('')
  const [adoptChar, setAdoptChar] = useState<Record<string, string>>({})
  const [adoptScene, setAdoptScene] = useState<Record<string, string>>({})
  // 画廊筛选
  const [filterProject, setFilterProject] = useState('')
  const [filterGroup, setFilterGroup] = useState('')
  const [filterKind, setFilterKind] = useState('')

  // 生成任务：模型就绪 / 队列 / 正在跑的那个 job
  const [modelsReady, setModelsReady] = useState(false)
  const [queueCount, setQueueCount] = useState(0)
  const [genJob, setGenJob] = useState<{ id: string; total: number } | null>(null)
  const [genInfo, setGenInfo] = useState<{ pct: number | null; detail: string } | null>(null)

  async function reloadCandidates() {
    try {
      const r = await api.assetCandidates()
      setCandidates(r.candidates)
      setLoadError('')
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('t2i.read-failed'))
    }
  }

  async function reloadGroups() {
    try {
      const r = await api.assetGroups()
      setAssetGroups(r.groups)
    } catch {
      /* 分组读失败不影响候选图 */
    }
  }

  useEffect(() => {
    void reloadCandidates()
    void reloadGroups()

    // 模型状态 / 队列每 10 秒刷新一次（下载中也能看到变化）
    let alive = true
    let timer = 0
    const tick = async () => {
      try {
        const [ms, q] = await Promise.all([api.modelsStatus(), api.globalQueue()])
        if (!alive) return
        setModelsReady(ms.ready)
        setQueueCount(q.count)
      } catch {
        /* 状态读失败不影响页面 */
      }
      if (alive) timer = window.setTimeout(tick, 10000)
    }
    void tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [])

  // 轮询生成任务：3 秒一次，完成/失败后自动刷新候选图
  useEffect(() => {
    if (!genJob) return
    let alive = true
    let timer = 0
    const jobId = genJob.id
    const friendly = (detail: string): string => {
      if (!detail) return t('t2i.gen.running')
      if (/Model Initializ/i.test(detail)) return t('t2i.gen.loading-model')
      if (/%\||it\/s/.test(detail)) return t('t2i.gen.sampling')
      return detail.length > 60 ? detail.slice(0, 60) + '…' : detail
    }
    const tick = async () => {
      try {
        const r = await api.jobProgress(jobId)
        if (!alive) return
        const p = r.progress
        setGenInfo({
          pct: p?.pct ?? null,
          detail: friendly(p?.detail || (r.job.state === 'queued'
            ? t('t2i.gen.queued') : t('t2i.gen.running'))),
        })
        if (r.job.state === 'done') {
          setGenJob(null)
          setGenInfo(null)
          await reloadCandidates()
          toast(tpl('t2i.gen.done-n', { n: genJob.total }), 'ok')
          return
        }
        if (r.job.state === 'failed' || r.job.state === 'cancelled') {
          setGenJob(null)
          setGenInfo(null)
          await reloadCandidates()
          toast(r.job.error || t('t2i.gen.failed'), 'err')
          return
        }
      } catch {
        /* 网络抖动：下一轮再试 */
      }
      if (alive) timer = window.setTimeout(tick, 3000)
    }
    void tick()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [genJob?.id])


/** 参考图 → 提示词里的 <Picture n> 描述。
 *
 * 为什么要这段：MiniMaxH3ImageConditioning 的 images 只是"喂给文本编码器"，
 * 提示词里不出现 <Picture 1> 时模型基本无视它们（实测：只写风格词 → 全身像变成陌生动漫少女）。
 */
function buildSubjectClause(refIds: string[], pool: { character_id: string; name: string; description?: string }[]) {
  if (!refIds.length) return ''
  const lines = refIds.map((cid, i) => {
    const c = pool.find((x) => x.character_id === cid)
    if (!c) return ''
    const desc = (c.description || '').replace(/\s+/g, ' ').trim()
    const short = desc.length > 220 ? desc.slice(0, 220).replace(/[,，]\s*\S*$/, '') : desc
    return `<Picture ${i + 1}> is ${c.name}${short ? ': ' + short : ''}`
  }).filter(Boolean)
  return lines.length ? lines.join('. ') + '.' : ''
}

/** 画风兜底：用户没写 2D/赛璐璐时补上，避免默认跑成 3D 或写实 */
const STYLE_GUARD =
  '2D anime, cel-shaded, clean line art, flat color, hand-painted background, 1996 rural Shandong summer.'

  async function generate() {
    const styleWords = compilePrompt(segs).compiled
    const subject = buildSubjectClause(refIds, refPool)
    const hasStyle = /cel[- ]?shad|2D anime|line art/i.test(styleWords)
    const compiled = [subject, styleWords, hasStyle ? '' : STYLE_GUARD]
      .filter(Boolean).join(' ').trim()
    if (!compiled) {
      toast(t('t2i.need-prompt'), 'err')
      return
    }
    if (!preset) return
    setGenInfo({ pct: null, detail: t('t2i.gen.submitting') })
    try {
      const r = await api.generateAssets({
        kind,
        prompt: compiled,
        width: preset.width,
        height: preset.height,
        steps,
        seed: seedMode === 'fixed' ? fixedSeed : -1,
        count,
        project_id: pid || '',
        group_id: filterGroup && filterGroup !== '__none' ? filterGroup : '',
        refs: refIds.length ? refIds : undefined,
      })
      setGenJob({ id: r.job_id, total: r.count })
      toast(tpl('t2i.gen.queued-n', { n: r.queue_position }), 'ok')
    } catch (e) {
      setGenInfo(null)
      toast(e instanceof Error ? e.message : t('t2i.gen.failed'), 'err')
    }
  }

  async function cancelGen() {
    if (!genJob) return
    try {
      await api.cancelJob(genJob.id)
    } catch {
      /* 取消失败：轮询会看到最终状态 */
    }
  }

  function toggleRef(cid: string) {
    setRefIds((prev) => {
      if (prev.includes(cid)) return prev.filter((x) => x !== cid)
      if (prev.length >= 4) {
        toast(t('t2i.refs.max'), 'err')
        return prev
      }
      return [...prev, cid]
    })
  }

  // 切项目后旧角色的参考图不再适用
  useEffect(() => { setRefIds([]) }, [pid])

  /** 改候选图的归属：项目 / 分组 / 类型（空串 = 不归属 / 未分组） */
  async function setOwner(
    c: AssetCandidate,
    patch: { project_id?: string; group_id?: string; kind?: AssetKind },
  ) {
    setBusyId(c.id)
    try {
      const next = await api.patchAssetCandidate(c.id, patch)
      setCandidates((prev) => prev.map((x) => (x.id === next.id ? next : x)))
    } catch (e) {
      toast(e instanceof Error ? e.message : t('t2i.owner.saved'), 'err')
    } finally {
      setBusyId('')
    }
  }

  async function addGroup() {
    const name = window.prompt(t('t2i.new-group.prompt'))?.trim()
    if (!name) return
    try {
      await api.createAssetGroup(name)
      await reloadGroups()
      toast(tpl('t2i.group.created', { name }), 'ok')
    } catch (e) {
      toast(e instanceof Error ? e.message : t('t2i.group.failed'), 'err')
    }
  }

  async function adopt(c: AssetCandidate, target: 'character' | 'scene') {
    if (!pid) return
    setBusyId(c.id)
    try {
      if (target === 'character') {
        const cid = adoptChar[c.id]
        if (!cid) return
        await api.adoptAssetCandidate(c.id, { target, character_id: cid })
      } else {
        const sid = adoptScene[c.id]
        if (!sid) return
        await api.adoptAssetCandidate(c.id, {
          target, project_id: pid, scene_id: sid, role: 'main',
        })
      }
      toast(target === 'character' ? t('t2i.adopt.char') : t('t2i.adopt.scene'), 'ok')
      await reloadCandidates()
    } catch (e) {
      toast(e instanceof Error ? e.message : t('t2i.adopt.failed'), 'err')
    } finally {
      setBusyId('')
    }
  }

  async function removeCandidate(id: string) {
    setBusyId(id)
    try {
      await api.deleteAssetCandidate(id)
      await reloadCandidates()
    } catch (e) {
      toast(e instanceof Error ? e.message : t('t2i.delete.failed'), 'err')
    } finally {
      setBusyId('')
    }
  }

  const preset = findPreset(presetId)
  const warnings = presetWarnings(preset)
  const stylePreset = findStyle(styleId)

  function applyStyle(id: string) {
    const s = findStyle(id)
    if (!s) return
    setStyleId(id)
    setStyleFamily(s.family)
    // 风格段整体替换（风格是一套口径，不该和旧风格叠加）
    setSegs((prev) => ({ ...prev, style: s.en }))
  }

  const generating = !!genJob
  const genDisabled = !modelsReady || generating
  const genHint = !modelsReady ? t('t2i.locked') : ''

  const matchProject = (c: AssetCandidate) =>
    !filterProject || (filterProject === '__none' ? !c.project_id : c.project_id === filterProject)
  const matchGroup = (c: AssetCandidate) =>
    !filterGroup || (filterGroup === '__none' ? !c.group_id : c.group_id === filterGroup)
  const shown = candidates.filter((c) =>
    matchProject(c) && matchGroup(c) && (!filterKind || c.kind === filterKind))

  return (
    <div className="asset-studio">
      <header className="studio-head">
        <div className="studio-title">
          <strong>{t('nav.t2i')}</strong>
          <span className="muted">{t('t2i.subtitle')}</span>
        </div>
        <div className="studio-mode-tabs" role="tablist">
          {MODES.map((m) => (
            <button
              key={m.key}
              role="tab"
              aria-selected={mode === m.key}
              className={mode === m.key ? 'is-active' : ''}
              onClick={() => setMode(m.key)}
              disabled={m.key !== 't2i'}
              title={m.key !== 't2i' ? t('t2i.todo.tip') : undefined}
            >
              {t(m.labelKey)}{m.key !== 't2i' ? ` · ${t('t2i.todo')}` : ''}
            </button>
          ))}
        </div>
        <div className="studio-head-right">
          <ModelStatusPanel />
        </div>
      </header>

      <div className="studio-body">
        <aside className="studio-left">
          <div className="sl-block">
            <div className="sl-block-title">{t('t2i.type')}</div>
            <div className="sl-kind-row">
              {KINDS.map((k) => (
                <button
                  key={k.key}
                  className={`sl-kind${kind === k.key ? ' is-active' : ''}`}
                  onClick={() => setKind(k.key)}
                >{t(k.labelKey)}</button>
              ))}
            </div>
          </div>

          <div className="sl-block">
            <div className="sl-block-title">
              {t('t2i.refs')}
              {refIds.length > 0 && <span className="sl-ref-count"> · {refIds.length}/4</span>}
            </div>
            <div className="sl-refs">
              {refPool.map((c) => {
                const idx = refIds.indexOf(c.character_id) + 1
                const bust = (c.primary_ref || '').split('/').pop() || '0'
                const thumb = c.primary_ref
                  ? `/assets/characters/${encodeURIComponent(c.character_id)}?v=${encodeURIComponent(bust)}`
                  : ''
                return (
                  <button
                    key={c.character_id}
                    className={`sl-ref${idx ? ' is-active' : ''}`}
                    onClick={() => toggleRef(c.character_id)}
                    title={c.name}
                  >
                    {thumb
                      ? <img src={thumb} alt={c.name} loading="lazy" />
                      : <span className="sl-ref-ph">{c.name.slice(0, 1)}</span>}
                    {idx > 0 && <b className="sl-ref-badge">{idx}</b>}
                    <span className="sl-ref-name">{c.name}</span>
                  </button>
                )
              })}
              {!refPool.length && <div className="muted">{t('t2i.refs.empty')}</div>}
            </div>
            <div className="muted sl-ref-hint">{t('t2i.refs.hint')}</div>
          </div>

          <div className="sl-block">
            <div className="sl-block-title">{t('t2i.style')}</div>
            <div className="sl-kind-row">
              {STYLE_FAMILIES.map((f) => (
                <button
                  key={f.key}
                  className={`sl-kind${styleFamily === f.key ? ' is-active' : ''}`}
                  onClick={() => setStyleFamily(f.key)}
                  title={t(f.hint)}
                >{t(f.label)}</button>
              ))}
            </div>
            <div className="sl-style-list">
              {stylesOf(styleFamily).map((s) => (
                <button
                  key={s.id}
                  className={`sl-style${styleId === s.id ? ' is-active' : ''}`}
                  onClick={() => applyStyle(s.id)}
                  title={s.en}
                >
                  <span>{t(s.label)}</span>
                </button>
              ))}
            </div>
            {stylePreset && (
              <div className="muted sl-preset-info">
                {t(stylePreset.desc)}
                <div className="sl-style-en">{stylePreset.en}</div>
              </div>
            )}
            <div className="muted sl-preset-info">{t('t2i.style.hint')}</div>
          </div>

          <div className="sl-block">
            <div className="sl-block-title">{t('t2i.resolution')}</div>
            <select value={presetId} onChange={(e) => setPresetId(e.target.value)}>
              {RESOLUTION_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {t(p.label)} · {p.width}×{p.height} · {p.aspect} · {p.mp} MP
                </option>
              ))}
            </select>
            {preset && (
              <div className="muted sl-preset-info">
                {t(preset.useCase)} · {(preset.width * preset.height).toLocaleString()} px
                {megapixels(preset.width, preset.height) < 4 && (
                  <span className="warn-badge"> {t('t2i.low-res')}</span>
                )}
              </div>
            )}
            {warnings.map((w, i) => (
              <div key={i} className="sl-warning">{w}</div>
            ))}
            <div className="sl-warning">{t('t2i.res-range')}</div>
          </div>

          <div className="sl-block">
            <div className="sl-block-title">{t('t2i.count')}</div>
            <div className="sl-chip-row">
              {[1, 2, 4].map((n) => (
                <button
                  key={n}
                  className={`sl-chip${count === n ? ' is-active' : ''}`}
                  onClick={() => setCount(n as 1 | 2 | 4)}
                >{n}</button>
              ))}
            </div>
          </div>

          <div className="sl-block">
            <div className="sl-block-title">{t('t2i.steps')} <span className="muted">{steps}</span></div>
            <input type="range" min={4} max={12} step={1} value={steps}
              onChange={(e) => setSteps(Number(e.target.value))} />
          </div>

          <div className="sl-block">
            <div className="sl-block-title">{t('t2i.seed')}</div>
            <div className="sl-seed-row">
              <button className={`sl-chip${seedMode === 'random' ? ' is-active' : ''}`}
                onClick={() => setSeedMode('random')}>{t('t2i.seed.random')}</button>
              <button className={`sl-chip${seedMode === 'fixed' ? ' is-active' : ''}`}
                onClick={() => setSeedMode('fixed')}>{t('t2i.seed.fixed')}</button>
              {seedMode === 'fixed' && (
                <input type="number" value={fixedSeed} onChange={(e) => setFixedSeed(Number(e.target.value))} />
              )}
            </div>
          </div>

          <button
            className="sl-generate"
            disabled={genDisabled}
            title={genHint}
            onClick={() => void generate()}
          >
            {generating ? t('t2i.generating') : !modelsReady ? '🔒 ' + genHint : t('t2i.generate')}
          </button>

          {generating && (
            <div className="sl-gen-progress">
              <div className="sl-gen-bar">
                <div style={{ width: `${genInfo?.pct ?? 6}%` }} />
              </div>
              <div className="sl-gen-detail muted">
                {genInfo?.detail || t('t2i.gen.running')}
                {genInfo?.pct != null ? ` · ${genInfo.pct}%` : ''}
              </div>
              <button className="ghost danger sl-gen-cancel" onClick={() => void cancelGen()}>
                {t('btn.cancel')}
              </button>
            </div>
          )}

          <div className="sl-queue muted">{tpl('t2i.queue.count', { n: queueCount })}</div>
        </aside>

        <section className="studio-center">
          <div className="sc-header">
            <strong>{t('t2i.candidates')}</strong>
            <span className="muted">
              {candidates.length > 0
                ? tpl('t2i.candidates.count', { n: candidates.length })
                : t('t2i.candidates.hint')}
            </span>
          </div>

          <div className="sc-filters">
            <select value={filterProject} onChange={(e) => setFilterProject(e.target.value)}>
              <option value="">{t('t2i.filter.all-projects')}</option>
              <option value="__none">{t('t2i.filter.no-project')}</option>
              {projects.filter((p) => !p.archived).map((p) => (
                <option key={p.id} value={p.id}>{p.title}</option>
              ))}
            </select>
            <select value={filterGroup} onChange={(e) => setFilterGroup(e.target.value)}>
              <option value="">{t('t2i.filter.all-groups')}</option>
              <option value="__none">{t('t2i.filter.no-group')}</option>
              {assetGroups.map((g) => (
                <option key={g.id} value={g.id}>{g.name}{g.count ? `（${g.count}）` : ''}</option>
              ))}
            </select>
            <select value={filterKind} onChange={(e) => setFilterKind(e.target.value)}>
              <option value="">{t('t2i.filter.all-kinds')}</option>
              {KINDS.map((k) => <option key={k.key} value={k.key}>{t(k.labelKey)}</option>)}
            </select>
            <button className="ghost" onClick={() => void addGroup()}>{t('t2i.new-group')}</button>
            <span className="muted sc-filter-count">{shown.length} / {candidates.length}</span>
          </div>

          {candidates.length > 0 ? (
            shown.length > 0 ? (
              <div className="sc-grid">
                {shown.map((c) => (
                  <div key={c.id} className="sc-candidate">
                    <div className="sc-thumb">
                      {c.url
                        ? <img src={c.url} alt={`${t('t2i.candidates')} ${c.id}`} loading="lazy" />
                        : <span className="muted">{t('t2i.lost')}</span>}
                      {c.adopted_to && <span className="sc-adopted">{t('t2i.adopted')}</span>}
                    </div>
                    <div className="sc-meta muted">
                      seed {c.seed} · {c.elapsed_s ? `${c.elapsed_s.toFixed(0)}s` : '—'} ·
                      {c.width}×{c.height}
                    </div>
                    <div className="sc-owner">
                      <select
                        title={t('t2i.owner.project')}
                        value={c.project_id}
                        disabled={busyId === c.id}
                        onChange={(e) => void setOwner(c, { project_id: e.target.value })}
                      >
                        <option value="">{t('t2i.no-project')}</option>
                        {c.project_id && !projects.some((p) => p.id === c.project_id) && (
                          <option value={c.project_id}>{tpl('t2i.not-in-list', { id: c.project_id })}</option>
                        )}
                        {projects.filter((p) => !p.archived).map((p) => (
                          <option key={p.id} value={p.id}>{p.title}</option>
                        ))}
                      </select>
                      <select
                        title={t('t2i.owner.group')}
                        value={c.group_id}
                        disabled={busyId === c.id}
                        onChange={(e) => void setOwner(c, { group_id: e.target.value })}
                      >
                        <option value="">{t('t2i.filter.no-group')}</option>
                        {assetGroups.map((g) => (
                          <option key={g.id} value={g.id}>{g.name}</option>
                        ))}
                      </select>
                      <select
                        title={t('t2i.owner.kind')}
                        value={c.kind}
                        disabled={busyId === c.id}
                        onChange={(e) => void setOwner(c, { kind: e.target.value as AssetKind })}
                      >
                        {KINDS.map((k) => (
                          <option key={k.key} value={k.key}>{t(k.labelKey)}</option>
                        ))}
                      </select>
                    </div>
                    <div className="sc-actions">
                      <SearchSelect
                        placeholder={adoptChar[c.id] || t('t2i.pick-char')}
                        options={characters.map((ch) => ({
                          value: ch.character_id, label: ch.name, hint: ch.character_id,
                        }))}
                        onPick={(cid) => setAdoptChar((p) => ({ ...p, [c.id]: cid }))}
                      />
                      <button
                        disabled={!adoptChar[c.id] || busyId === c.id || !c.url}
                        onClick={() => void adopt(c, 'character')}
                      >{t('t2i.set-gallery')}</button>
                    </div>
                    <div className="sc-actions">
                      <SearchSelect
                        placeholder={adoptScene[c.id] || t('t2i.pick-scene')}
                        options={scenes.map((s) => ({
                          value: s.scene_id, label: s.name || s.scene_id, hint: s.scene_id,
                        }))}
                        onPick={(sid) => setAdoptScene((p) => ({ ...p, [c.id]: sid }))}
                      />
                      <button
                        disabled={!adoptScene[c.id] || busyId === c.id || !c.url}
                        onClick={() => void adopt(c, 'scene')}
                      >{t('t2i.set-plate')}</button>
                      <button
                        className="ghost danger"
                        disabled={busyId === c.id}
                        onClick={() => void removeCandidate(c.id)}
                      >{t('btn.delete')}</button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="sc-empty muted">{t('t2i.empty')}</div>
            )
          ) : (
            <div className="sc-grid">
              {[...Array(count)].map((_, i) => (
                <div key={i} className="sc-placeholder">
                  <div className="sc-ph">
                    {modelsReady ? t('t2i.ph.ready') : t('t2i.ph.locked')}
                  </div>
                  <div className="sc-ph-foot muted">{tpl('t2i.ph.candidate', { n: i + 1 })}</div>
                </div>
              ))}
            </div>
          )}

          {loadError && <div className="sc-error">{loadError}</div>}
          <div className="sc-tip muted">
            {candidates.length === 0 && !modelsReady ? t('t2i.tip.none') : t('t2i.tip.how')}
          </div>
        </section>

        <aside className="studio-right">
          <PromptAssistant
            value={segs}
            onChange={setSegs}
            kind={kind}
            refNames={refIds.map((cid) => refPool.find((c) => c.character_id === cid)?.name || cid)}
          />
        </aside>
      </div>
    </div>
  )
}
