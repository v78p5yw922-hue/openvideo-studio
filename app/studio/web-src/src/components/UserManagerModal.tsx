/** 管理员用户管理：新建 / 改资料 / 停用 / 重置密码 / 删除。 */

import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { confirmDialog } from '../lib/confirm'
import { t, tpl } from '../lib/i18n'
import { toast } from '../lib/store'
import type { AuthUser } from '../lib/types'
import './UserManagerModal.css'

interface Props {
  me: AuthUser
}

export function UserManagerPanel({ me }: Props) {
  const [users, setUsers] = useState<AuthUser[]>([])
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({
    username: '', password: '', display_name: '', phone: '', role: 'member',
  })
  const [creating, setCreating] = useState(false)

  async function load() {
    try {
      const r = await api.listUsers()
      setUsers(r.users)
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  useEffect(() => { void load() }, [])

  async function create() {
    if (!form.username.trim() || form.password.length < 6) return
    setBusy(true)
    try {
      const r = await api.createUser({
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim() || form.username.trim(),
        phone: form.phone.trim(),
        role: form.role,
      })
      toast(tpl('users.created', { name: r.user.username }), 'ok')
      setForm({ username: '', password: '', display_name: '', phone: '', role: 'member' })
      setCreating(false)
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    } finally {
      setBusy(false)
    }
  }

  async function patch(u: AuthUser, body: Parameters<typeof api.updateUser>[1]) {
    try {
      const r = await api.updateUser(u.id, body)
      setUsers((prev) => prev.map((x) => (x.id === r.user.id ? r.user : x)))
      toast(tpl('users.updated', { name: r.user.username }), 'ok')
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  async function resetPassword(u: AuthUser) {
    const pwd = window.prompt(tpl('users.reset.prompt', { name: u.display_name || u.username }))
    if (!pwd) return
    if (pwd.length < 6) { toast(t('users.password') + ' ≥ 6', 'err'); return }
    try {
      await api.resetPassword(u.id, pwd)
      toast(t('users.reset.done'), 'ok')
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  async function remove(u: AuthUser) {
    const ok = await confirmDialog({
      title: t('users.delete'),
      message: tpl('users.delete.confirm', { name: u.display_name || u.username }),
      confirmText: t('users.delete'),
      danger: true,
    })
    if (!ok) return
    try {
      await api.deleteUser(u.id)
      toast(tpl('users.deleted', { name: u.username }), 'ok')
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'err')
    }
  }

  return (
    <div className="users-panel">
      <div className="users-toolbar">
        <span className="muted">{tpl('users.count', { n: users.length })}</span>
        <span className="spacer" />
        <button className="ghost" onClick={() => setCreating((v) => !v)}>{t('users.new')}</button>
      </div>

      {creating && (
        <div className="users-new">
          <div className="users-new-grid">
            <label className="field">
              <span>{t('users.username')}</span>
              <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            </label>
            <label className="field">
              <span>{t('users.password')}</span>
              <input type="password" value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </label>
            <label className="field">
              <span>{t('users.display')}</span>
              <input value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            </label>
            <label className="field">
              <span>{t('users.phone')}</span>
              <input value={form.phone} inputMode="tel"
                onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </label>
            <label className="field">
              <span>{t('users.role')}</span>
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="member">{t('users.role.member')}</option>
                <option value="admin">{t('users.role.admin')}</option>
              </select>
            </label>
          </div>
          <div className="users-new-actions">
            <button className="ghost" onClick={() => setCreating(false)}>{t('users.cancel')}</button>
            <button className="primary" disabled={busy || !form.username.trim() || form.password.length < 6}
              onClick={() => void create()}>{t('users.create')}</button>
          </div>
        </div>
      )}

      <div className="users-table">
        <div className="users-row users-head">
          <span>{t('users.username')}</span>
          <span>{t('users.display')}</span>
          <span>{t('users.phone')}</span>
          <span>{t('users.role')}</span>
          <span />
        </div>
        {users.length === 0 && <div className="users-empty muted">{t('users.empty')}</div>}
        {users.map((u) => (
          <div key={u.id} className="users-row">
            <span className="users-username">
              {u.username}
              {u.id === me.id && <em className="users-self">{t('users.self')}</em>}
            </span>
            <input value={u.display_name}
              onChange={(e) => setUsers((prev) => prev.map((x) => (
                x.id === u.id ? { ...x, display_name: e.target.value } : x)))}
              onBlur={(e) => {
                if (e.target.value !== u.display_name) void patch(u, { display_name: e.target.value })
              }} />
            <input value={u.phone} inputMode="tel"
              onChange={(e) => setUsers((prev) => prev.map((x) => (
                x.id === u.id ? { ...x, phone: e.target.value } : x)))}
              onBlur={(e) => {
                if (e.target.value !== u.phone) void patch(u, { phone: e.target.value })
              }} />
            <select value={u.role} disabled={u.id === me.id}
              onChange={(e) => void patch(u, { role: e.target.value })}>
              <option value="member">{t('users.role.member')}</option>
              <option value="admin">{t('users.role.admin')}</option>
            </select>
            <span className="users-ops">
              <button className="ghost" onClick={() => void resetPassword(u)}>{t('users.reset')}</button>
              <button className="ghost" disabled={u.id === me.id} onClick={() => void remove(u)}>
                {t('users.delete')}
              </button>
              {u.id !== me.id && (
                <button className="ghost" onClick={() => void patch(u, { active: !u.active })}>
                  {u.active ? t('users.disabled') : t('users.active')}
                </button>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
