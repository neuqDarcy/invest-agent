import { useState } from 'react'
import { buildKnowledgeBase, askKnowledge, getKnowledgeStatus } from '../api'

export default function KnowledgePage() {
  const [code, setCode] = useState('')
  const [question, setQuestion] = useState('')
  const [building, setBuilding] = useState(false)
  const [asking, setAsking] = useState(false)
  const [buildResult, setBuildResult] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [status, setStatus] = useState(null)
  const [error, setError] = useState('')

  async function handleBuild() {
    if (!code) return
    setBuilding(true)
    setError('')
    setBuildResult(null)
    try {
      const data = await buildKnowledgeBase(code)
      setBuildResult(data)
      // 同步更新状态
      const s = await getKnowledgeStatus(code)
      setStatus(s)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setBuilding(false)
    }
  }

  async function handleAsk(e) {
    e.preventDefault()
    if (!code || !question) return
    setAsking(true)
    setError('')
    setAnswer(null)
    try {
      const data = await askKnowledge(code, question)
      setAnswer(data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setAsking(false)
    }
  }

  async function handleCheckStatus() {
    if (!code) return
    try {
      const s = await getKnowledgeStatus(code)
      setStatus(s)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    }
  }

  return (
    <div className="page">
      <h2>知识库问答</h2>
      <p className="desc">基于公司年报建立知识库，支持财务数字精确查询和语义问答</p>

      {/* 建库区 */}
      <div className="card">
        <div className="kb-section-title">第一步：建立知识库</div>
        <div className="form-row">
          <input
            placeholder="股票代码，如 600519"
            value={code}
            onChange={e => setCode(e.target.value)}
            className="input"
            style={{ width: 200 }}
          />
          <button onClick={handleBuild} disabled={!code || building} className="btn-primary">
            {building ? '建库中，请稍候...' : '建立知识库'}
          </button>
          <button onClick={handleCheckStatus} disabled={!code} className="btn-ghost">
            查看状态
          </button>
        </div>

        {building && (
          <div className="kb-loading">
            正在下载年报并向量化，首次约需 1-3 分钟，请耐心等待...
          </div>
        )}

        {buildResult && (
          <div className="kb-build-result">
            <div className="kb-stats">
              <span className="stat-item success">✓ 新入库 {buildResult.indexed} 份</span>
              <span className="stat-item skip">↩ 跳过 {buildResult.skipped} 份</span>
              {buildResult.failed > 0 && (
                <span className="stat-item fail">✗ 失败 {buildResult.failed} 份</span>
              )}
            </div>
            <div className="kb-details">
              {buildResult.details.map((d, i) => (
                <div key={i} className={`detail-item ${d.startsWith('[成功]') ? 'ok' : d.startsWith('[跳过]') ? 'skip' : 'fail'}`}>
                  {d}
                </div>
              ))}
            </div>
          </div>
        )}

        {status && status.indexed_count > 0 && (
          <div className="kb-status">
            <span className="status-dot" />
            已入库 {status.indexed_count} 份报告：
            {status.reports.map(r => (
              <span key={r.id} className="report-tag">{r.title}</span>
            ))}
          </div>
        )}

        {status && status.indexed_count === 0 && (
          <div className="kb-status empty">该股票尚未建立知识库</div>
        )}
      </div>

      {/* 问答区 */}
      <div className="card">
        <div className="kb-section-title">第二步：提问</div>
        <form onSubmit={handleAsk}>
          <div className="qa-input-row">
            <input
              placeholder="输入问题，如：2023年营收和净利润是多少？公司主要风险有哪些？"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              className="input qa-input"
            />
            <button type="submit" disabled={!code || !question || asking} className="btn-primary">
              {asking ? '思考中...' : '提问'}
            </button>
          </div>
          <div className="qa-examples">
            常用问题：
            {[
              '2023年营收和净利润是多少？同比增速如何？',
              '公司主要的经营风险有哪些？',
              '公司的核心竞争力是什么？',
              '经营活动现金流情况如何？',
            ].map(q => (
              <span key={q} className="example-tag" onClick={() => setQuestion(q)}>{q}</span>
            ))}
          </div>
        </form>

        {error && <div className="error">{error}</div>}

        {answer && (
          <div className="qa-result">
            <div className="qa-answer">{answer.answer}</div>
            {answer.sources?.length > 0 && (
              <div className="qa-sources">
                <span className="sources-label">数据来源：</span>
                {answer.sources.map((s, i) => (
                  <span key={i} className="source-tag">{s}</span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
