/**
 * API 类型定义 —— 与 studio/api/app.py + studio/core/service.py 一一对应。
 *
 * 这些类型是**前后端契约**。后端字段名不能改（历史数据、182 个测试都依赖），
 * 所以前端必须以这里为准，而不是反过来改后端。
 *
 * 生成依据：app.py 的路由与 Pydantic 入参、service.py 的返回字典。
 */

// ------------------------------------------------------------------ 基础

/** 登录用户（不带密码哈希） */
export interface AuthUser {
  id: string
  username: string
  display_name: string
  phone: string
  role: 'admin' | 'member'
  active: boolean
  created_at: string
  updated_at: string
}

/** 镜头状态机：empty → queued/running → review → passed/rejected → stale */
export type ShotState =
  | 'empty'      // 没有片段
  | 'queued'     // 已排队
  | 'running'    // 渲染中
  | 'review'     // 有片段，待审
  | 'passed'     // 已通过
  | 'rejected'   // 已否决
  | 'stale'      // 剧本/资产改过，旧片段失效
  | 'failed';    // 队列摘要用

export type Transition = 'cut' | 'chain';
export type Anchor = '' | 'plate';
export type UnetChoice = '' | 'ref2va';
export type PromptMode = '' | 'compile' | 'concat';
export type ReviewVerdict = 'pass' | 'reject';

export type JobState =
  | 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'paused';

// ------------------------------------------------------------------ 任务

export interface Job {
  id: string;
  project_id: string;
  kind: 'render' | 'preview' | 'assemble' | 'asset';
  /** JSON 字符串；解析后见 JobPayload */
  payload: string;
  state: JobState;
  error: string;
  created_at: string;
  started_at: string;
  finished_at: string;
}

export interface JobPayload {
  shots: string[];
  draft?: boolean;
  plan_id?: string;
  max_shots?: number;
  prompt_mode?: PromptMode;
  unet?: UnetChoice;
  /** 剩余自动重试次数（夜间批渲用 2） */
  retries_left?: number;
  auto_retry?: boolean;
}

/** 渲染进度（轮询 /api/projects/{pid} 里每镜的 progress 字段） */
export interface Progress {
  shot_id: string;
  /** 0–100，null 表示还在加载模型 */
  pct: number | null;
  /** loading | sampling | stalled | done */
  stage?: 'starting' | 'loading' | 'encoding' | 'sampling' | 'decoding' | 'stalled' | 'done';
  /** 引擎原始进度条文本，含 s/it 单步耗时 */
  detail?: string;
  started?: number;
}

// ------------------------------------------------------------------ 版本/审核

export interface Take {
  project_id: string;
  shot_id: string;
  version: number;
  path: string;
  fingerprint: string;
  elapsed_s: number;
  peak_mib: number;
  note: string;
  created_at: string;
  kind?: 'final' | 'draft';
}

export interface Review {
  shot_id: string;
  verdict: ReviewVerdict;
  reason: string;
  created_at: string;
}

export interface Preview {
  shot_id: string;
  path: string;
  created_at: string;
}

// ------------------------------------------------------------------ 镜头

export interface Shot {
  project_id: string;
  shot_id: string;
  ordinal: number;
  scene_id: string;
  cast: string[];
  camera: string;
  /** 六槽提示词原文：【机位】【人物】【开口】【节奏】【场景】【画质】 */
  prompt: string;
  dialogue: string;
  sound: string;
  music: string;
  transition: Transition;
  anchor: Anchor;
  duration: number;
  first_frame: string;
  fingerprint: string;
  updated_at: string;
  /** 人物镜接上一镜末帧（把末帧当第二张场景参考图） */
  chain_ref: boolean;

  // —— 仅 /api/projects/{pid} 详情接口附带 ——
  state: ShotState;
  take: Take | null;
  review: Review | null;
  preview: Preview | null;
  job: Job | null;
  preview_job: Job | null;
  last_job: Job | null;
  progress: Progress | null;
  /** chain 时的上一镜信息（用于显示"将从这里起步"的末帧缩略图） */
  chain_from: { shot_id: string | null; version: number | null; ready: boolean } | null;
}

// ------------------------------------------------------------------ 角色/场景

export interface Character {
  character_id: string;
  name: string;
  description: string;
  refs: string[];
  primary_ref: string;
  locked: boolean;
  /** 参考图与描述已人工核对过（勾了就不再提示 DESC_CONFLICT） */
  refs_verified: boolean;
  updated_at?: string;

  // —— 仅项目详情接口附带 ——
  /** 本项目中用到该角色的镜头号 */
  used_in?: string[];
  /** 本项目有专用覆盖（描述/参考图），不影响其他项目 */
  overridden?: boolean;

  // —— 仅资产管理目录接口（跨项目）附带 ——
  /** 该角色出现在哪些项目（catalog 接口） */
  projects?: Array<{ id: string; title: string }>;
}

export interface Scene {
  project_id: string;
  scene_id: string;
  name: string;
  camera: string;
  props: string;
  light: string;
  plate_path: string;
  plate_sha?: string;
  /** 同场景的其他机位参考图（母版之外的补充角度） */
  extra_plates: string[];
  locked?: boolean;
  updated_at?: string;

  // —— 仅资产管理目录接口附带 ——
  project_title?: string;
}

// ------------------------------------------------------------------ 项目

export interface Project {
  id: string;
  title: string;
  style: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectGroup {
  id: string;
  name: string;
  count: number;
}

export interface ProjectSummary extends Project {
  shots?: number;
  takes?: number;
  passed?: number;
  group_id?: string;
  archived?: number;
}

export interface ProjectDetail {
  project: Project;
  shots: Shot[];
  scenes: Scene[];
  characters: Character[];
  /** 各状态计数，含 total */
  counts: Record<string, number>;
}

// ------------------------------------------------------------------ 校验

export interface Issue {
  code: string;
  level: 'error' | 'warn';
  shot_id: string;
  character_id?: string;
  msg: string;
  /** 可直接照做的修复建议 */
  fix: string;
}

export interface ValidationReport {
  project_id: string;
  errors: Issue[];
  warnings: Issue[];
  ok: boolean;
}

// ------------------------------------------------------------------ 只读编译预览

export type CompilePreviewKind = 'zh' | 'en' | 'meta';

export interface CompilePreviewSegment {
  slot: string;
  kind: CompilePreviewKind;
  source: string;
  text: string;
}

export interface CompilePreviewError {
  slot: string;
  field: string;
  who: string;
  code: string;
  message: string;
  allowed: string[];
}

export interface CompilePreviewDropped {
  slot: string;
  source: string;
  reason: string;
  replaced_by: string;
}

export interface CompilePreviewResponse {
  compiled: string;
  segments: CompilePreviewSegment[];
  errors: CompilePreviewError[];
  dropped: CompilePreviewDropped[];
  elapsed_ms: number;
}

// ------------------------------------------------------------------ 资产工坊

export type AssetKind = 'character' | 'scene' | 'prop' | 'emotion';

export interface AssetCandidate {
  id: string;
  created_at: string;
  kind: AssetKind;
  prompt: string;
  negative: string;
  width: number;
  height: number;
  seed: number;
  steps: number;
  path: string;
  /** 静态直链 /assets/generated/...；不在该目录时为空串 */
  url: string;
  elapsed_s: number;
  source_path: string;
  adopted_to: string;
  /** 归属项目；空串 = 不归属任何项目 */
  project_id: string;
  /** 素材分组；空串 = 未分组 */
  group_id: string;
}

/** 素材分组（候选图分组，独立于项目分组） */
export interface AssetGroup {
  id: string;
  name: string;
  created_at?: string;
  count?: number;
}

/** 合成成片（资产管理页展示） */
export interface OutputVideo {
  name: string;
  url: string;
  /** 首帧缩略图直链 */
  poster: string;
  /** 服务器上的绝对文件路径（可复制给本地播放器） */
  path: string;
  size: number;
  mtime: number;
  project_id: string;
  project_title: string;
}

export interface AssetCatalogResponse {
  characters: Character[];
  scenes: Scene[];
  /** 成片视频 */
  outputs: OutputVideo[];
  /** 文生图产物（候选图） */
  generated: AssetCandidate[];
  project_id: string;
  group_id: string;
  projects: Array<{ id: string; title: string }>;
}

export type ModelFileState = 'missing' | 'downloading' | 'ready' | 'error';

export interface ModelFileStatus {
  key: string;
  file: string;
  filename: string;
  subdir: string;
  exists: boolean;
  bytes: number;
  bytes_on_disk: number;
  state: ModelFileState;
  pct: number;
  speed_mbps: number;
  eta_s: number;
  error: string;
}

export interface ModelsStatusResponse {
  models: ModelFileStatus[];
  ready: boolean;
  downloading: boolean;
}

// ------------------------------------------------------------------ 计划/确认

export interface PlanShot {
  shot_id: string;
  scene_id: string;
  cast: string[];
  duration: number;
  transition: Transition;
  reason?: string;
}

export interface PlanResult {
  plan_id: string;
  project_id: string;
  draft: boolean;
  shots: PlanShot[];
  count: number;
  est_minutes: number;
  worst_minutes: number;
  /** "HH:MM" */
  est_finish: string;
  warnings: Issue[];
  /** level === 'error' 的条目，非空则不能排队 */
  blocking: Issue[];
}

export interface ConfirmResult {
  ok: boolean;
  jobs: Job[];
  job: Job;
  executor: boolean;
  count: number;
  /** 已在队列里、被跳过的镜头 */
  skipped: string[];
}

// ------------------------------------------------------------------ 统计

export interface ProjectStats {
  project_id: string;
  shots: number;
  takes: number;
  rerendered_shots: number;
  rerender_rate: number;
  passed: number;
  pass_rate: number;
  first_take_pass: number;
  avg_shot_seconds: number;
  total_gpu_minutes: number;
  peak_mib: number;
  reject_reasons: Record<string, number>;
}

export interface GlobalQueue {
  active: Job[];
  count: number;
  executor: boolean;
}

export interface HealthInfo {
  ok: boolean;
  time: string;
  executor: boolean;
  /** 数据库绝对路径 */
  data: string;
}

// ------------------------------------------------------------------ 导入

export interface ImportPreview {
  /** 新剧本的镜头总数 */
  shots: number;
  /** 指纹未变、可保留的镜头 */
  kept: string[];
  /** 内容变了、需要重渲的镜头 */
  changed: string[];
  /** 新增镜头 */
  added: string[];
  /** 本次导入会删除的既有镜头 */
  removed: string[];
  warnings: string[];
  /** 剧本里的片名（用户没填项目名时用它） */
  title: string
}

export interface ImportResult {
  project_id: string;
  updated: boolean;
  title: string;
  characters: string[];
  scenes: string[];
  shots: string[];
  warnings: string[];
}

// ------------------------------------------------------------------ 请求体

export interface PlanRequest {
  shot_ids?: string[] | null;
  draft?: boolean;
  prompt_mode?: PromptMode;
  /** 已通过的镜头也重渲 */
  force?: boolean;
}

export interface ConfirmRequest {
  plan_id: string;
  draft?: boolean;
  pause_after_one?: boolean;
  prompt_mode?: PromptMode;
  unet?: UnetChoice;
  /** 失败自动重试次数；夜间批渲传 2 */
  retries?: number;
}

export interface AssignRequest {
  cast_add?: string[];
  cast_remove?: string[];
  cast_set?: string[];
  scene_id?: string;
}

export interface BatchRequest {
  shot_ids: string[];
  scene_id?: string;
  transition?: Transition;
  duration?: number;
  cast_add?: string[];
  cast_remove?: string[];
}

export interface ReviewRequest {
  shot_id: string;
  verdict: ReviewVerdict;
  reason?: string;
}
