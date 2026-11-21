/** 项目设置弹窗：风格串 / 分辨率 / 时长上限 / 词库附加词 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { toast } from '../lib/store'
import type { Project } from '../lib/types'
import { Modal } from './Modal'

type Settings = {
  style: string
  width: number
  height: number
  max_duration: number
  glossary_extra: string[]
}

export function ProjectSettingsModal({ project, onClose }: {
  project: Project
  onClose: () => void
}) {
  const [s, setS] = useState<Settings | null>(null)
  const [extra, setExtra] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.projectSettings(project.id)
      .then((d) => {
        const v = d as unknown as Settings
        setS({ ...v, glossary_extra: v.glossary_extra || [] })
        setExtra((v.glossary_extra || []).join('、'))
      })
      .catch((e) => toast(String(e), 'err'))
  }, [project.id])

  const save = async () => {
    if (!s) return
    setBusy(true)
    try {
      const list = extra.split(/[、,，\s]+/).map((x) => x.trim()).filter(Boolean)
      await api.saveProjectSettings(project.id, { ...s, glossary_extra: list })
      toast(t('pset.saved'), 'ok')
      onClose()
    } catch (e) {
      toast(String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={t('pset.title')} onClose={onClose}>
      {!s ? (
        <div className="muted">{t('settings.loading')}</div>
      ) : (
        <div className="pset">
          <label>
            <span>{t('pset.style')}</span>
            <textarea
              rows={3}
              value={s.style}
              onChange={(e) => setS({ ...s, style: e.target.value })}
              placeholder="2D anime, cel-shaded, clean line art, ..."
            />
          </label>

          <div className="pset-row">
            <label>
              <span>{t('pset.width')}</span>
              <input type="number" value={s.width}
                     onChange={(e) => setS({ ...s, width: Number(e.target.value) })} />
            </label>
            <label>
              <span>{t('pset.height')}</span>
              <input type="number" value={s.height}
                     onChange={(e) => setS({ ...s, height: Number(e.target.value) })} />
            </label>
            <label>
              <span>{t('pset.maxdur')}</span>
              <select value={s.max_duration}
                      onChange={(e) => setS({ ...s, max_duration: Number(e.target.value) })}>
                <option value={5}>5s</option>
                <option value={8}>8s</option>
                <option value={10}>10s</option>
              </select>
            </label>
          </div>

          <label>
            <span>{t('pset.extra')}</span>
            <input value={extra} onChange={(e) => setExtra(e.target.value)}
                   placeholder={t('pset.extra.placeholder')} />
          </label>

          <div className="pset-actions">
            <button className="ghost" onClick={onClose}>{t('btn.cancel')}</button>
            <button className="btn" disabled={busy} onClick={() => void save()}>
              {busy ? t('btn.saving') : t('btn.save')}
            </button>
          </div>
        </div>
      )}
    </Modal>
  )
}
