import type { Status } from '../lib/types'
import { pnlClass, signed, usd } from '../lib/format'
import { Stat } from './ui'

const TARGET = 100

/** The scoreboard: one number the whole project answers to — the rolling
 *  monthly $/day average across active books — with the target for scale. */
export default function Hero({ status }: { status: Status }) {
  const active = Object.values(status.books).filter((b) => b.active)
  const withAvg = active.filter((b) => b.rolling_avg !== null)
  const avg = withAvg.length ? withAvg.reduce((s, b) => s + (b.rolling_avg ?? 0), 0) : null
  const totalPnl = active.reduce((s, b) => s + b.pnl, 0)
  const sessions = Math.max(0, ...active.map((b) => b.sessions))
  const filled = Math.max(0, ...active.map((b) => b.rolling_window_filled))
  const gauge = avg === null ? 0 : Math.max(0, Math.min(1, avg / TARGET))

  return (
    <div className="relative overflow-hidden rounded-2xl border border-ink-700/60 bg-gradient-to-br from-ink-850 to-ink-900 px-6 py-6">
      <div className="pointer-events-none absolute -top-24 right-0 h-64 w-96 rounded-full bg-sky-400/5 blur-3xl" />
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <div className="text-[11px] uppercase tracking-[0.14em] text-ink-500">
            Rolling {status.rolling_window}-session average · all active books
          </div>
          <div className="mt-1 flex items-baseline gap-3">
            <span
              className={`font-mono text-5xl font-bold tabular-nums ${avg === null ? 'text-ink-500' : pnlClass(avg)}`}
            >
              {avg === null ? '—' : signed(avg, 0)}
            </span>
            <span className="text-sm text-ink-300">/ day, vs {usd(TARGET, 0)} target</span>
          </div>
          <div className="mt-3 h-1.5 w-72 max-w-full overflow-hidden rounded-full bg-ink-700/60">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sky-400 to-mint-400 transition-all duration-700"
              style={{ width: `${gauge * 100}%` }}
            />
          </div>
          <div className="mt-1.5 text-xs text-ink-500">
            {filled < status.rolling_window
              ? `window filling: ${filled}/${status.rolling_window} sessions`
              : 'window full — this number is the verdict'}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-8">
          <Stat
            label="Total P&L"
            value={<span className={pnlClass(totalPnl)}>{signed(totalPnl, 0)}</span>}
            sub="active books, since deploy"
          />
          <Stat label="Sessions" value={sessions} sub="forward, current rules" />
          <Stat
            label="Books live"
            value={`${active.length} / 2`}
            sub="max two — by design"
          />
        </div>
      </div>
    </div>
  )
}
