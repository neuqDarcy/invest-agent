import { useState } from 'react'
import { screenStocks } from '../api'

export default function ScreenPage() {
  const [form, setForm] = useState({
    market_cap_min: '',
    market_cap_max: '',
    pb_max: '',
    pe_max: '',
    top_n: 50,
  })
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState(null)
  const [error, setError] = useState('')

  function handleChange(e) {
    setForm(f => ({ ...f, [e.target.name]: e.target.value }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    setResults(null)
    const criteria = {}
    if (form.market_cap_min) criteria.market_cap_min = Number(form.market_cap_min)
    if (form.market_cap_max) criteria.market_cap_max = Number(form.market_cap_max)
    if (form.pb_max) criteria.pb_max = Number(form.pb_max)
    if (form.pe_max) criteria.pe_max = Number(form.pe_max)
    criteria.top_n = Number(form.top_n)
    try {
      const data = await screenStocks(criteria)
      setResults(data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <h2>智能选股</h2>
      <p className="desc">按市值、PB、PE 筛选股票，按综合评分排序</p>

      <form onSubmit={handleSubmit} className="card">
        <div className="form-grid">
          <label>市值下限（亿）<input name="market_cap_min" value={form.market_cap_min} onChange={handleChange} placeholder="如 50" className="input" /></label>
          <label>市值上限（亿）<input name="market_cap_max" value={form.market_cap_max} onChange={handleChange} placeholder="如 200" className="input" /></label>
          <label>PB 上限<input name="pb_max" value={form.pb_max} onChange={handleChange} placeholder="如 3" className="input" /></label>
          <label>PE 上限<input name="pe_max" value={form.pe_max} onChange={handleChange} placeholder="如 30" className="input" /></label>
          <label>返回数量<input name="top_n" type="number" value={form.top_n} onChange={handleChange} className="input" /></label>
        </div>
        <button type="submit" disabled={loading} className="btn-primary">
          {loading ? '筛选中...' : '开始筛选'}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {results && (
        <div className="card">
          <div className="table-header">共筛出 {results.count} 只股票</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>代码</th><th>名称</th><th>市值（亿）</th>
                  <th>现价</th><th>PB</th><th>PE</th><th>评分</th>
                </tr>
              </thead>
              <tbody>
                {results.stocks.map(s => (
                  <tr key={s.code}>
                    <td className="code">{s.code}</td>
                    <td>{s.name}</td>
                    <td>{s.market_cap}</td>
                    <td>¥{s.current_price}</td>
                    <td>{s.pb ?? '-'}</td>
                    <td>{s.pe ?? '-'}</td>
                    <td>{(s.score * 100).toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
