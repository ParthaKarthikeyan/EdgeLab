export interface GateCheck {
  name: string
  passed: boolean
  detail: string
}

export interface GateResult {
  gate?: string
  passed: boolean
  checks?: GateCheck[]
  generated?: string
}

export interface BookStatus {
  label: string
  active: boolean
  interval: string
  dd_budget_pct: number
  rules_version: number
  deployment_date: string | null
  sessions: number
  pnl: number
  rolling_avg: number | null
  rolling_window_filled: number
  stage: 'gate_a' | 'awaiting_paper' | 'paper' | 'gate_c_proving' | 'promoted'
  gate_a: GateResult | null
  gate_b: GateResult | null
  gate_c: GateResult | null
}

export interface Status {
  generated: string
  bankroll: number
  rolling_window: number
  books: Record<string, BookStatus>
}

export interface DayRow {
  date: string
  book_start: number
  book_end: number
  pnl: number
  pnl_pct: number
  trades: number
  open_positions: number
  stopped: string | null
}

export interface Ledger {
  book: string
  rules_version: number
  deployment_date: string
  bankroll: number
  cash: number
  positions: Record<string, { units: number; entry: number; entry_time: string }>
  history: DayRow[]
  last_run?: string
}

export interface Trade {
  date: string
  symbol: string
  side: string
  units: number
  entry_time: string
  exit_time: string
  intended_entry: number
  intended_exit: number
  entry_price: number
  exit_price: number
  pnl: number
  slippage: number
  reason: string
}

export interface TradeLog {
  book: string
  history: Trade[]
}

export interface LiveState {
  book: string
  label: string
  updated_at: string
  equity: number
  bankroll: number
  pnl: number
  day_pnl: number
  stopped: string | null
  positions: { symbol: string; units: number; entry: number; entry_time: string }[]
  last_prices: Record<string, number>
  notes: string[]
}

export interface ResearchSymbol {
  bars: number
  trades_per_year: number
  base: Record<string, number | null>
  stressed: Record<string, number | null>
  folds_stressed: Record<string, number | null>[]
  gate_a: GateResult
}

export interface Research {
  book: string
  label: string
  interval: string
  cost_bps_base: number
  cost_bps_stressed: number
  generated: string
  passed: boolean
  symbols: Record<string, ResearchSymbol>
}

export interface WorkflowRun {
  name: string
  status: string
  conclusion: string | null
  html_url: string
  updated_at: string
}
