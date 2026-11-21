/**
 * AppShell —— 左侧边栏 + 右侧内容区（顶部标题栏由各页面渲染）。
 * 公共 UI 不随路由切换卸载，服务端轮询持续运行。
 */

import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { AssetManagerModal } from './components/AssetManagerModal'
import { ConfirmHost } from './components/ConfirmHost'
import { ImportModal } from './components/ImportModal'
import { PlanModal } from './components/PlanModal'
import { PlayerModal } from './components/PlayerModal'
import { PromptEditorModal } from './components/PromptEditorModal'
import { Sidebar } from './components/Sidebar'
import { TopNav } from './components/TopNav'
import { LoginPage } from './components/LoginPage'
import { StatusBar } from './components/StatusBar'
import { ToastHost } from './components/ToastHost'
import { VersionModal } from './components/VersionModal'
import { boot, poll, useStore } from './lib/store'
import { bootstrapAuth, logout, useAuth } from './lib/auth'
import { useAppModals } from './lib/app-modals'
import { t, tpl, useI18n } from './lib/i18n'
import { Router } from './router'

export function App() {
  const { detail, loading, error } = useStore()
  const { user, ready } = useAuth()
  // 订阅语言：切中英文时整棵树跟着重渲染（t() 只是读快照）
  useI18n((s) => s.lang)

  useEffect(() => { void bootstrapAuth() }, [])
  useEffect(() => {
    if (!user) return
    void boot()
    const timer = window.setInterval(() => { void poll() }, 15000)
    return () => window.clearInterval(timer)
  }, [user?.id])

  const modal = useAppModals((s) => s.modal)
  const player = useAppModals((s) => s.player)
  const closeModal = useAppModals((s) => s.closeModal)
  const closePlayer = useAppModals((s) => s.closePlayer)

  if (!ready) return <div className="boot-loading">{t('shell.loading')}</div>
  if (!user) return <LoginPage />

  return (
    <div className="app">
      <Sidebar user={user} onLogout={() => void logout()} />

      <div className="app-main">
        <TopNav />
        <main className="main">
          <Router />
        </main>
        <StatusBar />
      </div>

      {loading && !detail && <div className="boot-loading">{t('shell.loading')}</div>}
      {error && <div className="error-bar">{tpl('shell.backend-error', { err: error })}</div>}

      {modal?.kind === 'asset' && (
        <AssetManagerModal
          kind={modal.target}
          id={modal.id}
          pid={modal.projectId}
          onClose={closeModal}
        />
      )}
      {modal?.kind === 'plan' && (
        <PlanModal shotIds={modal.ids} onClose={closeModal} />
      )}
      {modal?.kind === 'import' && (
        <ImportModal onClose={closeModal} />
      )}
      {modal?.kind === 'takes' && (
        <VersionModal sid={modal.sid} onClose={closeModal} />
      )}
      {modal?.kind === 'prompt' && (
        <PromptEditorModal sid={modal.sid} onClose={closeModal} />
      )}

      {player && <PlayerModal {...player} onClose={closePlayer} />}

      <ConfirmHost />
      <ToastHost />
    </div>
  )
}

// 默认重定向：/app → /create/video/rendering
export function DefaultRedirect() {
  return <Navigate to="/create/video/rendering" replace />
}
