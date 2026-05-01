import { useState } from 'react'
import { updateProfile } from '../api'

const STYLES = [
  { value: 'value',  label: '价值投资', desc: '低估值、高护城河、长期持有' },
  { value: 'growth', label: '成长投资', desc: '高增速、扩张期企业' },
  { value: 'garp',   label: 'GARP',    desc: '合理价格买成长（折中）' },
]
const RISKS = [
  { value: 'conservative', label: '保守', desc: '稳健为主，回撤优先' },
  { value: 'moderate',     label: '均衡', desc: '收益与风险兼顾' },
  { value: 'aggressive',   label: '激进', desc: '追求高收益，接受大波动' },
]
const HORIZONS = [
  { value: 'short',  label: '短期 <1年' },
  { value: 'medium', label: '中期 1-3年' },
  { value: 'long',   label: '长期 >3年' },
]
const INDUSTRIES = ['消费', '科技', '医药', '金融', '能源', '制造', '地产', '农业']

export default function ProfilePage({ user, token, onUpdate, onClose }) {
  const p = user?.profile || {}
  const [style, setStyle]         = useState(p.invest_style || 'value')
  const [risk, setRisk]           = useState(p.risk_level || 'moderate')
  const [horizon, setHorizon]     = useState(p.invest_horizon || 'long')
  const [target, setTarget]       = useState(p.target_return || 15)
  const [industries, setIndustries] = useState(p.focus_industries || [])
  const [notes, setNotes]         = useState(p.notes || '')
  const [saving, setSaving]       = useState(false)

  function toggleIndustry(ind) {
    setIndustries(prev =>
      prev.includes(ind) ? prev.filter(i => i !== ind) : [...prev, ind]
    )
  }

  async function handleSave() {
    setSaving(true)
    try {
      const data = await updateProfile(token, {
        invest_style: style, risk_level: risk,
        invest_horizon: horizon, target_return: Number(target),
        focus_industries: industries, notes,
      })
      onUpdate(data.profile)
    } catch (e) { console.error(e) }
    finally { setSaving(false) }
  }

  return (
    <div className="profile-overlay" onClick={onClose}>
      <div className="profile-panel" onClick={e => e.stopPropagation()}>
        <div className="profile-header">
          <span className="profile-title">投资画像</span>
          <button className="profile-close" onClick={onClose}>×</button>
        </div>

        <div className="profile-body">
          <div className="profile-section">
            <div className="profile-label">投资风格</div>
            <div className="profile-options">
              {STYLES.map(s => (
                <button key={s.value} className={`profile-opt ${style === s.value ? 'active' : ''}`}
                  onClick={() => setStyle(s.value)}>
                  <div className="opt-label">{s.label}</div>
                  <div className="opt-desc">{s.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="profile-section">
            <div className="profile-label">风险偏好</div>
            <div className="profile-options">
              {RISKS.map(r => (
                <button key={r.value} className={`profile-opt ${risk === r.value ? 'active' : ''}`}
                  onClick={() => setRisk(r.value)}>
                  <div className="opt-label">{r.label}</div>
                  <div className="opt-desc">{r.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="profile-section">
            <div className="profile-label">投资周期</div>
            <div className="profile-options horizon">
              {HORIZONS.map(h => (
                <button key={h.value} className={`profile-opt ${horizon === h.value ? 'active' : ''}`}
                  onClick={() => setHorizon(h.value)}>
                  <div className="opt-label">{h.label}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="profile-section">
            <div className="profile-label">目标年化收益率：<strong>{target}%</strong></div>
            <input type="range" min="5" max="50" step="1" value={target}
              onChange={e => setTarget(e.target.value)} className="profile-slider" />
            <div className="profile-slider-labels"><span>5%</span><span>25%</span><span>50%</span></div>
          </div>

          <div className="profile-section">
            <div className="profile-label">关注行业（可多选）</div>
            <div className="profile-industries">
              {INDUSTRIES.map(ind => (
                <button key={ind} className={`industry-tag ${industries.includes(ind) ? 'active' : ''}`}
                  onClick={() => toggleIndustry(ind)}>{ind}</button>
              ))}
            </div>
          </div>

          <div className="profile-section">
            <div className="profile-label">备注（投资理念、特殊偏好等）</div>
            <textarea className="input profile-notes" rows={3} value={notes}
              onChange={e => setNotes(e.target.value)} placeholder="例如：只投自己看得懂的行业..." />
          </div>
        </div>

        <div className="profile-footer">
          <button className="btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存画像'}
          </button>
        </div>
      </div>
    </div>
  )
}
