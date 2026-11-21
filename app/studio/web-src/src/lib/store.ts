/**
 * 全局 store —— 30 行 useSyncExternalStore，不引入状态库。
 *
 * 关键约定（UI-SPEC 第五节）：
 *  - 服务端状态（projects/detail/validation/stats/queue）是唯一真相，靠 15 秒轮询刷新
 *  - 客户端状态（currentShotId/checked）只由本地交互改，轮询绝不重置
 *  - toast 独立队列，3 秒自动移除，不参与页面重渲染
 */

import { useSyncExternalStore } from 'react'
import { api } from './api'
import { t, tpl } from './i18n'
import { confirmDialog } from './confirm'
import type {
  ProjectGroup,
  ConfirmResult, GlobalQueue, HealthInfo, PlanResult, ProjectDetail, ProjectStats,
  ProjectSummary, PromptMode, ReviewVerdict, Shot, Transition, UnetChoice,
  ValidationReport,
} from './types'

export type ToastKind = 'ok' | 'err' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  text: string
}

export interface RunOptions {
  /** 草稿 832×480 / 12 步，约 5 分钟一镜 */
  draft: boolean
  promptMode: PromptMode
  unet: UnetChoice
  /** 跑完一镜暂停 */
  pauseAfterOne: boolean
  /** 已通过的镜头也重渲（A/B 对比） */
  force: boolean
}

export interface StoreState {
  // 服务端状态
  projects: ProjectSummary[]
  groups: ProjectGroup[]
  pid: string | null
  detail: ProjectDetail | null
  validation: ValidationReport | null
  stats: ProjectStats | null
  queue: GlobalQueue | null
  health: HealthInfo | null

  // 客户端状态
  currentShotId: string | null
  checked: Set<string>
  /** 分镜列表模式：查看 / 审核 / 成片 / 批量设置 */
  listMode: 'view' | 'review' | 'compose' | 'settings'
  plan: PlanResult | null
  opts: RunOptions
  /** 最近一次合成的成片（只在本次会话里记着，刷新后要重新合成） */
  lastOutput: { url: string; shots: number; subtitles: boolean } | null

  toasts: Toast[]
  loading: boolean
  error: string | null
}

let state: StoreState = {
  projects: [],
  groups: [],
  pid: null,
  detail: null,
  validation: null,
  stats: null,
  queue: null,
  health: null,
  currentShotId: null,
  checked: new Set(),
  listMode: 'view',
  plan: null,
  opts: { draft: false, promptMode: 'compile', unet: '', pauseAfterOne: false, force: false },
  lastOutput: null,
  toasts: [],
  loading: false,
  error: null,
}

const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((l) => l())
}

function set(patch: Partial<StoreState>) {
  state = { ...state, ...patch }
  emit()
}

export const getState = () => state

export function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useStore(): StoreState {
  return useSyncExternalStore(subscribe, getState, getState)
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// ------------------------------------------------------------------ toast

let toastSeq = 0

export function toast(text: string, kind: ToastKind = 'info') {
  const id = ++toastSeq
  set({ toasts: [...state.toasts, { id, kind, text }] })
  window.setTimeout(() => dismissToast(id), 3600)
}

export function dismissToast(id: number) {
  set({ toasts: state.toasts.filter((t) => t.id !== id) })
}

// ------------------------------------------------------------------ 服务端

/** 重新拉项目列表与分组（导入/重命名/归档后必须调，否则下拉还是旧的） */
export async function refreshProjects(includeArchived = true) {
  try {
    const [projects, groups] = await Promise.all([
      api.listProjects(includeArchived),
      api.listGroups(),
    ])
    set({ projects: projects.projects, groups: groups.groups })
  } catch (e) {
    toast(tpl('store.refresh-projects-failed', { err: errText(e) }), 'err')
  }
}

/** 首屏：拿项目列表 + 健康状态，然后打开 URL 指定或第一个项目 */
export async function boot() {
  try {
    const [projects, health] = await Promise.all([api.listProjects(), api.health()])
    set({ projects: projects.projects, health })
    void api.listGroups().then((g) => set({ groups: g.groups })).catch(() => undefined)
    const want = new URLSearchParams(location.search).get('project')
    const pid = want && projects.projects.some((p) => p.id === want)
      ? want
      : projects.projects[0]?.id ?? null
    if (pid) await openProject(pid)
  } catch (e) {
    set({ error: errText(e) })
    toast(tpl('store.load-failed', { err: errText(e) }), 'err')
  }
}

/** 切换项目：只有真的换了项目才清空勾选和当前镜头 */
export async function openProject(pid: string) {
  if (state.pid !== pid) {
    set({ pid, currentShotId: null, checked: new Set(), plan: null })
  }
  // 列表也刷一遍：新导入的项目才能出现在下拉里
  await Promise.all([refresh(true), refreshProjects()])
}

/**
 * 刷新服务端状态。initial=true 时显示整页加载态。
 * 轮询（poll）只覆盖数据，不动 currentShotId / checked / 滚动位置。
 */
export async function refresh(initial = false) {
  const pid = state.pid
  if (!pid) return
  if (initial) set({ loading: true })
  try {
    const [detail, validation, stats, queue] = await Promise.all([
      api.getProject(pid),
      api.validate(pid),
      api.stats(pid),
      api.globalQueue(),
    ])
    if (state.pid !== pid) return // 期间切了项目，丢弃这批结果
    set({ detail, validation, stats, queue, loading: false, error: null })
    // 当前镜头若已被删除，清掉选中态（其余情况一律保留）
    if (state.currentShotId && !detail.shots.some((s) => s.shot_id === state.currentShotId)) {
      set({ currentShotId: null })
    }
  } catch (e) {
    set({ loading: false, error: errText(e) })
  }
}

export const poll = () => refresh(false)

/** 显式操作后刷新（不改客户端状态） */
export async function reload() {
  await refresh(false)
}

/** 停止全部排队/渲染任务（含正在跑的那一镜） */
export async function stopRendering() {
  const count = state.queue?.count ?? 0
  if (count === 0) return
  const ok = await confirmDialog({
    title: t('status.stop'),
    message: tpl('store.stop.confirm', { n: count }),
    confirmText: t('status.stop'),
    danger: true,
  })
  if (!ok) return
  try {
    const r = await api.stopAll()
    toast(tpl('store.stop.done', {
      n: r.cancelled,
      killed: r.killed.length ? t('store.stop.killed') : '',
    }), 'ok')
    await poll()
  } catch (e) {
    toast(tpl('store.stop.failed', { err: errText(e) }), 'err')
  }
}

// ------------------------------------------------------------------ 客户端

export function selectShot(sid: string | null) {
  set({ currentShotId: sid })
}

export function toggleChecked(sid: string) {
  const next = new Set(state.checked)
  if (next.has(sid)) next.delete(sid)
  else next.add(sid)
  set({ checked: next })
}

export function setCheckedAll(all: boolean) {
  const shots = state.detail?.shots ?? []
  set({ checked: all ? new Set(shots.map((s) => s.shot_id)) : new Set() })
}

export function setCheckedList(ids: string[]) {
  set({ checked: new Set(ids) })
}

export function setListMode(mode: 'view' | 'review' | 'compose' | 'settings') {
  // 切换模式时清空选择，避免跨模式误操作
  set({ listMode: mode, checked: new Set() })
}

export async function switchProject(pid: string) {
  await openProject(pid)
}

export function setOpts(patch: Partial<RunOptions>) {
  set({ opts: { ...state.opts, ...patch } })
}

export function setPlan(plan: PlanResult | null) {
  set({ plan })
}

// ------------------------------------------------------------------ 镜头编辑

/** 只提交后端 save_shots 认的字段（与旧界面一致） */
function shotPayload(shot: Shot) {
  return {
    shot_id: shot.shot_id,
    scene_id: shot.scene_id,
    cast: shot.cast,
    camera: shot.camera,
    prompt: shot.prompt,
    dialogue: shot.dialogue,
    sound: shot.sound,
    transition: shot.transition,
    anchor: shot.anchor,
    duration: shot.duration,
  }
}

function shotsOrThrow(): Shot[] {
  const shots = state.detail?.shots
  if (!shots) throw new Error(t('store.project-not-ready'))
  return shots
}

/** 保存单镜的文本字段（机位/台词/提示词/锚定/转场/时长） */
export async function saveShotFields(sid: string, patch: Partial<Shot>) {
  const pid = state.pid
  if (!pid) return
  const shots = shotsOrThrow().map((s) => (s.shot_id === sid ? { ...s, ...patch } : s))
  try {
    await api.saveShots(pid, shots.map(shotPayload))
    await reload()
    toast(t('store.saved'), 'ok')
  } catch (e) {
    toast(tpl('store.save-failed', { err: errText(e) }), 'err')
  }
}

/** 拖拽/点击指派：角色增减、场景、转场 */
export async function assignShot(
  sid: string,
  body: { cast_add?: string[]; cast_remove?: string[]; scene_id?: string; transition?: Transition },
) {
  const pid = state.pid
  if (!pid) return
  try {
    await api.assignShot(pid, sid, body)
    await reload()
    const msg = body.scene_id
      ? tpl('store.assign.scene', { name: body.scene_id, sid })
      : body.cast_add?.length || body.cast_remove?.length
        ? tpl('store.assign.cast', { sid })
        : tpl('store.assign.shot', { sid })
    toast(msg, 'ok')
  } catch (e) {
    toast(tpl('store.assign-failed', { err: errText(e) }), 'err')
  }
}

/** 左栏点角色 = 有则移除、无则加入（每镜最多 3 个） */
export async function toggleCast(sid: string, cid: string) {
  const shot = state.detail?.shots.find((s) => s.shot_id === sid)
  if (!shot) return
  if (shot.cast.includes(cid)) await assignShot(sid, { cast_remove: [cid] })
  else await assignShot(sid, { cast_add: [cid] })
}

export async function toggleChainRef(sid: string, enabled: boolean) {
  const pid = state.pid
  if (!pid) return
  try {
    await api.setChainRef(pid, sid, enabled)
    await reload()
    toast(enabled ? t('store.chain-ref.on') : t('store.chain-ref.off'), 'ok')
  } catch (e) {
    toast(tpl('store.set-failed', { err: errText(e) }), 'err')
  }
}

/** 批量修改：场景/转场走 batch 接口，时长走 save_shots（后端 batch 不支持时长） */
export async function batchEdit(
  ids: string[],
  edit: { scene_id?: string; transition?: Transition | ''; duration?: number },
) {
  const pid = state.pid
  if (!pid || ids.length === 0) return
  try {
    const { duration, transition, scene_id } = edit
    if (scene_id || transition) {
      await api.batchShots(pid, {
        shot_ids: ids,
        ...(scene_id ? { scene_id } : {}),
        ...(transition ? { transition } : {}),
      })
    }
    if (typeof duration === 'number' && Number.isFinite(duration) && duration > 0) {
      const all = shotsOrThrow().map((s) => (ids.includes(s.shot_id) ? { ...s, duration } : s))
      await api.saveShots(pid, all.map(shotPayload))
    }
    await reload()
    toast(tpl('store.batch.done', { n: ids.length }), 'ok')
  } catch (e) {
    toast(tpl('store.batch-failed', { err: errText(e) }), 'err')
  }
}

/** 第 2 镜起 chain_ref；传 ids 只作用于选中集，否则全部（第 1 镜除外）。 */
export async function chainRefAll(enabled = true, ids?: string[]) {
  const pid = state.pid
  if (!pid || !state.detail) return
  const all = ids ?? state.detail.shots.filter(s => s.ordinal > 0).map(s => s.shot_id)
  if (!all.length) {
    toast(t('store.chain.need-two'), 'err')
    return
  }
  try {
    await api.batchShots(pid, { shot_ids: all, chain_ref: enabled } as never)
    await reload()
    toast(enabled
      ? tpl('store.chain-ref-all.on', { n: all.length })
      : tpl('store.chain-ref-all.off', { n: all.length }), 'ok')
  } catch (e) {
    toast(tpl('store.failed', { err: errText(e) }), 'err')
  }
}

export async function chainAll() {
  const pid = state.pid
  if (!pid) return
  try {
    const all = shotsOrThrow().map((s, i) => ({
      ...s,
      transition: (i === 0 ? 'cut' : 'chain') as Transition,
      anchor: i === 0 ? s.anchor : ('' as Shot['anchor']),
    }))
    await api.saveShots(pid, all.map(shotPayload))
    await reload()
    toast(t('store.chain-all.done'), 'ok')
  } catch (e) {
    toast(tpl('store.failed', { err: errText(e) }), 'err')
  }
}

/** 硬切：传 ids 作用于选中集，否则全部镜头 transition=cut。 */
export async function cutAll(ids?: string[]) {
  const pid = state.pid
  if (!pid) return
  try {
    const all = ids ?? shotsOrThrow().map((s) => s.shot_id)
    if (!all.length) return
    await api.batchShots(pid, { shot_ids: all, transition: 'cut' } as never)
    await reload()
    toast(tpl('store.batch.done', { n: all.length }), 'ok')
  } catch (e) {
    toast(tpl('store.failed', { err: errText(e) }), 'err')
  }
}

export async function autoTransitions() {
  const pid = state.pid
  if (!pid) return
  try {
    const r = await api.autoTransitions(pid)
    await reload()
    toast(tpl('store.auto-transition.done', { n: r.changed }), 'ok')
  } catch (e) {
    toast(tpl('store.auto-transition.failed', { err: errText(e) }), 'err')
  }
}

/** 按角色智能转场（选中集）：与上一镜角色相同则 chain，否则 cut；首镜 cut。 */
export async function autoTransitionsSelected(ids: string[]) {
  const pid = state.pid
  if (!pid || ids.length === 0) return
  const all = shotsOrThrow()
  const sel = new Set(ids)
  const castKey = (c: string[] | undefined) => [...(c ?? [])].sort().join('|')
  let changed = 0
  const patched = all.map((sh, i) => {
    if (!sel.has(sh.shot_id)) return sh
    const want: Transition = i === 0
      ? 'cut'
      : (castKey(all[i - 1].cast) === castKey(sh.cast) ? 'chain' : 'cut')
    if (sh.transition === want) return sh
    changed += 1
    return { ...sh, transition: want }
  })
  try {
    await api.saveShots(pid, patched.map(shotPayload))
    await reload()
    toast(tpl('store.auto-transition.done', { n: changed }), 'ok')
  } catch (e) {
    toast(tpl('store.auto-transition.failed', { err: errText(e) }), 'err')
  }
}

export async function reviewShot(sid: string, verdict: ReviewVerdict, reason = '') {
  const pid = state.pid
  if (!pid) return
  try {
    await api.review(pid, { shot_id: sid, verdict, reason })
    await reload()
    toast(verdict === 'pass' ? t('store.review.pass') : t('store.review.reject'), 'ok')
  } catch (e) {
    toast(tpl('store.review.failed', { err: errText(e) }), 'err')
  }
}

/** 批量审核：通过 / 打回选中的镜头（没有成片的会被跳过） */
export async function batchReviewShots(verdict: ReviewVerdict, reason = '') {
  const pid = state.pid
  const ids = [...state.checked]
  if (!pid || ids.length === 0) return
  if (verdict === 'reject') {
    const ok = await confirmDialog({
      title: t('bulk.reject'),
      message: tpl('store.batch-reject.confirm', { n: ids.length }),
      confirmText: t('insp.reject'), danger: true,
    })
    if (!ok) return
  }
  try {
    const r = await api.batchReview(pid, ids, verdict, reason)
    set({ checked: new Set() })
    await reload()
    const skip = r.skipped.length ? tpl('store.batch-review.skip', { n: r.skipped.length }) : ''
    toast(tpl(verdict === 'pass' ? 'store.batch-review.passed' : 'store.batch-review.rejected', {
      n: r.count, skip,
    }), 'ok')
  } catch (e) {
    toast(tpl('store.batch-review.failed', { err: errText(e) }), 'err')
  }
}

export async function deleteShot(sid: string) {
  const pid = state.pid
  if (!pid) return
  try {
    await api.deleteShot(pid, sid)
    if (state.currentShotId === sid) set({ currentShotId: null })
    await reload()
    toast(tpl('store.shot.deleted', { sid }), 'ok')
  } catch (e) {
    toast(tpl('store.delete.failed', { err: errText(e) }), 'err')
  }
}

export async function adoptTake(sid: string) {
  const pid = state.pid
  if (!pid) return
  try {
    await api.adoptTake(pid, sid)
    await reload()
    toast(tpl('store.adopt.done', { sid }), 'ok')
  } catch (e) {
    toast(tpl('store.failed', { err: errText(e) }), 'err')
  }
}

export async function deleteTake(sid: string, version: number) {
  const pid = state.pid
  if (!pid) return
  try {
    const r = await api.deleteTakes(pid, sid, version)
    await reload()
    toast(tpl('store.take.deleted', { v: r.deleted_version, n: r.remaining.length }), 'ok')
  } catch (e) {
    toast(tpl('store.delete.failed', { err: errText(e) }), 'err')
  }
}

// ------------------------------------------------------------------ 计划 / 渲染

export interface PlanOptions extends Partial<RunOptions> {
  /** 失败自动重试次数（夜间批渲传 2） */
  retries?: number
}

function planMode(extra?: Partial<RunOptions>) {
  return { ...state.opts, ...extra }
}

/** 只出计划，不改队列；返回 null 表示失败（已 toast） */
export async function makePlan(ids: string[], extra?: Partial<RunOptions>): Promise<PlanResult | null> {
  const pid = state.pid
  if (!pid) return null
  const o = planMode(extra)
  try {
    const plan = await api.plan(pid, {
      shot_ids: ids.length ? ids : null,
      draft: o.draft,
      prompt_mode: o.promptMode,
      force: o.force,
    })
    set({ plan })
    return plan
  } catch (e) {
    toast(tpl('store.plan.failed', { err: errText(e) }), 'err')
    return null
  }
}

export async function confirmPlan(
  plan: PlanResult,
  extra?: Partial<RunOptions> & { retries?: number },
): Promise<ConfirmResult | null> {
  const pid = state.pid
  if (!pid || !plan) return null
  const o = planMode(extra)
  try {
    const r = await api.confirm(pid, {
      plan_id: plan.plan_id,
      draft: o.draft,
      pause_after_one: o.pauseAfterOne,
      prompt_mode: o.promptMode,
      unet: o.unet,
      retries: extra?.retries ?? 0,
    })
    set({ plan: null })
    await reload()
    const skipped = r.skipped?.length ? tpl('store.queue.skipped', { n: r.skipped.length }) : ''
    toast(tpl('store.queued', { n: r.count, skipped }), 'ok')
    return r
  } catch (e) {
    toast(tpl('store.queue.failed', { err: errText(e) }), 'err')
    return null
  }
}

/** 单镜渲染：必须 force=true，否则已通过/待审的镜头会被计划跳过 */
export async function renderOne(sid: string): Promise<boolean> {
  const plan = await makePlan([sid], { force: true })
  if (!plan) return false
  if (plan.blocking.length) {
    toast(tpl('store.cannot-render', { msg: plan.blocking[0].msg }), 'err')
    return false
  }
  if (plan.count === 0) {
    toast(t('store.render-not-needed'), 'info')
    return false
  }
  const r = await confirmPlan(plan)
  return Boolean(r)
}

/** 夜间批渲：全部镜头 + force + 失败重试 2 次；不改用户转场 */
export async function nightRun(): Promise<boolean> {
  const shots = state.detail?.shots ?? []
  if (!shots.length) {
    toast(t('store.no-shots'), 'err')
    return false
  }
  const plan = await makePlan(shots.map((s) => s.shot_id), { force: true })
  if (!plan) return false
  if (plan.blocking.length) {
    toast(tpl('store.cannot-queue', { msg: plan.blocking[0].msg }), 'err')
    return false
  }
  const r = await confirmPlan(plan, { retries: 2 })
  return Boolean(r)
}

export async function assembleNow(opts?: { with_subs?: boolean; allow_stale?: boolean; shot_ids?: string[] }) {
  const pid = state.pid
  if (!pid) return null
  try {
    const r = await api.assemble(pid, opts)
    set({ lastOutput: { url: r.url, shots: r.shots, subtitles: r.subtitles } })
    toast(t('store.assemble.done'), 'ok')
    await reload()
    return r
  } catch (e) {
    toast(tpl('store.assemble.failed', { err: errText(e) }), 'err')
    return null
  }
}

// ------------------------------------------------------------------ 角色 / 场景

export async function chooseTakeVersion(pid: string, sid: string, version: number) {
  try {
    await api.chooseTake(pid, sid, version)
    toast(tpl('store.chosen', { v: version }), 'ok')
    await reload()
    return true
  } catch (e) {
    toast(tpl('store.choose.failed', { err: errText(e) }), 'err')
    return false
  }
}

export async function adoptVersion(pid: string, sid: string, version: number) {
  try {
    await api.adoptVersion(pid, sid, version)
    toast(tpl('store.adopted-version', { v: version }), 'ok')
    await reload()
    return true
  } catch (e) {
    toast(tpl('store.adopt.failed', { err: errText(e) }), 'err')
    return false
  }
}

export async function chooseLatestAll(pid: string) {
  try {
    const r = await api.chooseLatestAll(pid)
    toast(tpl('store.latest-all.done', { n: r.chosen.length }), 'ok')
    await reload()
    return true
  } catch (e) {
    toast(tpl('store.op.failed', { err: errText(e) }), 'err')
    return false
  }
}

export async function passAllShots(pid: string) {
  try {
    const r = await api.passAll(pid)
    toast(tpl('store.pass-all.done', { n: r.passed.length }), 'ok')
    await reload()
    return true
  } catch (e) {
    toast(tpl('store.op.failed', { err: errText(e) }), 'err')
    return false
  }
}

export async function verifyCharacter(cid: string, verified: boolean) {
  try {
    await api.verifyCharacter(cid, verified)
    await reload()
    toast(verified ? t('store.verified') : t('store.unverified'), 'ok')
  } catch (e) {
    toast(tpl('store.op.failed', { err: errText(e) }), 'err')
  }
}

export async function saveOverride(cid: string, description: string) {
  const pid = state.pid
  if (!pid) return
  try {
    if (description.trim()) await api.setCastOverride(pid, cid, { description: description.trim() })
    else await api.clearCastOverride(pid, cid)
    await reload()
    toast(description.trim() ? t('store.override.saved') : t('store.override.cleared'), 'ok')
  } catch (e) {
    toast(tpl('store.save-failed', { err: errText(e) }), 'err')
  }
}

/** 保存角色参考图顺序（第一张 = 主图）；本剧覆盖角色写 project_cast */
export async function saveRefOrder(cid: string, refs: string[]) {
  const pid = state.pid
  const ch = state.detail?.characters.find((c) => c.character_id === cid)
  if (!pid || !ch) return
  try {
    // 专门接口：只改顺序，不碰描述（旧写法会把描述整段回写，容易误清空）
    if (ch.overridden) await api.setCastOverride(pid, cid, { refs })
    else await api.reorderCharacterRefs(cid, refs)
    await reload()
    toast(t('store.ref-order.saved'), 'ok')
  } catch (e) {
    toast(tpl('store.save-failed', { err: errText(e) }), 'err')
  }
}

