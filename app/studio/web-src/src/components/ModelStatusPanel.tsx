/**
 * 模型状态小面板 —— 扫描 ComfyUI/models 下两个文生图文件（只读）。
 * 用户可能在外部下载，所以面板只报告进度，不提供下载/取消。
 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import type { ModelFileStatus } from '../lib/types'

function human(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(0)} MB`
  return `${bytes} B`
}

export function ModelStatusPanel() {
  const [models, setModels] = useState<ModelFileStatus[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    let timer = 0

    async function tick() {
      try {
        const r = await api.modelsStatus()
        if (!alive) return
        setModels(r.models)
        setError('')
      } catch (e) {
        if (!alive) return
        setError(e instanceof Error ? e.message : t('ms.read-failed'))
      }
    }

    void tick()
    // 下载中每 5 秒刷新；用 setTimeout 串行，避免请求堆积
    const loop = () => {
      timer = window.setTimeout(async () => {
        await tick()
        if (alive) loop()
      }, 5000)
    }
    loop()

    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [])

  const allReady = models.length > 0 && models.every((m) => m.state === 'ready')
  const anyDownloading = models.some((m) => m.state === 'downloading')
  const totalNeeded = models.reduce(
    (a, m) => a + Math.max(0, m.bytes - m.bytes_on_disk), 0)

  return (
    <div className="model-status" aria-label={t('ms.title')}>
      <div className={`ms-indicator ms-${allReady ? 'ready' : anyDownloading ? 'dl' : 'missing'}`}>
        {allReady ? t('ms.ready') : anyDownloading ? t('ms.downloading') : t('ms.not-ready')}
      </div>
      <div className="ms-list">
        {models.map((m) => (
          <div key={m.key} className={`ms-row ms-${m.state}`}>
            <span className="ms-dot" aria-hidden />
            <span className="ms-label">{m.file}</span>
            <span className="muted ms-size">
              {m.state === 'ready'
                ? human(m.bytes_on_disk || m.bytes)
                : `${human(m.bytes_on_disk)} / ${human(m.bytes)}`}
            </span>
            {m.state === 'downloading' && (
              <>
                <div className="ms-bar">
                  <div className="ms-bar-fill" style={{ width: `${m.pct}%` }} />
                </div>
                <span className="ms-pct muted">{m.pct}%</span>
              </>
            )}
          </div>
        ))}
        {models.length === 0 && !error && <div className="muted">{t('ms.reading')}</div>}
        {error && <div className="ms-error">{error}</div>}
      </div>
      {!allReady && (
        <div className="ms-hint muted">
          {anyDownloading
            ? tpl('ms.remaining', { size: human(totalNeeded) })
            : t('ms.missing')}
        </div>
      )}
    </div>
  )
}
