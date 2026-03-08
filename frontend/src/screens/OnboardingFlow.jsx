import { useState } from 'react'
import './OnboardingFlow.css'

const TOTAL_STEPS = 4

// ── Diet options (Rank A from PRD) ───────────────────────────────────────────
const DIET_OPTIONS = [
  { value: 'dry_kibble',  label: 'Dry kibble',  emoji: '🥣' },
  { value: 'wet_food',    label: 'Wet food',    emoji: '🍖' },
  { value: 'raw_food',    label: 'Raw food',    emoji: '🥩' },
  { value: 'homemade',    label: 'Homemade',    emoji: '🍳' },
  { value: 'mixed',       label: 'Mix of these', emoji: '🔀' },
]

// ── Lifestyle options (Rank A from PRD) ──────────────────────────────────────
const LIFESTYLE_OPTIONS = [
  { value: 'indoor',  label: 'Mostly indoors',  emoji: '🏠' },
  { value: 'outdoor', label: 'Mostly outdoors', emoji: '🌿' },
  { value: 'mixed',   label: 'Both',            emoji: '↔️' },
]

export default function OnboardingFlow({ onComplete, loading = false }) {
  const [step, setStep] = useState(1)
  const [data, setData] = useState({
    parentName: '',
    petName: '',
    species: '',
    breed: '',
    ageYears: '',
    gender: '',
    dietType: '',
    lifestyle: '',
  })

  function update(field, value) {
    setData(d => ({ ...d, [field]: value }))
  }

  function next() {
    if (step < TOTAL_STEPS) setStep(s => s + 1)
    else onComplete(data)
  }

  function back() {
    if (step > 1) setStep(s => s - 1)
  }

  function skip() {
    onComplete(data)
  }

  const canContinue = {
    1: data.parentName.trim().length > 0,
    2: data.petName.trim().length > 0 && data.species !== '',
    3: true, // breed/age/gender are optional extras
    4: true, // entire step is optional
  }[step]

  return (
    <div className="onboarding">
      {/* Progress bar */}
      <div className="ob-progress">
        {step > 1 && (
          <button className="ob-back-btn" onClick={back}>←</button>
        )}
        <div className="ob-dots">
          {Array.from({ length: TOTAL_STEPS }, (_, i) => (
            <div
              key={i}
              className={`ob-dot ${i + 1 <= step ? 'ob-dot--filled' : ''}`}
            />
          ))}
        </div>
        <span className="ob-step-label">{step} of {TOTAL_STEPS}</span>
      </div>

      {/* Step content */}
      <div className="ob-content">
        {step === 1 && <Step1 data={data} update={update} />}
        {step === 2 && <Step2 data={data} update={update} />}
        {step === 3 && <Step3 data={data} update={update} />}
        {step === 4 && <Step4 data={data} update={update} />}
      </div>

      {/* Footer actions */}
      <div className="ob-footer">
        {step === 4 && (
          <button className="ob-skip-btn" onClick={skip}>
            Skip for now
          </button>
        )}
        <button
          className={`ob-continue-btn ${canContinue && !loading ? 'ob-continue-btn--active' : ''}`}
          onClick={next}
          disabled={!canContinue || loading}
        >
          {loading
            ? 'Setting up...'
            : step === TOTAL_STEPS
              ? `All set! Meet AnyMall-chan →`
              : 'Continue →'}
        </button>
      </div>
    </div>
  )
}

// ── Step 1: Parent name ───────────────────────────────────────────────────────
function Step1({ data, update }) {
  return (
    <div className="ob-step">
      <div className="ob-illustration">👋</div>
      <h2 className="ob-title">Hi there!</h2>
      <p className="ob-subtitle">
        What should AnyMall-chan call you?
      </p>
      <div className="ob-field">
        <label className="ob-label">Your name</label>
        <input
          className="ob-input"
          type="text"
          placeholder="e.g. Sarah"
          value={data.parentName}
          onChange={e => update('parentName', e.target.value)}
          autoFocus
        />
      </div>
      <p className="ob-hint">
        AnyMall-chan will use this to personalise your experience
      </p>
    </div>
  )
}

// ── Step 2: Pet name + species ────────────────────────────────────────────────
function Step2({ data, update }) {
  return (
    <div className="ob-step">
      <div className="ob-illustration">🐾</div>
      <h2 className="ob-title">
        {data.parentName ? `Nice to meet you, ${data.parentName}!` : 'Tell us about your pet!'}
      </h2>
      <p className="ob-subtitle">Let's start with the basics</p>

      <div className="ob-field">
        <label className="ob-label">Your pet's name</label>
        <input
          className="ob-input"
          type="text"
          placeholder="e.g. Luna, Mochi, Buddy..."
          value={data.petName}
          onChange={e => update('petName', e.target.value)}
          autoFocus
        />
      </div>

      <div className="ob-field">
        <label className="ob-label">What kind of pet?</label>
        <div className="ob-species-grid">
          {[
            { value: 'dog', emoji: '🐕', label: 'Dog' },
            { value: 'cat', emoji: '🐱', label: 'Cat' },
          ].map(opt => (
            <button
              key={opt.value}
              className={`ob-species-card ${data.species === opt.value ? 'selected' : ''}`}
              onClick={() => update('species', opt.value)}
            >
              <span className="species-emoji">{opt.emoji}</span>
              <span className="species-label">{opt.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Step 3: Pet details ───────────────────────────────────────────────────────
function Step3({ data, update }) {
  const name = data.petName || 'your pet'
  const speciesEmoji = data.species === 'cat' ? '🐱' : '🐕'

  return (
    <div className="ob-step">
      <div className="ob-illustration">{speciesEmoji}</div>
      <h2 className="ob-title">A bit more about {name}</h2>
      <p className="ob-subtitle">This helps AnyMall-chan personalise everything</p>

      <div className="ob-field">
        <label className="ob-label">Breed <span className="ob-optional">(optional)</span></label>
        <input
          className="ob-input"
          type="text"
          placeholder={data.species === 'cat' ? 'e.g. Persian, Siamese...' : 'e.g. Shiba Inu, Labrador...'}
          value={data.breed}
          onChange={e => update('breed', e.target.value)}
        />
      </div>

      <div className="ob-field">
        <label className="ob-label">Age <span className="ob-optional">(optional)</span></label>
        <div className="ob-age-row">
          <button
            className="ob-age-btn"
            onClick={() => update('ageYears', Math.max(0, (parseInt(data.ageYears) || 0) - 1))}
          >−</button>
          <div className="ob-age-display">
            <span className="ob-age-number">{data.ageYears || 0}</span>
            <span className="ob-age-unit">years old</span>
          </div>
          <button
            className="ob-age-btn"
            onClick={() => update('ageYears', (parseInt(data.ageYears) || 0) + 1)}
          >+</button>
        </div>
        {(parseInt(data.ageYears) || 0) === 0 && (
          <p className="ob-age-note">Under 1 year? Set to 0 — AnyMall-chan will ask!</p>
        )}
      </div>

      <div className="ob-field">
        <label className="ob-label">Gender <span className="ob-optional">(optional)</span></label>
        <div className="ob-gender-row">
          {[
            { value: 'male',   label: '♂ Male' },
            { value: 'female', label: '♀ Female' },
            { value: '',       label: '? Not sure' },
          ].map(g => (
            <button
              key={g.value}
              className={`ob-gender-btn ${data.gender === g.value ? 'selected' : ''}`}
              onClick={() => update('gender', g.value)}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Step 4: Quick setup (optional) ───────────────────────────────────────────
function Step4({ data, update }) {
  const name = data.petName || 'your pet'

  return (
    <div className="ob-step">
      <div className="ob-illustration">🐢</div>
      <h2 className="ob-title">One quick thing</h2>
      <p className="ob-subtitle">
        Optional — helps AnyMall-chan give better advice from day one
      </p>

      <div className="ob-field">
        <label className="ob-label">What does {name} eat?</label>
        <div className="ob-option-grid">
          {DIET_OPTIONS.map(opt => (
            <button
              key={opt.value}
              className={`ob-option-card ${data.dietType === opt.value ? 'selected' : ''}`}
              onClick={() => update('dietType', data.dietType === opt.value ? '' : opt.value)}
            >
              <span className="ob-option-emoji">{opt.emoji}</span>
              <span className="ob-option-label">{opt.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="ob-field">
        <label className="ob-label">Where does {name} mostly live?</label>
        <div className="ob-lifestyle-row">
          {LIFESTYLE_OPTIONS.map(opt => (
            <button
              key={opt.value}
              className={`ob-lifestyle-btn ${data.lifestyle === opt.value ? 'selected' : ''}`}
              onClick={() => update('lifestyle', data.lifestyle === opt.value ? '' : opt.value)}
            >
              <span>{opt.emoji}</span>
              <span>{opt.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="ob-rank-note">
        <span className="ob-rank-icon">💡</span>
        <span>
          Everything else — health history, exercise, routine — AnyMall-chan learns naturally through conversation.
          No forms, just chat.
        </span>
      </div>
    </div>
  )
}
