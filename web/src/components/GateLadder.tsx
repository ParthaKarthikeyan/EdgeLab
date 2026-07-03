import { useState } from 'react'
import type { BookStatus, GateResult } from '../lib/types'
import { Badge, Card, CardHeader } from './ui'

const STAGE_LABEL: Record<BookStatus['stage'], { text: string; tone: 'mint' | 'amber' | 'sky' | 'slate' | 'violet' }> = {
  gate_a: { text: 'in research', tone: 'slate' },
  awaiting_paper: { text: 'passed A — awaiting paper', tone: 'sky' },
  paper: { text: 'paper · proving B', tone: 'amber' },
  gate_c_proving: { text: 'passed B · proving C', tone: 'violet' },
  promoted: { text: 'promoted', tone: 'mint' },
}

function GateChip({ name, result, reached }: { name: string; result: GateResult | null; reached: boolean }) {
  const state = !reached ? 'idle' : result === null ? 'pending' : result.passed ? 'pass' : 'active'
  const styles: Record<string, string> = {
    idle: 'border-ink-700 text-ink-500 bg-transparent',
    pending: 'border-ink-700 text-ink-300 bg-ink-800/60',
    active: 'border-amber-400/40 text-amber-400 bg-amber-400/10',
    pass: 'border-mint-500/40 text-mint-400 bg-mint-500/10',
  }
  return (
    <div className={`flex h-8 w-8 items-center justify-center rounded-full border text-xs font-bold ${styles[state]}`}>
      {state === 'pass' ? '✓' : name}
    </div>
  )
}

function Checks({ result }: { result: GateResult }) {
  return (
    <ul className="mt-2 space-y-1.5">
      {(result.checks ?? []).map((c) => (
        <li key={c.name} className="flex items-start gap-2 text-xs">
          <span className={c.passed ? 'text-mint-400' : 'text-rose-400'}>
            {c.passed ? '✓' : '✗'}
          </span>
          <span className="text-ink-300">{c.detail}</span>
        </li>
      ))}
    </ul>
  )
}

function BookRow({ name, book }: { name: string; book: BookStatus }) {
  const [open, setOpen] = useState(false)
  const stage = STAGE_LABEL[book.stage]
  const reachedB = book.stage !== 'gate_a' && book.stage !== 'awaiting_paper'
  const reachedC = book.stage === 'gate_c_proving' || book.stage === 'promoted'
  const current: GateResult | null =
    book.stage === 'gate_a' || book.stage === 'awaiting_paper'
      ? book.gate_a
      : book.stage === 'paper'
        ? book.gate_b
        : book.gate_c

  return (
    <div className="border-t border-ink-700/50 first:border-t-0">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors hover:bg-ink-800/40"
      >
        <div className="flex items-center gap-2">
          <GateChip name="A" result={book.gate_a} reached />
          <span className="h-px w-4 bg-ink-700" />
          <GateChip name="B" result={book.gate_b} reached={reachedB || book.stage === 'awaiting_paper'} />
          <span className="h-px w-4 bg-ink-700" />
          <GateChip name="C" result={book.gate_c} reached={reachedC} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{book.label}</span>
            <Badge tone={stage.tone}>{stage.text}</Badge>
            {book.active && <Badge tone="sky">trading</Badge>}
          </div>
          <div className="mt-0.5 truncate font-mono text-xs text-ink-500">
            {name} · {book.interval} · rules v{book.rules_version}
            {book.deployment_date ? ` · forward since ${book.deployment_date}` : ''}
          </div>
        </div>
        <span className={`text-ink-500 transition-transform ${open ? 'rotate-90' : ''}`}>›</span>
      </button>
      {open && (
        <div className="px-5 pb-4 pl-[7.5rem]">
          {current?.checks?.length ? (
            <Checks result={current} />
          ) : (
            <p className="text-xs text-ink-500">
              No gate detail yet — research has not been committed for this book.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default function GateLadder({ books }: { books: Record<string, BookStatus> }) {
  const order = Object.entries(books).sort(
    ([, a], [, b]) => Number(b.active) - Number(a.active),
  )
  return (
    <Card>
      <CardHeader
        title="Gate ladder"
        sub="A: edge survives 2x costs out-of-sample · B: forward evidence on real fills (30 sessions, or 14+ with 40+ trades) · C: rolling average holds over 60"
      />
      <div className="mt-2">
        {order.map(([name, book]) => (
          <BookRow key={name} name={name} book={book} />
        ))}
      </div>
    </Card>
  )
}
