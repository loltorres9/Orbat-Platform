import { useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { setSession } from '../api/client'

export function AuthCallback() {
  const [params] = useSearchParams()
  const navigate = useNavigate()

  useEffect(() => {
    const session = params.get('session')
    if (session) {
      setSession(session)
      navigate('/', { replace: true })
    }
  }, [params, navigate])

  return <div className="loading">Signing in...</div>
}
