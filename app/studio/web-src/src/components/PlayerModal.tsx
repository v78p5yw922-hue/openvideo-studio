/** 播放器：合成成片 / 单镜片段共用。 */

import { t } from '../lib/i18n'
import { Modal } from './Modal'
import { VideoPlayer } from './VideoPlayer'
import './PlayerModal.css'

interface Props {
  title: string
  src: string
  meta?: string
  download?: string
  onClose: () => void
}

export function PlayerModal({ title, src, meta, download, onClose }: Props) {
  return (
    <Modal
      title={title}
      onClose={onClose}
      wide
      footer={(
        <>
          {meta && <span className="muted">{meta}</span>}
          <div className="spacer" />
          {download && <a className="player-download" href={download} download>{t('player.download')}</a>}
          <button className="primary" onClick={onClose}>{t('btn.close')}</button>
        </>
      )}
    >
      <div className="player-wrap">
        <VideoPlayer className="player" src={src} controls muted={false} autoPlay loop={false} />
      </div>
    </Modal>
  )
}
