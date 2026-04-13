import { useEffect, useRef, useState } from 'react'
import ChatBubble from '../components/ChatBubble.jsx'
import ConfidenceBar from '../components/ConfidenceBar.jsx'
import RecipeCard from '../components/RecipeCard.jsx'
import { sendMessage, fetchSetup, fetchConfidence, BASE } from '../api.js'
import './Chat.css'

const SPECIES_EMOJI = { dog: '🐕', cat: '🐱' }

// Generate a session ID once per page load — persists for the full conversation
function makeSessionId() {
  return crypto.randomUUID()
}

export default function Chat({ selectedPets, userCode, language, onBack }) {
  const [sessionId] = useState(makeSessionId)   // created once, never changes
  const [messages, setMessages] = useState([])
  const [inputText, setInputText] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [confidenceScore, setConfidenceScore] = useState(0)
  const [confidenceColor, setConfidenceColor] = useState('red')
  const [activeRedirect, setActiveRedirect] = useState(null)
  const [suggestedQuestions, setSuggestedQuestions] = useState([])

  const messagesEndRef = useRef(null)
  const textareaRef = useRef(null)

  const petIds = selectedPets.map(p => p.pet_id)
  const primaryPet = selectedPets[0]
  const petNames = selectedPets.map(p => p.name).join(' & ')

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

  // Fetch setup (confidence + suggested questions) on mount
  useEffect(() => {
    if (!primaryPet) return
    fetchSetup(petIds, userCode, language, 'anymall')
      .then(data => {
        setConfidenceScore(data.confidence_score ?? 0)
        setConfidenceColor(data.confidence_color ?? 'red')
        if (data.suggested_questions?.length) {
          setSuggestedQuestions(data.suggested_questions)
        }
      })
      .catch(err => console.warn('Could not fetch setup:', err))
  }, [])

  // Show opening greeting when chat first mounts
  useEffect(() => {
    const greeting = language === 'JA'
      ? (selectedPets.length === 1
        ? `こんにちは！${primaryPet.name}の調子はどうですか？🐾`
        : `こんにちは！${petNames}の調子はどうですか？🐾`)
      : (selectedPets.length === 1
        ? `Hi! How's ${primaryPet.name} doing today? 🐾`
        : `Hi! How are ${petNames} doing today? 🐾`)
    setMessages([{ id: 1, text: greeting, isUser: false }])
  }, [])

  async function handleSend(overrideText) {
    const text = (overrideText || inputText).trim()
    if (!text || isTyping) return

    setInputText('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'

    setSuggestedQuestions([])  // hide chips once user sends any message
    setMessages(prev => [...prev, { id: Date.now(), text, isUser: true }])
    setIsTyping(true)

    try {
      const data = await sendMessage({
        sessionId,
        message: text,
        petIds,
        userCode,
        language,
        displayName: 'Sarah',
      })

      if (data.redirect) {
        console.log('[Redirect payload]', data.redirect)
        setActiveRedirect(data.redirect)
      } else {
        setActiveRedirect(null)
      }

      // Flatten recipes and attach to the message object so they persist in scroll history
      const flatRecipes = []
      if (data.recipes_by_pet && Object.keys(data.recipes_by_pet).length > 0) {
        for (const [petIdStr, recipes] of Object.entries(data.recipes_by_pet)) {
          const petId = parseInt(petIdStr)
          const petName = selectedPets.find(p => p.pet_id === petId)?.name || null
          if (Array.isArray(recipes)) {
            recipes.forEach(recipe => flatRecipes.push({ ...recipe, petName }))
          }
        }
      }

      const isHtmlResponse = (
        data.output_mode === 'food_recipes_info' ||
        data.output_mode === 'food_info'
      )

      setIsTyping(false)
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        text: data.message,
        isUser: false,
        recipes: flatRecipes,
        isHtml: isHtmlResponse,
      }])

      // Refresh confidence after background pipeline finishes
      setTimeout(() => {
        fetchConfidence(petIds, userCode)
          .then(fresh => {
            setConfidenceScore(fresh.confidence_score ?? data.confidence_score)
            setConfidenceColor(fresh.confidence_color ?? data.confidence_color)
          })
          .catch(() => {})
      }, 4000)
    } catch (err) {
      setIsTyping(false)
      const text = err.isRejection
        ? err.message
        : `I'm having trouble connecting right now. Please try again! 🐢`
      setMessages(prev => [...prev, { id: Date.now() + 1, text, isUser: false }])
    }
  }

  function handleChipTap(questionText) {
    if (isTyping) return
    // Set the input text and let handleSend do the rest
    setInputText(questionText)
    // Use a microtask so React flushes the state update before handleSend reads it
    setTimeout(() => handleSend(questionText), 0)
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const petEmoji = SPECIES_EMOJI[primaryPet?.species] || '🐾'

  return (
    <div className="chat">
      {/* ── Header ── */}
      <div className="chat-header">
        <button className="chat-back-btn" onClick={onBack}>←</button>
        <div className="chat-header-pet">
          <div className="chat-pet-avatar">{petEmoji}</div>
          <div>
            <div className="chat-pet-name">{petNames}</div>
            <div className="chat-pet-meta">
              {selectedPets.map(p => p.breed).join(' & ')}
            </div>
          </div>
        </div>
        <ConfidenceBar score={confidenceScore} label={confidenceColor} variant="compact" />
      </div>

      {/* ── Messages ── */}
      <div className="chat-messages">
        <div className="chat-date-divider">
          <span>Today</span>
        </div>

        {messages.map(msg => (
          <div key={msg.id}>
            {/* Recipe carousel — attached to the message it belongs to */}
            {!msg.isUser && msg.recipes?.length > 0 && (
              <div className="chat-recipe-carousel">
                <div className="chat-recipe-label">🍽️ おすすめレシピ</div>
                <div className="chat-recipe-scroll">
                  {msg.recipes.map((r, idx) => (
                    <RecipeCard key={`${r.petName ?? 'pet'}-${r.id}-${idx}`} recipe={r} petName={r.petName} />
                  ))}
                </div>
              </div>
            )}
            <ChatBubble message={msg.text} isUser={msg.isUser} isHtml={msg.isHtml} />
          </div>
        ))}

        {/* Suggested question chips — shown only on blank state */}
        {suggestedQuestions.length > 0 && messages.length <= 1 && !isTyping && (
          <div className="chat-suggested-questions">
            {suggestedQuestions.map((q, i) => (
              <button
                key={i}
                className="suggested-chip"
                onClick={() => handleChipTap(q.text)}
              >
                {q.text}
              </button>
            ))}
          </div>
        )}

        {isTyping && <ChatBubble isTyping />}

        <div ref={messagesEndRef} />
      </div>

      {/* ── Sticky redirect nudge ── */}
      {activeRedirect && (
        <div className="chat-sticky-nudge">
          <button
            className="redirect-sticky-btn"
            onClick={() => {
              const ALLOWED_MODULES = ['health', 'food']
              if (!ALLOWED_MODULES.includes(activeRedirect.module)) {
                console.warn('Blocked redirect to unknown module:', activeRedirect.module)
                return
              }
              const params = new URLSearchParams({
                query: activeRedirect.context.query,
                pet_id: activeRedirect.context.pet_id,
                pet_summary: activeRedirect.context.pet_summary,
                urgency: activeRedirect.urgency,
              })
              window.open(`${BASE}/api/v1/simulator/${activeRedirect.module}?${params}`, '_blank')
            }}
            style={{
              background: activeRedirect.display.style === 'urgent' ? '#ef4444' : '#f97316',
            }}
          >
            {activeRedirect.display.label} →
          </button>
          <button
            className="redirect-dismiss"
            onClick={() => setActiveRedirect(null)}
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Input ── */}
      <div className="chat-input-bar">
        <div className="chat-input-wrap">
          <textarea
            ref={textareaRef}
            className="chat-input"
            placeholder={language === 'JA' ? `${petNames}について...` : `Message about ${petNames}...`}
            value={inputText}
            onChange={e => {
              setInputText(e.target.value)
              const ta = e.target
              ta.style.height = 'auto'
              ta.style.height = `${ta.scrollHeight}px`
            }}
            onKeyDown={handleKeyDown}
            rows={1}
          />
          <button
            className={`chat-send-btn ${inputText.trim() ? 'active' : ''}`}
            onClick={handleSend}
            disabled={!inputText.trim() || isTyping}
          >
            ↑
          </button>
        </div>
        <p className="chat-input-hint">AnyMall-chan learns naturally — just chat normally</p>
      </div>
    </div>
  )
}
