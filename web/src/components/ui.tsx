import type { ReactNode } from 'react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-2xl border border-ink-700/60 bg-ink-900/70 backdrop-blur-sm shadow-[0_1px_0_0_rgba(255,255,255,0.04)_inset,0_10px_30px_-15px_rgba(0,0,0,0.8)] ${className}`}
    >
      {children}
    </section>
  )
}

export function CardHeader({
  title,
  sub,
  right,
}: {
  title: string
  sub?: string
  right?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-1">
      <div>
        <h2 className="text-sm font-semibold tracking-wide text-ink-100">{title}</h2>
        {sub && <p className="mt-0.5 text-xs text-ink-300">{sub}</p>}
      </div>
      {right}
    </div>
  )
}

export function Badge({
  tone,
  children,
  pulse = false,
}: {
  tone: 'mint' | 'rose' | 'amber' | 'sky' | 'slate' | 'violet'
  children: ReactNode
  pulse?: boolean
}) {
  const tones: Record<string, string> = {
    mint: 'bg-mint-500/10 text-mint-400 border-mint-500/25',
    rose: 'bg-rose-400/10 text-rose-400 border-rose-400/25',
    amber: 'bg-amber-400/10 text-amber-400 border-amber-400/25',
    sky: 'bg-sky-400/10 text-sky-400 border-sky-400/25',
    violet: 'bg-violet-400/10 text-violet-400 border-violet-400/25',
    slate: 'bg-ink-700/40 text-ink-300 border-ink-700',
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${tones[tone]}`}
    >
      {pulse && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
        </span>
      )}
      {children}
    </span>
  )
}

export function Stat({
  label,
  value,
  valueClass = '',
  sub,
}: {
  label: string
  value: ReactNode
  valueClass?: string
  sub?: ReactNode
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-[0.14em] text-ink-500">{label}</div>
      <div className={`mt-1 font-mono text-xl font-semibold tabular-nums ${valueClass}`}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-ink-300">{sub}</div>}
    </div>
  )
}
