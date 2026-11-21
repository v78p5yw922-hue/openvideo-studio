/** 提示词编辑：草稿与真实 H3 编译结果并排，预览只读且不启动渲染。 */

import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { saveShotFields, useStore } from '../lib/store'
import type { CompilePreviewResponse } from '../lib/types'
import { Modal } from './Modal'
import './PromptEditorModal.css'

interface Props {
  sid: string
  onClose: () => void
}

export function PromptEditorModal({ sid, onClose }: Props) {
  const { detail } = useStore()
  const pid = detail?.project.id
  const shot = detail?.shots.find((s) => s.shot_id === sid)
  const shotPrompt = shot?.prompt
  const [text, setText] = useState(shot?.prompt ?? '')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<CompilePreviewResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const requestSeq = useRef(0)

  useEffect(() => {
    if (!pid || shotPrompt === undefined) return
    const id = ++requestSeq.current
    setLoading(true)
    setPreviewError('')
    // 打开时立即编译；编辑草稿后防抖 300ms。StrictMode 重跑也仍是立即执行。
    const timer = window.setTimeout(() => {
      api.compilePreview(pid, sid, text)
        .then((data) => {
          if (requestSeq.current !== id) return
          setReport(data)
        })
        .catch((error: unknown) => {
          if (requestSeq.current !== id) return
          setReport(null)
          setPreviewError(error instanceof Error ? error.message : t('pe.unknown-error'))
        })
        .finally(() => {
          if (requestSeq.current === id) setLoading(false)
        })
    }, text === shotPrompt ? 0 : 300)
    return () => {
      window.clearTimeout(timer)
      if (requestSeq.current === id) requestSeq.current += 1
    }
  }, [pid, sid, text, shotPrompt])

  if (!shot || !pid) return null

  return (
    <Modal
      title={tpl('pe.title', { sid })}
      onClose={onClose}
      wide
      footer={(
        <>
          <span className="muted">{tpl('pe.chars', { n: text.length })}</span>
          <div className="spacer" />
          <button onClick={onClose}>{t('pe.cancel')}</button>
          <button className="primary" disabled={busy || text === shot.prompt} onClick={async () => {
            setBusy(true)
            await saveShotFields(sid, { prompt: text })
            setBusy(false)
            onClose()
          }}>{busy ? t('pe.saving') : t('pe.save')}</button>
        </>
      )}
    >
      <textarea
        className="prompt-editor"
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        placeholder={t('pe.placeholder')}
      />
      <section className="compile-panel" aria-label={t('pe.preview')}>
        <header className="compile-header">
          <strong>{t('pe.compile')} <span className="compile-readonly">{t('pe.readonly')}</span></strong>
          <span className="compile-status" role="status">
            {loading ? t('pe.compiling') : report ? `${report.elapsed_ms.toFixed(1)} ms` : ''}
          </span>
        </header>
        <div className="compile-legend" aria-label="legend">
          <span className="compile-key zh">{t('pe.legend.zh')}</span>
          <span className="compile-key en">{t('pe.legend.en')}</span>
          <span className="compile-key meta">{t('pe.legend.meta')}</span>
        </div>

        {previewError && (
          <div className="compile-request-error" role="alert">
            {tpl('pe.request-failed', { err: previewError })}
          </div>
        )}

        {report && report.errors.length > 0 && (
          <div className="compile-issues" role="alert">
            <strong>{tpl('pe.issues', { n: report.errors.length })}</strong>
            {report.errors.map((error, index) => (
              <div className="compile-issue" key={`${error.code}-${error.who}-${error.field}-${index}`}>
                <div>
                  【{error.slot}】{error.who && `${error.who} · `}{error.field}：{error.message}
                </div>
                {error.allowed.length > 0 && (
                  <div className="compile-allowed">{tpl('pe.allowed', { list: error.allowed.join(' / ') })}</div>
                )}
              </div>
            ))}
          </div>
        )}

        {report && report.dropped.length > 0 && (
          <div className="compile-dropped">
            {report.dropped.map((item, index) => (
              <div key={`${item.slot}-${index}`} title={item.source ? tpl('pe.source', { src: item.source }) : undefined}>
                {tpl('pe.dropped', { slot: item.slot, reason: item.reason })}
              </div>
            ))}
          </div>
        )}

        {report && !previewError && (
          <div className="compile-preview-text" aria-label={t('pe.final')}>
            {report.segments.map((segment, index) => (
              <span
                key={index}
                className={`compile-segment ${segment.kind}`}
                title={segment.source ? `${segment.slot}：${segment.source}` : segment.slot}
              >{segment.text}</span>
            ))}
          </div>
        )}
      </section>
    </Modal>
  )
}
