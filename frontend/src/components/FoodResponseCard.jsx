import { useState } from 'react'
import './FoodResponseCard.css'

/**
 * Renders a food AI response with:
 *  - Favicon-only sources bar (max 3 visible, +N overflow badge)
 *  - "For You" / "For Your Vet" tab switcher
 *
 * The LLM returns raw HTML. We parse it with DOMParser to extract the three
 * sections (sources, for-you body, for-vet body) and render them with React
 * state so tabs work without any inline JavaScript in the HTML.
 */
export default function FoodResponseCard({ html }) {
  const [activeTab, setActiveTab] = useState('you')

  // Parse once — DOMParser is browser-only, fine in React/Vite
  const doc = new DOMParser().parseFromString(html, 'text/html')

  const forYouBody     = doc.querySelector('.fr-for-you  .fr-body')?.innerHTML ?? ''
  const forVetBody     = doc.querySelector('.fr-for-vet  .fr-body')?.innerHTML ?? ''
  const sourceAnchors  = Array.from(doc.querySelectorAll('.fr-sources a.fr-source-link'))

  const visibleSources  = sourceAnchors.slice(0, 3)
  const overflowCount   = sourceAnchors.length - visibleSources.length

  return (
    <div className="frc">

      {/* ── Favicon sources bar ─────────────────────────────────────────── */}
      {sourceAnchors.length > 0 && (
        <div className="frc-sources">
          <span className="frc-sources-label">Sources</span>
          <div className="frc-fav-stack">
            {visibleSources.map((a, i) => {
              const imgSrc = a.querySelector('img')?.getAttribute('src') ?? ''
              const title  = a.querySelector('span')?.textContent ?? ''
              const href   = a.getAttribute('href') ?? '#'
              return (
                <a
                  key={i}
                  className="frc-fav"
                  style={{ zIndex: visibleSources.length - i }}
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={title}
                >
                  {imgSrc && (
                    <img
                      src={imgSrc}
                      alt=""
                      onError={e => { e.currentTarget.style.display = 'none' }}
                    />
                  )}
                </a>
              )
            })}
            {overflowCount > 0 && (
              <span className="frc-more">+{overflowCount}</span>
            )}
          </div>
        </div>
      )}

      {/* ── Tab bar ─────────────────────────────────────────────────────── */}
      <div className="frc-tabs">
        <button
          className={`frc-tab${activeTab === 'you' ? ' frc-tab--on' : ''}`}
          onClick={() => setActiveTab('you')}
        >
          🍽️ For You
        </button>
        <button
          className={`frc-tab${activeTab === 'vet' ? ' frc-tab--on' : ''}`}
          onClick={() => setActiveTab('vet')}
        >
          🩺 For Your Vet
        </button>
      </div>

      {/* ── Active tab content ───────────────────────────────────────────── */}
      <div
        className="frc-body"
        dangerouslySetInnerHTML={{
          __html: activeTab === 'you' ? forYouBody : forVetBody,
        }}
      />

    </div>
  )
}
