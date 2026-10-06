import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { advance, describe as describeRun, duration, estimate, filled, layout, lineText, readOptions, remaining, toPercent, toStep, DEFAULTS } from '../hooks/format'

const NOW = Date.parse('2026-10-06T12:00:00Z')
const MIN = 60_000
const TOOL = 'mcp__progress-bar__set_progress'

const BAND = (bodyColumns: number, isWorking = true) => ({
  plugin: 'progress-bar',
  component: 'AbovePrompt' as const,
  surface: 'terminal' as const,
  props: { hasSurvey: false, isWorking, maxRows: 10, bodyColumns, scroll: { offset: 0, bodyRows: 10 }, view: {} },
})

function engine(on: On) {
  const clock = mock.clock(on, { now: NOW })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('classic.SessionStart', () => ({}))
  on('tool.register', () => ({ value: { tool: TOOL } }))
  on('prompt.submit', (_$, e) => ({ text: e.text }))
  // What the engine draws when the plugin passes: no Box, so a test can tell.
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  return clock
}

const COMPOSE = (surfaces: string[]) =>
  ({ model: 'm', promptModel: 'm', surfaces, tools: [], outputStyle: null, traits: [] }) as never

describe('format', () => {
  test('a reported percentage is a whole number from 0 to 100', () => {
    expect(toPercent(43)).toBe(43)
    expect(toPercent(43.6)).toBe(44)
    expect(toPercent('60')).toBe(60)
    expect(toPercent(-5)).toBe(0)
    expect(toPercent(250)).toBe(100)
    expect(toPercent('lots')).toBeUndefined()
    expect(toPercent(undefined)).toBeUndefined()
    expect(toPercent(NaN)).toBeUndefined()
    expect(toPercent('')).toBeUndefined()
  })

  test('a step is one short line', () => {
    expect(toStep('  Updating\n the   tests ')).toBe('Updating the tests')
    expect(toStep('')).toBeUndefined()
    expect(toStep(7)).toBeUndefined()
    expect(toStep('x'.repeat(100))).toHaveLength(60)
    expect(toStep('x'.repeat(100))?.endsWith('…')).toBe(true)
  })

  test('durations read at a glance', () => {
    expect(duration(45_000)).toBe('45s')
    expect(duration(130_000)).toBe('2m10s')
    expect(duration(14 * MIN)).toBe('14m')
    expect(duration(65 * MIN)).toBe('1h05m')
    expect(duration(-5)).toBe('0s')
    expect(estimate(20_000)).toBe('<1m')
    expect(estimate(12 * MIN)).toBe('12m')
    expect(estimate(125 * MIN)).toBe('2h05m')
  })

  test('a run keeps its start until it finishes', () => {
    const a = advance(null, { percent: 10, step: 'Reading' }, NOW)
    expect(a).toEqual({ percent: 10, step: 'Reading', startedAt: NOW, startPercent: 10, updatedAt: NOW })
    const b = advance(a, { percent: 40 }, NOW + 5 * MIN)
    expect(b).toMatchObject({ percent: 40, step: undefined, startedAt: NOW, startPercent: 10, updatedAt: NOW + 5 * MIN })
    const done = advance(b, { percent: 100 }, NOW + 9 * MIN)
    expect(done.startedAt).toBe(NOW)
    // A report after 100 is a new task.
    const next = advance(done, { percent: 5 }, NOW + 20 * MIN)
    expect(next).toMatchObject({ startedAt: NOW + 20 * MIN, startPercent: 5 })
  })

  test('time left comes from the speed since the first report', () => {
    const p = { percent: 40, startedAt: NOW, startPercent: 10, updatedAt: NOW + 6 * MIN }
    // 30 points in 6 minutes: 60 points left take 12.
    expect(remaining(p, NOW + 6 * MIN)).toBe(12 * MIN)
    // Two minutes after the report, two of those are spent.
    expect(remaining(p, NOW + 8 * MIN)).toBe(10 * MIN)
    expect(remaining(p, NOW + 30 * MIN)).toBe(0)
  })

  test('there is no estimate without enough to go on', () => {
    expect(remaining({ percent: 12, startedAt: NOW, startPercent: 10, updatedAt: NOW + 10 * MIN }, NOW)).toBeUndefined()
    expect(remaining({ percent: 50, startedAt: NOW, startPercent: 10, updatedAt: NOW + 10_000 }, NOW)).toBeUndefined()
    expect(remaining({ percent: 100, startedAt: NOW, startPercent: 0, updatedAt: NOW + 10 * MIN }, NOW)).toBeUndefined()
  })

  test('a run left unfinished says it is paused, and stops counting', () => {
    const p = { percent: 43, step: 'Refactoring', startedAt: NOW, startPercent: 5, updatedAt: NOW + 4 * MIN }
    const running = describeRun(p, NOW + 5 * MIN, true, DEFAULTS)
    expect(running.phase).toBe('running')
    expect(running.pieces[1]).toBe('5m00s')
    const paused = describeRun(p, NOW + 60 * MIN, false, DEFAULTS)
    expect(paused.phase).toBe('paused')
    expect(paused.pieces).toEqual(['Refactoring', '4m00s', 'paused'])
  })

  test('a long silence while working is pointed out', () => {
    const p = { percent: 43, startedAt: NOW, startPercent: 40, updatedAt: NOW }
    expect(describeRun(p, NOW + 2 * MIN, true, DEFAULTS).pieces.join()).not.toContain('last update')
    expect(describeRun(p, NOW + 5 * MIN, true, DEFAULTS).pieces).toContain('last update 5m00s ago')
  })

  test('a finished run reports how long it took', () => {
    const p = { percent: 100, step: 'Done', startedAt: NOW, startPercent: 5, updatedAt: NOW + 9 * MIN }
    const v = describeRun(p, NOW + 99 * MIN, false, DEFAULTS)
    expect(v.phase).toBe('done')
    expect(v.pieces).toEqual(['Done', 'done in 9m00s'])
  })

  test('the time left can be switched off', () => {
    const p = { percent: 40, startedAt: NOW, startPercent: 10, updatedAt: NOW + 6 * MIN }
    expect(describeRun(p, NOW + 6 * MIN, true, DEFAULTS).pieces.join()).toContain('~12m left')
    expect(describeRun(p, NOW + 6 * MIN, true, { ...DEFAULTS, show_eta: false }).pieces.join()).not.toContain('left')
  })

  test('a bar shows any progress and never looks finished early', () => {
    expect(filled(0, 24)).toBe(0)
    expect(filled(1, 24)).toBe(1)
    expect(filled(50, 24)).toBe(12)
    expect(filled(99, 24)).toBe(23)
    expect(filled(100, 24)).toBe(24)
  })

  test('a narrow line shrinks the bar, then drops words from the end', () => {
    const view = describeRun(
      { percent: 40, step: 'Updating the auth tests', startedAt: NOW, startPercent: 10, updatedAt: NOW + 6 * MIN },
      NOW + 6 * MIN,
      true,
      DEFAULTS,
    )
    const wide = layout(view, 120, DEFAULTS)
    expect(wide.cells).toBe(24)
    expect(wide.tail).toBe('Updating the auth tests · 6m00s · ~12m left')
    const mid = layout(view, 70, DEFAULTS)
    expect(mid.cells).toBeLessThan(24)
    expect(mid.cells).toBeGreaterThanOrEqual(10)
    expect(mid.tail).toBe(wide.tail)
    const narrow = layout(view, 50, DEFAULTS)
    expect(narrow.tail).toBe('Updating the auth tests · 6m00s')
    expect(lineText(narrow).length).toBeLessThanOrEqual(48)
    expect(layout(view, 20, DEFAULTS).tail).toBe('')
  })

  test('options are clamped and defaults filled in', () => {
    expect(readOptions(undefined)).toEqual(DEFAULTS)
    expect(readOptions({ bar_width: 500 }).bar_width).toBe(60)
    expect(readOptions({ bar_width: 2 }).bar_width).toBe(10)
    expect(readOptions({ show_eta: false, auto_report: false })).toMatchObject({ show_eta: false, auto_report: false })
    expect(readOptions({ show_eta: 'no' }).show_eta).toBe(true)
  })
})

const report = ($: Engine, input: Record<string, unknown>, extra: object = {}) =>
  $.tool.call({ tool: TOOL, ...input, ...extra } as never)

async function band($: Engine, columns = 120, isWorking = true) {
  const ui = await $.ui.mount(BAND(columns, isWorking))
  const text = (await ui.find({ type: 'Box' }))?.text
  return { ui, text }
}

test('registers a tool the model can call', async ($, on) => {
  mock.clock(on, { now: NOW })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  let spec: { name: string; inputSchema?: { required?: string[] } } | undefined
  on('tool.register', (_$, e) => {
    spec = e as never
    return { value: { tool: TOOL } }
  })
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  expect(spec?.name).toBe('set_progress')
  expect(spec?.inputSchema?.required).toEqual(['percent'])
})

test('the bar is not there before Claude reports', async ($, on) => {
  engine(on)
  const { ui, text } = await band($)
  expect(text).toBeUndefined()
  await ui.unmount()
})

test('a report draws the bar with the exact percentage and the step', async ($, on) => {
  engine(on)
  const r = await report($, { percent: 43, step: 'Refactoring auth' })
  expect(r.deny).toBeUndefined()
  expect(r.result).toBe('Progress set to 43%.')

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND(120), surface })
    const line = (await ui.find({ type: 'Box' }))?.text ?? ''
    expect(line).toContain('43%')
    expect(line).toContain('Refactoring auth')
    expect(line).toContain('━')
    expect(line).toContain('─')
    await ui.unmount()
  }
})

test('the bar moves with each report and counts elapsed time', async ($, on) => {
  const clock = engine(on)
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await report($, { percent: 10 })
  await clock.advance(6 * MIN)
  await report($, { percent: 40, step: 'Writing tests' })
  const { ui, text } = await band($)
  expect(text).toContain('40%')
  expect(text).toContain('Writing tests · 6m00s · ~12m left')
  await ui.unmount()
})

test('time keeps counting between reports while Claude works', async ($, on) => {
  const clock = engine(on)
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await report($, { percent: 10 })
  await clock.advance(6 * MIN)
  await report($, { percent: 40 })
  await clock.advance(2 * MIN)
  const { ui, text } = await band($)
  expect(text).toContain('8m00s')
  expect(text).toContain('~10m left')
  await ui.unmount()
})

test('a bar left unfinished when the turn ends reads paused', async ($, on) => {
  const clock = engine(on)
  await report($, { percent: 43, step: 'Refactoring' })
  await clock.advance(30 * MIN)
  const { ui, text } = await band($, 120, false)
  expect(text).toContain('43%')
  expect(text).toContain('paused')
  expect(text).not.toContain('left')
  await ui.unmount()
})

test('100 turns the bar green and says how long it took', async ($, on) => {
  const clock = engine(on)
  await report($, { percent: 5 })
  await clock.advance(9 * MIN)
  await report($, { percent: 100, step: 'All tests pass' })
  const { ui, text } = await band($, 120, false)
  expect(text).toContain('100% ✓')
  expect(text).toContain('done in 9m00s')
  expect((await ui.find({ type: 'Text', text: '100% ✓' }))?.props.color).toBe('green')
  await ui.unmount()
})

test('the next prompt clears a finished bar but not an unfinished one', async ($, on) => {
  engine(on)
  await report($, { percent: 100 })
  await $.prompt.submit({ text: 'next thing' } as never)
  const a = await band($)
  expect(a.text).toBeUndefined()
  await a.ui.unmount()

  await report($, { percent: 30 })
  await $.prompt.submit({ text: 'carry on' } as never)
  const b = await band($)
  expect(b.text).toContain('30%')
  await b.ui.unmount()
})

test('a report that is not a percentage is refused with the reason', async ($, on) => {
  engine(on)
  const r = await report($, { percent: 'lots' })
  expect(r.deny).toContain('0 to 100')
  const { ui, text } = await band($)
  expect(text).toBeUndefined()
  await ui.unmount()
})

test('a subagent cannot move the bar', async ($, on) => {
  engine(on)
  await report($, { percent: 20 })
  const r = await report($, { percent: 90 }, { agentId: 'a1' })
  expect(r.deny).toContain('main agent')
  const { ui, text } = await band($)
  expect(text).toContain('20%')
  await ui.unmount()
})

test('a /clear empties the bar and keeps the tool', async ($, on) => {
  mock.clock(on, { now: NOW })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('classic.SessionStart', () => ({}))
  let registered = 0
  on('tool.register', () => {
    registered += 1
    return { value: { tool: TOOL } }
  })
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await $.classic.SessionStart({ source: 'clear' })
  expect(registered).toBe(2)
})

test('the tool is always in the list and never asks permission', async ($, on) => {
  engine(on)
  on('tool.describe', () => ({ description: 'x', isDeferred: true }))
  const described = await $.tool.describe?.({ tool: TOOL, description: 'x', isDeferred: true } as never)
  expect(described?.isDeferred).toBe(false)
  const { decision } = await $.tool.check({ tool: TOOL, input: { percent: 10 } })
  expect(decision).toBe('allow')
})

test('Claude is told to report progress', async ($, on) => {
  engine(on)
  on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' }] }))
  const { sections } = await $.prompt.compose(COMPOSE(['terminal']))
  const mine = sections.find(s => s.id === 'progress-bar:report')
  expect(mine?.scope).toBe('session')
  expect(mine?.text).toContain(TOOL)
  expect(mine?.text).toContain('100')
  expect(sections[0]?.id).toBe('intro')
})

test('nothing is added where nothing draws', async ($, on) => {
  engine(on)
  on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' }] }))
  const { sections } = await $.prompt.compose(COMPOSE([]))
  expect(sections.map(s => s.id)).toEqual(['intro'])
})

test('the instruction can be switched off', { options: { auto_report: false } }, async ($, on) => {
  engine(on)
  on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' }] }))
  const { sections } = await $.prompt.compose(COMPOSE(['terminal']))
  expect(sections.map(s => s.id)).toEqual(['intro'])
})

test('the bar can be dismissed and comes back with the next report', async ($, on) => {
  engine(on)
  await report($, { percent: 43 })
  const a = await band($, 120, false)
  await a.ui.press({ plugin: 'progress-bar', key: 'hide' } as never)
  await a.ui.unmount()
  const b = await band($, 120, false)
  expect(b.text).toBeUndefined()
  await b.ui.unmount()

  await report($, { percent: 50 })
  const c = await band($, 120, false)
  expect(c.text).toContain('50%')
  await c.ui.unmount()
})

test('hides what is switched off', { options: { show_eta: false } }, async ($, on) => {
  const clock = engine(on)
  await report($, { percent: 10 })
  await clock.advance(6 * MIN)
  await report($, { percent: 40 })
  const { ui, text } = await band($)
  expect(text).not.toContain('left')
  await ui.unmount()
})

test('a narrow terminal still shows the percentage', async ($, on) => {
  engine(on)
  await report($, { percent: 43, step: 'Refactoring the authentication module' })
  const { ui, text } = await band($, 40)
  expect(text).toContain('43%')
  await ui.unmount()
})
