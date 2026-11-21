/**
 * Sidebar —— 左侧一级导航 + 账户抽屉。
 *
 * 规则（用户明确要求）：
 *  - 侧边栏只放一级菜单，二级/三级菜单交给右侧内容区顶部
 *  - 顶部 logo + 平台名，中部菜单，底部头像 + 昵称
 *  - 可收起：收起后只显示图标与头像
 *  - 点头像弹出抽屉：主题 / 语言 / 个人主页 / 退出登录
 */

import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { setAuthUser } from '../lib/auth'
import { t, tpl } from '../lib/i18n'
import { useI18n } from '../lib/i18n'
import { toast } from '../lib/store'
import { useTheme } from '../lib/theme'
import type { AuthUser } from '../lib/types'
import { Modal } from './Modal'
import './Sidebar.css'

const COLLAPSE_KEY = 'ov-sidebar-collapsed'

interface Props {
  user: AuthUser
  onLogout?: () => void
}

export function Sidebar({ user, onLogout }: Props) {
  const nav = useNavigate()
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === '1')
  const [drawer, setDrawer] = useState(false)
  const [profile, setProfile] = useState(false)

  const userName = user.display_name || user.username

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  return (
    <>
      <aside className={`sidebar${collapsed ? ' is-collapsed' : ''}`}>
        <div className="sidebar-top">
          <div className="sidebar-brand" title={t('brand')}>
            <LogoMark />
            {!collapsed && <span className="sidebar-wordmark">{t('brand')}</span>}
          </div>
          <button
            className="sidebar-collapse"
            onClick={() => setCollapsed((v) => !v)}
            title={collapsed ? t('nav.expand') : t('nav.collapse')}
            aria-label={collapsed ? t('nav.expand') : t('nav.collapse')}
          >
            <IconDock collapsed={collapsed} />
          </button>
        </div>

        <nav className="sidebar-nav" aria-label={t('nav.menu')}>
          <SideItem to="/assets" end label={t('nav.assets')} icon={<IconAssets />} collapsed={collapsed} />
          <SideItem to="/create" label={t('nav.create')} icon={<IconCreate />} collapsed={collapsed} />
          <SideItem to="/projects" label={t('nav.projects')} icon={<IconProjects />} collapsed={collapsed} />
        </nav>

        <div className="sidebar-bottom">
          <button className="sidebar-user" onClick={() => setDrawer(true)} title={t('user.account')}>
            <span className="sidebar-avatar">{userName.slice(0, 1)}</span>
            {!collapsed && (
              <span className="sidebar-user-meta">
                <span className="sidebar-user-name">{userName}</span>
                <span className="sidebar-user-role">
                  {user.role === 'admin' ? t('user.admin') : t('user.member')}
                </span>
              </span>
            )}
          </button>

          {drawer && (
            <AccountDrawer
              user={user}
              userName={userName}
              onClose={() => setDrawer(false)}
              onProfile={() => { setDrawer(false); setProfile(true) }}
              onManageUsers={user.role === 'admin' ? () => { setDrawer(false); nav('/users') } : undefined}
              onSettings={() => { setDrawer(false); nav('/settings') }}
              onLogout={onLogout}
            />
          )}
        </div>
      </aside>

      {profile && (
        <ProfileModal user={user} onClose={() => setProfile(false)} />
      )}
    </>
  )
}

function SideItem({ to, end, label, icon, collapsed }: {
  to: string; end?: boolean; label: string; icon: React.ReactNode; collapsed: boolean
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => `sidebar-item${isActive ? ' is-active' : ''}`}
      title={collapsed ? label : undefined}
    >
      <span className="sidebar-item-icon">{icon}</span>
      {!collapsed && <span className="sidebar-item-label">{label}</span>}
    </NavLink>
  )
}

function AccountDrawer({ user, userName, onClose, onProfile, onManageUsers, onSettings, onLogout }: {
  user: AuthUser
  userName: string
  onClose: () => void
  onProfile: () => void
  onManageUsers?: () => void
  onSettings?: () => void
  onLogout?: () => void
}) {
  const [theme, toggleTheme] = useTheme()
  const { lang, setLang } = useI18n()

  return (
    <>
      <div className="pop-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }} />
      <div className="account-pop" role="dialog" aria-modal="true" aria-label={t('user.account')}>
        <header className="drawer-head">
          <span className="sidebar-avatar lg">{userName.slice(0, 1)}</span>
          <div>
            <div className="drawer-name">{userName}</div>
            <div className="drawer-role">
              {user.role === 'admin' ? t('user.admin') : t('user.member')} · @{user.username}
            </div>
          </div>
          <button className="ghost drawer-close" onClick={onClose} aria-label={t('btn.close')}>✕</button>
        </header>

        <div className="drawer-section">
          <div className="drawer-section-title">{t('user.theme')}</div>
          <div className="segmented">
            <button className={theme === 'dark' ? 'is-active' : ''} onClick={() => { if (theme !== 'dark') toggleTheme() }}>
              {t('theme.dark-label')}
            </button>
            <button className={theme === 'light' ? 'is-active' : ''} onClick={() => { if (theme !== 'light') toggleTheme() }}>
              {t('theme.light-label')}
            </button>
          </div>
        </div>

        <div className="drawer-section">
          <div className="drawer-section-title">{t('user.language')}</div>
          <div className="segmented">
            <button className={lang === 'zh' ? 'is-active' : ''} onClick={() => setLang('zh')}>
              {t('lang.zh-label')}
            </button>
            <button className={lang === 'en' ? 'is-active' : ''} onClick={() => setLang('en')}>
              {t('lang.en-label')}
            </button>
          </div>
        </div>

        <div className="drawer-actions">
          <button className="drawer-action" onClick={onProfile}>
            <span>{t('user.profile')}</span><span className="chev">›</span>
          </button>
          {onSettings && (
            <button className="drawer-action" onClick={onSettings}>
              <span>{t('user.settings')}</span><span className="chev">›</span>
            </button>
          )}
          {onManageUsers && (
            <button className="drawer-action" onClick={onManageUsers}>
              <span>{t('user.manage')}</span><span className="chev">›</span>
            </button>
          )}
          {onLogout && (
            <button className="drawer-action danger" onClick={onLogout}>
              <span>{t('user.logout')}</span><span className="chev">›</span>
            </button>
          )}
        </div>
      </div>
    </>
  )
}

function ProfileModal({ user, onClose }: { user: AuthUser; onClose: () => void }) {
  const [displayName, setDisplayName] = useState(user.display_name || user.username)
  const [phone, setPhone] = useState(user.phone || '')
  const [oldPwd, setOldPwd] = useState('')
  const [pwd, setPwd] = useState('')
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      const r = await api.updateMe({
        display_name: displayName.trim(),
        phone: phone.trim(),
        password: pwd || undefined,
        old_password: pwd ? oldPwd : undefined,
      })
      setAuthUser(r.user)
      toast(t('profile.updated'), 'ok')
      onClose()
    } catch (e) {
      toast(tpl('profile.failed', { err: e instanceof Error ? e.message : String(e) }), 'err')
    } finally {
      setBusy(false)
    }
  }

  const canSave = !busy && (!pwd || (oldPwd.length > 0 && pwd.length >= 6))

  return (
    <Modal
      title={t('profile.title')}
      onClose={onClose}
      footer={(
        <>
          <span className="spacer" />
          <button onClick={onClose}>{t('profile.cancel')}</button>
          <button className="primary" disabled={!canSave} onClick={() => void save()}>
            {t('profile.save')}
          </button>
        </>
      )}
    >
      <div className="profile-form">
        <label className="field">
          <span>{t('profile.name')}</span>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </label>
        <label className="field">
          <span>{t('profile.phone')}</span>
          <input value={phone} inputMode="tel" onChange={(e) => setPhone(e.target.value)} />
        </label>
        <label className="field">
          <span>{t('profile.old-password')}</span>
          <input type="password" value={oldPwd} placeholder={t('profile.old-password.hint')}
            onChange={(e) => setOldPwd(e.target.value)} />
        </label>
        <label className="field">
          <span>{t('profile.password')}</span>
          <input type="password" value={pwd} placeholder={t('profile.password.hint')}
            onChange={(e) => setPwd(e.target.value)} />
        </label>
      </div>
    </Modal>
  )
}

/* ---------------------------------------------------------------- 图标 */

function LogoMark() {
  return (
    <svg className="logo-mark" width="26" height="26" viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id="ovg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5aa0ff" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="28" height="28" rx="8" fill="url(#ovg)" opacity="0.16" />
      <path d="M12 10.5v11l9-5.5-9-5.5z" fill="url(#ovg)" />
      <rect x="5" y="5" width="22" height="22" rx="6" fill="none" stroke="url(#ovg)" strokeWidth="1.4" opacity="0.55" />
    </svg>
  )
}

/** 收起/展开：仿调试器停靠位置切换（左/右两块） */
function IconDock({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="4" width="18" height="16" rx="2.5" stroke="currentColor" strokeWidth="1.5" />
      {collapsed ? (
        <rect x="13" y="4" width="8" height="16" rx="2.5" fill="currentColor" opacity="0.22" stroke="none" />
      ) : (
        <rect x="3" y="4" width="8" height="16" rx="2.5" fill="currentColor" opacity="0.22" stroke="none" />
      )}
      <path d={collapsed ? 'M13 4v16' : 'M11 4v16'} stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

function IconProjects() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l1.8 2h7.6A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5v-9Z"
        stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M7.5 12.5h9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.55" />
    </svg>
  )
}

function IconAssets() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
      <rect x="3" y="3" width="8" height="8" rx="2" />
      <rect x="13" y="3" width="8" height="8" rx="2" />
      <rect x="3" y="13" width="8" height="8" rx="2" />
      <rect x="13" y="13" width="8" height="8" rx="2" />
    </svg>
  )
}

function IconCreate() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
      <rect x="2.5" y="6" width="19" height="13" rx="2.5" />
      <path d="M2.5 10h19M7 6V4M12 6V4M17 6V4" />
      <path d="M10 13.5l4 2.2-4 2.2v-4.4z" fill="currentColor" stroke="none" />
    </svg>
  )
}
