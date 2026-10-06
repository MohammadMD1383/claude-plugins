import type { ProgressState } from '../types'

export type Options = {
  auto_report: boolean
  show_eta: boolean
  bar_width: number
}

export const DEFAULTS: Options = { auto_report: true, show_eta: true, bar_width: 24 }

export const MIN_CELLS = 10
export const MAX_STEP = 60
const SEPARATOR = ' · '
const PADDING = 2
/** An update is called stale once a working turn has gone this long without one. */
const STALE_MS = 3 * 60_000
/** The estimate needs this much movement over this much time, or it is noise. */
const ETA_MIN_PERCENT = 5
const ETA_MIN_MS = 30_000

export function readOptions(raw: Partial<Record<keyof Options, unknown>> | undefined): Options {
  const o = { ...DEFAULTS }
  if (!raw) return o
  if (typeof raw.auto_report === 'boolean') o.auto_report = raw.auto_report
  if (typeof raw.show_eta === 'boolean') o.show_eta = raw.show_eta
  if (typeof raw.bar_width === 'number' && Number.isFinite(raw.bar_width)) {
    o.bar_width = Math.min(60, Math.max(MIN_CELLS, Math.round(raw.bar_width)))
  }
  return o
}

/** A reported percentage as a whole number 0-100; undefined when it is not a number. */
export function toPercent(value: unknown): number | undefined {
  const n = typeof value === 'string' && value.trim() !== '' ? Number(value) : value
  if (typeof n !== 'number' || !Number.isFinite(n)) return undefined
  return Math.min(100, Math.max(0, Math.round(n)))
}

/** One line of what Claude is doing, cut to MAX_STEP; undefined when empty. */
export function toStep(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const text = value.replace(/\s+/g, ' ').trim()
  if (text === '') return undefined
  return text.length > MAX_STEP ? text.slice(0, MAX_STEP - 1) + '…' : text
}

/**
 * The state after a report. A report after a finished run (or the first one)
 * starts a new run; any other continues the current one, keeping its start so
 * elapsed time and the estimate span the whole run.
 */
export function advance(
  prev: ProgressState | null,
  report: { percent: number; step?: string },
  now: number,
): ProgressState {
  const continuing = prev !== null && prev.percent < 100
  return {
    percent: report.percent,
    step: report.step,
    startedAt: continuing ? prev.startedAt : now,
    startPercent: continuing ? prev.startPercent : report.percent,
    updatedAt: now,
  }
}

/** 45_000 -> "45s", 130_000 -> "2m10s", 14 minutes -> "14m", 65 minutes -> "1h05m". */
export function duration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 10) return `${m}m${String(s % 60).padStart(2, '0')}s`
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h${String(m % 60).padStart(2, '0')}m`
}

/** Minutes are as fine as an estimate gets: "<1m", "12m", "1h05m". */
export function estimate(ms: number): string {
  const minutes = Math.round(ms / 60_000)
  if (minutes < 1) return '<1m'
  if (minutes < 60) return `${minutes}m`
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, '0')}m`
}

/**
 * Time left, by extrapolating the speed since the run's first report. Undefined
 * until it has moved enough, for long enough, to say anything.
 */
export function remaining(p: ProgressState, now: number): number | undefined {
  if (p.percent >= 100) return undefined
  const moved = p.percent - p.startPercent
  const took = p.updatedAt - p.startedAt
  if (moved < ETA_MIN_PERCENT || took < ETA_MIN_MS) return undefined
  const left = ((100 - p.percent) * took) / moved
  // The estimate was made at the last report; time since then is already spent.
  return Math.max(0, left - (now - p.updatedAt))
}

export type Phase = 'running' | 'paused' | 'done'

export type View = {
  percent: number
  phase: Phase
  /** What follows the percentage, most important first. */
  pieces: string[]
}

/**
 * What to say about a run. `isWorking` is whether a turn is running: a run that
 * is unfinished while no turn is running has been left at that percentage, and
 * says so rather than looking alive.
 */
export function describe(p: ProgressState, now: number, isWorking: boolean, o: Options): View {
  const phase: Phase = p.percent >= 100 ? 'done' : isWorking ? 'running' : 'paused'
  const pieces: string[] = []
  if (p.step) pieces.push(p.step)

  const end = phase === 'running' ? now : p.updatedAt
  pieces.push(phase === 'done' ? `done in ${duration(end - p.startedAt)}` : duration(end - p.startedAt))

  if (phase === 'running') {
    const left = o.show_eta ? remaining(p, now) : undefined
    if (left !== undefined) pieces.push(`~${estimate(left)} left`)
    if (now - p.updatedAt >= STALE_MS) pieces.push(`last update ${duration(now - p.updatedAt)} ago`)
  } else if (phase === 'paused') {
    pieces.push('paused')
  }
  return { percent: p.percent, phase, pieces }
}

export type Layout = { cells: number; filled: number; head: string; tail: string }

/** Filled cells of a bar for `percent`; anything above 0 shows one, anything under 100 leaves one empty. */
export function filled(percent: number, cells: number): number {
  if (percent <= 0) return 0
  if (percent >= 100) return cells
  return Math.min(cells - 1, Math.max(1, Math.round((percent / 100) * cells)))
}

/**
 * The bar and the words that fit in `columns`: the full-width bar with
 * everything, then a narrower bar, then fewer words (the last drop first).
 */
export function layout(view: View, columns: number, o: Options): Layout {
  const head = view.phase === 'done' ? '100% ✓' : `${view.percent}%`
  const room = columns - PADDING
  for (let n = view.pieces.length; n >= 0; n--) {
    const tail = view.pieces.slice(0, n).join(SEPARATOR)
    const extra = 1 + head.length + (tail ? 2 + tail.length : 0)
    const cells = Math.min(o.bar_width, room - extra)
    if (cells >= MIN_CELLS) return { cells, filled: filled(view.percent, cells), head, tail }
  }
  return { cells: MIN_CELLS, filled: filled(view.percent, MIN_CELLS), head, tail: '' }
}

/** The line as plain text, for tests and the status line. */
export function lineText(l: Layout): string {
  return '━'.repeat(l.filled) + '─'.repeat(l.cells - l.filled) + ` ${l.head}` + (l.tail ? `  ${l.tail}` : '')
}

/** What Claude is told, appended to its system prompt. */
export function instructions(tool: string): string {
  return [
    '# Progress reporting',
    '',
    `The person watches a progress bar that only you can fill, by calling \`${tool}\`. They often step away and rely on it to know how far along you are.`,
    '',
    '- For any task that will take more than a few tool calls, call it once when you have sized up the work, before the first change (say 5), then again after each real milestone: a file finished, a step done, a test run. Not after every tool call.',
    '- `percent` is the share of the WHOLE task that is finished, counting what is still ahead (tests, verification, the summary). Keep it honest. If you find more work than you thought, lower it rather than leave a number you no longer believe.',
    '- Report 100 only when everything is finished and verified, as your last action before the final answer.',
    '- `step` is a few words on what you are doing now ("Updating the auth tests").',
    '- Skip it for questions and quick one-step edits.',
  ].join('\n')
}
