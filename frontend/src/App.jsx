import { useState, useEffect } from 'react'
import StockPage from './pages/StockPage'
import AuthPage from './pages/AuthPage'
import ProfilePage from './pages/ProfilePage'
import './App.css'

const NAV = [
  {
    key: 'stock',
    label: '研究分析',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
      </svg>
    ),
  },
]

export default function App() {
  const [tab, setTab]             = useState('stock')
  const [user, setUser]           = useState(null)
  const [showProfile, setShowProfile] = useState(false)

  // 启动时从 localStorage 恢复登录状态
  useEffect(() => {
    const saved = localStorage.getItem('user')
    if (saved) {
      try { setUser(JSON.parse(saved)) } catch (_) {}
    }
  }, [])

  function handleLogin(userData) {
    setUser(userData)
    localStorage.setItem('user', JSON.stringify(userData))
  }

  function handleLogout() {
    setUser(null)
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  function handleProfileUpdate(profile) {
    const updated = { ...user, profile }
    setUser(updated)
    localStorage.setItem('user', JSON.stringify(updated))
  }

  // 未登录显示认证页
  if (!user) return <AuthPage onLogin={handleLogin} />

  return (
    <div className="app-shell">
      {/* 左侧导航 */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <svg width="28" height="28" viewBox="0 0 32 32" fill="none">
            <rect width="32" height="32" rx="8" fill="#1a1a1a" />
            <polyline points="6 22 12 14 17 18 22 10 26 10"
              stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          <span className="sidebar-logo-text">投研 Agent</span>
        </div>

        <nav className="sidebar-nav">
          {NAV.map(item => (
            <button
              key={item.key}
              className={`sidebar-item ${tab === item.key ? 'active' : ''}`}
              onClick={() => setTab(item.key)}
            >
              <span className="sidebar-icon">{item.icon}</span>
              <span className="sidebar-label">{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          {/* 用户信息 */}
          <button className="sidebar-user" onClick={() => setShowProfile(true)}>
            <div className="sidebar-avatar">{user.username?.[0]?.toUpperCase()}</div>
            <div className="sidebar-user-info">
              <div className="sidebar-username">{user.username}</div>
              <div className="sidebar-user-style">
                {user.profile?.invest_style === 'value' ? '价值投资' :
                 user.profile?.invest_style === 'growth' ? '成长投资' : 'GARP'}
              </div>
            </div>
          </button>
          <button className="sidebar-logout" onClick={handleLogout} title="退出登录">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </button>
        </div>
      </aside>

      {/* 主内容 */}
      <main className="app-main">
        {tab === 'stock' && <StockPage />}
      </main>

      {/* 用户画像弹窗 */}
      {showProfile && (
        <ProfilePage
          user={user}
          token={localStorage.getItem('token')}
          onUpdate={handleProfileUpdate}
          onClose={() => setShowProfile(false)}
        />
      )}
    </div>
  )
}
