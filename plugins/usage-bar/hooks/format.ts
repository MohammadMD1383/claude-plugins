import type { UsageBarAgent, UsageBarLimit, UsageBarSnapshot } from '../types'

export type Options = {
  show_context: boolean
  show_five_hour: boolean
  show_weekly: boolean
  show_reset: boolean
  show_meters: boolean
  placement: 'above-prompt' | 'footer'
  warn_pct: number
  critical_pct: number
}

export const DEFAULTS: Options = {
  show_context: true,
  show_five_hour: true,
  show_weekly: true,
  show_reset: true,
  show_meters: true,
  placement: 'above-prompt',
  warn_pct: 70,
  critical_pct: 90,
}

export type Level = 'ok' | 'warn' | 'critical'

export type Segment = {
  id: 'context' | 'five_hour' | 'seven_day'
  label: string
  /** 0-100, drives the meter and the color; absent before the first reading. */
  pct?: number
  value: string
  reset?: string
  level: Level
}

/** How much a line carries: everything, then without meters, then without resets. */
export type Density = { meters: boolean; resets: boolean }

export const METER_CELLS = 6
export const SEPARATOR = '  ·  '

export function readOptions(raw: Partial<Record<keyof Options, unknown>> | undefined): Options {
  const o = { ...DEFAULTS }
  if (!raw) return o
  for (const key of ['show_context', 'show_five_hour', 'show_weekly', 'show_reset', 'show_meters'] as const) {
    if (typeof raw[key] === 'boolean') o[key] = raw[key] as boolean
  }
  if (raw.placement === 'footer' || raw.placement === 'above-prompt') o.placement = raw.placement
  if (typeof raw.warn_pct === 'number') o.warn_pct = raw.warn_pct
  if (typeof raw.critical_pct === 'number') o.critical_pct = raw.critical_pct
  return o
}

/** 84_312 -> "84k", 1_000_000 -> "1M", 950 -> "950". */
export function tokens(n: number): string {
  if (n >= 1_000_000) return trim(n / 1_000_000) + 'M'
  if (n >= 1_000) return (n >= 100_000 ? String(Math.round(n / 1000)) : trim(n / 1000)) + 'k'
  return String(Math.round(n))
}

function trim(x: number): string {
  const s = x.toFixed(x >= 10 ? 0 : 1)
  return s.endsWith('.0') ? s.slice(0, -2) : s
}

/** Time until `resetsAt`: "42m", "2h14m", "3d4h", "now". */
export function countdown(resetsAt: string | undefined, now: number): string | undefined {
  if (!resetsAt) return undefined
  const at = Date.parse(resetsAt)
  if (Number.isNaN(at)) return undefined
  const minutes = Math.ceil((at - now) / 60_000)
  if (minutes <= 0) return 'now'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    const m = minutes % 60
    return m ? `${hours}h${String(m).padStart(2, '0')}m` : `${hours}h`
  }
  const days = Math.floor(hours / 24)
  const h = hours % 24
  return h ? `${days}d${h}h` : `${days}d`
}

export function level(pct: number | undefined, o: Options): Level {
  if (pct === undefined) return 'ok'
  if (pct >= o.critical_pct) return 'critical'
  if (pct >= o.warn_pct) return 'warn'
  return 'ok'
}

function percent(p: number): string {
  return `${Math.round(p)}%`
}

/** The standard context window, for a subagent on another model than the main thread. */
export const STANDARD_WINDOW = 200_000
export const MAX_AGENTS = 16

/** Input tokens a response was answered over: uncached, cache-written and cache-read. */
export function contextTokens(u: {
  input_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
}): number {
  return u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
}

/** A subagent on the main thread's model shares its window; any other gets the standard one. */
export function agentWindow(model: string | undefined, s: UsageBarSnapshot | null): number {
  if (s && s.window > 0 && (model === undefined || model === s.model)) return s.window
  return STANDARD_WINDOW
}

/** `agents` with `id` set to `agent`, keeping only the most recent MAX_AGENTS. */
export function withAgent(
  agents: Record<string, UsageBarAgent>,
  id: string,
  agent: UsageBarAgent,
): Record<string, UsageBarAgent> {
  const { [id]: _, ...rest } = agents
  const entries = [...Object.entries(rest), [id, agent] as const]
  return Object.fromEntries(entries.slice(-MAX_AGENTS))
}

/**
 * Which transcript is on screen: the main conversation (no `agentId`), or a
 * subagent's, whose context replaces the main one in the bar.
 */
export type View = { agentId?: string; agent?: UsageBarAgent }

export function segments(s: UsageBarSnapshot | null, now: number, o: Options, view: View = {}): Segment[] {
  if (!s) return []
  const out: Segment[] = []

  if (o.show_context) {
    const inAgent = view.agentId !== undefined
    const window = inAgent ? agentWindow(view.agent?.model, s) : s.window
    const used = inAgent ? view.agent?.tokens : s.tokens
    if (window > 0) {
      const pct = !inAgent && s.percent !== undefined ? s.percent : used !== undefined ? (used / window) * 100 : undefined
      out.push({
        id: 'context',
        label: inAgent ? 'agent' : 'ctx',
        pct,
        value: `${used !== undefined ? tokens(used) : '–'}/${tokens(window)}`,
        level: level(pct, o),
      })
    }
  }

  const windows = [
    { id: 'five_hour', label: '5h', on: o.show_five_hour },
    { id: 'seven_day', label: 'week', on: o.show_weekly },
  ] as const
  for (const w of windows) {
    if (!w.on) continue
    const reading = s.limits.find(l => l.kind === w.id)
    if (!reading) continue
    const limit = current(reading, now)
    out.push({
      id: w.id,
      label: w.label,
      pct: limit.percentUsed,
      value: percent(limit.percentUsed),
      reset: o.show_reset ? countdown(limit.resetsAt, now) : undefined,
      level: level(limit.percentUsed, o),
    })
  }
  return out
}

const WEEK = 7 * 24 * 60 * 60_000

/**
 * A reading as it stands at `now`. Readings only arrive with responses, so a
 * window that reset while the session sat idle still carries its old figures:
 * past its reset it is empty again. A weekly window resets on a fixed cadence,
 * so its next reset is a whole number of weeks on; a 5-hour window starts with
 * the next message, so until then it has no reset time.
 */
export function current(limit: UsageBarLimit, now: number): UsageBarLimit {
  const at = limit.resetsAt ? Date.parse(limit.resetsAt) : Number.NaN
  if (Number.isNaN(at) || at > now) return limit
  if (limit.kind === 'seven_day') {
    const weeks = Math.floor((now - at) / WEEK) + 1
    return { kind: limit.kind, percentUsed: 0, resetsAt: new Date(at + weeks * WEEK).toISOString() }
  }
  return { kind: limit.kind, percentUsed: 0 }
}

/** Filled cells of a meter for `pct`; any usage above zero shows at least one. */
export function filled(pct: number | undefined): number {
  if (pct === undefined || pct <= 0) return 0
  return Math.min(METER_CELLS, Math.max(1, Math.round((pct / 100) * METER_CELLS)))
}

export function segmentText(seg: Segment, d: Density): string {
  let text = `${seg.label} `
  if (d.meters && seg.pct !== undefined) text += '━'.repeat(METER_CELLS) + ' '
  text += seg.value
  if (d.resets && seg.reset) text += ` ↻ ${seg.reset}`
  return text
}

export function lineText(segs: Segment[], d: Density): string {
  return segs.map(seg => segmentText(seg, d)).join(SEPARATOR)
}

/** The richest density whose line fits in `columns`; the last one regardless. */
export function fit(segs: Segment[], columns: number, o: Options): Density {
  const tries: Density[] = [
    { meters: o.show_meters, resets: true },
    { meters: false, resets: true },
    { meters: false, resets: false },
  ]
  return tries.find(d => lineText(segs, d).length <= columns) ?? { meters: false, resets: false }
}
