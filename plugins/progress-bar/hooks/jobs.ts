import type { Job, JobStatus } from '../types'
import { MIN_CELLS, duration, estimate, filled, remaining, type Options } from './format'

/** Jobs kept at once; the oldest finished ones go first. */
export const MAX_JOBS = 20
/** Rows drawn at once; the rest are counted. */
export const MAX_ROWS = 3
const MAX_LABEL = 40
const MAX_STEP = 60
const PADDING = 2
const SEPARATOR = ' · '
const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
/** A job that moves slowly by nature gets this many of its usual gaps before it is called stalled. */
const PATIENCE = 5
const MAX_STALL_MS = 60 * 60_000

const ID = /^[A-Za-z0-9._-]{1,48}$/

const text = (v: unknown, max: number): string | undefined => {
  if (typeof v !== 'string') return undefined
  const t = v.replace(/\s+/g, ' ').trim()
  if (t === '') return undefined
  return t.length > max ? t.slice(0, max - 1) + '…' : t
}

const count = (v: unknown): number | undefined =>
  typeof v === 'number' && Number.isFinite(v) && v >= 0 ? Math.floor(v) : undefined

const clamp = (n: number) => Math.min(100, Math.max(0, n))

/**
 * The jobs after one message from `pbar` (or anything that speaks its JSON).
 * Anything malformed is ignored: the socket is open to every process of the
 * person's, and a bad line must never take the bar down.
 */
export function ingest(jobs: Record<string, Job>, raw: unknown, now: number): Record<string, Job> {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return jobs
  const m = raw as Record<string, unknown>
  const id = typeof m.id === 'string' && ID.test(m.id) ? m.id : undefined
  const op = m.op === 'start' || m.op === 'set' || m.op === 'done' || m.op === 'fail' ? m.op : undefined
  if (id === undefined || op === undefined) return jobs

  const prev = op === 'start' ? undefined : jobs[id]
  const total = count(m.total) || prev?.total || undefined
  let done = m.inc !== undefined ? (prev?.done ?? 0) + (count(m.inc) ?? 0) : (count(m.done) ?? prev?.done)
  if (total !== undefined && done === undefined) done = 0
  if (total !== undefined && done !== undefined) done = Math.min(done, total)

  let percent: number | undefined
  if (total !== undefined && done !== undefined) percent = clamp(Math.floor((done / total) * 100))
  else if (typeof m.percent === 'number' && Number.isFinite(m.percent)) percent = clamp(Math.round(m.percent))
  else percent = prev?.percent

  const fraction = percent === undefined ? 0 : percent / 100
  const wasFraction = prev?.percent === undefined ? 0 : prev.percent / 100
  const isMove = prev !== undefined && fraction > wasFraction
  const gap = isMove ? now - prev.movedAt : 0

  const job: Job = {
    id,
    label: text(m.label, MAX_LABEL) ?? prev?.label ?? id,
    status: 'running',
    done,
    total,
    percent,
    step: text(m.step, MAX_STEP) ?? prev?.step,
    pid: count(m.pid) || prev?.pid || undefined,
    startedAt: prev?.startedAt ?? now,
    updatedAt: now,
    idleSec: count(m.idle) ?? 0,
    movedAt: isMove || prev === undefined ? now : prev.movedAt,
    startFraction: prev?.startFraction ?? fraction,
    gapMs: isMove ? (prev.gapMs === 0 ? gap : Math.round(prev.gapMs * 0.7 + gap * 0.3)) : (prev?.gapMs ?? 0),
    seq: (prev?.seq ?? 0) + 1,
    isStallNoticed: isMove ? false : prev?.isStallNoticed,
  }

  if (op === 'done') {
    job.status = 'done'
    job.endedAt = now
    if (total !== undefined) {
      job.done = total
      job.percent = 100
    }
    job.msg = text(m.msg, MAX_STEP)
  } else if (op === 'fail') {
    job.status = 'failed'
    job.endedAt = now
    job.msg = text(m.msg, MAX_STEP)
  }
  return cap({ ...jobs, [id]: job })
}

function cap(jobs: Record<string, Job>): Record<string, Job> {
  const all = Object.values(jobs)
  if (all.length <= MAX_JOBS) return jobs
  const doomed = all
    .filter(j => j.status !== 'running')
    .sort((a, b) => a.updatedAt - b.updatedAt)
    .slice(0, all.length - MAX_JOBS)
  const next = { ...jobs }
  for (const j of doomed) delete next[j.id]
  return next
}

/** Marks a running job whose process is gone. */
export function markDied(jobs: Record<string, Job>, id: string, now: number): Record<string, Job> {
  const j = jobs[id]
  if (j === undefined || j.status !== 'running') return jobs
  return { ...jobs, [id]: { ...j, status: 'died', endedAt: now, msg: 'process gone' } }
}

/** How long the job has said nothing, counting what it admitted to when it last reported. */
export function idleMs(j: Job, now: number): number {
  return j.idleSec * 1000 + Math.max(0, now - j.updatedAt)
}

/** Silence that is too long for this job: the configured limit, or longer for a job that is slow by nature. */
export function stallLimit(j: Job, o: Options): number {
  return Math.min(MAX_STALL_MS, Math.max(o.stall_after * 1000, PATIENCE * j.gapMs))
}

export function isStalled(j: Job, now: number, o: Options): boolean {
  return j.status === 'running' && idleMs(j, now) >= stallLimit(j, o)
}

/** Time left, by the same rule as Claude's own bar: speed since the job's first report. */
export function jobRemaining(j: Job, now: number): number | undefined {
  if (j.status !== 'running' || j.percent === undefined) return undefined
  return remaining(
    {
      percent: j.percent,
      startPercent: Math.round(j.startFraction * 100),
      startedAt: j.startedAt,
      updatedAt: j.movedAt,
    },
    now,
  )
}

/** Items per minute once there is enough to say it, e.g. "5.6/min" or "40/h". */
export function rate(j: Job, now: number): string | undefined {
  if (j.total === undefined || j.done === undefined || j.done < 3) return undefined
  const end = j.endedAt ?? j.movedAt
  const minutes = (end - j.startedAt) / 60_000
  if (minutes < 1 / 3) return undefined
  const perMin = j.done / minutes
  if (perMin >= 1) return `${perMin < 10 ? perMin.toFixed(1) : Math.round(perMin)}/min`
  return `${Math.round(perMin * 60)}/h`
}

const elapsed = (j: Job, now: number) => (j.endedAt ?? now) - j.startedAt

/** The count and percentage that head a row: "118/240 49%", "49%", or nothing the job can say. */
export function jobHead(j: Job): string {
  const pct = j.percent === undefined ? '' : `${j.percent}%`
  if (j.status === 'done') return j.total !== undefined ? `${j.total}/${j.total} ✓` : '✓'
  if (j.total !== undefined && j.done !== undefined) return `${j.done}/${j.total} ${pct}`
  return pct
}

export type Tone = 'running' | 'stalled' | 'failed' | 'done'

export type Row = {
  tone: Tone
  glyph: string
  cells: number
  filled: number
  /** Where the sliding block starts, for a bar with nothing to measure; -1 otherwise. */
  slide: number
  head: string
  tail: string
}

export function toneOf(j: Job, now: number, o: Options): Tone {
  if (j.status === 'done') return 'done'
  if (j.status === 'failed' || j.status === 'died') return 'failed'
  return isStalled(j, now, o) ? 'stalled' : 'running'
}

/** What follows the head, most important first. */
export function pieces(j: Job, now: number, o: Options): string[] {
  const out: string[] = [j.label]
  const took = duration(elapsed(j, now))
  const at = j.total !== undefined && j.done !== undefined ? ` at ${j.done}/${j.total}` : j.percent !== undefined ? ` at ${j.percent}%` : ''
  switch (toneOf(j, now, o)) {
    case 'done':
      out.push(`done in ${took}`)
      if (j.msg) out.push(j.msg)
      break
    case 'failed':
      out.push(j.status === 'died' ? `died${at}, process gone` : `failed${at}${j.msg ? `: ${j.msg}` : ''}`)
      out.push(`after ${took}`)
      if (j.step) out.push(`last: ${j.step}`)
      break
    case 'stalled':
      out.push(`no progress for ${duration(idleMs(j, now))}`)
      out.push(took)
      if (j.step) out.push(`last: ${j.step}`)
      break
    default: {
      out.push(took)
      const left = o.show_eta ? jobRemaining(j, now) : undefined
      if (left !== undefined) out.push(`~${estimate(left)} left`)
      const r = rate(j, now)
      if (r) out.push(r)
      if (j.step) out.push(j.step)
    }
  }
  return out
}

/**
 * One row's bar and words in `columns`: the full-width bar with everything,
 * then a narrower bar, then fewer words (the last dropped first).
 */
export function jobRow(j: Job, now: number, columns: number, o: Options): Row {
  const tone = toneOf(j, now, o)
  const glyph = tone === 'done' ? '✓' : tone === 'failed' ? '✕' : tone === 'stalled' ? '⚠' : SPINNER[j.seq % SPINNER.length]!
  const head = jobHead(j)
  const all = pieces(j, now, o)
  const room = columns - PADDING - 2
  const place = (cells: number, tail: string): Row => ({
    tone,
    glyph,
    cells,
    filled: tone === 'done' && j.percent === undefined ? cells : j.percent === undefined ? 0 : filled(j.percent, cells),
    slide: j.percent === undefined && tone !== 'done' ? j.seq % Math.max(1, cells - 2) : -1,
    head,
    tail,
  })
  for (let n = all.length; n >= 0; n--) {
    const tail = all.slice(0, n).join(SEPARATOR)
    const extra = 1 + head.length + (tail ? 2 + tail.length : 0)
    const cells = Math.min(o.bar_width, room - extra)
    if (cells >= MIN_CELLS) return place(cells, tail)
  }
  return place(MIN_CELLS, '')
}

/** The bar as plain text, for tests. */
export function rowText(r: Row): string {
  let bar: string
  if (r.slide >= 0) bar = '─'.repeat(r.slide) + '━━━' + '─'.repeat(Math.max(0, r.cells - r.slide - 3))
  else bar = '━'.repeat(r.filled) + '─'.repeat(r.cells - r.filled)
  return `${r.glyph} ${bar} ${r.head}` + (r.tail ? `  ${r.tail}` : '')
}

const ORDER: Record<Tone, number> = { failed: 0, stalled: 1, running: 2, done: 3 }

/** Jobs to draw: trouble first, then what is running, then what finished; newest first within each. */
export function visible(jobs: Record<string, Job>, now: number, o: Options): { shown: Job[]; more: number } {
  const all = Object.values(jobs).sort(
    (a, b) => ORDER[toneOf(a, now, o)] - ORDER[toneOf(b, now, o)] || b.updatedAt - a.updatedAt,
  )
  return { shown: all.slice(0, MAX_ROWS), more: Math.max(0, all.length - MAX_ROWS) }
}

/** The job as one plain line, for the tool Claude reads. */
export function jobLine(j: Job, now: number, o: Options): string {
  const tone = toneOf(j, now, o)
  const state: Record<Tone, string> = {
    running: 'running',
    stalled: `STALLED (no progress for ${duration(idleMs(j, now))})`,
    failed: j.status === 'died' ? 'DIED (its process is gone)' : `FAILED${j.msg ? ` (${j.msg})` : ''}`,
    done: 'done',
  }
  const progress = jobHead(j).replace(' ✓', '')
  const rest = [`elapsed ${duration(elapsed(j, now))}`]
  const left = o.show_eta && tone === 'running' ? jobRemaining(j, now) : undefined
  if (left !== undefined) rest.push(`about ${estimate(left)} left`)
  if (j.step) rest.push(`last: ${j.step}`)
  return `${j.id} "${j.label}": ${state[tone]}${progress ? `, ${progress}` : ''}, ${rest.join(', ')}`
}

export type Finished = { job: Job; kind: 'done' | 'failed' | 'stalled' }

/** What changed between two states that the person should hear about. */
export function transitions(before: Record<string, Job>, after: Record<string, Job>): Finished[] {
  const out: Finished[] = []
  for (const job of Object.values(after)) {
    const was = before[job.id]
    if (job.status === 'done' && was?.status !== 'done') out.push({ job, kind: 'done' })
    else if ((job.status === 'failed' || job.status === 'died') && was?.status !== job.status) out.push({ job, kind: 'failed' })
  }
  return out
}

/** Marks every newly stalled running job as announced and returns them. */
export function noticeStalls(jobs: Record<string, Job>, now: number, o: Options): { jobs: Record<string, Job>; stalled: Job[] } {
  const stalled: Job[] = []
  let next = jobs
  for (const j of Object.values(jobs)) {
    if (isStalled(j, now, o) && !j.isStallNoticed) {
      const marked = { ...j, isStallNoticed: true }
      next = { ...next, [j.id]: marked }
      stalled.push(marked)
    }
  }
  return { jobs: next, stalled }
}

export const STATUS_WORD: Record<JobStatus, string> = { running: 'running', done: 'done', failed: 'failed', died: 'died' }

/** What Claude is told about `pbar`, appended to its system prompt after the progress section. */
export function jobInstructions(tool: string, bin: string): string {
  return [
    '# Long commands',
    '',
    `Wrap a shell command that will run longer than about two minutes in \`${bin}/pbar run --label "<what it does>" -- <command>\` and run it in the background. The person sees a live bar for it above their prompt (items done, rate, time left, and a warning if it stops making progress or dies), and the bar costs you nothing: do not poll the command. Add \`--total N\` when you know how many items it handles (each output line counts, or only lines matching \`--match REGEX\`); otherwise percentages and \`done/total\` in its output are followed on their own. \`pbar\` passes the command's output and exit code through unchanged.`,
    `To check on jobs, call \`${tool}\` rather than reading logs. You are woken when a job finishes only if the person turned that on.`,
  ].join('\n')
}
