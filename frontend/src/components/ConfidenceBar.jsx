import './ConfidenceBar.css'

/**
 * ConfidenceBar
 * variant: 'full' | 'compact'
 * score: 0–100
 * label: 'green' | 'yellow' | 'red'
 */
export default function ConfidenceBar({ score = 0, label = 'red', variant = 'full' }) {
  const pct = Math.min(Math.max(score, 0), 100)

  const colorMap = {
    green:  { bar: '#22C55E', bg: '#DCFCE7', text: '#15803D' },
    yellow: { bar: '#EAB308', bg: '#FEF9C3', text: '#A16207' },
    red:    { bar: '#EF4444', bg: '#FEE2E2', text: '#B91C1C' },
  }
  const colors = colorMap[label] || colorMap.red

  const stateText = {
    green:  'AnyMall-chan knows this pet well!',
    yellow: 'A few more chats will help a lot',
    red:    'Let\'s get to know this pet!',
  }

  if (variant === 'compact') {
    return (
      <div className="confidence-compact" style={{ '--bar-color': colors.bar }}>
        <div className="confidence-compact-track">
          <div
            className="confidence-compact-fill"
            style={{ width: `${pct}%`, background: colors.bar }}
          />
        </div>
        <span className="confidence-compact-score" style={{ color: colors.text }}>
          {pct}%
        </span>
      </div>
    )
  }

  return (
    <div className="confidence-full" style={{ background: colors.bg }}>
      <div className="confidence-full-header">
        <span className="confidence-turtle">🐢</span>
        <div>
          <div className="confidence-full-label" style={{ color: colors.text }}>
            Understanding level
          </div>
          <div className="confidence-full-state" style={{ color: colors.text }}>
            {stateText[label]}
          </div>
        </div>
        <span className="confidence-full-score" style={{ color: colors.text }}>
          {pct}%
        </span>
      </div>
      <div className="confidence-full-track">
        <div
          className="confidence-full-fill"
          style={{ width: `${pct}%`, background: colors.bar }}
        />
      </div>
    </div>
  )
}
