import axios from 'axios'

const client = axios.create({ baseURL: 'http://localhost:8000/api' })

// 自动附加 token
client.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export async function analyzeReport(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await client.post('/analyze', form)
  return res.data
}

export async function getValuation(stockCode, industryName = null, model = 'pb') {
  const res = await client.post('/valuation', {
    stock_code: stockCode,
    industry_name: industryName,
    model,
  })
  return res.data
}

export async function screenStocks(criteria) {
  const res = await client.post('/screen', criteria)
  return res.data
}

export async function buildKnowledgeBase(stockCode, reportTypes = ['annual'], startYear = 2020, endYear = 2024) {
  const res = await client.post('/knowledge/build', {
    stock_code: stockCode,
    report_types: reportTypes,
    start_year: startYear,
    end_year: endYear,
  })
  return res.data
}

export async function getKnowledgeStatus(stockCode) {
  const res = await client.get(`/knowledge/status/${stockCode}`)
  return res.data
}

export async function askKnowledge(stockCode, question, reportType = 'annual') {
  const res = await client.post('/knowledge/ask', {
    stock_code: stockCode,
    question,
    report_type: reportType,
  })
  return res.data
}

export async function getStockOverview(stockCode) {
  const res = await client.get(`/stock/overview/${stockCode}`)
  return res.data
}

export async function uploadBrokerReport(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await client.post('/knowledge/broker/upload', form)
  return res.data
}

export async function getBrokerReports(stockCode = null) {
  const params = stockCode ? { stock_code: stockCode } : {}
  const res = await client.get('/knowledge/broker/list', { params })
  return res.data
}

export async function submitFeedback(stockCode, question, answer, sources, rating, comment = '') {
  const res = await client.post('/knowledge/feedback', {
    stock_code: stockCode,
    question,
    answer,
    sources,
    rating,
    comment,
  })
  return res.data
}

export async function getFMManagers() {
  const res = await client.get('/fm/managers')
  return res.data
}

export async function fmChat(sessionId, managerId, message) {
  const res = await client.post('/fm/chat', {
    session_id: sessionId || null,
    manager_id: managerId,
    message,
  })
  return res.data
}

export async function fmClearSession(sessionId) {
  await client.delete(`/fm/session/${sessionId}`)
}

export async function unifiedChat(sessionId, managerId, message, currentStockCode = null) {
  const res = await client.post('/chat', {
    session_id: sessionId || null,
    manager_id: managerId,
    message,
    current_stock_code: currentStockCode,
  })
  return res.data
}

export async function searchStocks(keyword) {
  const res = await client.get('/stock/search', { params: { q: keyword } })
  return res.data
}

export async function getNotes(stockCode = '') {
  const res = await client.get('/notes', { params: stockCode ? { stock_code: stockCode } : {} })
  return res.data
}

export async function createNote(content, stockCode = '', title = '') {
  const res = await client.post('/notes', { content, stock_code: stockCode, title })
  return res.data
}

export async function updateNote(noteId, content, title = '') {
  const res = await client.put(`/notes/${noteId}`, { content, title })
  return res.data
}

export async function deleteNote(noteId) {
  await client.delete(`/notes/${noteId}`)
}

export async function* unifiedChatStream(sessionId, managerId, message, stockCode, lastRoute, stockName) {
  const res = await fetch('http://localhost:8000/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId || null,
      manager_id: managerId,
      message,
      current_stock_code: stockCode || null,
      last_route: lastRoute || null,
      current_stock_name: stockName || null,
    }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try { yield JSON.parse(line.slice(6)) } catch (_) {}
      }
    }
  }
}

export async function getChatHistory(stockCode = '') {
  const res = await client.get('/chat/history', { params: stockCode ? { stock_code: stockCode } : {} })
  return res.data
}

export async function clearChatHistory(stockCode = '') {
  await client.delete('/chat/history', { params: stockCode ? { stock_code: stockCode } : {} })
}

// ── 认证 ──
export async function register(username, password) {
  const res = await client.post('/auth/register', { username, password })
  return res.data
}

export async function login(username, password) {
  const res = await client.post('/auth/login', { username, password })
  return res.data
}

export async function getMe(token) {
  const res = await client.get('/auth/me', { headers: { Authorization: `Bearer ${token}` } })
  return res.data
}

export async function updateProfile(token, profile) {
  const res = await client.put('/auth/profile', profile, {
    headers: { Authorization: `Bearer ${token}` }
  })
  return res.data
}

// ── 自选列表 ──
export async function getWatchlist() {
  const res = await client.get('/watchlist')
  return res.data
}

export async function addToWatchlist(stockCode, stockName = '') {
  const res = await client.post('/watchlist', { stock_code: stockCode, stock_name: stockName })
  return res.data
}

export async function removeFromWatchlist(stockCode) {
  await client.delete(`/watchlist/${stockCode}`)
}
