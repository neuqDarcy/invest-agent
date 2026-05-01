import { useState } from 'react'
import { register, login } from '../api'

export default function AuthPage({ onLogin }) {
  const [mode, setMode]       = useState('login')   // 'login' | 'register'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!username.trim() || !password.trim()) return
    setLoading(true); setError('')
    try {
      const fn = mode === 'login' ? login : register
      const data = await fn(username.trim(), password)
      localStorage.setItem('token', data.token)
      localStorage.setItem('user', JSON.stringify(data.user))
      onLogin(data.user)
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-logo">
          <svg viewBox="0 0 32 32" fill="none" width="36" height="36">
            <rect width="32" height="32" rx="8" fill="#2563eb" />
            <polyline points="6 22 12 14 17 18 22 10 26 10"
              stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          <span className="auth-logo-text">研报分析 Agent</span>
        </div>

        <div className="auth-tabs">
          <button className={`auth-tab ${mode === 'login' ? 'active' : ''}`} onClick={() => { setMode('login'); setError('') }}>登录</button>
          <button className={`auth-tab ${mode === 'register' ? 'active' : ''}`} onClick={() => { setMode('register'); setError('') }}>注册</button>
        </div>

        <form onSubmit={handleSubmit} className="auth-form">
          <div className="auth-field">
            <label>用户名</label>
            <input className="input" placeholder="请输入用户名" value={username}
              onChange={e => setUsername(e.target.value)} autoFocus />
          </div>
          <div className="auth-field">
            <label>密码</label>
            <input className="input" type="password" placeholder="请输入密码（至少6位）"
              value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          {error && <div className="error auth-error">{error}</div>}
          <button type="submit" disabled={!username.trim() || !password.trim() || loading}
            className="btn-primary auth-submit">
            {loading ? '处理中...' : mode === 'login' ? '登录' : '注册'}
          </button>
        </form>
      </div>
    </div>
  )
}
