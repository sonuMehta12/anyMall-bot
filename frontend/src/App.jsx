import Chat from './screens/Chat.jsx'

// Phase 0 — context is hardcoded in the backend (dummy_context.py)
// No onboarding, no pet selection, no login. Just the chat interface.
const PET = { name: 'Luna', species: 'dog', breed: 'Shiba Inu' }
const OWNER = 'Shara'

export default function App() {
  return (
    <div className="phone-shell">
      <Chat pet={PET} parentName={OWNER} />
    </div>
  )
}
