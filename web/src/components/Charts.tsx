import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { DayRow } from '../lib/types'
import { signed, usd } from '../lib/format'

const AXIS = { stroke: '#4a5670', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }

function ChartTip({ active, payload, label, mode }: {
  active?: boolean
  payload?: { value: number }[]
  label?: string
  mode: 'equity' | 'pnl'
}) {
  if (!active || !payload?.length) return null
  const v = payload[0].value
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-xs shadow-xl">
      <div className="text-ink-500">{label}</div>
      <div className={`mt-0.5 font-mono font-semibold ${mode === 'pnl' ? (v >= 0 ? 'text-mint-400' : 'text-rose-400') : 'text-ink-100'}`}>
        {mode === 'pnl' ? signed(v) : usd(v, 0)}
      </div>
    </div>
  )
}

export function EquityChart({ rows, bankroll }: { rows: DayRow[]; bankroll: number }) {
  const data = [
    { date: 'start', equity: bankroll },
    ...rows.map((r) => ({ date: r.date.slice(5), equity: r.book_end })),
  ]
  const last = rows.length ? rows[rows.length - 1].book_end : bankroll
  const up = last >= bankroll
  const color = up ? '#34d399' : '#fb7185'
  return (
    <ResponsiveContainer width="100%" height={190}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <defs>
          <linearGradient id={`eq-${up}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} minTickGap={40} />
        <YAxis
          domain={['auto', 'auto']}
          tick={AXIS}
          tickLine={false}
          axisLine={false}
          width={54}
          tickFormatter={(v: number) => usd(v, 0).replace('.00', '')}
        />
        <ReferenceLine y={bankroll} stroke="#4a5670" strokeDasharray="4 4" />
        <Tooltip content={<ChartTip mode="equity" />} cursor={{ stroke: '#4a5670' }} />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={color}
          strokeWidth={2}
          fill={`url(#eq-${up})`}
          animationDuration={500}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function DailyPnlChart({ rows }: { rows: DayRow[] }) {
  const data = rows.map((r) => ({ date: r.date.slice(5), pnl: r.pnl }))
  return (
    <ResponsiveContainer width="100%" height={150}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <XAxis dataKey="date" tick={AXIS} tickLine={false} axisLine={false} minTickGap={40} />
        <YAxis
          tick={AXIS}
          tickLine={false}
          axisLine={false}
          width={54}
          tickFormatter={(v: number) => signed(v, 0)}
        />
        <ReferenceLine y={0} stroke="#4a5670" />
        <ReferenceLine y={100} stroke="#38bdf8" strokeDasharray="4 4" opacity={0.5} />
        <Tooltip content={<ChartTip mode="pnl" />} cursor={{ fill: 'rgba(74,86,112,0.15)' }} />
        <Bar dataKey="pnl" radius={[3, 3, 0, 0]} animationDuration={500}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.pnl >= 0 ? '#34d399' : '#fb7185'} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
