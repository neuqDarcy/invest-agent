import { useState } from 'react'
import { uploadBrokerReport, getBrokerReports } from '../api'

const RATING_COLOR = {
  '买入': '#16a34a', '增持': '#2563eb', '中性': '#64748b',
  '减持': '#f59e0b', '卖出': '#dc2626',
}

export default function BrokerPage() {
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [reports, setReports] = useState(null)
  const [filterCode, setFilterCode] = useState('')
  const [error, setError] = useState('')

  async function handleUpload(e) {
    e.preventDefault()
    if (!file) return
    setUploading(true)
    setError('')
    setResult(null)
    try {
      const data = await uploadBrokerReport(file)
      setResult(data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setUploading(false)
    }
  }

  async function handleList() {
    try {
      const data = await getBrokerReports(filterCode || null)
      setReports(data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    }
  }

  return (
    <div className="page">
      <h2>券商研报</h2>
      <p className="desc">上传券商研报 PDF，自动解析评级、目标价、核心观点、盈利预测</p>

      {/* 上传区 */}
      <div className="card">
        <div className="kb-section-title">上传研报</div>
        <form onSubmit={handleUpload}>
          <div className="upload-area">
            <input type="file" accept=".pdf" onChange={e => setFile(e.target.files[0])} id="broker-file" />
            <label htmlFor="broker-file" className="upload-label">
              {file ? `✓ ${file.name}` : '点击选择券商研报 PDF'}
            </label>
          </div>
          <button type="submit" disabled={!file || uploading} className="btn-primary">
            {uploading ? '解析中，请稍候...' : '上传并解析'}
          </button>
        </form>

        {error && <div className="error" style={{marginTop: 12}}>{error}</div>}

        {result && (
          <div className="broker-result">
            <div className="broker-header">
              <span className="broker-title">{result.title}</span>
              {result.rating && (
                <span className="broker-rating" style={{color: RATING_COLOR[result.rating] || '#64748b'}}>
                  {result.rating}
                </span>
              )}
              {result.target_price && (
                <span className="broker-target">目标价 ¥{result.target_price}</span>
              )}
            </div>

            <div className="broker-meta">
              {result.broker && <span>券商：{result.broker}</span>}
              {result.analyst && <span>分析师：{result.analyst}</span>}
              {result.report_date && <span>日期：{result.report_date}</span>}
              {result.stock_name && <span>标的：{result.stock_name}（{result.stock_code}）</span>}
              <span className="chunk-count">已入库 {result.chunk_count} 个段落</span>
            </div>

            {result.core_views?.length > 0 && (
              <div className="broker-section">
                <div className="section-label">核心观点</div>
                <ul className="view-list">
                  {result.core_views.map((v, i) => <li key={i}>{v}</li>)}
                </ul>
              </div>
            )}

            {result.profit_forecast && Object.keys(result.profit_forecast).length > 0 && (
              <div className="broker-section">
                <div className="section-label">盈利预测</div>
                <table className="forecast-table">
                  <thead>
                    <tr><th>年份</th><th>营收（亿）</th><th>净利润（亿）</th><th>EPS</th></tr>
                  </thead>
                  <tbody>
                    {Object.entries(result.profit_forecast).map(([year, d]) => (
                      <tr key={year}>
                        <td>{year}E</td>
                        <td>{d.revenue ?? '-'}</td>
                        <td>{d.net_profit ?? '-'}</td>
                        <td>{d.eps ?? '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {result.risk_warnings?.length > 0 && (
              <div className="broker-section">
                <div className="section-label">风险提示</div>
                <ul className="risk-list">
                  {result.risk_warnings.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 研报列表 */}
      <div className="card">
        <div className="kb-section-title">已入库研报</div>
        <div className="form-row" style={{marginBottom: 12}}>
          <input
            placeholder="按股票代码筛选（可选）"
            value={filterCode}
            onChange={e => setFilterCode(e.target.value)}
            className="input"
            style={{width: 200}}
          />
          <button onClick={handleList} className="btn-primary">查询</button>
        </div>

        {reports && (
          reports.reports.length === 0
            ? <div className="kb-status empty">暂无入库研报</div>
            : <div className="report-list">
                {reports.reports.map(r => (
                  <div key={r.id} className="report-card">
                    <div className="report-card-header">
                      <span className="report-card-title">{r.title}</span>
                      {r.rating && (
                        <span className="broker-rating" style={{color: RATING_COLOR[r.rating] || '#64748b'}}>
                          {r.rating}
                        </span>
                      )}
                      {r.target_price && <span className="broker-target">¥{r.target_price}</span>}
                    </div>
                    <div className="broker-meta">
                      {r.broker && <span>{r.broker}</span>}
                      {r.analyst && <span>{r.analyst}</span>}
                      {r.report_date && <span>{r.report_date}</span>}
                      {r.stock_name && <span>{r.stock_name}（{r.stock_code}）</span>}
                    </div>
                    {Array.isArray(r.core_views) && r.core_views.length > 0 && (
                      <ul className="view-list compact">
                        {r.core_views.slice(0, 2).map((v, i) => <li key={i}>{v}</li>)}
                        {r.core_views.length > 2 && <li className="more">...共 {r.core_views.length} 条观点</li>}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
        )}
      </div>
    </div>
  )
}
