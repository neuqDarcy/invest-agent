import { useState, useRef, useEffect } from 'react'
import { getFMManagers, fmChat, fmClearSession } from '../api'

const QUICK_QUESTIONS = [
  '帮我找几只消费行业的优质公司，市值100-500亿',
  '分析一下贵州茅台是否值得长期持有',
  '帮我筛选自由现金流充裕、估值偏低的公司',
  '找一些类似彼得林奇风格的成长型小盘股',
]

export default function FundManagerPage() {
  const [managers, setManagers]     = useState([])
  const [managerId, setManagerId]   = useState('value')
  const [sessionId, setSessionId]   = useState(null)
  const [messages, setMessages]     = useState([])
  const [input, setInput]           = useState('')
  const [loading, setLoading]       = useState(false)
  const messagesEndRef               = useRef(null)
  const inputRef                     = useRef(null)

  useEffect(() => {
    getFMManagers().then(setManagers).catch(() => {})
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function handleSend(text) {
    const msg = (text || input).trim()
    if (!msg || loading) return
    setInput('')
    setLoading(true)

    // 立即显示用户消息
    setMessages(prev => [...prev, { type: 'user', content: msg }])

    try {
      const res = await fmChat(sessionId, managerId, msg)
      if (!sessionId) setSessionId(res.session_id)

      // 把 agent 返回的消息逐条加入
      const agentMsgs = res.messages.map(m => {
        if (m.role === 'tool') {
          return { type: 'tool', tool: m.tool_name, content: m.content }
        }
        if (m.role === 'assistant' && m.tool_calls?.length > 0) {
          return { type: 'thinking', calls: m.tool_calls, content: m.content }
        }
        if (m.role === 'assistant') {
          return { type: 'assistant', content: m.content }
        }
        return null
      }).filter(Boolean)

      setMessages(prev => [...prev, ...agentMsgs])
    } catch (e) {
      setMessages(prev => [...prev, {
        type: 'error',
        content: e.response?.data?.detail || e.message,
      }])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  async function handleClear() {
    if (sessionId) await fmClearSession(sessionId).catch(() => {})
    setSessionId(null)
    setMessages([])
  }

  const currentManager = managers.find(m => m.id === managerId)

  return (
    <div className="fm-page">
      {/* 左侧：基金经理选择 */}
      <div className="fm-sidebar">
        <div className="fm-sidebar-title">基金经理</div>
        {managers.map(m => (
          <button
            key={m.id}
            className={`fm-manager-card ${managerId === m.id ? 'active' : ''}`}
            onClick={() => { setManagerId(m.id); handleClear() }}
          >
            <div className="fm-manager-avatar">{m.name[0]}</div>
            <div className="fm-manager-info">
              <div className="fm-manager-name">{m.name}</div>
              <div className="fm-manager-style">{m.style}</div>
            </div>
          </button>
        ))}

        {currentManager && (
          <div className="fm-manager-detail">
            <div className="fm-detail-label">分析重点</div>
            {currentManager.analysis_priorities.map((p, i) => (
              <div key={i} className="fm-priority-item">
                <span className="fm-priority-dot" />
                {p}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 右侧：对话区 */}
      <div className="fm-chat">
        {/* 顶部栏 */}
        <div className="fm-chat-header">
          <div className="fm-chat-title">
            {currentManager ? `与「${currentManager.name}」对话` : '基金经理对话'}
          </div>
          {messages.length > 0 && (
            <button className="btn-ghost fm-clear-btn" onClick={handleClear}>
              新对话
            </button>
          )}
        </div>

        {/* 消息列表 */}
        <div className="fm-messages">
          {messages.length === 0 && (
            <div className="fm-welcome">
              <div className="fm-welcome-title">
                {currentManager ? currentManager.description : '选择一位基金经理开始对话'}
              </div>
              <div className="fm-quick-questions">
                {QUICK_QUESTIONS.map(q => (
                  <button key={q} className="fm-quick-btn" onClick={() => handleSend(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => {
            if (msg.type === 'user') return (
              <div key={i} className="fm-msg fm-msg-user">
                <div className="fm-msg-bubble">{msg.content}</div>
              </div>
            )

            if (msg.type === 'thinking') return (
              <div key={i} className="fm-msg fm-msg-thinking">
                <div className="fm-thinking-header">
                  <span className="fm-thinking-icon">⚙</span>
                  <span>调用工具：{msg.calls.map(c => c.name).join('、')}</span>
                </div>
                {msg.content && <div className="fm-thinking-text">{msg.content}</div>}
              </div>
            )

            if (msg.type === 'tool') return (
              <div key={i} className="fm-msg fm-msg-tool">
                <div className="fm-tool-header">
                  <span className="fm-tool-icon">📊</span>
                  <span className="fm-tool-name">{msg.tool}</span>
                </div>
                <pre className="fm-tool-result">{msg.content.slice(0, 400)}{msg.content.length > 400 ? '...' : ''}</pre>
              </div>
            )

            if (msg.type === 'assistant') return (
              <div key={i} className="fm-msg fm-msg-assistant">
                <div className="fm-assistant-avatar">
                  {currentManager?.name[0] || 'A'}
                </div>
                <div className="fm-assistant-content">
                  {msg.content}
                </div>
              </div>
            )

            if (msg.type === 'error') return (
              <div key={i} className="fm-msg">
                <div className="error">{msg.content}</div>
              </div>
            )

            return null
          })}

          {loading && (
            <div className="fm-msg fm-msg-thinking">
              <div className="fm-thinking-header">
                <span className="fm-thinking-icon">⚙</span>
                <span>正在思考...</span>
              </div>
              <div className="sp-thinking"><span /><span /><span /></div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* 输入框 */}
        <div className="fm-input-area">
          <input
            ref={inputRef}
            className="input fm-input"
            placeholder="和基金经理对话，如：帮我分析一下茅台..."
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
            disabled={loading}
          />
          <button
            className="btn-primary fm-send-btn"
            onClick={() => handleSend()}
            disabled={!input.trim() || loading}
          >
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
