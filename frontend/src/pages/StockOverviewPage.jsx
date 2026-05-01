import { useState } from 'react'
import { getStockOverview } from '../api'

const STATUS_COLOR = { 低估: '#16a34a', 合理: '#2563eb', 高估: '#dc2626' }
const STATUS_BG = { 低估: '#f0fdf4', 合理: '#eff6ff', 高估: '#fff1f2' }

function MetricValue({ value }) {
  if (value == null) return <span className="no-data">—</span>
  const abs = Math.abs(value)
  if (abs >= 1e8) return <span>{(value / 1e8).toFixed(2)} 亿</span>
  if (abs >= 1e4) return <span>{(value / 1e4).toFixed(2)} 万</span>
  return <span>{value.toFixed(2)}</span>
}

export default function StockOverviewPage() {
  const [code, setCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!code.trim()) return
    setLoading(true)
    setError('')
    setData(null)
    try {
      const res = await getStockOverview(code.trim())
      setData(res)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <h2>股票概览</h2>
      <p className="desc">输入股票代码，查看实时行情、估值指标、财务数据和合理价格区间</p>

      <form onSubmit={handleSubmit} className="card form-row">
        <input
          placeholder="股票代码，如 600519"
          value={code}
          onChange={e => setCode(e.target.value)}
          className="input"
          style={{ maxWidth: 200 }}
        />
        <button type="submit" disabled={!code.trim() || loading} className="btn-primary">
          {loading ? '查询中...' : '查询'}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {data && (
        <>
          {/* 股票头部信息 */}
          <div className="card ov-header">
            <div className="ov-title">
              <span className="ov-name">{data.name}</span>
              <span className="ov-code">{data.stock_code}</span>
              {data.valuation && (
                <span className="ov-badge" style={{
                  background: STATUS_BG[data.valuation.current_status],
                  color: STATUS_COLOR[data.valuation.current_status],
                }}>
                  {data.valuation.current_status}
                </span>
              )}
            </div>
            <div className="ov-price">¥{data.current_price}</div>
          </div>

          {/* 行情 + 估值指标 */}
          <div className="card">
            <div className="ov-section-title">市场指标</div>
            <div className="ov-metrics-grid">
              <div className="ov-metric">
                <div className="ov-metric-label">总市值</div>
                <div className="ov-metric-value">{data.market_cap != null ? `${data.market_cap} 亿` : '—'}</div>
              </div>
              <div className="ov-metric">
                <div className="ov-metric-label">PE（TTM）</div>
                <div className="ov-metric-value">{data.pe_ttm != null ? data.pe_ttm.toFixed(2) : '—'}</div>
              </div>
              <div className="ov-metric">
                <div className="ov-metric-label">PB</div>
                <div className="ov-metric-value">{data.pb != null ? data.pb.toFixed(2) : '—'}</div>
              </div>
              <div className="ov-metric">
                <div className="ov-metric-label">52周最高</div>
                <div className="ov-metric-value high">{data.week52_high != null ? `¥${data.week52_high}` : '—'}</div>
              </div>
              <div className="ov-metric">
                <div className="ov-metric-label">52周最低</div>
                <div className="ov-metric-value low">{data.week52_low != null ? `¥${data.week52_low}` : '—'}</div>
              </div>
              {data.pb_stats && (
                <div className="ov-metric">
                  <div className="ov-metric-label">PB历史分位</div>
                  <div className="ov-metric-value">{data.pb_stats.pb_percentile}%</div>
                </div>
              )}
            </div>
          </div>

          {/* 估值区间 */}
          {data.valuation && (
            <div className="card">
              <div className="ov-section-title">估值区间（{data.valuation.model}）</div>
              <div className="val-grid">
                <div className="val-item buy">
                  <div className="val-label">买入参考价</div>
                  <div className="val-value">≤ ¥{data.valuation.buy_price}</div>
                </div>
                <div className="val-item fair">
                  <div className="val-label">合理区间</div>
                  <div className="val-value">¥{data.valuation.fair_value_low} ~ ¥{data.valuation.fair_value_high}</div>
                </div>
                <div className="val-item sell">
                  <div className="val-label">卖出参考价</div>
                  <div className="val-value">≥ ¥{data.valuation.sell_price}</div>
                </div>
              </div>
              <div className="val-reasoning">
                <pre>{data.valuation.reasoning}</pre>
              </div>
            </div>
          )}

          {/* 财务指标 */}
          {Object.keys(data.metrics_by_year).length > 0 && (
            <div className="card">
              <div className="ov-section-title">财报财务指标</div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>指标</th>
                      {Object.keys(data.metrics_by_year).map(y => <th key={y}>{y}年</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {/* 收集所有指标名 */}
                    {Array.from(
                      new Set(Object.values(data.metrics_by_year).flatMap(m => Object.keys(m)))
                    ).map(metric => (
                      <tr key={metric}>
                        <td>{metric}</td>
                        {Object.keys(data.metrics_by_year).map(y => (
                          <td key={y}>
                            <MetricValue value={data.metrics_by_year[y][metric] ?? null} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {Object.keys(data.metrics_by_year).length === 0 && (
            <div className="card" style={{ color: '#94a3b8', fontSize: 14 }}>
              财务指标暂无数据，请先通过「知识库问答」页面为该股票建立知识库。
            </div>
          )}
        </>
      )}
    </div>
  )
}
