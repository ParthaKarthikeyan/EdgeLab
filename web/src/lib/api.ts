import type { Ledger, LiveState, Research, Status, TradeLog, WorkflowRun } from './types'

export const OWNER = 'ParthaKarthikeyan'
export const REPO = 'EdgeLab'
const RAW = `https://raw.githubusercontent.com/${OWNER}/${REPO}/master`

async function getJSON<T>(url: string): Promise<T | null> {
  try {
    const r = await fetch(url, { cache: 'no-store' })
    if (!r.ok) return null
    return (await r.json()) as T
  } catch {
    return null
  }
}

const bust = () => `t=${Math.floor(Date.now() / 1000)}`

export const fetchStatus = () => getJSON<Status>(`${RAW}/ledger/status.json?${bust()}`)

export const fetchLedger = (book: string) =>
  getJSON<Ledger>(`${RAW}/ledger/${book}_ledger.json?${bust()}`)

export const fetchTrades = (book: string) =>
  getJSON<TradeLog>(`${RAW}/ledger/${book}_trades.json?${bust()}`)

export const fetchResearch = (book: string) =>
  getJSON<Research>(`${RAW}/ledger/research/${book}.json?${bust()}`)

/** Gist id ships in the built site (public/config.json). */
let gistId: string | null | undefined
export async function fetchLive(book: string): Promise<LiveState | null> {
  if (gistId === undefined) {
    const cfg = await getJSON<{ gist_id?: string }>(`${import.meta.env.BASE_URL}config.json`)
    gistId = cfg?.gist_id || null
  }
  if (!gistId) return null
  return getJSON<LiveState>(
    `https://gist.githubusercontent.com/${OWNER}/${gistId}/raw/${book}_live.json?${bust()}`,
  )
}

export async function fetchRuns(): Promise<WorkflowRun[]> {
  const doc = await getJSON<{ workflow_runs: WorkflowRun[] }>(
    `https://api.github.com/repos/${OWNER}/${REPO}/actions/runs?per_page=10`,
  )
  return doc?.workflow_runs ?? []
}
