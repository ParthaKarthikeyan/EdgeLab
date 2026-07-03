import type { BookStatus, Ledger, LiveState, Trade } from '../lib/types'
import { ago, pnlClass, signed, usd } from '../lib/format'
import { DailyPnlChart, EquityChart } from './Charts'
import { Badge, Card, CardHeader, Stat } from './ui'

function Positions({ live }: { live: LiveState }) {
  if (!live.positions.length)
    return <p className="px-5 pb-1 text-xs text-ink-500">flat — no open positions</p>
  return (
    <div className="px-5 pb-1">
      {live.positions.map((p) => {
        const px = live.last_prices[p.symbol] ?? p.entry
        const upnl = p.units * (px - p.entry)
        return (
          <div
            key={p.symbol}
            className="flex items-center justify-between border-t border-ink-700/40 py-2 font-mono text-xs first:border-t-0"
          >
            <span className="font-semibold text-ink-100">{p.symbol}</span>
            <span className="text-ink-300">
              {p.units.toFixed(6)} @ {usd(p.entry)}
            </span>
            <span className={pnlClass(upnl)}>{signed(upnl)}</span>
          </div>
        )
      })}
    </div>
  )
}

function TradesTable({ trades }: { trades: Trade[] }) {
  const recent = [...trades].slice(-8).reverse()
  if (!recent.length)
    return <p className="px-5 pb-4 text-xs text-ink-500">no closed trades yet</p>
  return (
    <div className="overflow-x-auto px-5 pb-4">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider text-ink-500">
            <th className="py-1.5 pr-3 font-medium">Date</th>
            <th className="py-1.5 pr-3 font-medium">Symbol</th>
            <th className="py-1.5 pr-3 font-medium">Exit</th>
            <th className="py-1.5 pr-3 text-right font-medium">P&L</th>
            <th className="py-1.5 text-right font-medium">Slip</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {recent.map((t, i) => (
            <tr key={i} className="border-t border-ink-700/40">
              <td className="py-1.5 pr-3 text-ink-300">{t.date}</td>
              <td className="py-1.5 pr-3">{t.symbol}</td>
              <td className="py-1.5 pr-3 text-ink-300">{t.reason}</td>
              <td className={`py-1.5 pr-3 text-right ${pnlClass(t.pnl)}`}>{signed(t.pnl)}</td>
              <td className={`py-1.5 text-right ${t.slippage > 0 ? 'text-amber-400' : 'text-ink-500'}`}>
                {t.slippage >= 0 ? '+' : ''}
                {t.slippage.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function BookCard({
  status,
  ledger,
  live,
  trades,
}: {
  status: BookStatus
  ledger: Ledger | null
  live: LiveState | null
  trades: Trade[]
}) {
  const bankroll = ledger?.bankroll ?? 10000
  const dep = ledger?.deployment_date ?? ''
  const rows = (ledger?.history ?? []).filter((r) => r.date >= dep)
  const equity = live?.equity ?? (rows.length ? rows[rows.length - 1].book_end : bankroll)
  const pnl = equity - bankroll
  const fresh = live && Date.now() - new Date(live.updated_at.replace(' ', 'T') + 'Z').getTime() < 2 * 3600e3

  return (
    <Card>
      <CardHeader
        title={status.label}
        sub={`$${bankroll.toLocaleString()} book · ${status.interval} bars · DD budget ${status.dd_budget_pct}%`}
        right={
          <div className="flex items-center gap-2">
            {live?.stopped && <Badge tone="rose">−2% stop</Badge>}
            {fresh ? (
              <Badge tone="mint" pulse>
                live · {ago(live.updated_at)}
              </Badge>
            ) : (
              <Badge tone="slate">{live ? `last ${ago(live.updated_at)}` : 'awaiting first run'}</Badge>
            )}
          </div>
        }
      />
      <div className="grid grid-cols-3 gap-6 px-5 pt-3 pb-2">
        <Stat label="Equity" value={usd(equity, 0)} />
        <Stat
          label="Since deploy"
          value={<span className={pnlClass(pnl)}>{signed(pnl, 0)}</span>}
          sub={dep ? `since ${dep}` : undefined}
        />
        <Stat
          label="Today"
          value={
            <span className={pnlClass(live?.day_pnl ?? 0)}>{signed(live?.day_pnl ?? 0, 0)}</span>
          }
        />
      </div>
      {rows.length > 0 ? (
        <div className="px-2">
          <EquityChart rows={rows} bankroll={bankroll} />
          <DailyPnlChart rows={rows} />
        </div>
      ) : (
        <p className="px-5 py-6 text-center text-xs text-ink-500">
          equity curve appears after the first session is committed
        </p>
      )}
      {live && <Positions live={live} />}
      <TradesTable trades={trades.filter((t) => t.date >= dep)} />
    </Card>
  )
}
