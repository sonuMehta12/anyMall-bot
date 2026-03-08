import { useEffect, useRef, useState } from 'react'
import ChatBubble from '../components/ChatBubble.jsx'
import ConfidenceBar from '../components/ConfidenceBar.jsx'
import { sendMessage } from '../api.js'
import './Chat.css'

const SPECIES_EMOJI = { dog: '🐕', cat: '🐱' }

// Generate a session ID once per page load — persists for the full conversation
function makeSessionId() {
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export default function Chat({ pet, parentName }) {
  const [sessionId] = useState(makeSessionId)   // created once, never changes
  const [messages, setMessages] = useState([])
  const [inputText, setInputText] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [confidenceScore, setConfidenceScore] = useState(0)
  const [confidenceColor, setConfidenceColor] = useState('red')

  const messagesEndRef = useRef(null)

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

  // Show opening greeting when chat first mounts
  useEffect(() => {
    setMessages([{
      id: 1,
      text: `Hi ${parentName}! How's ${pet.name} doing today? 🐾`,
      isUser: false,
    }])
  }, [])   // empty array = runs once on mount

  async function handleSend() {
    const text = inputText.trim()
    if (!text || isTyping) return

    setInputText('')

    // Show user message immediately
    setMessages(prev => [...prev, { id: Date.now(), text, isUser: true }])
    setIsTyping(true)

    try {
      const data = await sendMessage({ sessionId, message: text })

      setConfidenceScore(data.confidence_score ?? 0)
      setConfidenceColor(data.confidence_color ?? 'red')

      if (data.redirect) {
        console.log('[Redirect payload]', data.redirect)
      }

      setIsTyping(false)
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        text: data.message,
        isUser: false,
        redirect: data.redirect || null,
      }])
    } catch (err) {
      setIsTyping(false)
      setMessages(prev => [...prev, {
        id: Date.now() + 1,
        text: `I'm having trouble connecting right now. Please try again! 🐢`,
        isUser: false,
      }])
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const petEmoji = SPECIES_EMOJI[pet.species] || '🐾'

  return (
    <div className="chat">
      {/* ── Header ── */}
      <div className="chat-header">
        <div className="chat-header-pet">
          <div className="chat-pet-avatar">{petEmoji}</div>
          <div>
            <div className="chat-pet-name">{pet.name}</div>
            <div className="chat-pet-meta">{pet.breed}</div>
          </div>
        </div>
        <ConfidenceBar score={confidenceScore} label={confidenceColor} variant="compact" />
      </div>

      {/* ── Messages ── */}
      <div className="chat-messages">
        <div className="chat-date-divider">
          <span>Today</span>
        </div>

        {messages.map(msg => {
          const r = msg.redirect
          let btnLabel = null
          let btnColor = null
          if (r) {
            btnLabel = r.module === 'food' ? 'Talk to Food Specialist →' : 'Talk to Health Assistant →'
            btnColor = r.urgency === 'high' ? '#ef4444' : r.urgency === 'medium' ? '#f97316' : '#22c55e'
          }
          return (
            <div key={msg.id}>
              <ChatBubble message={msg.text} isUser={msg.isUser} />
              {r && (
                <div style={{ display: 'flex', justifyContent: 'flex-start', padding: '4px 16px 8px' }}>
                  <button
                    onClick={() => {
                      const params = new URLSearchParams({
                        query: r.pre_populated_query,
                        urgency: r.urgency,
                        pet_summary: r.pet_summary,
                      })
                      window.open(`${r.deep_link}?${params}`, '_blank')
                    }}
                    style={{
                      background: btnColor,
                      color: '#fff',
                      border: 'none',
                      borderRadius: '20px',
                      padding: '8px 18px',
                      fontSize: '0.85rem',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    {btnLabel}
                  </button>
                </div>
              )}
            </div>
          )
        })}

        {isTyping && <ChatBubble isTyping />}

        <div ref={messagesEndRef} />
      </div>

      {/* ── Input ── */}
      <div className="chat-input-bar">
        <div className="chat-input-wrap">
          <textarea
            className="chat-input"
            placeholder={`Message about ${pet.name}...`}
            value={inputText}
            onChange={e => setInputText(e.target.value)}
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
