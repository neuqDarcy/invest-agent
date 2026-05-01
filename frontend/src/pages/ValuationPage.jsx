import { useState } from 'react'
import { getValuation } from '../api'

const STATUS_COLOR = { 低估: '#16a34a', 合理: '#2563eb', 高估: '#dc2626' }

export default function ValuationPage() {
  const [code, setCode] = useState('')
  const [industry, setIndustry] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!code) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const data = await getValuation(code, industry || null)
      setResult(data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <h2>估值分析</h2>
      <p className="desc">基于历史 PB 分位，计算合理买入 / 卖出价格区间</p>

      <form onSubmit={handleSubmit} className="card form-row">
        <input
          placeholder="股票代码，如 600519"
          value={code}
          onChange={e => setCode(e.target.value)}
          className="input"
        />
        <input
          placeholder="所属行业（可选）"
          value={industry}
          onChange={e => setIndustry(e.target.value)}
          className="input"
        />
        <button type="submit" disabled={!code || loading} className="btn-primary">
          {loading ? '查询中...' : '查询'}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {result && (
        <div className="card">
          <div className="val-header">
            <span className="val-code">{result.stock_code}</span>
            <span className="val-status" style={{ color: STATUS_COLOR[result.current_status] }}>
              {result.current_status}
            </span>
            <span className="val-price">当前价 ¥{result.current_price}</span>
          </div>

          <div className="val-grid">
            <div className="val-item buy">
              <div className="val-label">买入参考价</div>
              <div className="val-value">≤ ¥{result.buy_price}</div>
            </div>
            <div className="val-item fair">
              <div className="val-label">合理区间</div>
              <div className="val-value">¥{result.fair_value_low} ~ ¥{result.fair_value_high}</div>
            </div>
            <div className="val-item sell">
              <div className="val-label">卖出参考价</div>
              <div className="val-value">≥ ¥{result.sell_price}</div>
            </div>
          </div>

          <div className="val-reasoning">
            <pre>{result.reasoning}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
