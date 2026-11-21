/** /users —— 用户管理（管理员） */

import { Navigate } from 'react-router-dom'
import { UserManagerPanel } from '../components/UserManagerModal'
import { useAuth } from '../lib/auth'

export function UsersPage() {
  const { user } = useAuth()
  if (!user) return null
  if (user.role !== 'admin') return <Navigate to="/create/video/rendering" replace />
  return (
    <div className="page">
      <div className="page-body">
        <UserManagerPanel me={user} />
      </div>
    </div>
  )
}
