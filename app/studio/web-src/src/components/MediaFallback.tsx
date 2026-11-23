/** 媒体占位：图片/视频/音频加载失败时显示，不报错、不白屏。 */

import { useState } from 'react'
import { t } from '../lib/i18n'

export function SmartImg({ src, alt, className, onError }: {
  src?: string; alt?: string; className?: string; onError?: () => void
}) {
  const [bad, setBad] = useState(false)
  if (!src || bad) {
    return (
      <div className={`media-ph${className ? ' ' + className : ''}`} title={t('media.missing')}>
        <span>🖼</span>
        <em>{t('media.missing')}</em>
      </div>
    )
  }
  return (
    <img src={src} alt={alt || ''} className={className} loading="lazy"
         onError={() => { setBad(true); onError?.() }} />
  )
}

export function SmartAudio({ url }: { url?: string }) {
  if (!url) return <span className="media-ph inline">{t('media.no-audio')}</span>
  return null
}
