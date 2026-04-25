import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

// Note: index.css is no longer used — all styles live in App.css
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
