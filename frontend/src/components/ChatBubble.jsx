import './ChatBubble.css'

export default function ChatBubble({ message, isUser, isTyping = false }) {
  if (isTyping) {
    return (
      <div className="bubble-row bubble-row--bot">
        <div className="bubble-avatar">🐢</div>
        <div className="bubble bubble--bot bubble--typing">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
        </div>
      </div>
    )
  }

  return (
    <div className={`bubble-row ${isUser ? 'bubble-row--user' : 'bubble-row--bot'}`}>
      {!isUser && <div className="bubble-avatar">🐢</div>}
      <div className={`bubble ${isUser ? 'bubble--user' : 'bubble--bot'}`}>
        {message}
      </div>
    </div>
  )
}
