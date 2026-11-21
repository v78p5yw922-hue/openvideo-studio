/** 登录页：默认管理员 admin / wsmm1234（首次启动自动创建）。 */

import { useState } from 'react'
import { login, useAuth } from '../lib/auth'
import { t, useI18n } from '../lib/i18n'
import { useTheme } from '../lib/theme'
import './LoginPage.css'

export function LoginPage() {
  const { busy, error } = useAuth()
  const { lang, setLang } = useI18n()
  const [theme, toggleTheme] = useTheme()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    await login(username, password, remember)
  }

  return (
    <div className="login-page">
      <div className="login-toolbar">
        <button className="ghost" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}>
          {lang === 'zh' ? 'English' : '中文'}
        </button>
        <button className="ghost" onClick={toggleTheme}>
          {theme === 'dark' ? t('theme.light-label') : t('theme.dark-label')}
        </button>
      </div>

      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <LogoMark />
          <div>
            <h1>{t('brand')}</h1>
            <p>{t('login.subtitle')}</p>
          </div>
        </div>

        <label className="field">
          <span>{t('login.username')}</span>
          <input
            autoFocus
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="admin"
          />
        </label>

        <label className="field">
          <span>{t('login.password')}</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </label>

        <label className="login-remember">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          {t('login.remember')}
        </label>

        {error && <div className="login-error" role="alert">{error}</div>}

        <button className="primary login-submit" disabled={busy || !username || !password}>
          {busy ? t('login.submitting') : t('login.submit')}
        </button>

        <p className="login-hint muted">{t('login.hint')}</p>
      </form>
    </div>
  )
}

function LogoMark() {
  return (
    <svg width="40" height="40" viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id="ovlg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#5aa0ff" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="28" height="28" rx="8" fill="url(#ovlg)" opacity="0.16" />
      <path d="M12 10.5v11l9-5.5-9-5.5z" fill="url(#ovlg)" />
      <rect x="5" y="5" width="22" height="22" rx="6" fill="none" stroke="url(#ovlg)" strokeWidth="1.4" opacity="0.55" />
    </svg>
  )
}
