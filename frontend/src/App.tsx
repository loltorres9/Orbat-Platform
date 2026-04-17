import { Routes, Route, Link } from 'react-router-dom'
import { useAuth } from './hooks/useAuth'
import { Login } from './pages/Login'
import { AuthCallback } from './pages/AuthCallback'
import { OrbatView } from './pages/OrbatView'
import { AdminBuilder } from './pages/AdminBuilder'
import { Home } from './pages/Home'

export function App() {
  const { user, loading, logout } = useAuth()

  if (loading) {
    return <div className="loading">Loading...</div>
  }

  return (
    <div className="app">
      <nav className="navbar">
        <Link to="/" className="nav-brand">ORBAT Platform</Link>
        <div className="nav-right">
          {user ? (
            <>
              <span className="nav-user">
                {user.avatar && <img src={user.avatar} alt="" className="nav-avatar" />}
                {user.username}
              </span>
              <button onClick={logout} className="btn btn-sm">Logout</button>
            </>
          ) : (
            <Link to="/login" className="btn btn-primary btn-sm">Login with Discord</Link>
          )}
        </div>
      </nav>

      <main className="container">
        <Routes>
          <Route path="/" element={<Home user={user} />} />
          <Route path="/login" element={<Login />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/orbat/:operationId" element={<OrbatView user={user} />} />
          <Route path="/builder/:operationId" element={<AdminBuilder user={user} />} />
        </Routes>
      </main>
    </div>
  )
}
