import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { countdown, current, DEFAULTS, filled, isGateway, level, parseUsage, readOptions, summary, usageUrl } from '../hooks/quota'

const NOW = Date.parse('2026-10-04T12:00:00Z')
const FIVE = '2026-10-04T14:14:00Z'
const WEEK = '2026-10-08T16:00:00Z'

// The row shape both gateways produce from Google's fetchAvailableModels quotaInfo.
const row = (remainingPercentage: number, resetAt: string | null = FIVE) => ({
  used: 1000 - Math.round(remainingPercentage * 10),
  total: 1000,
  remainingPercentage,
  resetAt,
})

const QUOTAS = {
  claude_gpt_session: row(62, FIVE),
  claude_gpt_weekly: row(88, WEEK),
  gemini_session: row(10, FIVE),
  gemini_weekly: row(40, WEEK),
  'claude-sonnet-4-6': row(62, FIVE),
}

// OmniRoute: GET /api/usage/om-usage?format=json
const OMNIROUTE = {
  allowed: true,
  personal: null,
  provider: null,
  providers: [
    { connectionId: 'c1', provider: 'codex', plan: 'Plus', quotas: { session: row(5) } },
    { connectionId: 'c2', provider: 'antigravity', plan: 'Google AI Pro', quotas: QUOTAS },
  ],
}

// 9router: GET /api/usage/<connectionId>
const NINE_ROUTER = { plan: 'Antigravity', quotas: QUOTAS }

describe('parseUsage', () => {
  test('reads the claude windows out of an OmniRoute response', () => {
    const q = parseUsage(OMNIROUTE, 'claude')
    expect(q?.plan).toBe('Google AI Pro')
    expect(q?.windows).toEqual([
      { id: 'session', label: '5h', percentUsed: 38, resetsAt: FIVE },
      { id: 'weekly', label: 'week', percentUsed: 12, resetsAt: WEEK },
    ])
  })

  test('reads a 9router response the same way', () => {
    expect(parseUsage(NINE_ROUTER, 'claude')?.windows.map(w => w.percentUsed)).toEqual([38, 12])
  })

  test('picks the family, or both with prefixed labels', () => {
    expect(parseUsage(NINE_ROUTER, 'gemini')?.windows.map(w => w.percentUsed)).toEqual([90, 60])
    const all = parseUsage(NINE_ROUTER, 'all')
    expect(all?.windows.map(w => w.label)).toEqual(['claude 5h', 'claude week', 'gemini 5h', 'gemini week'])
    expect(new Set(all?.windows.map(w => w.id)).size).toBe(4)
  })

  test('falls back to the most used per-model row when there are no session rows', () => {
    const q = parseUsage(
      { plan: 'x', quotas: { 'claude-sonnet-4-6': row(70), 'claude-opus-4-6-thinking': row(20, WEEK) } },
      'claude',
    )
    expect(q?.windows).toEqual([{ id: 'model:claude-opus-4-6-thinking', label: 'quota', percentUsed: 80, resetsAt: WEEK }])
  })

  test('takes a normalized response from a purpose-built translator', () => {
    const q = parseUsage({ windows: [{ label: '5h', percentUsed: 41.5, resetsAt: FIVE }, { label: 'bad' }] }, 'claude')
    expect(q?.windows).toEqual([{ id: 'window:0', label: '5h', percentUsed: 41.5, resetsAt: FIVE }])
  })

  test('uses used/total when a row has no percentage, and clamps', () => {
    const q = parseUsage({ quotas: { claude_gpt_session: { used: 250, total: 1000 }, claude_gpt_weekly: { usedPercentage: 140 } } }, 'claude')
    expect(q?.windows.map(w => w.percentUsed)).toEqual([25, 100])
  })

  test('gives null for anything without Antigravity quota', () => {
    expect(parseUsage(null, 'claude')).toBeNull()
    expect(parseUsage({ allowed: false, error: { message: 'no' } }, 'claude')).toBeNull()
    expect(parseUsage({ allowed: true, providers: [{ provider: 'codex', quotas: { session: row(5) } }] }, 'claude')).toBeNull()
    expect(parseUsage({ quotas: { 'gemini-3.1-pro-low': row(5) } }, 'claude')).toBeNull()
  })
})

describe('format', () => {
  test('countdowns read at a glance', () => {
    expect(countdown(FIVE, NOW)).toBe('2h14m')
    expect(countdown(WEEK, NOW)).toBe('4d4h')
    expect(countdown('2026-10-04T11:00:00Z', NOW)).toBe('now')
    expect(countdown(undefined, NOW)).toBeUndefined()
  })

  test('a window that reset meanwhile reads empty', () => {
    const w = { id: 'session', label: '5h', percentUsed: 90, resetsAt: '2026-10-04T11:00:00Z' }
    expect(current(w, NOW)).toEqual({ id: 'session', label: '5h', percentUsed: 0, resetsAt: undefined })
    expect(current({ ...w, resetsAt: FIVE }, NOW).percentUsed).toBe(90)
  })

  test('levels and meters', () => {
    expect(level(69, DEFAULTS)).toBe('ok')
    expect(level(70, DEFAULTS)).toBe('warn')
    expect(level(90, DEFAULTS)).toBe('critical')
    expect(filled(0)).toBe(0)
    expect(filled(1)).toBe(1)
    expect(filled(100)).toBe(6)
  })

  test('the /agy text lists every window', () => {
    const q = parseUsage(NINE_ROUTER, 'claude')!
    expect(summary(q, NOW)).toBe('Antigravity (Antigravity)\n  5h: 38% used, resets in 2h14m\n  week: 12% used, resets in 4d4h')
  })
})

describe('options', () => {
  test('defaults, and the usage URL follows the gateway', () => {
    expect(usageUrl(readOptions(undefined))).toBe('http://localhost:20128/api/usage/om-usage?format=json')
    expect(usageUrl(readOptions({ gateway_url: 'http://127.0.0.1:9000/' }))).toBe('http://127.0.0.1:9000/api/usage/om-usage?format=json')
    expect(usageUrl(readOptions({ usage_url: 'http://localhost:20128/api/usage/abc' }))).toBe('http://localhost:20128/api/usage/abc')
    expect(readOptions({ poll_seconds: 1 }).poll_seconds).toBe(15)
    expect(readOptions({ family: 'nope' }).family).toBe('claude')
  })

  test('a session is on the gateway when host and port match', () => {
    const o = readOptions(undefined)
    expect(isGateway('http://localhost:20128', o)).toBe(true)
    expect(isGateway('http://localhost:20128/v1', o)).toBe(true)
    expect(isGateway('https://api.anthropic.com', o)).toBe(false)
    expect(isGateway('http://localhost:3000', o)).toBe(false)
    expect(isGateway(undefined, o)).toBe(false)
    expect(isGateway('not a url', o)).toBe(false)
  })
})

const BAND = (bodyColumns: number) => ({
  plugin: 'agy-link',
  component: 'AbovePrompt' as const,
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns, scroll: { offset: 0, bodyRows: 10 }, view: {} },
})

type World = {
  requests: { url: string; authorization?: string }[]
  env: Record<string, string>
  toasts: string[]
  commands: string[]
  clock: ReturnType<typeof mock.clock>
}

// The world beneath the mod: a clock, an environment, the gateway's usage endpoint and the host calls it makes.
function world(on: On, answer: () => { status: number; text: string } | Error, env: Record<string, string>): World {
  const clock = mock.clock(on, { now: NOW })
  const w: World = { requests: [], env: { ...env }, toasts: [], commands: [], clock }
  mock.env(on, w.env)
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('env.set', (_$, e) => {
    if (e.value === undefined) delete w.env[e.name]
    else w.env[e.name] = e.value
    return { value: undefined }
  })
  on('command.register', (_$, e) => {
    w.commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('http.fetch', (_$, e) => {
    w.requests.push({ url: e.url, authorization: e.init?.headers?.authorization })
    const a = answer()
    if (a instanceof Error) throw a
    return { value: { status: a.status, ok: a.status >= 200 && a.status < 300, headers: {}, text: a.text } }
  })
  // What the engine, or another band such as usage-bar, draws beneath the mod.
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>base</Text>
  })
  on('ui.toast', (_$, e) => {
    w.toasts.push(e.text)
    return { value: undefined }
  })
  return w
}

const GATEWAY_ENV = { ANTHROPIC_BASE_URL: 'http://localhost:20128' }
const ok = () => ({ status: 200, text: JSON.stringify(OMNIROUTE) })

const START = { cwd: '/', source: 'startup' } as never

test('draws the quota above the prompt while the session uses the gateway', { options: { gateway_key: 'sk-gw' } }, async ($, on) => {
  const w = world(on, ok, GATEWAY_ENV)
  await $.session.start(START)
  await w.clock.advance(0)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  const line = (await ui.find({ type: 'Box' }))?.text ?? ''
  expect(line).toContain('AGY')
  expect(line).toContain('5h')
  expect(line).toContain('38%')
  expect(line).toContain('2h14m')
  expect(line).toContain('week')
  expect(line).toContain('12%')
  expect(line).toContain('━')
  expect(w.requests[0]).toEqual({ url: 'http://localhost:20128/api/usage/om-usage?format=json', authorization: 'Bearer sk-gw' })
  await ui.unmount()
})

test('stays hidden while the session does not use the gateway', async ($, on) => {
  const w = world(on, ok, { ANTHROPIC_BASE_URL: 'https://api.anthropic.com' })
  await $.session.start(START)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect(await ui.find({ type: 'Box' })).toBeUndefined()
  expect(w.requests).toEqual([])
  await ui.unmount()
})

test('show_when always draws it anyway', { options: { show_when: 'always' } }, async ($, on) => {
  const w = world(on, ok, { ANTHROPIC_BASE_URL: 'https://api.anthropic.com' })
  await $.session.start(START)
  await w.clock.advance(0)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect((await ui.find({ type: 'Box' }))?.text ?? '').toContain('38%')
  await ui.unmount()
})

test('a narrow line sheds meters, then countdowns', async ($, on) => {
  const w = world(on, ok, GATEWAY_ENV)
  await $.session.start(START)
  await w.clock.advance(0)
  const wide = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect((await wide.find({ type: 'Box' }))?.text ?? '').toContain('━')
  await wide.unmount()
  const narrow = await $.ui.mount({ ...BAND(40), surface: 'terminal' })
  const line = (await narrow.find({ type: 'Box' }))?.text ?? ''
  expect(line).not.toContain('━')
  expect(line).toContain('38%')
  await narrow.unmount()
})

test('says why when the gateway is down and nothing was read yet', async ($, on) => {
  const w = world(on, () => new Error('ECONNREFUSED'), GATEWAY_ENV)
  await $.session.start(START)
  await w.clock.advance(0)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect((await ui.find({ type: 'Box' }))?.text ?? '').toContain('gateway unreachable')
  await ui.unmount()
})

test('a rejected key is named', async ($, on) => {
  const w = world(on, () => ({ status: 403, text: '{"allowed":false}' }), GATEWAY_ENV)
  await $.session.start(START)
  await w.clock.advance(0)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect((await ui.find({ type: 'Box' }))?.text ?? '').toContain('key may not read usage')
  await ui.unmount()
})

test('keeps the last reading, marked, when a later read fails', { options: { poll_seconds: 60 } }, async ($, on) => {
  let up = true
  const w = world(on, () => (up ? ok() : { status: 500, text: '' }), GATEWAY_ENV)
  await $.session.start(START)
  await w.clock.advance(0)
  up = false
  await w.clock.advance(61_000)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  const line = (await ui.find({ type: 'Box' }))?.text ?? ''
  expect(line).toContain('38%')
  expect(line).toContain('HTTP 500')
  expect(w.requests.length).toBeGreaterThan(1)
  await ui.unmount()
})

test('activate points the session at the gateway with the gateway key', { options: { activate: true, gateway_key: 'sk-gw' } }, async ($, on) => {
  const w = world(on, ok, {})
  await $.session.start(START)
  expect(w.env.ANTHROPIC_BASE_URL).toBe('http://localhost:20128')
  expect(w.env.ANTHROPIC_AUTH_TOKEN).toBe('sk-gw')
})

test('activate without a key does nothing, so the claude.ai login is never sent', { options: { activate: true } }, async ($, on) => {
  const w = world(on, ok, {})
  await $.session.start(START)
  expect(w.env.ANTHROPIC_BASE_URL).toBeUndefined()
  expect(w.env.ANTHROPIC_AUTH_TOKEN).toBeUndefined()
  expect(w.toasts.join(' ')).toContain('gateway API key')
})

test('/agy prints the windows', async ($, on) => {
  const w = world(on, ok, GATEWAY_ENV)
  await $.session.start(START)
  await w.clock.advance(0)
  expect(w.commands).toEqual(['agy'])
  const out = await $.command.run({ command: 'agy', args: '' } as never)
  expect(JSON.stringify(out)).toContain('5h: 38% used, resets in 2h14m')
})

test('stacks under the band another mod draws instead of replacing it', async ($, on) => {
  const w = world(on, ok, GATEWAY_ENV)
  await $.session.start(START)
  await w.clock.advance(0)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  const line = (await ui.find({ type: 'Box' }))?.text ?? ''
  expect(line).toContain('base')
  expect(line).toContain('AGY')
  expect(line.indexOf('base')).toBeLessThan(line.indexOf('AGY'))
  await ui.unmount()
})
