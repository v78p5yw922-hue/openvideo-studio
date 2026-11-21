/** /settings —— 系统设置（从个人中心进入，不占主菜单）
 *
 *  内容：环境体检 / 路径 / 模型 / 渲染默认 / 队列策略 / 关于
 *  只读为主：路径与模型名改 config.toml 更稳；页面负责"看得见 + 能复制 + 能重新体检"。
 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { t, tpl } from '../lib/i18n'
import { toast } from '../lib/store'
import './SettingsPage.css'

type Check = { key: string; label: string; status: 'ok' | 'warn' | 'fail'; detail: string; hint: string }
type Doctor = { ok: boolean; summary: string; fail: number; warn: number; checks: Check[] }
type Cfg = {
  config_file: string; data_dir: string; content_dir: string
  comfy: { url: string; root: string }
  models: Record<string, string>
  render: { width?: number; height?: number; steps?: number; max_wall?: number }
  gpu: { policy?: string }
}

const ICON: Record<string, string> = { ok: '●', warn: '▲', fail: '✕' }

export function SettingsPage() {
  const nav = useNavigate()
  const [doc, setDoc] = useState<Doctor | null>(null)
  const [cfg, setCfg] = useState<Cfg | null>(null)
  const [busy, setBusy] = useState(false)
  const [glossary, setGlossary] = useState<{ total: number; cats: number } | null>(null)

  const load = useCallback(async (toastIt = false) => {
    setBusy(true)
    try {
      const [d, c] = await Promise.all([
        api.doctor() as unknown as Promise<Doctor>,
        api.configView() as unknown as Promise<Cfg>,
      ])
      setDoc(d)
      setCfg(c)
      if (toastIt) toast(d.ok ? t('settings.recheck.ok') : t('settings.recheck.bad'), d.ok ? 'ok' : 'err')
    } catch (e) {
      if (toastIt) toast(String(e), 'err')
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    api.glossary('')
      .then((g) => {
        const cats = (g.categories as unknown[]) || []
        setGlossary({ total: Number(g.total || 0), cats: cats.length })
      })
      .catch(() => setGlossary(null))
  }, [])

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      toast(t('settings.copied'), 'ok')
    } catch {
      toast(t('settings.copy-failed'), 'err')
    }
  }

  return (
    <div className="page settings-page">
      <div className="page-head">
        <div>
          <h1>{t('settings.title')}</h1>
          <p className="muted">{t('settings.subtitle')}</p>
        </div>
        <button className="btn" onClick={() => void load(true)} disabled={busy}>
          {busy ? t('settings.checking') : t('settings.recheck')}
        </button>
      </div>

      <div className="page-body settings-body">
        {/* 环境 */}
        <section className="card">
          <header className="card-head">
            <h2>{t('settings.env')}</h2>
            {doc && (
              <span className={`pill ${doc.ok ? 'is-ok' : 'is-bad'}`}>
                {doc.summary}
              </span>
            )}
          </header>
          <div className="checks">
            {(doc?.checks || []).map((c) => (
              <div key={c.key} className={`check is-${c.status}`}>
                <span className="check-icon">{ICON[c.status]}</span>
                <span className="check-label">{c.label}</span>
                <span className="check-detail">{c.detail}</span>
              </div>
            ))}
            {!doc && <div className="muted">{t('settings.loading')}</div>}
          </div>
          {doc && !doc.ok && (
            <ul className="hints">
              {doc.checks.filter((c) => c.status !== 'ok' && c.hint).map((c) => (
                <li key={`${c.key}-hint`}><b>{c.label}</b>：{c.hint}</li>
              ))}
            </ul>
          )}
        </section>

        {/* 路径 */}
        <section className="card">
          <header className="card-head"><h2>{t('settings.paths')}</h2></header>
          {cfg ? (
            <table className="kv">
              <tbody>
                {[
                  ['settings.paths.config', cfg.config_file],
                  ['settings.paths.data', cfg.data_dir],
                  ['settings.paths.content', cfg.content_dir],
                  ['settings.paths.comfy', cfg.comfy.root],
                  ['settings.paths.engine', cfg.comfy.url],
                ].map(([k, v]) => (
                  <tr key={k}>
                    <th>{t(k)}</th>
                    <td className="mono">{v || '—'}</td>
                    <td className="right">
                      {v && <button className="ghost sm" onClick={() => void copy(v)}>{t('settings.copy')}</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="muted">{t('settings.loading')}</div>}
          <p className="foot-hint">{t('settings.paths.hint')}</p>
        </section>

        {/* 模型 */}
        <section className="card">
          <header className="card-head"><h2>{t('settings.models')}</h2></header>
          {cfg ? (
            <table className="kv">
              <tbody>
                {Object.entries(cfg.models).map(([k, v]) => (
                  <tr key={k}>
                    <th>{k}</th>
                    <td className="mono">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="muted">{t('settings.loading')}</div>}
          <p className="foot-hint">{t('settings.models.hint')}</p>
        </section>

        {/* 渲染默认 */}
        <section className="card">
          <header className="card-head"><h2>{t('settings.render')}</h2></header>
          {cfg ? (
            <table className="kv">
              <tbody>
                <tr><th>{t('settings.render.size')}</th><td className="mono">{cfg.render.width}×{cfg.render.height}</td></tr>
                <tr><th>{t('settings.render.steps')}</th><td className="mono">{cfg.render.steps}</td></tr>
                <tr><th>{t('settings.render.wall')}</th><td className="mono">{cfg.render.max_wall === 0 ? t('settings.render.wall.off') : cfg.render.max_wall}</td></tr>
                <tr><th>{t('settings.gpu.policy')}</th><td className="mono">{cfg.gpu.policy || 'queue'}</td></tr>
              </tbody>
            </table>
          ) : <div className="muted">{t('settings.loading')}</div>}
          <p className="foot-hint">{t('settings.render.hint')}</p>
        </section>

        {/* 词库 */}
        <section className="card">
          <header className="card-head"><h2>{t('settings.glossary')}</h2></header>
          <p className="muted">
            {glossary
              ? tpl('settings.glossary.info', { n: glossary.total, c: glossary.cats })
              : t('settings.loading')}
          </p>
          <button className="btn sm" onClick={() => nav('/glossary')}>{t('settings.glossary.open')}</button>
        </section>

        {/* 写作技能 */}
        <section className="card">
          <header className="card-head"><h2>{t('settings.skills')}</h2></header>
          <p className="muted">{t('settings.skills.info')}</p>
          <button className="btn sm" onClick={() => nav('/skills')}>{t('settings.skills.open')}</button>
        </section>

        {/* 规范 */}
        <section className="card">
          <header className="card-head"><h2>{t('settings.spec')}</h2></header>
          <p className="muted">{t('settings.spec.info')}</p>
          <button className="btn sm" onClick={() => nav('/spec')}>{t('settings.spec.open')}</button>
        </section>

        {/* 关于 */}
        <section className="card">
          <header className="card-head"><h2>{t('settings.about')}</h2></header>
          <p className="muted">{t('settings.about.body')}</p>
        </section>
      </div>
    </div>
  )
}
