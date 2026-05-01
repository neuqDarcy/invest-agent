import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  getStockOverview, buildKnowledgeBase, getKnowledgeStatus,
  uploadBrokerReport, getBrokerReports, submitFeedback, unifiedChatStream, searchStocks,
  getNotes, createNote, updateNote, deleteNote, getChatHistory, clearChatHistory,
  getWatchlist, addToWatchlist, removeFromWatchlist,
} from '../api'

const STATUS_COLOR = { 低估: '#16a34a', 合理: '#2563eb', 高估: '#dc2626' }
const STATUS_BG    = { 低估: '#f0fdf4', 合理: '#eff6ff', 高估: '#fff1f2' }

function StockSearch({ onSelect }) {
  const [value, setValue]       = useState('')
  const [results, setResults]   = useState([])
  const [open, setOpen]         = useState(false)
  const [active, setActive]     = useState(-1)
  const timerRef                 = useRef(null)
  const wrapRef                  = useRef(null)

  useEffect(() => {
    function handleClick(e) {
      if (!wrapRef.current?.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  function handleChange(e) {
    const v = e.target.value
    setValue(v)
    setActive(-1)
    clearTimeout(timerRef.current)
    if (!v.trim()) { setResults([]); setOpen(false); return }
    timerRef.current = setTimeout(async () => {
      const data = await searchStocks(v).catch(() => [])
      setResults(data)
      setOpen(data.length > 0)
    }, 200)
  }

  function handleKeyDown(e) {
    if (!open) return
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, results.length - 1)) }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setActive(a => Math.max(a - 1, 0)) }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (active >= 0) select(results[active])
      else if (results.length > 0) select(results[0])
    }
    if (e.key === 'Escape') setOpen(false)
  }

  function select(stock) {
    setValue(`${stock.name}（${stock.code}）`)
    setOpen(false)
    setResults([])
    onSelect(stock.code)
  }

  return (
    <div className="stock-search-wrap" ref={wrapRef}>
      <input
        className="input sp2-search-input"
        placeholder="搜索股票名称或代码"
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onFocus={() => results.length > 0 && setOpen(true)}
        autoComplete="off"
      />
      {open && (
        <div className="stock-dropdown">
          {results.map((s, i) => (
            <div
              key={s.code}
              className={`stock-dropdown-item ${i === active ? 'active' : ''}`}
              onMouseDown={() => select(s)}
            >
              <span className="sdi-name">{s.name}</span>
              <span className="sdi-code">{s.code}</span>
              {s.industry && <span className="sdi-industry">{s.industry}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const QUICK_QUESTIONS = [
  '帮我分析一下这家公司的基本面',
  '近几年自由现金流情况怎么样？',
  '按巴菲特标准，这家公司估值合理吗？',
  '找 ROE > 15%、消费行业、市值 50-500 亿的低估公司',
  '找自由现金流充裕、负债率低于 40%、PB 历史低位的公司',
  '找类似彼得林奇风格的被低估小盘成长股，市值 30-100 亿',
]

function MetricValue({ value }) {
  if (value == null) return <span style={{ color: '#cbd5e1' }}>—</span>
  const abs = Math.abs(value)
  if (abs >= 1e8) return <span>{(value / 1e8).toFixed(2)} 亿</span>
  if (abs >= 1e4) return <span>{(value / 1e4).toFixed(2)} 万</span>
  return <span>{value.toFixed(2)}</span>
}

function FeedbackBar({ code, question, answer, sources }) {
  const [state, setState] = useState(null)
  const [comment, setComment] = useState('')

  async function submit(rating) {
    if (state === 'done') return
    if (rating === 0 && state !== 'bad') { setState('bad'); return }
    try {
      await submitFeedback(code || '', question, answer, sources, rating, comment)
      setState('done')
    } catch (_) {}
  }

  if (state === 'done') return <div className="fb-done">感谢反馈 ✓</div>
  return (
    <div className="fb-bar">
      <button className={`fb-btn ${state === 'good' ? 'active-good' : ''}`} onClick={() => submit(1)}>👍</button>
      <button className={`fb-btn ${state === 'bad'  ? 'active-bad'  : ''}`} onClick={() => submit(0)}>👎</button>
      {state === 'bad' && (
        <div className="fb-comment">
          <input className="input fb-input" placeholder="哪里不对？" value={comment}
            onChange={e => setComment(e.target.value)} onKeyDown={e => e.key === 'Enter' && submit(0)} />
          <button className="btn-primary fb-submit" onClick={() => submit(0)}>提交</button>
        </div>
      )}
    </div>
  )
}

export default function StockPage() {
  // 视图：'watchlist' | 'detail'
  const [view, setView]             = useState('watchlist')

  // 自选列表
  const [watchlist, setWatchlist]   = useState([])

  // 股票信息
  const [codeInput, setCodeInput]   = useState('')
  const [stockCode, setStockCode]   = useState('')
  const [overview, setOverview]     = useState(null)
  const [ovLoading, setOvLoading]   = useState(false)
  const [ovError, setOvError]       = useState('')

  // 对话
  const [messages, setMessages]     = useState([])
  const [input, setInput]           = useState('')
  const [sending, setSending]       = useState(false)
  const [sessionId, setSessionId]   = useState(null)
  const sessionIdRef                 = useRef(null)   // 同步存储，避免 state 异步问题
  const lastRouteRef                 = useRef(null)   // 记录上一轮的路由类型
  const messagesEndRef               = useRef(null)
  const inputRef                     = useRef(null)

  // 资料管理
  const [docsOpen, setDocsOpen]     = useState(false)
  const [kbStatus, setKbStatus]     = useState(null)
  const [brokerList, setBrokerList] = useState(null)
  const [building, setBuilding]     = useState(false)
  const [buildMsg, setBuildMsg]     = useState('')
  const [uploading, setUploading]   = useState(false)
  const [uploadMsg, setUploadMsg]   = useState('')
  const fileRef                      = useRef()

  // 笔记
  const [rightTab, setRightTab]     = useState('chat')   // 'chat' | 'notes'
  const [notes, setNotes]           = useState([])
  const [noteInput, setNoteInput]   = useState('')
  const [editingNote, setEditingNote] = useState(null)   // {id, content}
  const [noteSaving, setNoteSaving] = useState(false)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // 启动时加载自选列表
  useEffect(() => {
    getWatchlist().then(setWatchlist).catch(() => {})
  }, [])

  async function toggleWatch() {
    if (!stockCode) return
    const isWatched = overview?.watched || watchlist.some(w => w.stock_code === stockCode)
    if (isWatched) {
      await removeFromWatchlist(stockCode).catch(() => {})
      setWatchlist(prev => prev.filter(w => w.stock_code !== stockCode))
      if (overview) setOverview({ ...overview, watched: false })
    } else {
      await addToWatchlist(stockCode, overview?.name || '').catch(() => {})
      setWatchlist(prev => [{ stock_code: stockCode, stock_name: overview?.name || '' }, ...prev])
      if (overview) setOverview({ ...overview, watched: true })
    }
  }

  // 查询股票
  function handleSearch(e) {
    e.preventDefault()
    const c = codeInput.trim()
    if (!c) return
    setStockCode(c)
    setOverview(null); setOvError('')
    setView('detail')
  }

  useEffect(() => {
    if (!stockCode) return
    // 切换股票时：加载行情 + 加载历史对话
    setOvLoading(true)
    setMessages([])
    setSessionId(null)
    sessionIdRef.current = null
    lastRouteRef.current = null

    Promise.all([
      getStockOverview(stockCode),
      getChatHistory(stockCode),
    ]).then(([ov, history]) => {
      setOverview(ov)
      // 把历史记录转为消息格式
      if (history.length > 0) {
        const histMsgs = history.map(h => ({
          type: h.role === 'user' ? 'user' : 'assistant',
          content: h.content,
          sources: h.sources || [],
          question: '',
          fromHistory: true,
        }))
        setMessages(histMsgs)
      }
    }).catch(e => setOvError(e.response?.data?.detail || e.message))
      .finally(() => setOvLoading(false))
  }, [stockCode])

  // 打开资料面板
  async function openDocs() {
    const next = !docsOpen
    setDocsOpen(next)
    if (next && stockCode) {
      const [s, b] = await Promise.all([
        getKnowledgeStatus(stockCode).catch(() => null),
        getBrokerReports(stockCode).catch(() => null),
      ])
      if (s) setKbStatus(s)
      if (b) setBrokerList(b)
    }
  }

  async function handleBuild() {
    setBuilding(true); setBuildMsg('')
    try {
      const r = await buildKnowledgeBase(stockCode)
      setBuildMsg(`新入库 ${r.indexed} 份，跳过 ${r.skipped} 份`)
      setKbStatus(await getKnowledgeStatus(stockCode))
    } catch (e) { setBuildMsg(e.response?.data?.detail || e.message) }
    finally { setBuilding(false) }
  }

  async function handleUpload(e) {
    const f = e.target.files[0]; if (!f) return
    setUploading(true); setUploadMsg('')
    try {
      const r = await uploadBrokerReport(f)
      setUploadMsg(`已解析：${r.title}`)
      setBrokerList(await getBrokerReports(stockCode))
    } catch (e) { setUploadMsg(e.response?.data?.detail || e.message) }
    finally { setUploading(false) }
  }

  // 发送消息
  // 笔记函数
  async function loadNotes() {
    const data = await getNotes(stockCode || '').catch(() => [])
    setNotes(data)
  }

  useEffect(() => {
    if (rightTab === 'notes') loadNotes()
  }, [rightTab, stockCode])

  async function handleSaveNote() {
    const content = (editingNote?.content ?? noteInput).trim()
    if (!content) return
    setNoteSaving(true)
    try {
      if (editingNote?.id) {
        await updateNote(editingNote.id, content)
      } else {
        await createNote(content, stockCode || '')
      }
      setNoteInput('')
      setEditingNote(null)
      await loadNotes()
    } catch (e) { console.error(e) }
    finally { setNoteSaving(false) }
  }

  async function handleDeleteNote(id) {
    await deleteNote(id).catch(() => {})
    await loadNotes()
  }

  async function handleSend(text) {
    const msg = (text || input).trim()
    if (!msg || sending) return
    setInput('')
    setSending(true)

    // 立即加用户消息 + 空的 assistant 占位
    setMessages(prev => [...prev,
      { type: 'user', content: msg },
      { type: 'assistant', content: '', sources: [], question: msg },
    ])

    try {
      const stockName = overview?.name || null
      for await (const event of unifiedChatStream(sessionIdRef.current, 'value', msg, stockCode || null, lastRouteRef.current, stockName)) {

        if (event.type === 'session_id') {
          sessionIdRef.current = event.session_id
          setSessionId(event.session_id)
        }

        else if (event.type === 'route') {
          lastRouteRef.current = event.route
          if (event.stock_code && !stockCode) {
            setStockCode(event.stock_code)
            setCodeInput(event.stock_code)
            setView('detail')
          }
        }

        else if (event.type === 'thinking') {
          // 工具调用：插入到最后的 assistant 占位前面
          setMessages(prev => {
            const last = prev[prev.length - 1]
            return [...prev.slice(0, -1),
              { type: 'thinking', calls: event.calls },
              last,
            ]
          })
        }

        else if (event.type === 'tool') {
          setMessages(prev => {
            const last = prev[prev.length - 1]
            return [...prev.slice(0, -1),
              { type: 'tool', tool: event.tool_name, content: event.content },
              last,
            ]
          })
        }

        else if (event.type === 'token') {
          // 逐字追加到占位消息
          setMessages(prev => {
            const last = prev[prev.length - 1]
            return [...prev.slice(0, -1), { ...last, content: last.content + event.content }]
          })
        }

        else if (event.type === 'sources') {
          setMessages(prev => {
            const last = prev[prev.length - 1]
            return [...prev.slice(0, -1), { ...last, sources: event.sources }]
          })
        }

        else if (event.type === 'error') {
          setMessages(prev => {
            // 把空占位改为错误消息
            return [...prev.slice(0, -1), { type: 'error', content: event.message }]
          })
        }

        else if (event.type === 'done') {
          break
        }
      }
    } catch (e) {
      setMessages(prev => {
        const last = prev[prev.length - 1]
        if (last?.type === 'assistant' && !last.content) {
          return [...prev.slice(0, -1), { type: 'error', content: e.message }]
        }
        return [...prev, { type: 'error', content: e.message }]
      })
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  // 进入股票详情
  function openStock(code, name = '') {
    setStockCode(code)
    setCodeInput(code)
    setOverview(null); setOvError('')
    setView('detail')
  }

  return (
    <div className="sp2-layout">

      {/* ── 左侧面板：自选列表 or 股票详情 ── */}
      <div className="sp2-panel">

        {/* ── 自选列表视图 ── */}
        {view === 'watchlist' && (
          <>
            <div className="wl-panel-header">
              <span className="wl-panel-title">自选股</span>
              <StockSearch onSelect={code => openStock(code)} />
            </div>

            {watchlist.length === 0 ? (
              <div className="wl-empty">
                <div className="wl-empty-icon">☆</div>
                <div className="wl-empty-text">还没有自选股</div>
                <div className="wl-empty-sub">搜索股票后点击 ☆ 添加</div>
              </div>
            ) : (
              <div className="wl-list">
                {watchlist.map(w => (
                  <button key={w.stock_code} className="wl-item"
                    onClick={() => openStock(w.stock_code, w.stock_name)}>
                    <div className="wl-item-left">
                      <span className="wl-item-name">{w.stock_name || w.stock_code}</span>
                      <span className="wl-item-code">{w.stock_code}</span>
                    </div>
                    <span className="wl-item-arrow">›</span>
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {/* ── 股票详情视图 ── */}
        {view === 'detail' && (
          <>
            {/* 返回 + 搜索 */}
            <div className="sp2-nav-bar">
              <button className="sp2-back-btn" onClick={() => setView('watchlist')}>
                ‹ 自选
              </button>
              <div style={{flex:1}}>
                <StockSearch onSelect={code => openStock(code)} />
              </div>
            </div>

        {ovLoading && <div className="kb-loading" style={{margin:'8px 0'}}>加载中...</div>}
        {ovError   && <div className="error" style={{margin:'8px 0',fontSize:12}}>{ovError}</div>}

        {overview && (
          <>
            {/* 股票头部 */}
            <div className="sp2-stock-header">
              <div className="sp2-stock-title">
                <span className="sp2-stock-name">{overview.name}</span>
                <span className="sp2-stock-code">{overview.stock_code}</span>
                <button
                  className={`watch-btn ${overview.watched || watchlist.some(w => w.stock_code === stockCode) ? 'watched' : ''}`}
                  onClick={toggleWatch}
                  title={overview.watched ? '取消自选' : '加入自选'}
                >
                  {overview.watched || watchlist.some(w => w.stock_code === stockCode) ? '★' : '☆'}
                </button>
              </div>
              <div className="sp2-stock-row">
                <span className="sp2-price">¥{overview.current_price}</span>
                {overview.valuation && (
                  <span className="sp2-status" style={{
                    color: STATUS_COLOR[overview.valuation.current_status],
                    background: STATUS_BG[overview.valuation.current_status],
                  }}>{overview.valuation.current_status}</span>
                )}
              </div>
            </div>

            {/* 行情指标 */}
            <div className="sp2-metrics">
              {[
                ['市盈率（动）', overview.pe?.toFixed(2) ?? '—'],
                ['市净率',       overview.pb?.toFixed(2) ?? '—'],
                ['每股收益',     overview.eps != null ? `¥${overview.eps}` : '—'],
                ['每股净资产',   overview.bps != null ? `¥${overview.bps?.toFixed(2)}` : '—'],
                ['股息率',       overview.dv_ratio != null ? `${overview.dv_ratio?.toFixed(2)}%` : '—'],
                ['总市值',       overview.market_cap != null ? `${overview.market_cap}亿` : '—'],
                ['流通市值',     overview.circ_mv != null ? `${overview.circ_mv}亿` : '—'],
                ['流通股',       overview.float_share != null ? `${(overview.float_share / 10000).toFixed(2)}亿股` : '—'],
                ['52W高',        overview.week52_high != null ? `¥${overview.week52_high}` : '—'],
                ['52W低',        overview.week52_low  != null ? `¥${overview.week52_low}`  : '—'],
                ['PB历史分位',   overview.pb_stats ? `${overview.pb_stats.pb_percentile}%` : '—'],
              ].map(([l, v]) => (
                <div key={l} className="sp2-metric">
                  <div className="sp2-metric-label">{l}</div>
                  <div className="sp2-metric-value">{v}</div>
                </div>
              ))}
            </div>

            {/* 估值区间 */}
            {overview.valuation && (
              <div className="sp2-valuation">
                <div className="sp2-val-item buy">
                  <div className="sp2-val-label">买入参考</div>
                  <div className="sp2-val-price">≤¥{overview.valuation.buy_price}</div>
                </div>
                <div className="sp2-val-item fair">
                  <div className="sp2-val-label">合理区间</div>
                  <div className="sp2-val-price">¥{overview.valuation.fair_value_low}–{overview.valuation.fair_value_high}</div>
                </div>
                <div className="sp2-val-item sell">
                  <div className="sp2-val-label">卖出参考</div>
                  <div className="sp2-val-price">≥¥{overview.valuation.sell_price}</div>
                </div>
              </div>
            )}

            {/* 财务指标 */}
            {Object.keys(overview.metrics_by_year).length > 0 && (
              <div className="sp2-fin">
                <div className="sp2-section-label">财务指标</div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>指标</th>
                        {Object.keys(overview.metrics_by_year).map(y => <th key={y}>{y}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {Array.from(new Set(
                        Object.values(overview.metrics_by_year).flatMap(m => Object.keys(m))
                      )).map(metric => (
                        <tr key={metric}>
                          <td style={{whiteSpace:'nowrap'}}>{metric}</td>
                          {Object.keys(overview.metrics_by_year).map(y => (
                            <td key={y}><MetricValue value={overview.metrics_by_year[y][metric] ?? null} /></td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}

        {/* 资料管理 */}
        {stockCode && (
          <div className="sp2-docs">
            <button className="sp2-docs-toggle" onClick={openDocs}>
              资料管理 <span>{docsOpen ? '▲' : '▼'}</span>
            </button>
            {docsOpen && (
              <div className="sp2-docs-body">
                <div className="sp2-docs-section">
                  <div className="sp2-docs-label">年报知识库</div>
                  {kbStatus?.reports?.length > 0
                    ? kbStatus.reports.map(r => (
                        <div key={r.id} className="sp2-doc-file">📄 {r.title}</div>
                      ))
                    : <div className="sp2-docs-empty">暂无年报</div>
                  }
                  <button onClick={handleBuild} disabled={building} className="btn-ghost sp2-docs-btn">
                    {building ? '建库中...' : '建立/更新知识库'}
                  </button>
                  {buildMsg && <div className="sp2-docs-msg">{buildMsg}</div>}
                </div>

                <div className="sp2-docs-section">
                  <div className="sp2-docs-label">券商研报</div>
                  {brokerList?.reports?.length > 0
                    ? brokerList.reports.map(r => (
                        <div key={r.id} className="sp2-doc-file">📊 {r.title || r.broker}</div>
                      ))
                    : <div className="sp2-docs-empty">暂无研报</div>
                  }
                  <input ref={fileRef} type="file" accept=".pdf" style={{display:'none'}} onChange={handleUpload} />
                  <button onClick={() => fileRef.current.click()} disabled={uploading} className="btn-ghost sp2-docs-btn">
                    {uploading ? '解析中...' : '上传研报'}
                  </button>
                  {uploadMsg && <div className="sp2-docs-msg">{uploadMsg}</div>}
                </div>
              </div>
            )}
          </div>
        )}
          </>
        )}
      </div>

      {/* ── 右侧：对话 / 笔记 ────────────────────────────────────── */}
      <div className="sp2-chat">

        {/* Tab 切换 */}
        <div className="sp2-right-tabs">
          <button className={`sp2-right-tab ${rightTab === 'chat' ? 'active' : ''}`} onClick={() => setRightTab('chat')}>
            对话 {messages.length > 0 && <span className="sp2-note-count">{messages.filter(m => m.type === 'user').length}</span>}
          </button>
          <button className={`sp2-right-tab ${rightTab === 'notes' ? 'active' : ''}`} onClick={() => setRightTab('notes')}>
            我的笔记 {notes.length > 0 && <span className="sp2-note-count">{notes.length}</span>}
          </button>
          {messages.length > 0 && stockCode && (
            <button className="sp2-clear-btn" onClick={async () => {
              await clearChatHistory(stockCode).catch(() => {})
              setMessages([])
            }} title="清空对话记录">清空</button>
          )}
        </div>

        {/* ── 笔记面板 ── */}
        {rightTab === 'notes' && (
          <div className="sp2-notes-panel">
            {/* 输入框 */}
            <div className="sp2-note-editor">
              <textarea
                className="sp2-note-textarea"
                placeholder={`记录你对${stockCode ? `${stockCode}` : '投资'}的看法、分析、决策依据...`}
                value={editingNote ? editingNote.content : noteInput}
                onChange={e => editingNote
                  ? setEditingNote({ ...editingNote, content: e.target.value })
                  : setNoteInput(e.target.value)
                }
                rows={4}
              />
              <div className="sp2-note-actions">
                {editingNote && (
                  <button className="btn-ghost" onClick={() => setEditingNote(null)}>取消</button>
                )}
                <button className="btn-primary" onClick={handleSaveNote} disabled={noteSaving}>
                  {noteSaving ? '保存中...' : editingNote ? '更新笔记' : '保存笔记'}
                </button>
              </div>
            </div>

            {/* 笔记列表 */}
            <div className="sp2-notes-list">
              {notes.length === 0 && (
                <div className="sp2-notes-empty">还没有笔记，记录下你的第一个投资观察吧</div>
              )}
              {notes.map(note => (
                <div key={note.id} className="sp2-note-card">
                  <div className="sp2-note-header">
                    <span className="sp2-note-title">{note.title}</span>
                    <div className="sp2-note-btns">
                      <button className="sp2-note-btn" onClick={() => setEditingNote({ id: note.id, content: note.content })}>编辑</button>
                      <button className="sp2-note-btn del" onClick={() => handleDeleteNote(note.id)}>删除</button>
                    </div>
                  </div>
                  <div className="sp2-note-content">{note.content}</div>
                  <div className="sp2-note-meta">
                    {note.stock_code && <span className="sp2-note-stock">{note.stock_code}</span>}
                    <span className="sp2-note-date">{note.created_at?.slice(0, 16)}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── 对话面板 ── */}
        {rightTab === 'chat' && <div className="sp2-messages">
          {messages.length === 0 && (
            <div className="sp2-welcome">
              <div className="sp2-welcome-title">你好，我是你的投资研究助手</div>
              <div className="sp2-welcome-sub">
                可以问我具体公司的财报分析，也可以让我帮你选股
              </div>
              <div className="sp2-quick-list">
                {QUICK_QUESTIONS.map(q => (
                  <button key={q} className="sp2-quick-btn" onClick={() => handleSend(q)}>{q}</button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => {
            if (msg.type === 'user') return (
              <div key={i} className="sp2-msg sp2-msg-user">
                <div className="sp2-bubble">{msg.content}</div>
              </div>
            )

            if (msg.type === 'thinking') return (
              <div key={i} className="sp2-msg sp2-msg-thinking">
                ⚙ 调用：{msg.calls.map(c => c.name).join('、')}
              </div>
            )

            if (msg.type === 'tool') return (
              <div key={i} className="sp2-msg sp2-msg-tool">
                <span className="sp2-tool-tag">📊 {msg.tool}</span>
                <pre className="sp2-tool-pre">{msg.content.slice(0, 300)}{msg.content.length > 300 ? '...' : ''}</pre>
              </div>
            )

            if (msg.type === 'assistant') return (
              <div key={i} className="sp2-msg sp2-msg-assistant">
                <div className="sp2-assistant-avatar">✦</div>
                <div className="sp2-assistant-body">
                  <div className="sp2-assistant-text">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                  {msg.sources?.length > 0 && (
                    <div className="sp2-sources">
                      {msg.sources.map((s, j) => <span key={j} className="source-tag">{s}</span>)}
                    </div>
                  )}
                  <FeedbackBar
                    code={stockCode}
                    question={msg.question || ''}
                    answer={msg.content}
                    sources={msg.sources || []}
                  />
                </div>
              </div>
            )

            if (msg.type === 'error') return (
              <div key={i} className="sp2-msg"><div className="error">{msg.content}</div></div>
            )

            return null
          })}

          {sending && (
            <div className="sp2-msg sp2-msg-thinking">
              <span className="sp2-thinking-dots"><span/><span/><span/></span>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>}

        {/* 输入框仅在对话 Tab 显示 */}
        {rightTab === 'chat' && <div className="sp2-input-bar">
          <div className="sp2-input-wrap">
            <input
              ref={inputRef}
              className="sp2-input"
              placeholder="问财报数据、公司分析、选股策略..."
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
              disabled={sending}
            />
            <button
              className="sp2-send-btn"
              onClick={() => handleSend()}
              disabled={!input.trim() || sending}
            >
              ↑
            </button>
          </div>
        </div>}

      </div>
    </div>
  )
}
