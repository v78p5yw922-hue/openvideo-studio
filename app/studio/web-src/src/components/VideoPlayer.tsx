/**
 * VideoPlayer —— 可复用的视频组件。
 *
 * 使用场景：
 *  - hoverPlay：镜头卡里鼠标移入自动播放、移出暂停并回到第一帧
 *  - controls：版本弹窗 / 播放器里显示原生控件
 */

import { forwardRef, useRef } from 'react'
import './VideoPlayer.css'

interface Props {
  src: string
  poster?: string
  controls?: boolean
  autoPlay?: boolean
  loop?: boolean
  muted?: boolean
  playsInline?: boolean
  /** 鼠标移入播放、移出暂停（镜头卡用） */
  hoverPlay?: boolean
  className?: string
  onClick?: (e: React.MouseEvent<HTMLVideoElement>) => void
}

export const VideoPlayer = forwardRef<HTMLVideoElement, Props>(function VideoPlayer({
  src, poster, controls = false, autoPlay = false, loop = true, muted = true,
  playsInline = true, hoverPlay = false, className, onClick,
}, forwardedRef) {
  const innerRef = useRef<HTMLVideoElement | null>(null)

  function setRef(node: HTMLVideoElement | null) {
    innerRef.current = node
    if (typeof forwardedRef === 'function') forwardedRef(node)
    else if (forwardedRef) forwardedRef.current = node
  }

  function play() {
    if (!hoverPlay) return
    const v = innerRef.current
    if (!v) return
    void v.play().catch(() => undefined)
  }

  function pause() {
    if (!hoverPlay) return
    const v = innerRef.current
    if (!v) return
    v.pause()
    try { v.currentTime = 0 } catch { /* 元数据未就绪时忽略 */ }
  }

  return (
    <video
      ref={setRef}
      className={`video-player${className ? ` ${className}` : ''}`}
      src={src}
      poster={poster}
      controls={controls}
      muted={muted}
      loop={loop}
      playsInline={playsInline}
      autoPlay={autoPlay}
      preload="metadata"
      disablePictureInPicture
      controlsList="nodownload noplaybackrate"
      onMouseEnter={play}
      onMouseLeave={pause}
      onClick={onClick}
    />
  )
})
