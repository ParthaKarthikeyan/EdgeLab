import { useEffect, useState } from 'react'
import {
  OWNER,
  REPO,
  fetchLedger,
  fetchLive,
  fetchResearch,
  fetchRuns,
  fetchStatus,
  fetchTrades,
} from './lib/api'
import type { Ledger, LiveState, Research, Status, Trade, WorkflowRun } from './lib/types'
import ActionsCard from './components/ActionsCard'
import BookCard from './components/BookCard'
import GateLadder from './components/GateLadder'
import Hero from './components/Hero'
import ResearchCard from './components/ResearchCard'

const LIVE_POLL_MS = 90_000
const SLOW_POLL_MS = 300_000

interface BookData {
  ledger: Ledger | null
  trades: Trade[]
  live: LiveState | null
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [books, setBooks] = useState<Record<string, BookData>>({})
  const [research, setResearch] = useState<Research[]>([])
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [failed, setFailed] = useState(false)

  // slow loop: status + ledgers + research + workflow runs
  useEffect(() => {
    let alive = true
    async function slow() {
      const st = await fetchStatus()
      if (!alive) return
      if (!st) {
        setFailed(true)
        return
      }
      setFailed(false)
      setStatus(st)
      const names = Object.keys(st.books)
      const active = names.filter((n) => st.books[n].active)
      const [ledgers, tradeLogs, docs, wf] = await Promise.all([
        Promise.all(active.map(fetchLedger)),
        Promise.all(active.map(fetchTrades)),
        Promise.all(names.map(fetchResearch)),
        fetchRuns(),
      ])
      if (!alive) return
      setBooks((prev) => {
        const next: Record<string, BookData> = {}
        active.forEach((n, i) => {
          next[n] = {
            ledger: ledgers[i],
            trades: tradeLogs[i]?.history ?? [],
            live: prev[n]?.live ?? null,
          }
        })
        return next
      })
      setResearch(docs.filter((d): d is Research => d !== null))
      setRuns(wf)
    }
    slow()
    const id = setInterval(slow, SLOW_POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  // fast loop: gist live state for active books
  useEffect(() => {
    if (!status) return
    let alive = true
    const active = Object.keys(status.books).filter((n) => status.books[n].active)
    async function fast() {
      const states = await Promise.all(active.map(fetchLive))
      if (!alive) return
      setBooks((prev) => {
        const next = { ...prev }
        active.forEach((n, i) => {
          if (states[i]) next[n] = { ...(next[n] ?? { ledger: null, trades: [] }), live: states[i] }
        })
        return next
      })
    }
    fast()
    const id = setInterval(fast, LIVE_POLL_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [status])

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Edge<span className="text-sky-400">Lab</span>
          </h1>
          <p className="mt-1 text-sm text-ink-300">
            Gate-driven paper trading — backtests nominate, only the forward ledger promotes.
          </p>
        </div>
        <nav className="flex items-center gap-4 text-xs text-ink-500">
          <a
            className="hover:text-ink-100"
            href={`https://github.com/${OWNER}/${REPO}/blob/master/METHODOLOGY.md`}
            target="_blank"
            rel="noreferrer"
          >
            methodology
          </a>
          <a
            className="hover:text-ink-100"
            href={`https://github.com/${OWNER}/${REPO}`}
            target="_blank"
            rel="noreferrer"
          >
            repo
          </a>
          <a
            className="hover:text-ink-100"
            href={`https://${OWNER.toLowerCase()}.github.io/DayTrade/`}
            target="_blank"
            rel="noreferrer"
          >
            daytrade ↗
          </a>
        </nav>
      </header>

      {failed && (
        <div className="mb-6 rounded-xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-400">
          Could not load <span className="font-mono">ledger/status.json</span> — it appears after
          the first research or paper run commits.
        </div>
      )}

      {status && (
        <div className="space-y-6">
          <Hero status={status} />
          <GateLadder books={status.books} />
          <div className="grid gap-6 lg:grid-cols-2">
            {Object.entries(status.books)
              .filter(([, b]) => b.active)
              .map(([name, b]) => (
                <BookCard
                  key={name}
                  status={b}
                  ledger={books[name]?.ledger ?? null}
                  live={books[name]?.live ?? null}
                  trades={books[name]?.trades ?? []}
                />
              ))}
          </div>
          <ResearchCard docs={research} />
          <ActionsCard runs={runs} />
        </div>
      )}

      <footer className="mt-10 border-t border-ink-700/40 pt-4 text-center text-[11px] text-ink-500">
        paper trading only · updated {status?.generated ?? '—'} UTC · ledger data refreshes ~90s
      </footer>
    </div>
  )
}
