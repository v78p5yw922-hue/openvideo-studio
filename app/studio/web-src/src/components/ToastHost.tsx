/** Toast 队列：右下角堆叠，点一下就消。 */

import { dismissToast, useStore } from '../lib/store'
import './ToastHost.css'

export function ToastHost() {
  const { toasts } = useStore()
  if (!toasts.length) return null
  return (
    <div className="toast-host" role="status" aria-live="polite">
      {toasts.map((t) => (
        <button key={t.id} className={`toast ${t.kind}`} onClick={() => dismissToast(t.id)}>
          {t.text}
        </button>
      ))}
    </div>
  )
}
