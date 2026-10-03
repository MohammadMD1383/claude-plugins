import { describe, expect, mock, test } from 'claude-code/testing'
import type { On, SessionRateLimit, UsageUnit } from 'claude-code'

import { countdown, DEFAULTS, fit, filled, segments, tokens } from '../hooks/format'

const NOW = Date.parse('2026-10-03T12:00:00Z')
const LIMITS: SessionRateLimit[] = [
  { kind: 'five_hour', percentUsed: 31, resetsAt: '2026-10-03T14:14:00Z' },
  { kind: 'seven_day', percentUsed: 92.5, resetsAt: '2026-10-06T16:00:00Z' },
]
const BAND = (bodyColumns: number) => ({
  plugin: 'usage-bar',
  component: 'AbovePrompt' as const,
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns, scroll: { offset: 0, bodyRows: 10 }, view: {} },
})

function engine(on: On, rateLimits: SessionRateLimit[] = LIMITS) {
  const clock = mock.clock(on, { now: NOW })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.usage', () => ({ value: { startedAt: NOW, context: { window: 200_000 }, rateLimits: [] } }))
  on('session.measure', ($, e) => ({ changed: e.changed }))
  return {
    context: { tokens: 84_312, window: 200_000, percent: 42 },
    rateLimits,
    changed: ['context', 'rateLimits'] as UsageUnit[],
    clock,
  }
}

const measureOf = ({ clock: _, ...measure }: ReturnType<typeof engine>) => measure

describe('format', () => {
  test('tokens are short', () => {
    expect(tokens(950)).toBe('950')
    expect(tokens(84_312)).toBe('84k')
    expect(tokens(8_400)).toBe('8.4k')
    expect(tokens(200_000)).toBe('200k')
    expect(tokens(1_000_000)).toBe('1M')
  })

  test('countdowns read at a glance', () => {
    expect(countdown('2026-10-03T12:42:00Z', NOW)).toBe('42m')
    expect(countdown('2026-10-03T14:14:00Z', NOW)).toBe('2h14m')
    expect(countdown('2026-10-03T15:00:00Z', NOW)).toBe('3h')
    expect(countdown('2026-10-06T16:00:00Z', NOW)).toBe('3d4h')
    expect(countdown('2026-10-03T11:00:00Z', NOW)).toBe('now')
    expect(countdown(undefined, NOW)).toBeUndefined()
  })

  test('a meter shows any usage', () => {
    expect(filled(0)).toBe(0)
    expect(filled(1)).toBe(1)
    expect(filled(50)).toBe(3)
    expect(filled(100)).toBe(6)
  })

  test('sections switch off one by one', () => {
    const snap = { tokens: 1000, window: 200_000, limits: LIMITS }
    expect(segments(snap, NOW, DEFAULTS).map(s => s.id)).toEqual(['context', 'five_hour', 'seven_day'])
    expect(segments(snap, NOW, { ...DEFAULTS, show_context: false, show_weekly: false }).map(s => s.id)).toEqual(['five_hour'])
    expect(segments(snap, NOW, { ...DEFAULTS, show_reset: false })[1]?.reset).toBeUndefined()
    expect(segments(snap, NOW, DEFAULTS)[2]?.level).toBe('critical')
  })

  test('a narrow line sheds meters, then resets', () => {
    const segs = segments({ tokens: 1000, window: 200_000, limits: LIMITS }, NOW, DEFAULTS)
    expect(fit(segs, 200, DEFAULTS)).toEqual({ meters: true, resets: true })
    expect(fit(segs, 50, DEFAULTS)).toEqual({ meters: false, resets: true })
    expect(fit(segs, 30, DEFAULTS)).toEqual({ meters: false, resets: false })
  })
})

test('draws all three sections above the prompt', async ($, on) => {
  await $.session.measure(measureOf(engine(on)))
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND(120), surface })
    const line = (await ui.find({ type: 'Box' }))?.text ?? ''
    expect(line).toContain('ctx')
    expect(line).toContain('84k/200k')
    expect(line).toContain('5h')
    expect(line).toContain('31%')
    expect(line).toContain('2h14m')
    expect(line).toContain('week')
    expect(line).toContain('93%')
    expect(line).toContain('3d4h')
    expect(line).toContain('━')
    await ui.unmount()
  }
})

test('drops meters when narrow', async ($, on) => {
  await $.session.measure(measureOf(engine(on)))
  const ui = await $.ui.mount({ ...BAND(56), surface: 'terminal' })
  const line = (await ui.find({ type: 'Box' }))?.text ?? ''
  expect(line).not.toContain('━')
  expect(line).toContain('2h14m')
  await ui.unmount()
})

test('hides what is switched off', { options: { show_context: false, show_reset: false } }, async ($, on) => {
  await $.session.measure(measureOf(engine(on)))
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  const line = (await ui.find({ type: 'Box' }))?.text ?? ''
  expect(line).not.toContain('ctx')
  expect(line).not.toContain('2h14m')
  expect(line).toContain('31%')
  await ui.unmount()
})

test('off a subscription only the context shows', async ($, on) => {
  await $.session.measure(measureOf(engine(on, [])))
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  const line = (await ui.find({ type: 'Box' }))?.text ?? ''
  expect(line).toContain('84k/200k')
  expect(line).not.toContain('5h')
  await ui.unmount()
})

test('countdowns tick down', async ($, on) => {
  const { clock, ...measure } = engine(on)
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await $.session.measure(measure)
  await clock.advance(15 * 60_000)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect((await ui.find({ type: 'Box' }))?.text).toContain('1h59m')
  await ui.unmount()
})

test('footer placement appends to the hint line', { options: { placement: 'footer' } }, async ($, on) => {
  let tail: string | undefined
  on('ui.render', { component: 'PromptHint' }, ($, e) => {
    tail = e.props.tail
    const { Text } = $.ui.resolve(e)
    return <Text dimColor>{e.props.hint}</Text>
  })
  await $.session.measure(measureOf(engine(on)))
  const ui = await $.ui.mount({
    plugin: 'usage-bar',
    surface: 'terminal',
    component: 'PromptHint',
    props: { isDraft: false, isWorking: false, hint: '? for shortcuts' },
  })
  expect(tail).toBe('ctx 84k/200k · 5h 31% ↻ 2h14m · week 93% ↻ 3d4h')
  await ui.unmount()
})

test('shows the window before the first response', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect((await ui.find({ type: 'Box' }))?.text).toContain('ctx –/200k')
  await ui.unmount()
})

test('a window past the critical threshold turns red', async ($, on) => {
  await $.session.measure(measureOf(engine(on)))
  const ui = await $.ui.mount({ ...BAND(120), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: '93%' }))?.props.color).toBe('red')
  expect((await ui.find({ type: 'Text', text: '31%' }))?.props.color).toBeUndefined()
  await ui.unmount()
})
