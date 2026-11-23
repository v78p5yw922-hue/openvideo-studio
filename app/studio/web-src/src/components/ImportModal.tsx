/**
 * 导入剧本：先预览（不写库），确认破坏性变化后才真正导入。
 * 预览会显示新增/删除/失效的镜头，避免"直接覆盖"。
 */

import { useEffect, useRef, useState } from 'react'
import { api, isDestructive } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { errText, openProject, toast, useStore } from '../lib/store'
import type { ImportPreview, ProjectSummary } from '../lib/types'
import { Modal } from './Modal'
import './ImportModal.css'

interface Props {
  onClose: () => void
}

/** 生成一个不与现有项目冲突的新 ID */
function newProjectId(existing: Set<string>): string {
  let id = `p_${Date.now().toString(36)}`
  while (existing.has(id)) id += 'x'
  return id
}

export function ImportModal({ onClose }: Props) {
  const { pid, groups } = useStore()
  const [projectId, setProjectId] = useState(pid ?? '')
  const [markdown, setMarkdown] = useState('')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [armed, setArmed] = useState(false)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [title, setTitle] = useState('')
  const [groupId, setGroupId] = useState('')
  const [newGroupName, setNewGroupName] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.listProjects()
      .then(r => setProjects(r.projects))
      .catch(() => setProjects([]))
  }, [])

  const existing = projects.find(p => p.id === projectId.trim())

  async function doPreview() {
    if (!projectId.trim() || !markdown.trim()) return
    setBusy(true)
    setArmed(false)
    try {
      const p = await api.previewImport(projectId.trim(), markdown)
      setPreview(p)
    } catch (e) {
      toast(tpl('import.preview-failed', { err: errText(e) }), 'err')
    } finally {
      setBusy(false)
    }
  }

  async function doImport() {
    if (!preview) return
    const destructive = isDestructive(preview)
    if (destructive && !armed) {
      setArmed(true)
      return
    }
    setBusy(true)
    try {
      const r = await api.importMarkdown(
        projectId.trim(), markdown,
        groupId === '__new__' ? '' : groupId,
        groupId === '__new__'
          ? (newGroupName.trim() || title.trim() || preview.title || '')
          : (groupId ? '' : (title.trim() || preview.title || '')),
      )
      // 用户填了项目名就以它为准（剧本里的片名可能是旧的）
      const wanted = title.trim()
      if (wanted && wanted !== r.title) {
        try {
          await api.renameProject(r.project_id, wanted)
        } catch {
          toast(t('import.rename-failed'), 'err')
        }
      }
      await openProject(r.project_id)
      toast(tpl('import.done', {
        action: r.updated ? t('import.updated') : t('import.imported'),
        title: r.title,
        n: r.shots.length,
      }), 'ok')
      onClose()
    } catch (e) {
      toast(tpl('import.failed', { err: errText(e) }), 'err')
    } finally {
      setBusy(false)
    }
  }

  const destructive = preview ? isDestructive(preview) : false

  return (
    <Modal
      title={t('import.title')}
      onClose={onClose}
      wide
      footer={(
        <>
          <span className="spacer" />
          <button onClick={onClose}>{t('import.cancel')}</button>
          <button disabled={busy || !markdown.trim()} onClick={() => void doPreview()}>
            {t('import.preview')}
          </button>
          <button
            className={armed ? 'danger-solid' : 'primary'}
            disabled={busy || !preview}
            onClick={() => void doImport()}
          >
            {armed ? t('import.confirm-overwrite')
              : destructive ? t('import.import-need-confirm') : t('import.import')}
          </button>
        </>
      )}
    >
      <div className="form-rows">
        <div className="form-row">
          <label className="form-label" htmlFor="import-pid">{t('import.project-id')}</label>
          <div className="form-control">
            <input id="import-pid" className="form-input" value={projectId}
              onChange={(e) => { setProjectId(e.target.value); setPreview(null); setArmed(false) }}
              placeholder={t('import.project-id.placeholder')} />
            <button className="ghost sm nowrap"
              onClick={() => {
                setProjectId(newProjectId(new Set(projects.map(p => p.id))))
                setPreview(null)
                setArmed(false)
              }}>＋ {t('import.new-project')}</button>
            {pid && projectId !== pid && (
              <button className="ghost sm nowrap"
                onClick={() => { setProjectId(pid); setPreview(null); setArmed(false) }}>
                {t('import.back-current')}
              </button>
            )}
          </div>
        </div>

        <div className="form-row">
          <label className="form-label" htmlFor="import-group">{t('import.group')}</label>
          <div className="form-control">
            <select id="import-group" className="form-select"
              value={groupId} onChange={(e) => setGroupId(e.target.value)}>
              <option value="">＋ {t('import.group-auto')}</option>
              {groups.filter((g) => g.id).map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
              <option value="__new__">＋ {t('import.group-new')}</option>
            </select>
            {groupId === '__new__' && (
              <input className="form-input" value={newGroupName}
                placeholder={t('import.group-name.placeholder')}
                onChange={(e) => setNewGroupName(e.target.value)} />
            )}
          </div>
        </div>

        <div className="form-row">
          <label className="form-label" htmlFor="import-title">{t('import.name')}</label>
          <div className="form-control">
            <input id="import-title" className="form-input" value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={preview?.title
                ? tpl('import.name.placeholder-known', { title: preview.title })
                : t('import.name.placeholder')} />
          </div>
        </div>

        <div className="form-row">
          <span className="form-label">{t('import.md-label')}</span>
          <div className="form-control form-actions">
            <button className="ghost sm" onClick={() => fileRef.current?.click()}>
              {t('import.choose-file')}
            </button>
            <input ref={fileRef} type="file" accept=".md,.markdown,.txt" hidden
              onChange={async (e) => {
                const f = e.target.files?.[0]
                if (f) setMarkdown(await f.text())
                e.target.value = ''
              }} />
            <span className="muted sm">{t('import.md-hint')}</span>
          </div>
        </div>
      </div>

      <textarea
        className="import-md"
        value={markdown}
        onChange={(e) => { setMarkdown(e.target.value); setPreview(null); setArmed(false) }}
        placeholder={t('import.md-placeholder')}
        spellCheck={false}
      />

      {markdown.trim() && !preview && (
        <p className="muted">{t('import.preview-hint')}</p>
      )}

      {preview && (
        <div className="preview-box">
          <div className="preview-title">
            {t('import.preview-title')} <span className="muted">{tpl('import.preview-writing', { id: projectId.trim() })}</span>
          </div>
          <div className="preview-counts">
            <span>{tpl('import.preview.total', { n: preview.shots })}</span>
            <span>{tpl('import.preview.added', { n: preview.added.length })}</span>
            <span>{tpl('import.preview.kept', { n: preview.kept.length })}</span>
            <span>{tpl('import.preview.changed', { n: preview.changed.length })}</span>
            <span>{tpl('import.preview.removed', { n: preview.removed.length })}</span>
          </div>
          {preview.warnings.length > 0 && (
            <ul className="warn-list">
              {preview.warnings.slice(0, 6).map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          )}
          {destructive && (
            <div className="notice bad">
              {tpl('import.destructive', { removed: preview.removed.length, changed: preview.changed.length })}
              {armed && <b>{t('import.destructive-armed')}</b>}
            </div>
          )}
        </div>
      )}

      <p className="muted">{t('import.note')}</p>
    </Modal>
  )
}
