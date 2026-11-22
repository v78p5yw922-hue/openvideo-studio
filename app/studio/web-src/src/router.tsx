/**
 * 路由定义 —— 全小写英文路径，和 NavBar NavLink 对齐。
 *
 * 结构：
 * /create/video/rendering   → ① 分镜渲染
 * /create/video/compose     → ② 合成成片
 * /create/text-to-image     → 文生图
 * /assets                   → 资产管理
 */

import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { DefaultRedirect } from './App'
import { t } from './lib/i18n'

const AssetWorkspacePage = lazy(() => import('./pages/AssetWorkspacePage').then((m) => ({ default: m.AssetWorkspacePage })))
const RenderPage = lazy(() => import('./pages/RenderPage').then((m) => ({ default: m.RenderPage })))
const ComposePage = lazy(() => import('./pages/ComposePage').then((m) => ({ default: m.ComposePage })))
const TextToImagePage = lazy(() => import('./pages/TextToImagePage').then((m) => ({ default: m.TextToImagePage })))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((m) => ({ default: m.ProjectsPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const GlossaryPage = lazy(() => import('./pages/GlossaryPage').then((m) => ({ default: m.GlossaryPage })))
const SpecPage = lazy(() => import('./pages/SpecPage').then((m) => ({ default: m.SpecPage })))
const MusicPage = lazy(() => import('./pages/MusicPage').then((m) => ({ default: m.MusicPage })))
const SkillsPage = lazy(() => import('./pages/SkillsPage').then((m) => ({ default: m.SkillsPage })))
const UsersPage = lazy(() => import('./pages/UsersPage').then((m) => ({ default: m.UsersPage })))

function Fallback() {
  return (
    <div className="pane" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
      <span className="muted">{t('shell.loading')}</span>
    </div>
  )
}

export function Router() {
  return (
    <Suspense fallback={<Fallback />}>
      <Routes>
        {/* 默认入口：/app → 渲染页（保持旧版默认落点） */}
        <Route path="/" element={<DefaultRedirect />} />

        {/* 资产管理 */}
        <Route path="/assets" element={<AssetWorkspacePage />} />

        {/* 项目管理（列表页） */}
        <Route path="/projects" element={<ProjectsPage />} />

        {/* 用户管理（独立页面） */}
        <Route path="/users" element={<UsersPage />} />

        {/* 设置（从个人中心进入） */}
        <Route path="/settings" element={<SettingsPage />} />

        {/* 剧本词库 */}
        <Route path="/glossary" element={<GlossaryPage />} />

        {/* 规范（只读） */}
        <Route path="/spec" element={<SpecPage />} />

        {/* 写作技能（把 skill 交给任意模型用） */}
        <Route path="/skills" element={<SkillsPage />} />

        {/* Create 下的二级 + 三级 */}
        <Route path="/create">
          <Route index element={<Navigate to="video/rendering" replace />} />

          {/* Create-Video 下的三级 */}
          <Route path="video">
            <Route index element={<Navigate to="rendering" replace />} />
            <Route path="rendering" element={<RenderPage />} />
            <Route path="compose" element={<ComposePage />} />
          </Route>

          {/* Create-TextToImage */}
          <Route path="text-to-image" element={<TextToImagePage />} />
          {/* Create-Music：和视频/文生图并列 */}
          <Route path="music" element={<MusicPage />} />
        </Route>

        {/* 兜底 */}
        <Route path="*" element={<Navigate to="/create/video/rendering" replace />} />
      </Routes>
    </Suspense>
  )
}
