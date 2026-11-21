/**
 * SearchSelect —— 可搜索的下拉选择。
 * 用于「加角色 / 选场景」这类会越来越长的列表。
 */

import { useMemo, useRef, useState } from 'react'
import { usePopover } from '../lib/usePopover'
import './SearchSelect.css'

export interface SearchOption {
  value: string
  label: string
  hint?: string
}

interface Props {
  options: SearchOption[]
  placeholder: string
  onPick: (value: string) => void
  disabled?: boolean
  /** 触发器样式 */
  className?: string
}

export function SearchSelect({ options, placeholder, onPick, disabled, className }: Props) {
  const pop = usePopover()
  const [q, setQ] = useState('')
  const [up, setUp] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)

  function open() {
    setQ('')
    const box = triggerRef.current?.getBoundingClientRect()
    // 下方空间不足时向上弹，避免被窗口/滚动容器裁掉
    setUp(!!box && window.innerHeight - box.bottom < 300 && box.top > 300)
    pop.toggle()
  }

  function pick(v: string) {
    onPick(v)
    setQ('')
    pop.close()
  }

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase()
    if (!kw) return options
    return options.filter(
      (o) => o.label.toLowerCase().includes(kw) || o.value.toLowerCase().includes(kw),
    )
  }, [options, q])

  return (
    <div className={`search-select ${className ?? ''}`} ref={pop.ref}>
      <button
        ref={triggerRef}
        type="button"
        className="search-select-trigger"
        disabled={disabled}
        onClick={open}
      >
        <span>{placeholder}</span>
        <span className="caret">{pop.open ? '︿' : '﹀'}</span>
      </button>
      {pop.open && (
        <div className={`search-select-panel${up ? ' is-up' : ''}`}>
          <input
            autoFocus
            value={q}
            placeholder={placeholder}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') pop.close()
              if (e.key === 'Enter' && filtered.length > 0) pick(filtered[0].value)
            }}
          />
          <div className="search-select-list">
            {filtered.length === 0 && <div className="search-select-empty">—</div>}
            {filtered.map((o) => (
              <button key={o.value} type="button" className="search-select-option" onClick={() => pick(o.value)}>
                <span className="ss-label">{o.label}</span>
                {o.hint && <span className="ss-hint">{o.hint}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
