import type { WorkflowRun } from '../lib/types'
import { ago } from '../lib/format'
import { Card, CardHeader } from './ui'

function dot(run: WorkflowRun) {
  if (run.status !== 'completed') return 'bg-sky-400 animate-pulse'
  return run.conclusion === 'success' ? 'bg-mint-400' : 'bg-rose-400'
}

export default function ActionsCard({ runs }: { runs: WorkflowRun[] }) {
  return (
    <Card>
      <CardHeader title="Automation" sub="GitHub Actions — the bots run here, not on a laptop" />
      <div className="px-5 pt-1 pb-4">
        {runs.length === 0 && <p className="text-xs text-ink-500">no runs yet</p>}
        {runs.slice(0, 6).map((r, i) => (
          <a
            key={i}
            href={r.html_url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-3 border-t border-ink-700/40 py-2 text-xs first:border-t-0 hover:bg-ink-800/40"
          >
            <span className={`h-2 w-2 shrink-0 rounded-full ${dot(r)}`} />
            <span className="min-w-0 flex-1 truncate text-ink-100">{r.name}</span>
            <span className="shrink-0 font-mono text-ink-500">{ago(r.updated_at)}</span>
          </a>
        ))}
      </div>
    </Card>
  )
}
