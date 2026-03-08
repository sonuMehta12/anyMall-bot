import { useEffect, useState } from 'react'
import ConfidenceBar from '../components/ConfidenceBar.jsx'
import { fetchPets } from '../api.js'
import './PetSelect.css'

const SPECIES_EMOJI = { dog: '🐕', cat: '🐱' }

// Fallback data if backend isn't running
const FALLBACK_PETS = [
  {
    id: 'luna-001',
    name: 'Luna',
    species: 'dog',
    breed: 'Shiba Inu',
    life_stage: 'adult',
    date_of_birth: '2024-02-10',
    confidence: { score: 65, label: 'yellow' },
  },
  {
    id: 'koko-001',
    name: 'Koko',
    species: 'cat',
    breed: 'Persian',
    life_stage: 'adult',
    date_of_birth: '2020-07-22',
    confidence: { score: 15, label: 'red' },
  },
]

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

export default function PetSelect({ onSelectPet, onBack }) {
  const [pets, setPets] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchPets()
      .then(setPets)
      .catch(() => setPets(FALLBACK_PETS))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="petselect">
      {/* Header */}
      <div className="petselect-header">
        <button className="back-btn" onClick={onBack}>←</button>
        <div>
          <h2 className="petselect-title">Your Pets</h2>
          <p className="petselect-subtitle">Who would you like to check in with today?</p>
        </div>
        <div className="header-mascot">🐢</div>
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
        ) : (
          pets.map(pet => (
            <PetCard key={pet.id} pet={pet} onSelect={onSelectPet} />
          ))
        )}
      </div>
    </div>
  )
}

function PetCard({ pet, onSelect }) {
  const emoji = SPECIES_EMOJI[pet.species] || '🐾'
  const age = calcAge(pet.date_of_birth)
  const conf = pet.confidence || { score: 0, label: 'red' }

  const borderColor = {
    green: '#22C55E',
    yellow: '#EAB308',
    red: '#EF4444',
  }[conf.label] || '#EF4444'

  return (
    <div className="pet-card" style={{ '--border-color': borderColor }}>
      <div className="pet-card-top">
        <div className="pet-avatar" style={{ borderColor }}>
          {emoji}
        </div>
        <div className="pet-info">
          <h3 className="pet-name">{pet.name}</h3>
          <p className="pet-meta">
            {pet.breed}
            {age && <span> · {age}</span>}
            <span> · {pet.life_stage}</span>
          </p>
        </div>
      </div>

      <ConfidenceBar score={conf.score} label={conf.label} variant="full" />

      <button className="pet-chat-btn" onClick={() => onSelect(pet)}>
        Chat with {pet.name}
        <span>→</span>
      </button>
    </div>
  )
}
