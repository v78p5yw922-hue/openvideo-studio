/**
 * AppHeader —— 右侧内容区顶部：页面标题 + 项目切换 + 二级/三级菜单 + 页面操作。
 *
 * 侧边栏只放一级菜单；这里承担"当前页面"的所有导航与操作入口。
 */

import { useMemo, type ReactNode } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { confirmDialog } from '../lib/confirm'
import { t, tpl } from '../lib/i18n'
import { boot, errText, openProject, toast, useStore } from '../lib/store'
import { usePopover } from '../lib/usePopover'
import './AppHeader.css'

export interface HeaderTab {
  to: string
  label: string
  end?: boolean
}

interface Props {
  title: string
  subtitle?: string
  tabs?: HeaderTab[]
  actions?: ReactNode
  /** 精简模式：不渲染标题与右上项目操作区，只保留操作条（资产页用） */
  compact?: boolean
}

export function AppHeader({ title, subtitle, tabs, actions, compact }: Props) {
  const { projects, groups, pid, detail } = useStore()
  const nav = useNavigate()
  const loc = useLocation()
  const isHub = loc.pathname === '/projects'
  const renaming = usePopover()
  const moreMenu = usePopover()
  const projMenu = usePopover()

  const current = projects.find((p) => p.id === pid)
  const stats = subtitle ?? (detail
    ? tpl('header.stats', { shots: detail.shots.length, scenes: detail.scenes.length })
    : '')

  const groupedOptions = useMemo(() => {
    const out: Array<{ label: string; items: Array<{ value: string; label: string }> }> = []
    groups.filter((g) => g.id).forEach((g) => {
      const list = projects.filter((p) => (p.group_id || '') === g.id && !p.archived)
      if (list.length) out.push({ label: g.name, items: list.map((p) => ({ value: p.id, label: p.title })) })
    })
    const ungrouped = projects.filter((p) => !p.group_id && !p.archived)
    if (ungrouped.length) {
      out.push({ label: t('project.ungrouped'), items: ungrouped.map((p) => ({ value: p.id, label: p.title })) })
    }
    const archived = projects.filter((p) => p.archived)
    if (archived.length) {
      out.push({
        label: t('project.archived'),
        items: archived.map((p) => ({ value: p.id, label: `${p.title}${t('project.archived-tag')}` })),
      })
    }
    return out
  }, [projects, groups])

  async function doRename() {
    const input = document.querySelector<HTMLInputElement>('.header-rename-input')
    const next = (input?.value ?? '').trim()
    if (!pid || !next) return
    try {
      await api.renameProject(pid, next)
      await openProject(pid)
      toast(tpl('header.renamed', { name: next }), 'ok')
      renaming.close()
    } catch (e) {
      toast(tpl('header.rename.failed', { err: errText(e) }), 'err')
    }
  }

  async function doDelete() {
    if (!pid) return
    const ok = await confirmDialog({
      title: t('header.delete'),
      message: tpl('header.delete.confirm1', { name: current?.title ?? pid }),
      confirmText: t('btn.delete'),
      danger: true,
    })
    if (!ok) return
    const again = await confirmDialog({
      title: t('header.delete'),
      message: t('header.delete.confirm2'),
      confirmText: t('btn.delete'),
      danger: true,
    })
    if (!again) return
    try {
      await api.deleteProject(pid)
      toast(t('header.deleted'), 'ok')
      moreMenu.close()
      await boot()
    } catch (e) {
      toast(tpl('header.delete.failed', { err: errText(e) }), 'err')
    }
  }

  return (
    <header className="page-header">
      {!compact && (
      <div className="page-header-row">
        <div className="page-heading">
          <h1>{title}</h1>
          {stats && <span className="page-subtitle">{stats}</span>}
        </div>

        {!isHub && (
        <div className="page-project">
          <div className="popover" ref={projMenu.ref}>
            <button
              type="button"
              className="page-project-trigger"
              aria-label={t('header.project')}
              onClick={projMenu.toggle}
            >
              <span className="ppt-label">{current?.title ?? t('project.none')}</span>
              <span className="ppt-caret">{projMenu.open ? '︿' : '﹀'}</span>
            </button>
            {projMenu.open && (
              <div className="popover-panel menu project-menu align-right">
                {projects.length === 0 && <div className="menu-note">{t('project.none')}</div>}
                {groupedOptions.map((g) => (
                  <div key={g.label}>
                    <div className="menu-label">{g.label}</div>
                    {g.items.map((p) => (
                      <button
                        key={p.value}
                        className={p.value === pid ? 'is-active' : ''}
                        onClick={() => { projMenu.close(); void openProject(p.value) }}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>

          <button className="ghost" onClick={() => nav('/projects')} title={t('header.manage')}>
            {t('header.manage')}
          </button>

          <div className="popover" ref={renaming.ref}>
            <button type="button" className="ghost" disabled={!pid} onClick={renaming.toggle}>
              {t('header.rename')}
            </button>
            {renaming.open && (
              <div className="popover-panel rename-panel align-right">
                <input
                  autoFocus
                  aria-label={t('header.rename')}
                  className="header-rename-input"
                  defaultValue={current?.title ?? ''}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void doRename()
                    if (e.key === 'Escape') renaming.close()
                  }}
                />
                <div className="rename-actions">
                  <button className="primary" onClick={() => void doRename()}>{t('header.save')}</button>
                  <button onClick={renaming.close}>{t('header.cancel')}</button>
                </div>
              </div>
            )}
          </div>

          <div className="popover" ref={moreMenu.ref}>
            <button className="ghost" disabled={!pid} onClick={moreMenu.toggle} aria-label={t('header.more')}>⋯</button>
            {moreMenu.open && (
              <div className="popover-panel menu align-right">
                <div className="menu-label">{t('header.export')}</div>
                <a href={api.exportMarkdownUrl(pid ?? '')} download onClick={moreMenu.close}>{t('export.markdown')}</a>
                <a href={api.exportEdlUrl(pid ?? '')} download onClick={moreMenu.close}>{t('export.edl')}</a>
                <a href={api.exportOtioUrl(pid ?? '')} download onClick={moreMenu.close}>{t('export.otio')}</a>
                <div className="menu-divider" />
                <button className="danger" onClick={() => void doDelete()}>{t('header.delete')}</button>
                <div className="menu-note muted">{t('header.project-path')}: {pid}</div>
              </div>
            )}
          </div>
        </div>
        )}
      </div>
      )}

      {(tabs?.length || actions) && (
        <div className="page-header-row page-header-bar">
          {tabs?.length ? (
            <nav className="page-tabs" aria-label={title}>
              {tabs.map((tab) => (
                <NavLink
                  key={tab.to}
                  to={tab.to}
                  end={tab.end}
                  className={({ isActive }) => `page-tab${isActive ? ' is-active' : ''}`}
                >
                  {tab.label}
                </NavLink>
              ))}
            </nav>
          ) : <span />}
          <div className="spacer" />
          {actions && <div className="page-actions">{actions}</div>}
        </div>
      )}
    </header>
  )
}
