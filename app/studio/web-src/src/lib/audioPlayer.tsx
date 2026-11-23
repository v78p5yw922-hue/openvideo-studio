/**
 * 全局音频播放器：同一时间只允许一个音频在播。
 *
 * 用法：
 *   const { playing, toggle } = useAudioPlayer()
 *   <AudioBar url={c.url} label={...} />
 *
 * 原来每个 <audio controls> 都是独立元素，点两个就同时响；现在共用同一个
 * HTMLAudioElement，换源即停旧的。
 */

import { useEffect, useState } from 'react'
import './AudioBar.css'

let el: HTMLAudioElement | null = null
let currentUrl = ''
const listeners = new Set<() => void>()

function ensure(): HTMLAudioElement {
  if (!el) {
    el = new Audio()
    el.preload = 'metadata'
    el.addEventListener('play', notify)
    el.addEventListener('pause', notify)
    el.addEventListener('ended', notify)
    el.addEventListener('timeupdate', notify)
    el.addEventListener('loadedmetadata', notify)
  }
  return el
}

function notify() {
  listeners.forEach((fn) => fn())
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export function playUrl(url: string): void {
  if (!url) return
  const a = ensure()
  if (currentUrl !== url) {
    a.src = url
    currentUrl = url
  }
  void a.play().catch(() => undefined)
  notify()
}

export function toggleUrl(url: string): void {
  const a = ensure()
  if (currentUrl === url && !a.paused) {
    a.pause()
  } else {
    playUrl(url)
  }
  notify()
}

export function stopAll(): void {
  if (el) el.pause()
  notify()
}

export function useAudioPlayer() {
  const [tick, setTick] = useState(0)
  useEffect(() => subscribe(() => setTick((n) => n + 1)), [])
  void tick
  const a = el
  const playingUrl = a && !a.paused ? currentUrl : ''
  const time = a?.currentTime || 0
  const dur = Number.isFinite(a?.duration) ? (a?.duration as number) : 0
  return { playingUrl, time, dur, currentUrl, playUrl, toggleUrl, stopAll }
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export function AudioBar({ url, compact }: { url: string; compact?: boolean }) {
  const { playingUrl, time, dur, toggleUrl } = useAudioPlayer()
  const mine = playingUrl === url
  const pct = mine && dur > 0 ? Math.min(100, (time / dur) * 100) : 0

  if (!url) {
    return <span className="audiobar audiobar-empty">无音频</span>
  }
  return (
    <div className={`audiobar${compact ? ' is-compact' : ''}`}>
      <button className="audiobar-btn" onClick={() => toggleUrl(url)}
              aria-label={mine ? '暂停' : '播放'}>
        {mine ? '❚❚' : '▶'}
      </button>
      <div className="audiobar-bar"><i style={{ width: `${pct}%` }} /></div>
      <span className="audiobar-time">{mine ? `${fmt(time)} / ${fmt(dur)}` : fmt(dur)}</span>
    </div>
  )
}
