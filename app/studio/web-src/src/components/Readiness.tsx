/**
 * 就绪状态条：默认只占一行，点「查看」才展开问题清单。
 */

import { useState } from 'react'
import { t, tpl } from '../lib/i18n'
import { useStore } from '../lib/store'
import './Readiness.css'

interface Props {
  onJump: (sid: string) => void
}

export function Readiness({ onJump }: Props) {
  const { validation } = useStore()
  const [open, setOpen] = useState(false)
  if (!validation) return null
  const errors = validation.errors ?? []
  const warnings = validation.warnings ?? []
  const total = errors.length + warnings.length

  if (total === 0) {
    return <div className="readiness ok">{t('ready.ok')}</div>
  }

  return (
    <div className="readiness">
      <button className={`readiness-line ${errors.length ? 'bad' : 'warn'}`} onClick={() => setOpen((o) => !o)}>
        {errors.length ? '⛔' : '⚠'} {errors.length ? tpl('ready.errors', { n: errors.length }) : ''}
        {errors.length && warnings.length ? ' · ' : ''}
        {warnings.length ? tpl('ready.warnings', { n: warnings.length }) : ''}
        <span className="toggle">{open ? t('ready.collapse') : t('ready.view')}</span>
      </button>
      {open && (
        <ul className="readiness-list">
          {[...errors, ...warnings].map((i, n) => (
            <li key={n} className={i.level}>
              {i.shot_id && (
                <button className="link mono" onClick={() => onJump(i.shot_id)}>{i.shot_id}</button>
              )}
              <span className="msg">{i.msg}</span>
              {i.fix && <span className="fix">{tpl('ready.fix', { msg: i.fix })}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
