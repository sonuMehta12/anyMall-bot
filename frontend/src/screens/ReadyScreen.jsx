import './ReadyScreen.css'

const SPECIES_EMOJI = { dog: '🐕', cat: '🐱' }

export default function ReadyScreen({ pet, parentName, onStart }) {
  const petEmoji = SPECIES_EMOJI[pet.species] || '🐾'

  return (
    <div className="ready">
      <div className="ready-body">
        <div className="ready-mascots">
          <span className="ready-pet-emoji">{petEmoji}</span>
          <span className="ready-connector">meets</span>
          <span className="ready-turtle">🐢</span>
        </div>

        <h2 className="ready-title">
          {parentName}, you're all set!
        </h2>

        <p className="ready-desc">
          AnyMall-chan is ready to meet <strong>{pet.name}</strong> and start building their profile — one conversation at a time.
        </p>

        <div className="ready-profile-card">
          <div className="ready-profile-row">
            <span className="rp-label">Name</span>
            <span className="rp-value">{pet.name}</span>
          </div>
          {pet.breed && (
            <div className="ready-profile-row">
              <span className="rp-label">Breed</span>
              <span className="rp-value">{pet.breed}</span>
            </div>
          )}
          <div className="ready-profile-row">
            <span className="rp-label">Stage</span>
            <span className="rp-value rp-badge">{pet.life_stage}</span>
          </div>
          <div className="ready-profile-row">
            <span className="rp-label">Profile</span>
            <span className="rp-value rp-conf">
              <span
                className="rp-conf-dot"
                style={{ background: pet.confidence?.label === 'yellow' ? '#EAB308' : '#EF4444' }}
              />
              Just getting started
            </span>
          </div>
        </div>

        <div className="ready-next-steps">
          <div className="rns-icon">💬</div>
          <div>
            <div className="rns-title">What happens next</div>
            <div className="rns-text">
              AnyMall-chan will chat naturally with you to learn about {pet.name}. No long forms — just friendly conversation.
            </div>
          </div>
        </div>
      </div>

      <div className="ready-footer">
        <button className="ready-start-btn" onClick={onStart}>
          Start chatting with {pet.name}!
          <span>→</span>
        </button>
      </div>
    </div>
  )
}
