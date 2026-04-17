const API_BASE = import.meta.env.VITE_API_URL || ''

function getSession(): string | null {
  return localStorage.getItem('session')
}

export function setSession(session: string) {
  localStorage.setItem('session', session)
}

export function clearSession() {
  localStorage.removeItem('session')
}

export function isLoggedIn(): boolean {
  return !!getSession()
}

async function request(path: string, options: RequestInit = {}): Promise<Response> {
  const session = getSession()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  }
  if (session) {
    headers['Authorization'] = `Bearer ${session}`
  }
  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (resp.status === 401) {
    clearSession()
  }
  return resp
}

export const api = {
  get: (path: string) => request(path),
  post: (path: string, body?: unknown) =>
    request(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: (path: string, body?: unknown) =>
    request(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  delete: (path: string) => request(path, { method: 'DELETE' }),
}
