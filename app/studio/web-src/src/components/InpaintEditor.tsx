/**
 * 图片去水印编辑器：在图上框选一个或多个区域，调用后端 OpenCV inpaint 擦除。
 * - 不改变分辨率（坐标按 naturalWidth 缩放回图像像素）
 * - 可反复擦除：擦完显示结果，可继续框选下一轮
 * - 最终「确认使用」把处理后的 File 交给上传流程；「使用原图」则跳过处理
 */

import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { inpaintImage } from '../lib/api'
import { t } from '../lib/i18n'
import { toast } from '../lib/store'
import './InpaintEditor.css'

interface Box { x: number; y: number; w: number; h: number }

interface Props {
  file: File
  onConfirm: (file: File) => void
  onCancel: () => void
}

export function InpaintEditor({ file, onConfirm, onCancel }: Props) {
  const [current, setCurrent] = useState<File>(file)
  const [url, setUrl] = useState('')
  const [boxes, setBoxes] = useState<Box[]>([])
  const [busy, setBusy] = useState(false)
  const imgRef = useRef<HTMLImageElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const drawing = useRef<{ x: number; y: number } | null>(null)

  useEffect(() => {
    const u = URL.createObjectURL(current)
    setUrl(u)
    return () => URL.revokeObjectURL(u)
  }, [current])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  function toImgCoords(b: Box): Box {
    const img = imgRef.current
    if (!img || !img.clientWidth) return b
    const scale = img.naturalWidth / img.clientWidth
    return {
      x: Math.round(b.x * scale), y: Math.round(b.y * scale),
      w: Math.round(b.w * scale), h: Math.round(b.h * scale),
    }
  }

  function pointerPos(e: React.PointerEvent): { x: number; y: number } {
    const wrap = wrapRef.current
    if (!wrap) return { x: 0, y: 0 }
    const r = wrap.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }

  function onDown(e: React.PointerEvent) {
    if (busy) return
    const p = pointerPos(e)
    drawing.current = p
    setBoxes((bs) => [...bs, { x: p.x, y: p.y, w: 0, h: 0 }])
  }
  function onMove(e: React.PointerEvent) {
    if (!drawing.current) return
    const p = pointerPos(e)
    const s = drawing.current
    setBoxes((bs) => {
      if (!bs.length) return bs
      const next = [...bs]
      next[next.length - 1] = {
        x: Math.min(s.x, p.x), y: Math.min(s.y, p.y),
        w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y),
      }
      return next
    })
  }
  function onUp() { drawing.current = null }

  async function erase() {
    if (!boxes.length || busy) return
    setBusy(true)
    try {
      const imgBoxes = boxes.map(toImgCoords).filter((b) => b.w > 2 && b.h > 2)
      if (!imgBoxes.length) {
        toast(t('inpaint.no-box'), 'err')
        return
      }
      const blob = await inpaintImage(current, imgBoxes)
      const base = current.name.replace(/\.[^.]+$/, '')
      const cleaned = new File([blob], `${base}_clean.png`, { type: 'image/png' })
      setCurrent(cleaned)
      setBoxes([])
      toast(t('inpaint.erased'), 'ok')
    } catch (err) {
      toast((err as Error).message || t('inpaint.failed'), 'err')
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div className="inpaint-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="inpaint-card">
        <div className="inpaint-head">
          <strong>{t('inpaint.title')}</strong>
          <button type="button" onClick={onCancel} aria-label={t('btn.close')}>✕</button>
        </div>
        <div className="inpaint-body">
          <div
            className="inpaint-canvas"
            ref={wrapRef}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerLeave={onUp}
          >
            {url && <img ref={imgRef} src={url} alt="" draggable={false} />}
            {boxes.map((b, i) => (
              <div key={i} className="inpaint-box"
                style={{ left: b.x, top: b.y, width: b.w, height: b.h }} />
            ))}
          </div>
          <p className="inpaint-hint muted">{t('inpaint.hint')}</p>
        </div>
        <div className="inpaint-actions">
          <button type="button" onClick={() => setBoxes([])}
            disabled={busy || !boxes.length}>{t('inpaint.clear')}</button>
          <button type="button" onClick={() => setBoxes((bs) => bs.slice(0, -1))}
            disabled={busy || !boxes.length}>{t('inpaint.undo')}</button>
          <button type="button" className="primary" onClick={() => void erase()}
            disabled={busy || !boxes.length}>
            {busy ? t('inpaint.erasing') : t('inpaint.erase')}
          </button>
          <span className="spacer" />
          <button type="button" onClick={() => onConfirm(file)}>{t('inpaint.use-original')}</button>
          <button type="button" className="primary" onClick={() => onConfirm(current)}
            disabled={busy}>{t('inpaint.confirm')}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
