import type { AgyQuota, AgyWindow } from '../types'

export type Family = 'claude' | 'gemini' | 'all'
export type Level = 'ok' | 'warn' | 'critical'

export type Options = {
  gateway_url: string
  gateway_key: string
  activate: boolean
  usage_url: string
  family: Family
  show_when: 'gateway' | 'always'
  poll_seconds: number
  show_reset: boolean
  warn_pct: number
  critical_pct: number
}

export const DEFAULTS: Options = {
  gateway_url: 'http://localhost:20128',
  gateway_key: '',
  activate: false,
  usage_url: '',
  family: 'claude',
  show_when: 'gateway',
  poll_seconds: 60,
  show_reset: true,
  warn_pct: 70,
  critical_pct: 90,
}

export const METER_CELLS = 6

export function readOptions(raw: Partial<Record<keyof Options, unknown>> | undefined): Options {
  const o = { ...DEFAULTS }
  if (!raw) return o
  for (const key of ['gateway_url', 'gateway_key', 'usage_url'] as const) {
    if (typeof raw[key] === 'string') o[key] = (raw[key] as string).trim()
  }
  if (o.gateway_url === '') o.gateway_url = DEFAULTS.gateway_url
  for (const key of ['activate', 'show_reset'] as const) {
    if (typeof raw[key] === 'boolean') o[key] = raw[key] as boolean
  }
  if (raw.family === 'claude' || raw.family === 'gemini' || raw.family === 'all') o.family = raw.family
  if (raw.show_when === 'gateway' || raw.show_when === 'always') o.show_when = raw.show_when
  for (const key of ['poll_seconds', 'warn_pct', 'critical_pct'] as const) {
    if (typeof raw[key] === 'number' && Number.isFinite(raw[key])) o[key] = raw[key] as number
  }
  o.poll_seconds = Math.max(15, o.poll_seconds)
  return o
}

/** Where to read the quota from: the explicit URL, else OmniRoute's usage-command endpoint. */
export function usageUrl(o: Options): string {
  if (o.usage_url) return o.usage_url
  return `${o.gateway_url.replace(/\/+$/, '')}/api/usage/om-usage?format=json`
}

/** Whether `baseUrl` (a session's ANTHROPIC_BASE_URL) is the gateway: same host and port. */
export function isGateway(baseUrl: string | undefined, o: Options): boolean {
  if (!baseUrl) return false
  try {
    const a = new URL(baseUrl)
    const b = new URL(o.gateway_url)
    return a.hostname === b.hostname && (a.port || defaultPort(a.protocol)) === (b.port || defaultPort(b.protocol))
  } catch {
    return false
  }
}

function defaultPort(protocol: string): string {
  return protocol === 'https:' ? '443' : '80'
}

type Rec = Record<string, unknown>

const isRecord = (v: unknown): v is Rec => typeof v === 'object' && v !== null && !Array.isArray(v)

function num(v: unknown): number | undefined {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v)
  return undefined
}

const clamp = (n: number) => Math.max(0, Math.min(100, n))

/**
 * How much of a quota row is used, 0-100. Both gateways derive the row from
 * Google's `remainingFraction`, so `remainingPercentage` is the primary field;
 * `used/total` is the fallback. `remaining` is deliberately not read: it is a
 * count out of an arbitrary total in one gateway and a percentage in another.
 */
export function usedPercent(row: Rec): number | undefined {
  const usedPct = num(row.usedPercentage)
  if (usedPct !== undefined) return clamp(usedPct)
  const left = num(row.remainingPercentage)
  if (left !== undefined) return clamp(100 - left)
  const used = num(row.used)
  const total = num(row.total)
  if (used !== undefined && total !== undefined && total > 0) return clamp((used / total) * 100)
  return undefined
}

function resetOf(row: Rec): string | undefined {
  return typeof row.resetAt === 'string' && row.resetAt.trim() !== '' ? row.resetAt : undefined
}

/** The Antigravity quota rows both gateways name for the 5-hour and weekly windows. */
const ROWS = {
  claude: { session: 'claude_gpt_session', weekly: 'claude_gpt_weekly' },
  gemini: { session: 'gemini_session', weekly: 'gemini_weekly' },
} as const

function windowsFor(quotas: Rec, family: 'claude' | 'gemini', prefix: boolean): AgyWindow[] {
  const out: AgyWindow[] = []
  const name = (label: string) => (prefix ? `${family} ${label}` : label)
  const add = (id: string, label: string, key: string) => {
    const row = quotas[key]
    if (!isRecord(row)) return
    const pct = usedPercent(row)
    if (pct === undefined) return
    out.push({ id: prefix ? `${family}:${id}` : id, label: name(label), percentUsed: pct, resetsAt: resetOf(row) })
  }
  add('session', '5h', ROWS[family].session)
  add('weekly', 'week', ROWS[family].weekly)
  if (out.length > 0) return out

  // No session or weekly row (older gateway, free tier): the most used per-model row of the family.
  let worst: AgyWindow | undefined
  for (const [key, row] of Object.entries(quotas)) {
    if (!key.startsWith(`${family}-`) || !isRecord(row)) continue
    const pct = usedPercent(row)
    if (pct === undefined) continue
    if (!worst || pct > worst.percentUsed) {
      worst = { id: `model:${key}`, label: name('quota'), percentUsed: pct, resetsAt: resetOf(row) }
    }
  }
  return worst ? [worst] : []
}

function isAntigravity(provider: unknown): boolean {
  return typeof provider === 'string' && /antigravity|^agy$/i.test(provider)
}

/** The snapshot holding Antigravity's quotas, in OmniRoute's or 9router's response shape. */
function snapshotOf(body: Rec): Rec | undefined {
  if (Array.isArray(body.providers)) {
    return body.providers.find((p): p is Rec => isRecord(p) && isAntigravity(p.provider) && isRecord(p.quotas))
  }
  if (isRecord(body.provider) && isAntigravity(body.provider.provider) && isRecord(body.provider.quotas)) {
    return body.provider
  }
  if (isRecord(body.quotas)) return body
  return undefined
}

/**
 * Turns a gateway's usage response into the windows to draw. Understands
 * OmniRoute (`/api/usage/om-usage?format=json`), 9router (`/api/usage/<id>`)
 * and the normalized `{ windows: [{ label, percentUsed, resetsAt }] }` a
 * purpose-built translator can serve. Null when the body holds no Antigravity
 * quota.
 */
export function parseUsage(body: unknown, family: Family): AgyQuota | null {
  if (!isRecord(body)) return null

  if (Array.isArray(body.windows)) {
    const windows: AgyWindow[] = []
    for (const [i, w] of body.windows.entries()) {
      if (!isRecord(w)) continue
      const pct = num(w.percentUsed)
      if (pct === undefined || typeof w.label !== 'string') continue
      windows.push({
        id: typeof w.id === 'string' ? w.id : `window:${i}`,
        label: w.label,
        percentUsed: clamp(pct),
        resetsAt: typeof w.resetsAt === 'string' ? w.resetsAt : undefined,
      })
    }
    return windows.length > 0 ? { plan: typeof body.plan === 'string' ? body.plan : undefined, windows } : null
  }

  const snap = snapshotOf(body)
  if (!snap || !isRecord(snap.quotas)) return null
  const families = family === 'all' ? (['claude', 'gemini'] as const) : ([family] as const)
  const windows = families.flatMap(f => windowsFor(snap.quotas as Rec, f, family === 'all'))
  if (windows.length === 0) return null
  return { plan: typeof snap.plan === 'string' ? snap.plan : undefined, windows }
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

export function level(pct: number, o: Pick<Options, 'warn_pct' | 'critical_pct'>): Level {
  if (pct >= o.critical_pct) return 'critical'
  if (pct >= o.warn_pct) return 'warn'
  return 'ok'
}

/** Filled cells of a meter: any usage shows at least one. */
export function filled(pct: number): number {
  if (pct <= 0) return 0
  return Math.max(1, Math.min(METER_CELLS, Math.round((pct / 100) * METER_CELLS)))
}

/** A window that reset while the gateway was unreachable reads empty rather than stale. */
export function current(w: AgyWindow, now: number): AgyWindow {
  if (!w.resetsAt) return w
  const at = Date.parse(w.resetsAt)
  if (Number.isNaN(at) || at > now) return w
  return { ...w, percentUsed: 0, resetsAt: undefined }
}

/** One plain-text line per window, for the `/agy` command. */
export function summary(q: AgyQuota, now: number): string {
  const lines = [q.plan ? `Antigravity (${q.plan})` : 'Antigravity']
  for (const w of q.windows.map(w => current(w, now))) {
    const reset = countdown(w.resetsAt, now)
    lines.push(`  ${w.label}: ${Math.round(w.percentUsed)}% used${reset ? `, resets in ${reset}` : ''}`)
  }
  return lines.join('\n')
}
