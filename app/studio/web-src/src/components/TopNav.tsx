/**
 * TopNav —— 右侧内容区最顶部的二级菜单栏。
 *
 * 一级菜单在左侧边栏（资产管理 / 创作 / 项目管理），这里显示当前一级菜单的
 * 二级页签：创作下有「分镜视频 / 合成成片 / 文生图」，其余只有一个菜单名。
 * 侧边栏的「创作」在这些页之间始终保持激活。
 */

import { NavLink, useLocation } from 'react-router-dom'
import { t } from '../lib/i18n'
import './TopNav.css'

type Section = 'create' | 'assets' | 'projects' | 'users'

const CREATE_TABS: Array<{ to: string; key: string }> = [
  { to: '/create/video/rendering', key: 'rendering' },
  { to: '/create/video/compose', key: 'compose' },
  { to: '/create/text-to-image', key: 't2i' },
  { to: '/create/music', key: 'music' },
]

export function TopNav() {
  const { pathname } = useLocation()
  let section: Section | null = null
  if (pathname.startsWith('/create')) section = 'create'
  else if (pathname.startsWith('/assets')) section = 'assets'
  else if (pathname.startsWith('/projects')) section = 'projects'
  else if (pathname.startsWith('/users')) section = 'users'

  if (!section) return null

  return (
    <div className="topnav">
      <span className="topnav-section">{t(`topnav.${section}`)}</span>
      {section === 'create' && (
        <nav className="topnav-tabs" aria-label={t('topnav.create')}>
          {CREATE_TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end
              className={({ isActive }) => `topnav-tab${isActive ? ' is-active' : ''}`}
            >
              {t(`topnav.${tab.key}`)}
            </NavLink>
          ))}
        </nav>
      )}
    </div>
  )
}
