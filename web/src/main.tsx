import { Component, StrictMode, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

/** A rendering bug in one card must degrade to a message, not a black page. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto max-w-2xl px-6 py-16 text-center">
          <h1 className="text-xl font-bold">EdgeLab</h1>
          <p className="mt-3 text-sm text-ink-300">
            The dashboard hit a rendering error. The bots are unaffected — data lives in the
            repo's <span className="font-mono">ledger/</span> directory.
          </p>
          <pre className="mt-4 overflow-x-auto rounded-lg border border-ink-700 bg-ink-900 p-3 text-left font-mono text-xs text-rose-400">
            {String(this.state.error)}
          </pre>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
