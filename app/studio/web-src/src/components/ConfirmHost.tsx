/** 全局确认框宿主：挂在 App 根部一次即可。 */

import { useEffect } from 'react'
import { t } from '../lib/i18n'
import { useConfirm } from '../lib/confirm'
import { Modal } from './Modal'

export function ConfirmHost() {
  const { open, title, message, confirmText, cancelText, danger, settle } = useConfirm()

  // 确认框打开时，让 Modal 自己处理 Esc；这里只兜住 Enter
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Enter') settle(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, settle])

  if (!open) return null

  return (
    <Modal
      title={title}
      onClose={() => settle(false)}
      footer={(
        <>
          <span className="spacer" />
          {cancelText !== '' && (
            <button onClick={() => settle(false)}>{cancelText || t('btn.cancel')}</button>
          )}
          <button className={danger ? 'danger-solid' : 'primary'} onClick={() => settle(true)} autoFocus>
            {confirmText || t('btn.confirm')}
          </button>
        </>
      )}
    >
      {message && <p className="confirm-message">{message}</p>}
    </Modal>
  )
}
