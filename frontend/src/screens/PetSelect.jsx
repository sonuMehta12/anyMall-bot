import { useEffect, useState } from 'react'
import ConfidenceBar from '../components/ConfidenceBar.jsx'
import { fetchPets } from '../api.js'
import './PetSelect.css'

const SPECIES_EMOJI = { dog: '🐕', cat: '🐱' }

function calcAge(dob) {
  if (!dob) return null
  const diff = Date.now() - new Date(dob).getTime()
  const years = Math.floor(diff / (365.25 * 24 * 3600 * 1000))
  if (years < 1) {
    const months = Math.floor(diff / (30.44 * 24 * 3600 * 1000))
    return `${months}mo`
  }
  return `${years}yo`
}

export default function PetSelect({ userCode, onUserCodeChange, onStartChat }) {
  const [pets, setPets] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(new Set())

  // Fetch pets when userCode changes
  useEffect(() => {
    if (!userCode) return
    setLoading(true)
    setError(null)
    fetchPets(userCode)
      .then(data => {
        setPets(data)
        setSelected(new Set())
      })
      .catch(err => {
        setError(err.message)
        setPets([])
      })
      .finally(() => setLoading(false))
  }, [userCode])

  function togglePet(petId) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(petId)) {
        next.delete(petId)
      } else {
        // Max 2 pets
        if (next.size >= 2) return prev
        next.add(petId)
      }
      return next
    })
  }

  function handleStartChat() {
    const selectedPets = pets.filter(p => selected.has(p.pet_id))
    onStartChat(selectedPets)
  }

  return (
    <div className="petselect">
      {/* Header */}
      <div className="petselect-header">
        <div>
          <h2 className="petselect-title">Your Pets</h2>
          <p className="petselect-subtitle">Select 1 or 2 pets to chat about</p>
        </div>
        <div className="header-mascot">🐢</div>
      </div>

      {/* User code input */}
      <div className="usercode-input">
        <label className="usercode-label">User Code</label>
        <input
          className="usercode-field"
          type="password"
          value={userCode}
          onChange={e => onUserCodeChange(e.target.value)}
          placeholder="Enter your user code"
        />
      </div>

      {/* Onboarding info card */}
      <div className="onboarding-info">
        <div className="onboarding-info-icon">💡</div>
        <div>
          <div className="onboarding-info-title">What we collected at setup</div>
          <div className="onboarding-info-text">
            Name · Species · Breed · Age · Gender — the rest, AnyMall-chan learns naturally through chat.
          </div>
        </div>
      </div>

      {/* Pet cards */}
      <div className="petselect-list">
        {loading ? (
          <div className="loading-state">
            <div className="loading-dot" /><div className="loading-dot" /><div className="loading-dot" />
          </div>
        ) : error ? (
          <div className="error-state">Failed to load pets: {error}</div>
        ) : (
          pets.map(pet => (
            <PetCard
              key={pet.pet_id}
              pet={pet}
              isSelected={selected.has(pet.pet_id)}
              onToggle={() => togglePet(pet.pet_id)}
            />
          ))
        )}
      </div>

      {/* Start Chat button */}
      {selected.size > 0 && (
        <div className="petselect-footer">
          <button className="start-chat-btn" onClick={handleStartChat}>
            Chat with {selected.size === 1 ? '1 pet' : '2 pets'} →
          </button>
        </div>
      )}
    </div>
  )
}

function PetCard({ pet, isSelected, onToggle }) {
  const emoji = SPECIES_EMOJI[pet.species] || '🐾'
  const age = calcAge(pet.date_of_birth)
  const conf = pet.confidence || { score: 0, label: 'red' }

  const borderColor = isSelected
    ? '#7c3aed'
    : { green: '#22C55E', yellow: '#EAB308', red: '#EF4444' }[conf.label] || '#e5e7eb'

  return (
    <div
      className={`pet-card ${isSelected ? 'pet-card-selected' : ''}`}
      style={{ '--border-color': borderColor }}
      onClick={onToggle}
    >
      <div className="pet-card-top">
        <div className="pet-avatar" style={{ borderColor }}>
          {emoji}
        </div>
        <div className="pet-info">
          <h3 className="pet-name">{pet.name}</h3>
          <p className="pet-meta">
            {pet.breed}
            {age && <span> · {age}</span>}
            {pet.life_stage && <span> · {pet.life_stage}</span>}
          </p>
        </div>
        <div className={`pet-check ${isSelected ? 'pet-check-active' : ''}`}>
          {isSelected ? '✓' : ''}
        </div>
      </div>

      <ConfidenceBar score={conf.score} label={conf.label} variant="full" />
    </div>
  )
}
