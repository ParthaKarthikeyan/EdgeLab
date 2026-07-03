import type { Research } from '../lib/types'
import { Badge, Card, CardHeader } from './ui'

function pfText(v: number | null | undefined) {
  return v === null || v === undefined ? '∞' : v.toFixed(2)
}

export default function ResearchCard({ docs }: { docs: Research[] }) {
  if (!docs.length) return null
  return (
    <Card>
      <CardHeader
        title="Gate A research"
        sub="Frozen rules, walk-forward, judged at 2x modeled costs — every symbol must pass"
      />
      <div className="space-y-4 px-5 pt-2 pb-4">
        {docs.map((doc) => (
          <div key={doc.book} className="rounded-xl border border-ink-700/50 bg-ink-850/50 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{doc.label}</span>
                <Badge tone={doc.passed ? 'mint' : 'rose'}>
                  {doc.passed ? 'passes gate A' : 'fails gate A'}
                </Badge>
              </div>
              <span className="font-mono text-[11px] text-ink-500">
                stress {doc.cost_bps_stressed}bps rt · {doc.generated}
              </span>
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-ink-500">
                    <th className="py-1 pr-4 font-medium">Symbol</th>
                    <th className="py-1 pr-4 text-right font-medium">Ret @1x</th>
                    <th className="py-1 pr-4 text-right font-medium">Ret @2x</th>
                    <th className="py-1 pr-4 text-right font-medium">PF @2x</th>
                    <th className="py-1 pr-4 text-right font-medium">MaxDD</th>
                    <th className="py-1 pr-4 text-right font-medium">Folds +</th>
                    <th className="py-1 text-right font-medium">Trades/yr</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {Object.entries(doc.symbols).map(([sym, s]) => {
                    const wins = s.folds_stressed.filter(
                      (f) => (f.ret ?? 0) > 0 && (f.profit_factor ?? 0) > 1,
                    ).length
                    return (
                      <tr key={sym} className="border-t border-ink-700/40">
                        <td className="py-1.5 pr-4">{sym}</td>
                        <td className={`py-1.5 pr-4 text-right ${(s.base.ret ?? 0) >= 0 ? 'text-mint-400' : 'text-rose-400'}`}>
                          {(s.base.ret ?? 0) >= 0 ? '+' : ''}
                          {(s.base.ret ?? 0).toFixed(1)}%
                        </td>
                        <td className={`py-1.5 pr-4 text-right ${(s.stressed.ret ?? 0) >= 0 ? 'text-mint-400' : 'text-rose-400'}`}>
                          {(s.stressed.ret ?? 0) >= 0 ? '+' : ''}
                          {(s.stressed.ret ?? 0).toFixed(1)}%
                        </td>
                        <td className="py-1.5 pr-4 text-right text-ink-300">
                          {pfText(s.stressed.profit_factor)}
                        </td>
                        <td className="py-1.5 pr-4 text-right text-ink-300">
                          {(s.stressed.max_dd ?? 0).toFixed(1)}%
                        </td>
                        <td className="py-1.5 pr-4 text-right text-ink-300">
                          {wins}/{s.folds_stressed.length}
                        </td>
                        <td className="py-1.5 text-right text-ink-300">{s.trades_per_year}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}
