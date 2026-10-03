import type { UsageBarSnapshot } from '../types'

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

export function segments(s: UsageBarSnapshot | null, now: number, o: Options): Segment[] {
  if (!s) return []
  const out: Segment[] = []

  if (o.show_context && s.window > 0) {
    const pct = s.percent ?? (s.tokens !== undefined ? (s.tokens / s.window) * 100 : undefined)
    out.push({
      id: 'context',
      label: 'ctx',
      pct,
      value: `${s.tokens !== undefined ? tokens(s.tokens) : '–'}/${tokens(s.window)}`,
      level: level(pct, o),
    })
  }

  const windows = [
    { id: 'five_hour', label: '5h', on: o.show_five_hour },
    { id: 'seven_day', label: 'week', on: o.show_weekly },
  ] as const
  for (const w of windows) {
    if (!w.on) continue
    const limit = s.limits.find(l => l.kind === w.id)
    if (!limit) continue
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
