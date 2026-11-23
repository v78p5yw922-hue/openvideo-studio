/** 合成页的音频面板：BGM / 环境声，模型生成 / 自定义 / 不铺 + 试听。 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { confirmDialog } from '../lib/confirm'
import { toast, useStore } from '../lib/store'
import { AudioBar } from '../lib/audioPlayer'
import './AudioPanel.css'

type Kind = 'bgm' | 'ambience'
type Mode = 'off' | 'auto' | 'file'

interface Cfg {
  mode: Mode
  path?: string
  gain_db?: number
  duck_under_voice?: boolean
}

interface AudioCfg {
  bgm: Cfg
  ambience: Cfg
}

const DEFAULTS: AudioCfg = {
  bgm: { mode: 'auto', gain_db: -18, duck_under_voice: true },
  ambience: { mode: 'auto', gain_db: -26 },
}

export function AudioPanel() {
  const { pid } = useStore()
  const [cfg, setCfg] = useState<AudioCfg>(DEFAULTS)
  const [status, setStatus] = useState<{ running?: boolean; log?: string; error?: string;
    exists?: Record<string, boolean>;
    in_use?: Record<string, { mode?: string; applied?: boolean; reason?: string; path?: string }> }>({})
  const [busy, setBusy] = useState<Kind | ''>('')
  const [previewUrl, setPreviewUrl] = useState('')
  const [assets, setAssets] = useState<Array<{ id: string; prompt: string; url?: string;
    path?: string; project_id?: string; params?: Record<string, unknown> }>>([])
  const [pathInput, setPathInput] = useState('')
  const [browse, setBrowse] = useState<{ path: string; dirs: Array<{ name: string; path: string }>;
    files: Array<{ name: string; path: string }> } | null>(null)

  async function load() {
    if (!pid) return
    try {
      const r = await api.getAudio(pid)
      setCfg({ ...DEFAULTS, ...(r.audio as unknown as AudioCfg) })
      const st = await api.audioStatus(pid)
      setStatus(st)
      const c = await api.assetCandidates()
      setAssets((c.candidates || []).filter((x) => x.kind === 'music') as never)
    } catch { /* 忽略 */ }
  }

  useEffect(() => { void load() }, [pid])

  async function save(next: AudioCfg) {
    if (!pid) return
    setCfg(next)
    try {
      await api.putAudio(pid, next)
    } catch (e) {
      toast(tpl('audio.save-failed', { err: String(e) }), 'err')
    }
  }

  async function generate(kind: Kind) {
    if (!pid) return
    setBusy(kind)
    try {
      await api.generateBed(pid, kind)
      toast(t('audio.started'), 'ok')
      const timer = setInterval(async () => {
        const st = await api.audioStatus(pid)
        setStatus(st)
        if (!st.running) {
          clearInterval(timer)
          setBusy('')
          if (st.error) toast(tpl('audio.failed', { err: st.error }), 'err')
          else { toast(t('audio.done'), 'ok'); await load() }
        }
      }, 4000)
    } catch (e) {
      setBusy('')
      toast(tpl('audio.start-failed', { err: String(e) }), 'err')
    }
  }

  async function upload(kind: Kind, file: File) {
    if (!pid) return
    try {
      await api.uploadBed(pid, kind, file)
      toast(t('audio.uploaded'), 'ok')
      await load()
    } catch (e) {
      toast(tpl('audio.upload-failed', { err: String(e) }), 'err')
    }
  }

  async function removeBed(kind: Kind) {
    if (!pid) return
    const ok = await confirmDialog({
      title: t('audio.delete'),
      message: tpl('audio.delete-confirm', { kind: kind === 'bgm' ? t('audio.bgm') : t('audio.ambience') }),
      confirmText: t('audio.delete'),
      danger: true,
    })
    if (!ok) return
    try {
      await api.deleteBed(pid, kind)
      toast(t('audio.deleted'), 'ok')
      await load()
    } catch (e) {
      toast(tpl('audio.delete-failed', { err: String(e) }), 'err')
    }
  }

  async function useAsset(kind: Kind, assetId: string) {
    if (!pid) return
    const a = assets.find((x) => x.id === assetId)
    if (!a?.path) { toast(t('audio.no-path'), 'err'); return }
    try {
      await api.useAudioPath(pid, kind, a.path)
      toast(t('audio.picked').replace('{kind}', kind === 'bgm' ? t('audio.bgm') : t('audio.ambience')), 'ok')
      await load()
    } catch (e) {
      toast(String(e), 'err')
    }
  }

  async function usePath(kind: Kind) {
    if (!pid || !pathInput.trim()) return
    try {
      await api.useAudioPath(pid, kind, pathInput.trim())
      toast(t('audio.picked').replace('{kind}', kind === 'bgm' ? t('audio.bgm') : t('audio.ambience')), 'ok')
      await load()
    } catch (e) {
      toast(String(e), 'err')
    }
  }

  async function browseDir(path: string) {
    try {
      setBrowse(await api.systemAudioFiles(path))
    } catch (e) {
      toast(String(e), 'err')
    }
  }

  async function preview() {
    if (!pid) return
    try {
      const r = await api.audioPreview(pid)
      setPreviewUrl(r.url)
    } catch (e) {
      toast(tpl('audio.preview-failed', { err: String(e) }), 'err')
    }
  }

  function setMode(kind: Kind, mode: Mode) {
    void save({ ...cfg, [kind]: { ...cfg[kind], mode } })
  }

  function setGain(kind: Kind, gain: number) {
    void save({ ...cfg, [kind]: { ...cfg[kind], gain_db: gain } })
  }

  const has = status.exists ?? {}

  return (
    <section className="card">
      <h2>{t('audio.title')}</h2>
      <p className="muted">{t('audio.desc')}</p>

      <div className="audio-grid">
      {(['bgm', 'ambience'] as Kind[]).map((kind) => {
        const label = kind === 'bgm' ? t('audio.bgm') : t('audio.ambience')
        const c = cfg[kind] ?? DEFAULTS[kind]
        const exists = has[kind]
        return (
          <div key={kind} className={`audio-row kind-${kind}`}>
            <div className="audio-head">
              <b>{label}</b>
              {c.mode !== 'off' && (
                <span className={exists ? 'badge ok' : 'badge warn'}>
                  {exists ? t('audio.exists') : t('audio.missing')}
                </span>
              )}
            </div>
            <div className="row">
              {(['auto', 'file', 'off'] as Mode[]).map((m) => (
                <label key={m} className="check-row">
                  <input type="radio" checked={c.mode === m} onChange={() => setMode(kind, m)} />
                  {m === 'auto' ? t('audio.mode.auto') : m === 'file' ? t('audio.mode.file') : t('audio.mode.off')}
                </label>
              ))}
            </div>

            {c.mode !== 'off' && (
              <div className="row">
                <span className="muted">{t('audio.volume')}</span>
                <input type="range" min={-40} max={0} step={1}
                       value={c.gain_db ?? (kind === 'bgm' ? -18 : -26)}
                       onChange={(e) => setGain(kind, Number(e.target.value))} />
                <span className="muted">{c.gain_db ?? (kind === 'bgm' ? -18 : -26)} dB</span>
                {kind === 'bgm' && (
                  <label className="check-row">
                    <input type="checkbox" checked={c.duck_under_voice !== false}
                           onChange={(e) => void save({
                             ...cfg,
                             bgm: { ...cfg.bgm, duck_under_voice: e.target.checked },
                           })} />
                    {t('audio.duck')}
                  </label>
                )}
              </div>
            )}

            {(() => {
              const u = status.in_use?.[kind]
              if (!u) return null
              const label = u.applied
                ? (u.mode === 'file' ? t('audio.in-use.custom') : t('audio.in-use.auto'))
                : tpl('audio.in-use.none', { why: u.reason || '' })
              return <p className="muted sm">{t('audio.in-use')}：{label}</p>
            })()}

            <div className="row">
              <button className="ghost" disabled={busy === kind}
                      onClick={() => void generate(kind)}>
                {busy === kind ? t('audio.generating') : t('audio.generate')}
              </button>
              <label className="ghost file-btn">
                {t('audio.upload')}
                <input type="file" accept="audio/*" hidden
                       onChange={(e) => {
                         const f = e.target.files?.[0]
                         if (f) void upload(kind, f)
                       }} />
              </label>
              {exists && <AudioBar url={`/assets/audio/${pid}/${kind}.m4a`} />}
              {(exists || c.mode === 'file') && (
                <button className="ghost danger" onClick={() => void removeBed(kind)}>
                  {t('audio.delete')}
                </button>
              )}
            </div>
          </div>
        )
      })}
      </div>

      <div className="row">
        <button className="ghost" onClick={() => void preview()}>{t('audio.preview')}</button>
        {previewUrl && <AudioBar url={previewUrl} />}
      </div>
      {status.running && <p className="muted">{t('audio.running')}</p>}
      {status.error && <p className="muted">{tpl('audio.last-error', { err: status.error })}</p>}
    </section>
  )
}
