/**
 * 类型化 API 客户端 —— 覆盖后端全部 ~70 个路由。
 *
 * 约定：
 *  - 所有请求走同源 `/api/...`（开发时用 Vite proxy 指向 127.0.0.1:8801）
 *  - 非 2xx 时抛出 ApiError，`detail` 就是后端给的中文提示，可直接展示给用户
 *  - 上传走 multipart，其余走 JSON
 *
 * 不要在这里改 URL 或字段名：后端 `studio/api/app.py` 与 182 个测试依赖它们。
 */

import type {
  AssetCandidate, AssetCatalogResponse, AssetGroup, AssetKind, AssignRequest, AuthUser, BatchRequest,
  Character, CompilePreviewResponse, ConfirmRequest, ConfirmResult,
  GlobalQueue, HealthInfo, ImportPreview, ImportResult, Issue, Job, ModelsStatusResponse, PlanRequest,
  PlanResult, Project, ProjectDetail, ProjectGroup, ProjectStats, ProjectSummary, ReviewRequest,
  Progress,
  Scene, Shot, Take, Transition, UnetChoice, ValidationReport,
} from './types'

const BASE = ''  // 同源；Vite dev 用 proxy

/** 登录 token 的 localStorage key（auth.ts 与登录页共用） */
export const TOKEN_KEY = 'ov-token'

export function getToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* 隐私模式忽略 */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'content-type': 'application/json' }),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  })
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    // 登录过期：让 auth store 收到通知并回到登录页
    window.dispatchEvent(new Event('ov-unauthorized'))
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* 非 JSON 响应，保留 statusText */ }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const get = <T>(p: string) => request<T>(p)
const post = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const put = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: 'PUT', body: JSON.stringify(body) })
const patch = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: 'PATCH', body: JSON.stringify(body) })
const del = <T>(p: string) => request<T>(p, { method: 'DELETE' })

/** 单文件上传：字段名 `file`（旧接口都是这个） */
const upload = <T>(p: string, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return request<T>(p, { method: 'POST', body: fd })
}

/**
 * 多文件上传：字段名 `files`（后端用 `list[UploadFile]`）。
 * 角色图集 / 场景多角度都走这个，一次请求传完，不要再逐张打接口。
 */
const uploadMany = <T>(p: string, files: File[]) => {
  if (!files.length) throw new ApiError(400, '没有选择文件')
  const fd = new FormData()
  for (const f of files) fd.append('files', f, f.name)
  return request<T>(p, { method: 'POST', body: fd })
}

/** 去水印：框选擦除，返回擦除后的 PNG Blob（**分辨率不变**）。boxes 为图像像素坐标。 */
export async function inpaintImage(
  file: File,
  boxes: { x: number; y: number; w: number; h: number }[],
): Promise<Blob> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('boxes', JSON.stringify(boxes))
  const token = getToken()
  const res = await fetch(BASE + '/api/images/inpaint', {
    method: 'POST',
    headers: token ? { authorization: `Bearer ${token}` } : {},
    body: fd,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const b = await res.json()
      detail = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
    } catch { /* 非 JSON 错误响应 */ }
    throw new ApiError(res.status, detail)
  }
  return res.blob()
}

const enc = encodeURIComponent
const P = (pid: string) => `/api/projects/${enc(pid)}`

// ------------------------------------------------------------------ 项目

export const api = {
  // ---- 账号
  login: (username: string, password: string, remember = true) =>
    post<{ token: string; expires_at: string; user: AuthUser }>(
      '/api/auth/login', { username, password, remember }),
  logout: () => post<{ ok: boolean }>('/api/auth/logout', {}),
  me: () => get<{ user: AuthUser }>('/api/auth/me'),
  updateMe: (body: {
    display_name?: string; phone?: string; password?: string; old_password?: string
  }) => put<{ user: AuthUser }>('/api/auth/me', body),
  listUsers: () => get<{ users: AuthUser[] }>('/api/users'),
  createUser: (body: {
    username: string; password: string; display_name?: string; phone?: string; role?: string
  }) => post<{ user: AuthUser }>('/api/users', body),
  updateUser: (uid: string, body: {
    display_name?: string; phone?: string; role?: string; active?: boolean; password?: string
  }) => put<{ user: AuthUser }>(`/api/users/${enc(uid)}`, body),
  resetPassword: (uid: string, password: string) =>
    post<{ user: AuthUser }>(`/api/users/${enc(uid)}/reset-password`, { password }),
  deleteUser: (uid: string) => del<{ ok: boolean }>(`/api/users/${enc(uid)}`),

  // ---- 项目
  listProjects: (includeArchived = false) =>
    get<{ projects: ProjectSummary[] }>(
      `/api/projects${includeArchived ? '?include_archived=1' : ''}`),
  listGroups: () => get<{ groups: ProjectGroup[] }>('/api/groups'),
  createGroup: (name: string) => post<ProjectGroup>('/api/groups', { name }),
  renameGroup: (gid: string, name: string) =>
    patch<ProjectGroup>(`/api/groups/${enc(gid)}`, { name }),
  deleteGroup: (gid: string) => del<{ ok: boolean }>(`/api/groups/${enc(gid)}`),
  setProjectGroup: (pid: string, groupId: string) =>
    post<Project>(`${P(pid)}/group`, { group_id: groupId }),
  setProjectArchived: (pid: string, archived: boolean) =>
    post<Project>(`${P(pid)}/archive`, { archived }),
  batchProjects: (ids: string[], action: 'group' | 'archive' | 'unarchive', groupId = '') =>
    post<{ ok: boolean; count: number }>('/api/projects/batch',
      { ids, action, group_id: groupId }),
  reorderProjects: (ids: string[]) =>
    post<{ ok: boolean; count: number }>('/api/projects/order', { ids }),
  createProject: (body: { id: string; title: string; style?: string }) =>
    post<Project>('/api/projects', body),
  getProject: (pid: string) => get<ProjectDetail>(P(pid)),
  renameProject: (pid: string, title: string) => post<Project>(`${P(pid)}/rename`, { title }),
  /** 项目设置：风格串 / 分辨率 / 时长上限 / 词库附加词 */
  projectSettings: (pid: string) => get<Record<string, unknown>>(`${P(pid)}/settings`),
  saveProjectSettings: (pid: string, body: Record<string, unknown>) =>
    put<Record<string, unknown>>(`${P(pid)}/settings`, body),
  deleteProject: (pid: string) => del<{ deleted: void }>(P(pid)),

  // ---- 校验 / 统计 / 队列
  validate: (pid: string) => get<ValidationReport>(`${P(pid)}/validate`),
  stats: (pid: string) => get<ProjectStats>(`${P(pid)}/stats`),
  globalQueue: () => get<GlobalQueue>('/api/queue/global'),
  health: () => get<HealthInfo>('/api/health'),
  listJobs: (pid?: string) =>
    get<{ jobs: Job[] }>(pid ? `/api/jobs?project_id=${enc(pid)}` : '/api/jobs'),
  cancelJob: (jobId: string) => post<{ job: Job }>(`/api/jobs/${enc(jobId)}/cancel`, {}),
  /** 全局停止：杀掉正在渲的任务并取消所有排队任务 */
  useAudioPath: (pid: string, kind: string, path: string) =>
    post<{ ok: boolean; path: string }>(`${P(pid)}/audio/use-path`, { kind, path }),
  systemAudioFiles: (path = '') =>
    get<{ path: string; dirs: Array<{ name: string; path: string }>;
          files: Array<{ name: string; path: string; size: number }> }>
      (`/api/system/audio-files?path=${encodeURIComponent(path)}`),
  uploadAvatar: async (file: File) => {
    const r = await fetch('/api/auth/me/avatar', {
      method: 'POST', body: file,
      headers: { 'content-type': file.type || 'application/octet-stream' },
    })
    if (!r.ok) throw new Error(await r.text())
    return (await r.json()) as { ok: boolean; url: string }
  },
  mvPresets: () => get<{ presets: Array<{ id: string; kind: string; region: string;
    label: string; zh: string; en: string; style: string }> }>('/api/mv/presets'),
  mvPlan: (body: { music_id: string; preset_id: string; seconds?: number }) =>
    post<{ ok: boolean; project_id: string; title: string; shots: number }>('/api/mv/plan', body),
  deleteBed: (pid: string, kind: string) =>
    del<{ ok: boolean; kind: string; removed: string[] }>(`${P(pid)}/audio/${enc(kind)}`),
  activeJobs: () => get<{ jobs: Array<{ id: string; kind: string; state: string;
    project_id: string; target: string; page: string; pct?: number | null;
    detail?: string; started_at?: string }> }>('/api/jobs/active'),
  stopAll: () => post<{ ok: boolean; cancelled: number; killed: string[] }>('/api/stop', {}),
  clearQueue: (pid: string) => post<{ cancelled: number }>(`${P(pid)}/clear-queue`, {}),

  // ---- 镜头
  /** 后端路由是 PUT（studio/api/app.py::save_shots） */
  saveShots: (pid: string, shots: Array<Partial<Shot>>) =>
    request<{ shots: Shot[] }>(`${P(pid)}/shots`,
      { method: 'PUT', body: JSON.stringify({ shots }) }),
  compilePreview: (pid: string, sid: string, prompt: string) =>
    post<CompilePreviewResponse>(`${P(pid)}/shots/${enc(sid)}/compile-preview`, { prompt }),
  assignShot: (pid: string, sid: string, body: AssignRequest) =>
    post<Shot & { scene_changed: boolean; state: string }>(`${P(pid)}/shots/${enc(sid)}/assign`, body),
  /** 后端只认 {shot_ids, set:{...}}（app.py::batch_update） */
  batchShots: (pid: string, body: BatchRequest & { chain_ref?: boolean }) => {
    const { shot_ids, ...edit } = body
    return post<{ updated: string[]; count: number }>(`${P(pid)}/shots/batch`,
      { shot_ids, set: edit })
  },
  deleteShot: (pid: string, sid: string) =>
    del<{ shots: Shot[] }>(`${P(pid)}/shots/${enc(sid)}`),
  /** 后端字段名是 before（app.py::move_shot） */
  moveShot: (pid: string, sid: string, beforeShotId: string | null) =>
    post<{ order: string[]; warnings?: Issue[] }>(`${P(pid)}/shots/${enc(sid)}/move`,
      { before: beforeShotId }),
  autoTransitions: (pid: string) =>
    post<{ changed: number; order: Array<[string, string]> }>(`${P(pid)}/shots/auto-transition`, {}),
  setChainRef: (pid: string, sid: string, enabled: boolean) =>
    post<{ shots: Shot[] }>(`${P(pid)}/shots/${enc(sid)}/chain-ref`, { enabled }),

  // ---- 首帧
  setFirstFrame: (pid: string, sid: string, path: string) =>
    post<{ shots: Shot[] }>(`${P(pid)}/shots/${enc(sid)}/first-frame`, { path }),
  /** 上传自己的首帧图（后端只收 multipart 文件） */
  uploadFirstFrame: (pid: string, sid: string, file: File) =>
    upload<{ shots: Shot[] }>(`${P(pid)}/shots/${enc(sid)}/first-frame`, file),
  clearFirstFrame: (pid: string, sid: string) =>
    del<{ shots: Shot[] }>(`${P(pid)}/shots/${enc(sid)}/first-frame`),
  firstFrameFromPrevious: (pid: string, sid: string) =>
    post<{ shots: Shot[] }>(`${P(pid)}/shots/${enc(sid)}/first-frame/from-previous`, {}),

  // ---- 版本
  listTakes: (pid: string, sid: string) =>
    get<{ takes: Array<Pick<Take, 'version' | 'path'>> }>(`${P(pid)}/shots/${enc(sid)}/takes`),
  /** 后端返回 {shot_id, deleted_version, remaining:[version]} */
  deleteTakes: (pid: string, sid: string, version?: number) =>
    del<{ shot_id: string; deleted_version: number; remaining: number[] }>(
      `${P(pid)}/shots/${enc(sid)}/takes${version ? `?version=${version}` : ''}`),
  adoptTake: (pid: string, sid: string) =>
    post<{ shot_id: string; version: number; state: string }>(`${P(pid)}/shots/${enc(sid)}/adopt`, {}),
  /** 这一镜的全部版本（含选用标记、是否与当前剧本一致） */
  takesAll: (pid: string, sid: string) =>
    get<{ takes: Array<{ version: number; path: string; elapsed_s?: number; chosen?: boolean;
      matches_script?: boolean; metrics?: { duration?: number; frames?: number } }> }>(
      `${P(pid)}/shots/${enc(sid)}/takes/all`),
  chooseTake: (pid: string, sid: string, version: number) =>
    post<{ shot_id: string; version: number }>(
      `${P(pid)}/shots/${enc(sid)}/takes/${version}/choose`, {}),
  adoptVersion: (pid: string, sid: string, version: number) =>
    post<{ shot_id: string; version: number; state: string }>(
      `${P(pid)}/shots/${enc(sid)}/takes/${version}/adopt`, {}),
  chooseLatestAll: (pid: string) =>
    post<{ chosen: Array<{ shot_id: string; version: number }> }>(`${P(pid)}/takes/choose-latest`, {}),
  passAll: (pid: string) =>
    post<{ passed: string[] }>(`${P(pid)}/shots/pass-all`, {}),
  /** 片段视频直链（<video src> 用，不经过 fetch） */
  mediaUrl: (pid: string, sid: string, version: number) =>
    `/media/${enc(pid)}/${enc(sid)}/${version}`,
  /** 上一镜末帧（chain 预览图） */
  lastFrameUrl: (pid: string, sid: string) =>
    `/assets/lastframe/${enc(pid)}/${enc(sid)}?t=${Date.now()}`,

  // ---- 审核
  review: (pid: string, body: ReviewRequest) =>
    post<{ shots: Shot[] }>(`${P(pid)}/review`, body),
  batchReview: (pid: string, shotIds: string[], verdict: 'pass' | 'reject', reason = '') =>
    post<{ ok: boolean; count: number; done: string[]; skipped: string[] }>(
      `${P(pid)}/review/batch`, { shot_ids: shotIds, verdict, reason }),
  reviewQueue: (pid: string) =>
    get<{ shots: Array<Pick<Shot, 'shot_id' | 'scene_id' | 'cast' | 'camera' | 'dialogue' | 'transition'> & { take: Take }>; total: number }>(
      `${P(pid)}/review-queue`),

  // ---- 计划 / 确认
  plan: (pid: string, body: PlanRequest) => post<PlanResult>(`${P(pid)}/plan`, body),
  confirm: (pid: string, body: ConfirmRequest) =>
    post<ConfirmResult>(`${P(pid)}/confirm`, body),

  // ---- 合成
  /** 后端返回 {out, url, shots, subtitles}（service.assemble） */
  // ---- 音频床
  getAudio: (pid: string) => get<{ audio: Record<string, unknown> }>(`${P(pid)}/audio`),
  putAudio: (pid: string, audio: unknown) =>
    put<{ audio: Record<string, unknown> }>(`${P(pid)}/audio`, audio as Record<string, unknown>),
  audioStatus: (pid: string) =>
    get<{ running?: boolean; log?: string; error?: string; exists?: Record<string, boolean> }>(
      `${P(pid)}/audio/status`),
  generateBed: (pid: string, kind: 'bgm' | 'ambience') =>
    post<{ ok: boolean }>(`${P(pid)}/audio/generate?kind=${kind}`, {}),
  audioPreview: (pid: string) =>
    get<{ url: string; seconds: number }>(`${P(pid)}/audio/preview`),
  uploadBed: (pid: string, kind: 'bgm' | 'ambience', file: File) =>
    fetch(`${P(pid)}/audio/upload?kind=${kind}&name=${encodeURIComponent(file.name)}`, {
      method: 'POST',
      headers: { 'content-type': 'application/octet-stream' },
      body: file,
    }).then(async (r) => {
      if (!r.ok) throw new Error(await r.text())
      return r.json() as Promise<{ ok: boolean }>
    }),

  assemble: (pid: string, opts?: { width?: number; height?: number; with_subs?: boolean; allow_stale?: boolean; shot_ids?: string[] }) =>
    post<{ out: string; url: string; shots: number; subtitles: boolean }>(
      `${P(pid)}/assemble`, opts || {}),
  outputUrl: (pid: string, name: string) => `/outputs/${enc(pid)}/${enc(name)}`,
  /** 在本机资源管理器打开成片所在目录并选中该文件 */
  revealOutput: (pid: string, name: string) =>
    post<{ ok: boolean; path?: string; dir?: string }>(
      `/api/outputs/${enc(pid)}/${enc(name)}/reveal`, {}),

  // ---- 角色
  listCharacters: () => get<{ characters: Character[] }>('/api/characters'),
  saveCharacter: (body: { character_id: string; name: string; description?: string; refs?: string[] }) =>
    post<Character>('/api/characters', body),
  /** force=true 时角色被镜头占用也能删（会把这些镜头里的它移除） */
  deleteScene: (pid: string, sid: string) =>
    del<{ ok: boolean }>(`${P(pid)}/scenes/${enc(sid)}`),
  deleteCharacter: (cid: string, force = false) =>
    del<{ deleted: string }>(`/api/characters/${enc(cid)}${force ? '?force=1' : ''}`),
  /** 单张上传（旧接口，插入到列表最前）。新代码优先用 uploadCharacterRefs。 */
  addCharacterRef: (cid: string, file: File) =>
    upload<Character & { added?: string[] }>(`/api/characters/${enc(cid)}/ref`, file),
  /**
   * 一次上传多张参考图（角色图集）。
   *
   * - 按数组顺序**追加**到已有列表末尾
   * - `primaryIndex` 指定第几张作为渲染用的主参考图（不传则保持原主图）
   *
   * 后端：`POST /api/characters/{cid}/refs`，字段名 `files`，返回 `{...character, added: string[]}`
   */
  uploadCharacterRefs: (cid: string, files: File[], primaryIndex = -1) => {
    const q = primaryIndex >= 0 ? `?primary_index=${primaryIndex}` : ''
    return uploadMany<Character & { added: string[] }>(
      `/api/characters/${enc(cid)}/refs${q}`, files)
  },
  deleteCharacterRef: (cid: string, path: string) =>
    del<Character>(`/api/characters/${enc(cid)}/ref?path=${enc(path)}`),
  setPrimaryRef: (cid: string, path: string) =>
    post<Character>(`/api/characters/${enc(cid)}/ref/primary`, { path }),
  /** 只改参考图顺序（第 1 张 = 主图） */
  reorderCharacterRefs: (cid: string, refs: string[]) =>
    post<Character>(`/api/characters/${enc(cid)}/refs/order`, { refs }),
  /** 把第 idx 张换成新图，其余不动 */
  replaceCharacterRef: (cid: string, idx: number, file: File) =>
    upload<Character>(`/api/characters/${enc(cid)}/refs/${idx}/replace`, file),
  verifyCharacter: (cid: string, verified: boolean) =>
    post<Character>(`/api/characters/${enc(cid)}/verified`, { verified }),
  characterUrl: (cid: string, bust?: string) =>
    `/assets/characters/${enc(cid)}${bust ? `?v=${enc(bust)}` : ''}`,
  /** 本剧专用角色覆盖（不影响其他项目） */
  setCastOverride: (pid: string, cid: string, body: { description?: string; refs?: string[] }) =>
    post<Character>(`${P(pid)}/characters/${enc(cid)}/override`, body),
  clearCastOverride: (pid: string, cid: string) =>
    del<Character>(`${P(pid)}/characters/${enc(cid)}/override`),

  // ---- 场景
  saveScene: (pid: string, body: Partial<Scene> & { scene_id: string }) =>
    post<Scene>(`${P(pid)}/scenes`, body),
  uploadScenePlate: (pid: string, sid: string, file: File, force = false) =>
    upload<Scene>(`${P(pid)}/scenes/${enc(sid)}/plate${force ? '?force=1' : ''}`, file),
  addScenePlate: (pid: string, sid: string, file: File) =>
    upload<Scene>(`${P(pid)}/scenes/${enc(sid)}/plates`, file),
  /**
   * 一次追加多张场景角度图（母版之外的补充视角）。
   *
   * H3 的参考槽位总共 9 个，角色优先占用：
   *   3 角色 × 3 视角 = 9 → 场景没位置
   * 建议每个场景最多 2 张（母版另算），主要角色 3 张、配角 1–2 张。
   */
  addScenePlates: (pid: string, sid: string, files: File[]) =>
    uploadMany<Scene & { added: number }>(`${P(pid)}/scenes/${enc(sid)}/plates`, files),
  removeScenePlate: (pid: string, sid: string, index: number) =>
    del<Scene>(`${P(pid)}/scenes/${enc(sid)}/plates/${index}`),
  setSceneLock: (pid: string, sid: string, locked: boolean) =>
    post<Scene>(`${P(pid)}/scenes/${enc(sid)}/lock`, { locked }),
  sceneUrl: (pid: string, sid: string) => `/assets/scenes/${enc(pid)}/${enc(sid)}`,

  // ---- 资产工坊
  modelsStatus: () => get<ModelsStatusResponse>('/api/models/status'),
  /** 设置页：环境体检 */
  doctor: () => get<Record<string, unknown>>('/api/doctor'),
  /** 设置页：只读配置展示 */
  configView: () => get<Record<string, unknown>>('/api/config'),
  /** 音乐生成（MiniMax Music 3） */
  generateMusic: (body: { prompt: string; caption_zh?: string; lyrics?: string; seconds?: number;
                            seed?: number; project_id?: string; group_id?: string }) =>
    post<{ job_id: string; queue_position: number }>('/api/assets/generate',
         { kind: 'music', ...body }),
  candidateAsBgm: (candId: string, projectId: string) =>
    post<{ ok: boolean; path: string }>(`/api/assets/candidates/${encodeURIComponent(candId)}/as-bgm`,
         { project_id: projectId }),
  /** 技能页：skill 列表（含正文） */
  skills: () => get<Record<string, unknown>>('/api/skills'),
  /** 规范页：规则 + 历史教训 */
  spec: () => get<Record<string, unknown>>('/api/spec'),
  /** 词库：分类 + 词条 */
  glossary: (projectId = '') =>
    get<Record<string, unknown>>(`/api/glossary${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  glossaryAdd: (category: string, item: string) =>
    post<Record<string, unknown>>('/api/glossary/items', { category, item }),
  glossaryRemove: (category: string, item: string) =>
    del<Record<string, unknown>>(`/api/glossary/items?category=${encodeURIComponent(category)}&item=${encodeURIComponent(item)}`),
  glossaryReset: (category = '') =>
    post<Record<string, unknown>>(`/api/glossary/reset${category ? `?category=${encodeURIComponent(category)}` : ''}`, {}),
  /** 资产管理页：按项目 / 分组筛选角色与场景 */
  assetsCatalog: (opts?: { projectId?: string; groupId?: string }) => {
    const q = new URLSearchParams()
    if (opts?.projectId) q.set('project_id', opts.projectId)
    if (opts?.groupId) q.set('group_id', opts.groupId)
    const s = q.toString()
    return get<AssetCatalogResponse>(`/api/assets/catalog${s ? `?${s}` : ''}`)
  },
  assetGroups: () => get<{ groups: AssetGroup[] }>('/api/assets/groups'),
  createAssetGroup: (name: string) => post<AssetGroup>('/api/assets/groups', { name }),
  patchAssetCandidate: (id: string,
    body: { project_id?: string; group_id?: string; kind?: AssetKind }) =>
    patch<AssetCandidate>(`/api/assets/candidates/${enc(id)}`, body),
  assetCandidates: () => get<{ candidates: AssetCandidate[] }>('/api/assets/candidates'),
  uploadAssetRef: async (file: File) => {
    const r = await fetch(`/api/assets/refs/upload?name=${encodeURIComponent(file.name)}`, {
      method: 'POST', body: file,
      headers: { 'content-type': file.type || 'application/octet-stream' },
    })
    if (!r.ok) throw new Error(await r.text())
    return (await r.json()) as { path: string; name: string }
  },
  deleteAssetCandidate: (id: string) => del<{ ok: boolean }>(`/api/assets/candidates/${enc(id)}`),
  adoptAssetCandidate: (id: string, body: {
    target: 'character' | 'scene'; character_id?: string;
    project_id?: string; scene_id?: string; role?: 'main' | 'alt';
  }) => post<{ ok: boolean; candidate: AssetCandidate; adopted_to: string }>(
    `/api/assets/candidates/${enc(id)}/adopt`, body),

  /** 文生图：任务进 GPU 队列，返回 job_id；用 jobProgress 轮询 */
  generateAssets: (body: {
    kind: AssetKind; prompt: string; width: number; height: number;
    steps?: number; seed?: number; count?: number;
    project_id?: string; group_id?: string; refs?: string[]; ref_paths?: string[];
  }) => post<{
    job_id: string; accepted: boolean; count: number;
    queue_position: number; executor: boolean;
  }>('/api/assets/generate', body),
  jobProgress: (jobId: string) =>
    get<{ job: Job; progress: Progress | null }>(`/api/jobs/${enc(jobId)}/progress`),

  // ---- 导入 / 导出
  previewImport: (projectId: string, markdown: string) =>
    post<ImportPreview>('/api/import/preview', { project_id: projectId, markdown }),
  importMarkdown: (projectId: string, markdown: string,
                   groupId = '', groupName = '') =>
    post<ImportResult>('/api/import',
      { project_id: projectId, markdown, group_id: groupId, group_name: groupName }),
  exportMarkdownUrl: (pid: string) => `${P(pid)}/export/markdown`,
  exportEdlUrl: (pid: string) => `${P(pid)}/export/edl`,
  exportOtioUrl: (pid: string) => `${P(pid)}/export/otio`,
} as const

export type Api = typeof api

/** 导入预览里可能出现的破坏性变化，UI 必须让用户确认后再提交 */
export function isDestructive(preview: ImportPreview): boolean {
  // removed = 删镜头；changed = 重写镜头（旧成片会被标记失效）
  return (preview.removed?.length ?? 0) > 0 || (preview.changed?.length ?? 0) > 0
}

/** 把校验结果分成"必须改"和"建议改"两组 */
export function splitIssues(report: ValidationReport): { errors: Issue[]; warns: Issue[] } {
  return { errors: report.errors ?? [], warns: report.warnings ?? [] }
}

/** 转场合法性：chain 要求两镜同一批人，否则画面几乎不动 */
export function chainIsSane(prev: Shot | undefined, cur: Shot): boolean {
  if (cur.transition !== 'chain' || !prev) return true
  const a = [...prev.cast].sort().join(',')
  const b = [...cur.cast].sort().join(',')
  return a === b
}

export type { Transition, UnetChoice }
