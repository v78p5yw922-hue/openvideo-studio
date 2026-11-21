/**
 * 项目管理页：搜索、分组、重命名、排序、归档、删除、批量。
 * 从原来的弹窗升级为独立页面（顶部「管理」按钮进入）。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { ProjectSettingsModal } from '../components/ProjectSettingsModal'
import { confirmDialog } from '../lib/confirm'
import { t, tpl } from '../lib/i18n'
import { boot, errText, openProject, refreshProjects, toast, useStore } from '../lib/store'
import '../components/ProjectManager.css'
import './ProjectsPage.css'

export function ProjectsPage() {
  const { projects, groups, pid } = useStore()
  const nav = useNavigate()
  const [busy, setBusy] = useState(false)
  const [settingsFor, setSettingsFor] = useState<Project | null>(null)
  const [newGroup, setNewGroup] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [batchGroup, setBatchGroup] = useState('')
  /** 左侧分组列表筛选：'all' | '__none' | gid | '__archived' */
  const [activeGroup, setActiveGroup] = useState<string>('all')
  /** 批量模式：先指定动作，再勾选，再下一步 */
  const [batchMode, setBatchMode] = useState<null | 'move' | 'archive' | 'unarchive'>(null)
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true)
    try {
      await fn()
      await refreshProjects()
      toast(ok, 'ok')
    } catch (e) {
      toast(errText(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  async function createGroup() {
    const name = newGroup.trim()
    if (!name) return
    await run(async () => { await api.createGroup(name) }, tpl('pm.group.created', { name }))
    setNewGroup('')
  }

  function toggle(id: string) {
    const next = new Set(sel)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSel(next)
  }

  function pickGroup(gid: string) {
    setActiveGroup(gid)
    setSel(new Set())
  }

  function setMode(m: null | 'move' | 'archive' | 'unarchive') {
    setBatchMode(m)
    setSel(new Set())
    setBatchGroup('')
  }

  const activeList = projects.filter((p) => !p.archived)
  const allCount = activeList.length
  const noneCount = activeList.filter((p) => !p.group_id).length
  const archivedCount = projects.filter((p) => p.archived).length
  const groupCount = (gid: string) => activeList.filter((p) => (p.group_id || '') === gid).length

  const keyword = q.trim().toLowerCase()
  const visible = projects.filter((p) => {
    if (activeGroup === '__archived') return !!p.archived
    if (p.archived) return false
    if (activeGroup === 'all') return true
    if (activeGroup === '__none') return !p.group_id
    return (p.group_id || '') === activeGroup
  }).filter((p) => !keyword
    || (p.title || '').toLowerCase().includes(keyword)
    || p.id.toLowerCase().includes(keyword))
  const allSelected = visible.length > 0 && visible.every((p) => sel.has(p.id))
  const ids = [...sel]
  const inBatch = batchMode !== null

  async function confirmBatch() {
    if (ids.length === 0) return
    if (batchMode === 'archive') {
      const ok = await confirmDialog({
        title: t('pm.archive'),
        message: tpl('pm.archive.confirm', { n: ids.length }),
        confirmText: t('pm.archive'),
      })
      if (!ok) return
    }
    const action: 'group' | 'archive' | 'unarchive' = batchMode === 'move' ? 'group' : batchMode!
    const gid = batchMode === 'move' ? batchGroup : ''
    const groupName = groups.find((g) => g.id === gid)?.name || t('pm.ungrouped')
    await run(() => api.batchProjects(ids, action, gid),
      batchMode === 'archive' ? tpl('pm.archived', { n: ids.length })
        : batchMode === 'unarchive' ? tpl('pm.unarchived', { n: ids.length })
          : tpl('pm.moved', { n: ids.length, name: groupName }))
    setSel(new Set())
    setBatchMode(null)
  }

  async function open(p: { id: string }) {
    await openProject(p.id)
    nav('/create/video/rendering')
  }

  async function del(p: { id: string; title: string }) {
    const name = p.title || p.id
    const ok = await confirmDialog({
      title: t('pm.row.delete'),
      message: tpl('pm.delete.confirm', { name }),
      confirmText: t('btn.delete'),
      danger: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      await api.deleteProject(p.id)
      if (p.id === pid) await boot()
      else await refreshProjects()
      toast(tpl('pm.deleted', { name }), 'ok')
    } catch (e) {
      toast(errText(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  function startRename(p: { id: string; title: string }) {
    setEditing(p.id)
    setEditTitle(p.title || '')
  }

  async function commitRename(p: { id: string; title: string }) {
    const next = editTitle.trim()
    if (!next || next === p.title) { setEditing(null); return }
    await run(() => api.renameProject(p.id, next), tpl('pm.renamed', { name: next }))
    setEditing(null)
    if (p.id === pid) await openProject(pid)
  }

  /** 上移/下移：按当前可见顺序交换，并保存完整顺序 */
  async function move(p: { id: string }, dir: -1 | 1) {
    const i = visible.findIndex((x) => x.id === p.id)
    const j = i + dir
    if (i < 0 || j < 0 || j >= visible.length) return
    const all = projects.map((x) => x.id)
    const a = all.indexOf(p.id)
    const b = all.indexOf(visible[j].id)
    if (a < 0 || b < 0) return
    ;[all[a], all[b]] = [all[b], all[a]]
    await run(() => api.reorderProjects(all), t('pm.reordered'))
  }

  return (
    <div className="page">
      <div className="page-body flush">
        <div className="projects-page pm-layout">
          {/* 左侧分组小列表：点击筛选当前分组 */}
          <aside className="pm-rail scroll">
            <div className="pm-rail-head">{t('pm.rail.title')}</div>
            <button className={`pm-rail-item${activeGroup === 'all' ? ' is-active' : ''}`}
              onClick={() => pickGroup('all')}>
              <span>{t('pm.rail.all')}</span><b>{allCount}</b>
            </button>
            <button className={`pm-rail-item${activeGroup === '__none' ? ' is-active' : ''}`}
              onClick={() => pickGroup('__none')}>
              <span>{t('pm.rail.ungrouped')}</span><b>{noneCount}</b>
            </button>
            {groups.filter((g) => g.id).map((g) => (
              <div className="pm-rail-row" key={g.id}>
                <button className={`pm-rail-item${activeGroup === g.id ? ' is-active' : ''}`}
                  onClick={() => pickGroup(g.id)}>
                  <span>{g.name}</span><b>{groupCount(g.id)}</b>
                </button>
                <button className="pm-rail-x" title={t('pm.group.delete.tip')} disabled={busy}
                  onClick={async () => {
                    const ok = await confirmDialog({
                      title: t('pm.group.delete.tip'),
                      message: tpl('pm.group.delete.confirm', { name: g.name }),
                      confirmText: t('btn.delete'),
                      danger: true,
                    })
                    if (!ok) return
                    void run(async () => { await api.deleteGroup(g.id) }, tpl('pm.group.deleted', { name: g.name }))
                    if (activeGroup === g.id) setActiveGroup('all')
                  }}>×</button>
              </div>
            ))}
            <button className={`pm-rail-item${activeGroup === '__archived' ? ' is-active' : ''}`}
              onClick={() => pickGroup('__archived')}>
              <span>{t('pm.rail.archived')}</span><b>{archivedCount}</b>
            </button>
            <div className="pm-rail-new">
              <input value={newGroup} placeholder={t('pm.new-group.placeholder')}
                onChange={(e) => setNewGroup(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void createGroup() }} />
              <button className="primary" disabled={busy || !newGroup.trim()} onClick={() => void createGroup()}>
                ＋
              </button>
            </div>
          </aside>

          <div className="pm-main">
            <div className="pm-toolbar">
              <input className="pm-search" value={q} placeholder={t('pm.search')}
                onChange={(e) => { setQ(e.target.value); setSel(new Set()) }} />
              <span className="muted pm-count">{tpl('pm.page.sub', { n: projects.length })}</span>
              <span className="spacer" />
              {batchMode === null ? (
                <button className="ghost pm-batch-start" onClick={() => setMode('archive')}>
                  {t('pm.batch.start')}
                </button>
              ) : (
                <div className="pm-mode-switch">
                  <button type="button" className={batchMode === 'move' ? 'is-active' : ''}
                    onClick={() => { setMode('move'); setSel(new Set()) }}>{t('pm.move-to')}</button>
                  <button type="button" className={batchMode === 'archive' ? 'is-active' : ''}
                    onClick={() => { setMode('archive'); setSel(new Set()) }}>{t('pm.archive')}</button>
                  <button type="button" className={batchMode === 'unarchive' ? 'is-active' : ''}
                    onClick={() => { setMode('unarchive'); setSel(new Set()) }}>{t('pm.unarchive')}</button>
                </div>
              )}
            </div>

            {inBatch && (
              <div className="pm-batch-bar">
                <strong>{tpl('pm.selected', { n: ids.length })}</strong>
                <button className="ghost" onClick={() => setSel(new Set(visible.map((p) => p.id)))}
                  disabled={busy || visible.length === 0}>{t('pm.batch.select-all')}</button>
                {batchMode === 'move' && (
                  <select value={batchGroup} onChange={(e) => setBatchGroup(e.target.value)}>
                    <option value="">{t('pm.ungrouped-opt')}</option>
                    {groups.filter((g) => g.id).map((g) => (
                      <option key={g.id} value={g.id}>{g.name}</option>
                    ))}
                  </select>
                )}
                <span className="spacer" />
                <button className="primary"
                  disabled={busy || ids.length === 0 || (batchMode === 'move' && !batchGroup)}
                  onClick={() => void confirmBatch()}>{t('pm.batch.next')}</button>
                <button className="ghost" onClick={() => setMode(null)}>{t('pm.batch.exit')}</button>
              </div>
            )}

            <table className="pm-table">
              <thead>
                <tr>
                  {inBatch && (
                    <th className="pm-sel">
                      <input type="checkbox" checked={allSelected} disabled={busy || visible.length === 0}
                        onChange={(e) => setSel(e.target.checked ? new Set(visible.map((p) => p.id)) : new Set())} />
                    </th>
                  )}
                  <th>{t('pm.col.project')}</th>
                  <th>{t('pm.col.group')}</th>
                  <th className="pm-col-order">{t('pm.col.order')}</th>
                  <th>{t('pm.col.actions')}</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((p, vi) => (
                  <tr key={p.id}
                    className={`${p.id === pid ? 'is-current' : ''}${p.archived ? ' is-archived' : ''}`}>
                    {inBatch && (
                      <td className="pm-sel">
                        <input type="checkbox" checked={sel.has(p.id)} disabled={busy}
                          onChange={() => toggle(p.id)} />
                      </td>
                    )}
                    <td>
                      {editing === p.id ? (
                        <div className="pm-rename">
                          <input
                            autoFocus
                            value={editTitle}
                            onChange={(e) => setEditTitle(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') void commitRename(p)
                              if (e.key === 'Escape') setEditing(null)
                            }}
                          />
                          <button className="primary" disabled={busy || !editTitle.trim()}
                            onClick={() => void commitRename(p)}>{t('pm.row.save')}</button>
                          <button className="ghost" onClick={() => setEditing(null)}>{t('btn.cancel')}</button>
                        </div>
                      ) : (
                        <>
                          <div className="pm-title">{p.title || p.id}</div>
                          <div className="pm-sub muted">{p.id}{p.archived ? ` · ${t('pm.archived-tag')}` : ''}</div>
                        </>
                      )}
                    </td>
                    <td>
                      <select value={p.group_id || ''} disabled={busy}
                        onChange={(e) => {
                          const gid = e.target.value
                          void run(async () => { await api.setProjectGroup(p.id, gid) },
                            tpl('pm.moved', { n: 1, name: groups.find((g) => g.id === gid)?.name || t('pm.ungrouped') }))
                        }}>
                        <option value="">{t('pm.ungrouped-opt')}</option>
                        {groups.filter((g) => g.id).map((g) => (
                          <option key={g.id} value={g.id}>{g.name}</option>
                        ))}
                      </select>
                    </td>
                    <td className="pm-col-order">
                      <div className="pm-order">
                        <button disabled={busy || vi === 0} title={t('pm.row.up')} aria-label={t('pm.row.up')}
                          onClick={() => void move(p, -1)}>↑</button>
                        <button disabled={busy || vi === visible.length - 1} title={t('pm.row.down')} aria-label={t('pm.row.down')}
                          onClick={() => void move(p, 1)}>↓</button>
                      </div>
                    </td>
                    <td>
                      <div className="pm-acts">
                        <button className="pm-act" disabled={busy} onClick={() => void open(p)}>{t('pm.row.open')}</button>
                        <button className="pm-act" disabled={busy} onClick={() => startRename(p)}>{t('pm.row.rename')}</button>
                        <button className="pm-act" disabled={busy} onClick={() => setSettingsFor(p)}>{t('pset.open')}</button>
                        <button className="pm-act" disabled={busy}
                          onClick={() => {
                            const on = !p.archived
                            void run(async () => { await api.setProjectArchived(p.id, on) },
                              on ? tpl('pm.archived', { n: 1 }) : tpl('pm.unarchived', { n: 1 }))
                          }}>{p.archived ? t('pm.unarchive') : t('pm.archive')}</button>
                        <button className="pm-act danger" disabled={busy}
                          onClick={() => void del(p)}>{t('pm.row.delete')}</button>
                      </div>
                    </td>
                  </tr>
                ))}
                {visible.length === 0 && (
                  <tr><td colSpan={inBatch ? 5 : 4} className="muted pm-empty">
                    {projects.length === 0 ? t('pm.empty.none')
                      : activeGroup === '__archived' ? t('pm.empty.archived') : t('pm.empty.active')}
                  </td></tr>
                )}
              </tbody>
            </table>
            <p className="muted pm-note">{t('pm.note')}</p>
          </div>
        </div>
      </div>
          {settingsFor && (
        <ProjectSettingsModal project={settingsFor} onClose={() => setSettingsFor(null)} />
      )}
</div>
  )
}