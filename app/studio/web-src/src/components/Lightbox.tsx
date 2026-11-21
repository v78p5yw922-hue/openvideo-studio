/** 大图预览：点遮罩或关闭退出。 */

import { useEffect } from 'react'
import { t } from '../lib/i18n'
import './Lightbox.css'

interface Props {
  src: string
  alt?: string
  onClose: () => void
}

export function Lightbox({ src, alt, onClose }: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="lightbox" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <img src={src} alt={alt || t('asset.lightbox')} />
      <button className="lightbox-close" onClick={onClose}>{t('btn.close')}</button>
    </div>
  )
}
