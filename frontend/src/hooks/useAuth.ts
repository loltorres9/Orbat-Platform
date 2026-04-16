import { useState, useEffect } from 'react'
import { api, isLoggedIn, clearSession } from '../api/client'
import type { User } from '../api/types'

export function useAuth() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!isLoggedIn()) {
      setLoading(false)
      return
    }
    api.get('/api/auth/me')
      .then(r => r.ok ? r.json() : null)
      .then(data => { setUser(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const logout = async () => {
    await api.post('/api/auth/logout')
    clearSession()
    setUser(null)
  }

  return { user, loading, logout }
}
