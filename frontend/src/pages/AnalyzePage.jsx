import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { analyzeReport } from '../api'

export default function AnalyzePage() {
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState('')
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!file) return
    setLoading(true)
    setError('')
    setReport('')
    try {
      const data = await analyzeReport(file)
      setReport(data.report)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <h2>财报分析</h2>
      <p className="desc">上传公司财报、研报、公告（PDF），自动生成基本面分析报告</p>

      <form onSubmit={handleSubmit} className="card">
        <div className="upload-area">
          <input
            type="file"
            accept=".pdf"
            onChange={e => setFile(e.target.files[0])}
            id="file-input"
          />
          <label htmlFor="file-input" className="upload-label">
            {file ? `✓ ${file.name}` : '点击选择 PDF 文件'}
          </label>
        </div>
        <button type="submit" disabled={!file || loading} className="btn-primary">
          {loading ? '分析中...' : '开始分析'}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {report && (
        <div className="card report">
          <ReactMarkdown>{report}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}
