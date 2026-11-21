/** 折叠分组：检查器用，默认只展开最常改的两段。 */

import { useState, type ReactNode } from 'react'

interface Props {
  title: string
  defaultOpen?: boolean
  actions?: ReactNode
  children: ReactNode
}

export function Accordion({ title, defaultOpen = false, actions, children }: Props) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="acc">
      <div className="acc-head">
        <button
          type="button"
          className="acc-toggle"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          <span className="chev">{open ? '▾' : '▸'}</span>
          {title}
        </button>
        {actions}
      </div>
      {open && <div className="acc-body">{children}</div>}
    </section>
  )
}
