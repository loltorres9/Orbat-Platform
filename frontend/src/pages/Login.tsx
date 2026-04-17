const API_BASE = import.meta.env.VITE_API_URL || ''

export function Login() {
  return (
    <div className="login-page">
      <div className="login-card">
        <h1>ORBAT Platform</h1>
        <p>Sign in with your Discord account to view operations and request slots.</p>
        <a href={`${API_BASE}/api/auth/login`} className="btn btn-discord">
          <svg width="20" height="20" viewBox="0 0 71 55" fill="currentColor">
            <path d="M60.1 4.9A58.5 58.5 0 0045.4.2a.2.2 0 00-.2.1 40.8 40.8 0 00-1.8 3.7 54 54 0 00-16.2 0A37 37 0 0025.4.3a.2.2 0 00-.2-.1 58.4 58.4 0 00-14.7 4.6.2.2 0 00-.1 0A59.7 59.7 0 00.6 43.6a.2.2 0 000 .2A58.8 58.8 0 0018.1 54a.2.2 0 00.2-.1 42 42 0 003.6-5.9.2.2 0 00-.1-.3 38.8 38.8 0 01-5.5-2.6.2.2 0 01 0-.4l1.1-.9a.2.2 0 01.2 0 42 42 0 0035.6 0 .2.2 0 01.2 0l1.1.9a.2.2 0 010 .3 36.4 36.4 0 01-5.5 2.7.2.2 0 00-.1.3 47.2 47.2 0 003.6 5.9.2.2 0 00.3 0A58.6 58.6 0 0070 43.9a.2.2 0 000-.2A59.4 59.4 0 0060.1 5a.2.2 0 000 0zM23.7 35.9c-3.4 0-6.2-3.1-6.2-7s2.7-7 6.2-7 6.3 3.2 6.2 7-2.8 7-6.2 7zm22.9 0c-3.4 0-6.2-3.1-6.2-7s2.7-7 6.2-7 6.3 3.2 6.2 7-2.7 7-6.2 7z"/>
          </svg>
          Login with Discord
        </a>
      </div>
    </div>
  )
}
