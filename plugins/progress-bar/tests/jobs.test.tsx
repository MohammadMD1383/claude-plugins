import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { DEFAULTS, readOptions } from '../hooks/format'
import {
  idleMs,
  ingest,
  isStalled,
  jobInstructions,
  jobLine,
  jobRow,
  markDied,
  noticeStalls,
  rate,
  rowText,
  transitions,
  visible,
} from '../hooks/jobs'
import type { Job } from '../types'

const NOW = Date.parse('2026-10-11T12:00:00Z')
const SEC = 1000
const MIN = 60_000
const O = DEFAULTS
const STATUS = 'mcp__progress-bar__job_status'

const step = (jobs: Record<string, Job>, message: object, at: number) => ingest(jobs, message, NOW + at)

describe('ingest', () => {
  test('a start makes a running job; counting items gives a floored percentage', () => {
    let jobs = step({}, { id: 'shots', op: 'start', label: 'Screenshots', total: 240, pid: 41 }, 0)
    expect(jobs.shots).toMatchObject({ status: 'running', label: 'Screenshots', done: 0, total: 240, percent: 0, pid: 41 })
    jobs = step(jobs, { id: 'shots', op: 'set', done: 119 }, 5 * MIN)
    expect(jobs.shots).toMatchObject({ done: 119, percent: 49, total: 240, seq: 2, label: 'Screenshots' })
    // 239 of 240 is not 100%.
    jobs = step(jobs, { id: 'shots', op: 'set', done: 239 }, 9 * MIN)
    expect(jobs.shots?.percent).toBe(99)
  })

  test('a tick adds to the count and a count cannot pass its total', () => {
    let jobs = step({}, { id: 'a', op: 'start', total: 3 }, 0)
    jobs = step(jobs, { id: 'a', op: 'set', inc: 2 }, SEC)
    jobs = step(jobs, { id: 'a', op: 'set', inc: 2 }, 2 * SEC)
    expect(jobs.a).toMatchObject({ done: 3, percent: 100, status: 'running' })
  })

  test('a reported percentage stands alone, and a job can say nothing at all', () => {
    let jobs = step({}, { id: 'p', op: 'set', percent: 42.6 }, 0)
    expect(jobs.p).toMatchObject({ percent: 43, total: undefined })
    jobs = step(jobs, { id: 'q', op: 'set', step: 'warming up' }, 0)
    expect(jobs.q).toMatchObject({ percent: undefined, step: 'warming up', label: 'q' })
  })

  test('done and fail end the job and keep when it ended', () => {
    let jobs = step({}, { id: 'a', op: 'start', total: 10 }, 0)
    jobs = step(jobs, { id: 'a', op: 'done' }, 3 * MIN)
    expect(jobs.a).toMatchObject({ status: 'done', done: 10, percent: 100, endedAt: NOW + 3 * MIN })
    jobs = step(jobs, { id: 'b', op: 'start' }, 0)
    jobs = step(jobs, { id: 'b', op: 'fail', msg: 'exit 3' }, MIN)
    expect(jobs.b).toMatchObject({ status: 'failed', msg: 'exit 3' })
  })

  test('a report after the end revives the job, a start begins it over', () => {
    let jobs = step({}, { id: 'a', op: 'start', total: 10 }, 0)
    jobs = step(jobs, { id: 'a', op: 'set', done: 4 }, MIN)
    jobs = step(jobs, { id: 'a', op: 'fail' }, 2 * MIN)
    jobs = step(jobs, { id: 'a', op: 'set', done: 5 }, 3 * MIN)
    expect(jobs.a).toMatchObject({ status: 'running', done: 5 })
    expect(jobs.a?.endedAt).toBeUndefined()
    jobs = step(jobs, { id: 'a', op: 'start' }, 4 * MIN)
    expect(jobs.a).toMatchObject({ seq: 1, startedAt: NOW + 4 * MIN, total: undefined })
  })

  test('anything malformed is ignored', () => {
    const base = step({}, { id: 'a', op: 'start' }, 0)
    for (const bad of [null, 'text', 7, [], {}, { id: 'a' }, { op: 'set' }, { id: 'bad id!', op: 'set' }, { id: 'a', op: 'explode' }, { id: 'x'.repeat(60), op: 'set' }]) {
      expect(ingest(base, bad, NOW)).toBe(base)
    }
    const odd = step(base, { id: 'a', op: 'set', done: -4, total: 'lots', percent: 'high', step: 7, pid: 'me' }, SEC)
    expect(odd.a).toMatchObject({ status: 'running', percent: undefined, step: undefined, pid: undefined })
  })

  test('labels and steps are one short line', () => {
    const jobs = step({}, { id: 'a', op: 'set', label: 'x'.repeat(200), step: ' two\n lines ' }, 0)
    expect(jobs.a?.label).toHaveLength(40)
    expect(jobs.a?.step).toBe('two lines')
  })

  test('only twenty jobs are kept, the oldest finished first', () => {
    let jobs: Record<string, Job> = {}
    for (let i = 0; i < 22; i++) jobs = step(jobs, { id: `j${i}`, op: i < 3 ? 'done' : 'set', total: 5 }, i * SEC)
    expect(Object.keys(jobs)).toHaveLength(20)
    expect(jobs.j0).toBeUndefined()
    expect(jobs.j2).toBeDefined()
    expect(jobs.j21).toBeDefined()
  })
})

describe('silence', () => {
  const running = (at = 0) => step({}, { id: 'a', op: 'start', total: 100 }, at).a!

  test('what the job admitted to counts with the time since it reported', () => {
    const j = ingest({}, { id: 'a', op: 'set', idle: 30 }, NOW).a!
    expect(idleMs(j, NOW + 10 * SEC)).toBe(40 * SEC)
  })

  test('a job is stalled after the configured silence, not before', () => {
    const j = running()
    expect(isStalled(j, NOW + 119 * SEC, O)).toBe(false)
    expect(isStalled(j, NOW + 120 * SEC, O)).toBe(true)
    expect(isStalled(j, NOW + 60 * SEC, { ...O, stall_after: 30 })).toBe(true)
  })

  test('a job that moves slowly by nature gets more patience', () => {
    // One page every 4 minutes: two silent minutes is nothing, ten is not.
    let jobs = step({}, { id: 'a', op: 'start', total: 100 }, 0)
    for (let i = 1; i <= 4; i++) jobs = step(jobs, { id: 'a', op: 'set', done: i }, i * 4 * MIN)
    const j = jobs.a!
    expect(j.gapMs).toBe(4 * MIN)
    expect(isStalled(j, j.updatedAt + 10 * MIN, O)).toBe(false)
    expect(isStalled(j, j.updatedAt + 20 * MIN, O)).toBe(true)
  })

  test('a finished job is never stalled', () => {
    const j = step({}, { id: 'a', op: 'done' }, 0).a!
    expect(isStalled(j, NOW + 99 * MIN, O)).toBe(false)
  })

  test('a stall is announced once per stretch of silence', () => {
    const o = { ...O, stall_after: 30 }
    let jobs = step({}, { id: 'a', op: 'start', total: 100 }, 0)
    jobs = step(jobs, { id: 'a', op: 'set', done: 1 }, 10 * SEC)
    expect(noticeStalls(jobs, NOW + 40 * SEC, o).stalled).toHaveLength(0)
    const first = noticeStalls(jobs, NOW + 2 * MIN, o)
    expect(first.stalled.map(j => j.id)).toEqual(['a'])
    expect(noticeStalls(first.jobs, NOW + 3 * MIN, o).stalled).toHaveLength(0)
    // It moves, then goes quiet again: worth saying again.
    const moved = step(first.jobs, { id: 'a', op: 'set', done: 10 }, 3 * MIN)
    expect(noticeStalls(moved, NOW + 4 * MIN, o).stalled).toHaveLength(0)
    expect(noticeStalls(moved, NOW + 9 * MIN, o).stalled).toHaveLength(1)
  })

  test('a dead process ends a running job and nothing else', () => {
    const jobs = step({ ...step({}, { id: 'b', op: 'done' }, 0) }, { id: 'a', op: 'set', pid: 9 }, 0)
    const died = markDied(jobs, 'a', NOW + MIN)
    expect(died.a).toMatchObject({ status: 'died', endedAt: NOW + MIN })
    expect(markDied(jobs, 'b', NOW)).toBe(jobs)
    expect(markDied(jobs, 'nope', NOW)).toBe(jobs)
  })
})

describe('rows', () => {
  const counting = () => {
    let jobs = step({}, { id: 'shots', op: 'start', label: 'screenshots', total: 240 }, 0)
    jobs = step(jobs, { id: 'shots', op: 'set', done: 60, step: 'page-60.png' }, 10 * MIN)
    jobs = step(jobs, { id: 'shots', op: 'set', done: 118, step: 'page-118.png' }, 20 * MIN)
    return jobs.shots!
  }

  // A job that reports every ten seconds, then goes quiet at 118 of 240 after two minutes.
  const quick = () => {
    let jobs = step({}, { id: 'shots', op: 'start', label: 'screenshots', total: 240 }, 0)
    for (let i = 1; i <= 11; i++) jobs = step(jobs, { id: 'shots', op: 'set', done: i * 10 + (i === 11 ? 8 : 0), step: 'page-118.png' }, i * 10 * SEC)
    return jobs.shots!
  }

  test('a running job shows the count, percentage, time, estimate, rate and last output', () => {
    const j = counting()
    const text = rowText(jobRow(j, NOW + 20 * MIN, 140, O))
    expect(text).toContain('118/240 49%')
    expect(text).toContain('screenshots · 20m · ~21m left · 5.9/min · page-118.png')
    expect(text).toMatch(/^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] ━+─+ /)
    expect(jobRow(j, NOW + 20 * MIN, 140, O).tone).toBe('running')
  })

  test('the spinner turns once per report, so a frozen one is a frozen job', () => {
    const j = counting()
    const next = ingest({ [j.id]: j }, { id: 'shots', op: 'set', done: 119 }, NOW + 21 * MIN).shots!
    expect(jobRow(next, NOW, 140, O).glyph).not.toBe(jobRow(j, NOW, 140, O).glyph)
    // Redrawing without a report does not move it.
    expect(jobRow(j, NOW + 21 * MIN, 140, O).glyph).toBe(jobRow(j, NOW + 20 * MIN, 140, O).glyph)
  })

  test('silence turns the row yellow and says for how long', () => {
    const j = quick()
    expect(j.done).toBe(118)
    const row = jobRow(j, j.updatedAt + 5 * MIN, 140, O)
    expect(row.tone).toBe('stalled')
    expect(row.glyph).toBe('⚠')
    expect(rowText(row)).toContain('no progress for 5m00s')
    expect(rowText(row)).toContain('last: page-118.png')
    expect(rowText(row)).not.toContain('left')
  })

  test('a dead process is red and says where it stopped', () => {
    const j = markDied({ [counting().id]: counting() }, 'shots', NOW + 21 * MIN).shots!
    const row = jobRow(j, NOW + 99 * MIN, 140, O)
    expect(row.tone).toBe('failed')
    expect(rowText(row)).toContain('✕')
    expect(rowText(row)).toContain('died at 118/240, process gone')
    expect(rowText(row)).toContain('after 21m ·')
  })

  test('a failure carries the reason; a finished job says how long it took', () => {
    const failed = step({}, { id: 'a', op: 'fail', label: 'build', msg: 'exit 2', total: 10, done: 4 }, 0).a!
    expect(rowText(jobRow(failed, NOW, 140, O))).toContain('failed at 4/10: exit 2')
    const done = step(counting() ? { shots: counting() } : {}, { id: 'shots', op: 'done' }, 41 * MIN).shots!
    const text = rowText(jobRow(done, NOW + 99 * MIN, 140, O))
    expect(text).toContain('240/240 ✓')
    expect(text).toContain('done in 41m')
    expect(text).toMatch(/^✓ ━{24}/)
  })

  test('a job with nothing to measure slides a block along its bar', () => {
    const a = step({}, { id: 'a', op: 'set', label: 'indexing' }, 0).a!
    const b = ingest({ a }, { id: 'a', op: 'set' }, NOW + SEC).a!
    const ra = jobRow(a, NOW + SEC, 100, O)
    const rb = jobRow(b, NOW + SEC, 100, O)
    expect(ra.slide).toBeGreaterThanOrEqual(0)
    expect(rb.slide).not.toBe(ra.slide)
    expect(rowText(ra)).toContain('━━━')
    expect(rowText(ra)).toContain('indexing')
  })

  test('a narrow terminal drops words from the end and keeps the count', () => {
    const j = counting()
    const row = jobRow(j, NOW + 20 * MIN, 48, O)
    expect(row.head).toBe('118/240 49%')
    expect(row.cells).toBeGreaterThanOrEqual(10)
    expect(row.tail).toContain('screenshots')
    expect(row.tail).not.toContain('page-118')
  })

  test('the rate reads in minutes, or hours when slow, and waits for enough to go on', () => {
    expect(rate(counting(), NOW)).toBe('5.9/min')
    const slow = step({}, { id: 'a', op: 'start', total: 100 }, 0)
    const j = step(slow, { id: 'a', op: 'set', done: 4 }, 120 * MIN).a!
    expect(rate(j, NOW)).toBe('2/h')
    expect(rate(step({}, { id: 'a', op: 'set', done: 1, total: 9 }, MIN).a!, NOW)).toBeUndefined()
  })

  test('trouble is drawn first, the rest counted', () => {
    let jobs: Record<string, Job> = {}
    jobs = step(jobs, { id: 'ok1', op: 'set', percent: 10 }, 0)
    jobs = step(jobs, { id: 'done1', op: 'done' }, SEC)
    jobs = step(jobs, { id: 'bad', op: 'fail' }, 2 * SEC)
    jobs = step(jobs, { id: 'ok2', op: 'set', percent: 20 }, 3 * SEC)
    jobs = step(jobs, { id: 'ok3', op: 'set', percent: 30 }, 4 * SEC)
    const { shown, more } = visible(jobs, NOW + 5 * SEC, O)
    expect(shown.map(j => j.id)).toEqual(['bad', 'ok3', 'ok2'])
    expect(more).toBe(2)
  })

  test('a transition is reported once', () => {
    const before = step({}, { id: 'a', op: 'start' }, 0)
    const after = step(before, { id: 'a', op: 'done' }, SEC)
    expect(transitions(before, after).map(t => t.kind)).toEqual(['done'])
    expect(transitions(after, after)).toEqual([])
    const died = markDied(before, 'a', NOW)
    expect(transitions(before, died).map(t => t.kind)).toEqual(['failed'])
  })

  test('the status line for Claude names the verdict', () => {
    const j = counting()
    expect(jobLine(j, NOW + 20 * MIN, O)).toBe('shots "screenshots": running, 118/240 49%, elapsed 20m, about 21m left, last: page-118.png')
    const q = quick()
    expect(jobLine(q, q.updatedAt + 10 * MIN, O)).toContain('STALLED (no progress for 10m)')
    expect(jobLine(markDied({ shots: j }, 'shots', NOW).shots!, NOW, O)).toContain('DIED')
  })

  test('Claude is told how to wrap a long command', () => {
    const text = jobInstructions(STATUS, '/p/bin')
    expect(text).toContain('/p/bin/pbar run')
    expect(text).toContain(STATUS)
    expect(text).toContain('background')
  })
})

describe('options', () => {
  test('stall_after and wake_claude are clamped and checked', () => {
    expect(readOptions({ stall_after: 1 }).stall_after).toBe(10)
    expect(readOptions({ stall_after: 99999 }).stall_after).toBe(3600)
    expect(readOptions({ wake_claude: 'always' }).wake_claude).toBe('always')
    expect(readOptions({ wake_claude: 'sometimes' }).wake_claude).toBe('off')
    expect(readOptions(undefined)).toMatchObject({ watch_jobs: true, notify: true, wake_claude: 'off', stall_after: 120 })
  })
})

// The session around the plugin: a hub whose output the test writes, and a host that can answer `kill -0`.
const BAND = (bodyColumns: number) => ({
  plugin: 'progress-bar',
  component: 'AbovePrompt' as const,
  surface: 'terminal' as const,
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns, scroll: { offset: 0, bodyRows: 10 }, view: {} },
})

function session(on: On, lines: object[], alive = true) {
  const clock = mock.clock(on, { now: NOW })
  const calls = { spawned: [] as string[][], env: {} as Record<string, string | undefined>, notified: [] as string[], toasts: [] as string[], woke: [] as string[] }
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('classic.SessionStart', () => ({}))
  on('tool.register', () => ({ value: { tool: 'x' } }))
  on('session.id', () => ({ value: 'abcdef12-3456' }) as never)
  on('env.get', (_$, e) => ({ value: (e as never as { name: string }).name === 'PATH' ? '/usr/bin' : undefined }) as never)
  on('env.set', (_$, e) => {
    const { name, value } = e as never as { name: string; value?: string }
    calls.env[name] = value
    return { value: undefined } as never
  })
  on('process.spawn', async function* (_$, e) {
    calls.spawned.push([...e.argv])
    yield { stream: 'stdout' as const, text: JSON.stringify({ hub: 'ready' }) + '\n' }
    for (const line of lines) yield { stream: 'stdout' as const, text: JSON.stringify(line) + '\n' }
    // The hub runs for the life of the session.
    await new Promise(() => {})
    return { code: 0, signal: null }
  })
  on('process.run', () => ({ value: { exitCode: alive ? 0 : 1, stdout: '', stderr: alive ? '' : 'No such process', isStdoutTruncated: false, isStderrTruncated: false } }) as never)
  on('ui.toast', (_$, e) => {
    calls.toasts.push((e as never as { text: string }).text)
    return { value: undefined } as never
  })
  on('ui.notify', (_$, e) => {
    calls.notified.push((e as never as { text: string }).text)
    return { value: { isSent: true, channel: 'x' } } as never
  })
  on('prompt.submit', (_$, e) => {
    calls.woke.push(e.text)
    return { text: e.text }
  })
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>engine</Text>
  })
  return { clock, calls }
}

async function band($: Parameters<Parameters<typeof test>[1]>[0]) {
  const ui = await $.ui.mount(BAND(140))
  const text = (await ui.find({ type: 'Box' }))?.text
  return { ui, text }
}

const settle = async (clock: { advance: (ms: number) => Promise<void> }, ms = 2 * SEC) => clock.advance(ms)

test('the hub is started on the session socket and its path reaches every shell', async ($, on) => {
  const { calls } = session(on, [])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  const argv = calls.spawned[0]
  expect(argv?.[0]).toBe('python3')
  expect(argv?.[1]).toMatch(/\/bin\/pbar$/)
  expect(argv?.slice(2, 4)).toEqual(['hub', '--sock'])
  expect(argv?.[4]).toBe('/tmp/pbar-abcdef12.sock')
  expect(calls.env.PROGRESS_BAR_SOCK).toBe('/tmp/pbar-abcdef12.sock')
  expect(calls.env.PATH).toMatch(/\/bin:\/usr\/bin$/)
})

test('reports that arrive on the socket are drawn under Claude\'s own bar', async ($, on) => {
  const { clock } = session(on, [
    { v: 1, id: 'shots', op: 'start', label: 'screenshots', total: 240, pid: 77 },
    { v: 1, id: 'shots', op: 'set', done: 118, step: 'page-118.png', pid: 77 },
  ])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock)
  await $.tool.call({ tool: 'mcp__progress-bar__set_progress', percent: 30, step: 'Writing the page tests' } as never)
  const { ui, text } = await band($)
  expect(text).toContain('30%')
  expect(text).toContain('Writing the page tests')
  expect(text).toContain('118/240 49%')
  expect(text).toContain('screenshots')
  expect(text?.indexOf('30%')).toBeLessThan(text?.indexOf('118/240') ?? 0)
  await ui.unmount()
})

test('a silent job warns on screen, with a notification, and says so once', async ($, on) => {
  const { clock, calls } = session(on, [{ v: 1, id: 'shots', op: 'start', label: 'screenshots', total: 240 }])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock)
  await clock.advance(3 * MIN)
  const { ui, text } = await band($)
  expect(text).toContain('⚠')
  expect(text).toContain('no progress for')
  expect(calls.notified.filter(t => t.includes('no progress'))).toHaveLength(1)
  expect(calls.toasts.filter(t => t.includes('no progress'))).toHaveLength(1)
  expect(calls.woke).toEqual([])
  await ui.unmount()
})

test('a job whose process is gone is called dead within seconds', async ($, on) => {
  const { clock, calls } = session(on, [{ v: 1, id: 'shots', op: 'start', label: 'screenshots', total: 240, done: 12, pid: 77 }], false)
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock, 6 * SEC)
  const { ui, text } = await band($)
  expect(text).toContain('died at 12/240')
  expect(calls.notified.some(t => t.includes('died'))).toBe(true)
  await ui.unmount()
})

test('a job with no process to check is never called dead', async ($, on) => {
  const { clock } = session(on, [{ v: 1, id: 'a', op: 'start', label: 'manual', total: 5 }], false)
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock, 20 * SEC)
  const { ui, text } = await band($)
  expect(text).toContain('manual')
  expect(text).not.toContain('died')
  await ui.unmount()
})

test('finishing is announced, and the row can be dismissed', async ($, on) => {
  const { clock, calls } = session(on, [
    { v: 1, id: 'a', op: 'start', label: 'build', total: 4 },
    { v: 1, id: 'a', op: 'done' },
  ])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock)
  expect(calls.notified).toEqual(['✓ build finished in 0s'])
  const a = await band($)
  expect(a.text).toContain('4/4 ✓')
  await a.ui.press({ plugin: 'progress-bar', key: 'x-a' } as never)
  await a.ui.unmount()
  const b = await band($)
  expect(b.text).toBeUndefined()
  await b.ui.unmount()
})

test('the next prompt clears finished jobs and keeps the ones still going', async ($, on) => {
  const { clock } = session(on, [
    { v: 1, id: 'a', op: 'start', label: 'finished one', total: 4 },
    { v: 1, id: 'a', op: 'done' },
    { v: 1, id: 'b', op: 'start', label: 'still going', total: 4 },
  ])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock)
  await $.prompt.submit({ text: 'next' } as never)
  const { ui, text } = await band($)
  expect(text).not.toContain('finished one')
  expect(text).toContain('still going')
  await ui.unmount()
})

test('Claude is woken only when the person asked for it', { options: { wake_claude: 'on-failure' } }, async ($, on) => {
  const { clock, calls } = session(on, [
    { v: 1, id: 'a', op: 'start', label: 'fine', total: 4 },
    { v: 1, id: 'a', op: 'done' },
    { v: 1, id: 'b', op: 'start', label: 'broken', total: 4 },
    { v: 1, id: 'b', op: 'fail', msg: 'exit 2' },
  ])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock)
  expect(calls.woke).toHaveLength(1)
  expect(calls.woke[0]).toContain('broken failed')
  expect(calls.woke[0]).toContain(STATUS)
})

test('always wakes Claude for a finished job too', { options: { wake_claude: 'always' } }, async ($, on) => {
  const { clock, calls } = session(on, [
    { v: 1, id: 'a', op: 'start', label: 'fine', total: 4 },
    { v: 1, id: 'a', op: 'done' },
  ])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock)
  expect(calls.woke).toHaveLength(1)
  expect(calls.woke[0]).toContain('fine finished')
})

test('notifications can be switched off', { options: { notify: false } }, async ($, on) => {
  const { clock, calls } = session(on, [
    { v: 1, id: 'a', op: 'start', label: 'fine', total: 4 },
    { v: 1, id: 'a', op: 'done' },
  ])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  await settle(clock)
  expect(calls.notified).toEqual([])
  expect(calls.toasts).toHaveLength(1)
})

test('Claude can read the jobs without a shell', async ($, on) => {
  const { clock } = session(on, [{ v: 1, id: 'shots', op: 'start', label: 'screenshots', total: 240, done: 118 }])
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  expect((await $.tool.call({ tool: STATUS } as never)).result).toBe('No jobs have been reported.')
  await settle(clock)
  const r = await $.tool.call({ tool: STATUS } as never)
  expect(r.result).toContain('shots "screenshots": running, 118/240 49%')
  const { decision } = await $.tool.check({ tool: STATUS, input: {} })
  expect(decision).toBe('allow')
})

test('Claude is told about pbar, unless jobs are switched off', async ($, on) => {
  session(on, [])
  on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' }] }))
  const { sections } = await $.prompt.compose({ model: 'm', promptModel: 'm', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [] } as never)
  expect(sections.map(s => s.id)).toEqual(['intro', 'progress-bar:report', 'progress-bar:jobs'])
})

test('with jobs switched off nothing is started and nothing is drawn', { options: { watch_jobs: false } }, async ($, on) => {
  const { calls } = session(on, [{ v: 1, id: 'a', op: 'start', label: 'x', total: 1 }])
  on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' }] }))
  await $.session.start({ cwd: '/', source: 'startup' } as never)
  expect(calls.spawned).toEqual([])
  expect(calls.env.PROGRESS_BAR_SOCK).toBeUndefined()
  const { sections } = await $.prompt.compose({ model: 'm', promptModel: 'm', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [] } as never)
  expect(sections.map(s => s.id)).toEqual(['intro', 'progress-bar:report'])
})
