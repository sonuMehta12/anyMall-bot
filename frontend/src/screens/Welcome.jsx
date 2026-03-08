import './Welcome.css'

export default function Welcome({ onContinue, onTryDemo }) {
  return (
    <div className="welcome">
      <div className="welcome-top">
        <div className="welcome-mascot">
          <div className="mascot-glow" />
          <span className="mascot-emoji">🐢</span>
        </div>

        <div className="welcome-badge">Pet Companion</div>

        <h1 className="welcome-title">
          Meet<br />AnyMall-chan
        </h1>

        <p className="welcome-subtitle">
          Your pet's personal companion who gets to know them naturally — one conversation at a time.
        </p>

        <div className="welcome-quote">
          <span className="quote-mark">"</span>
          You understand without me having to say everything
          <span className="quote-mark">"</span>
        </div>
      </div>

      <div className="welcome-bottom">
        <div className="welcome-features">
          <div className="feature-item">
            <span className="feature-icon">💬</span>
            <span>Natural conversations</span>
          </div>
          <div className="feature-item">
            <span className="feature-icon">🧠</span>
            <span>Learns over time</span>
          </div>
          <div className="feature-item">
            <span className="feature-icon">❤️</span>
            <span>Always empathetic</span>
          </div>
        </div>

        <button className="welcome-cta" onClick={onContinue}>
          Set up my pet
          <span className="cta-arrow">→</span>
        </button>

        <button className="welcome-demo-btn" onClick={onTryDemo}>
          🐾 Try with demo pet (Luna)
        </button>

        <p className="welcome-note">
          Quick setup · only the basics · rest via conversation
        </p>
      </div>
    </div>
  )
}
