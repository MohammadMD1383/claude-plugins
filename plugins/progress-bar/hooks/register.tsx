import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, Timer } from 'claude-code'

import type { Job } from '../types'
import type { Options } from './format'

import { advance, describe, duration, instructions, layout, readOptions, toPercent, toStep } from './format'
import { ingest, jobInstructions, jobLine, jobRow, markDied, noticeStalls, transitions, visible } from './jobs'
import type { Finished } from './jobs'

const JOBS = { plugin: 'progress-bar', key: 'jobs' } as const
const jobs = atom(JOBS, {})
const progress = atom({ plugin: 'progress-bar', key: 'progress' } as const, null)
const isHidden = atom({ plugin: 'progress-bar', key: 'isHidden' } as const, false)
const now = atom({ plugin: 'progress-bar', key: 'now' } as const, 0)

const NAME = 'set_progress'
const TOOL = `mcp__progress-bar__${NAME}`
const STATUS_NAME = 'job_status'
const STATUS_TOOL = `mcp__progress-bar__${STATUS_NAME}`

const STATUS_SPEC = {
  name: STATUS_NAME,
  description:
    'Lists the background jobs reported through pbar (the person sees them as bars above their prompt): for each, whether it is running, stalled, done, failed or died, how far it got, and how long it has run. Cheap and instant; call it instead of reading logs or polling a command.',
  inputSchema: { type: 'object', properties: {} },
}

const SPEC = {
  name: NAME,
  description:
    'Sets the progress bar the person sees above their prompt: how much of the whole task is finished, as a percentage, and what you are doing now. Call it on any task longer than a few tool calls: once after sizing up the work, then at each milestone, and with 100 as the last action when everything is finished and verified.',
  inputSchema: {
    type: 'object',
    properties: {
      percent: {
        type: 'number',
        minimum: 0,
        maximum: 100,
        description: 'Share of the whole task that is finished, 0-100, counting work still ahead (tests, verification).',
      },
      step: {
        type: 'string',
        description: 'A few words on what you are doing now, e.g. "Updating the auth tests".',
      },
    },
    required: ['percent'],
  },
}

const TICK_MS = 2_000

const COLORS = { running: 'cyan', stalled: 'yellow', failed: 'red', done: 'green' } as const
const SOCKET_WAIT_NOTE = 'progress-bar: the job hub did not start (is python3 installed?); pbar cannot report.'

let ticker: Timer | undefined
// Ticks since the last check that running jobs' processes are still there.
let beats = 0
// Whether the hub has been started for this load of the module.
let isHubStarted = false
// Whether a run is under way, so the clock redraws only while there is something to count.
let isActive = false

async function declareTool($: EngineInterface) {
  try {
    await $.tool.register(SPEC)
    await $.tool.register(STATUS_SPEC)
  } catch {
    // The session is not bound yet; session.start tries again.
  }
}

async function announce($: EngineInterface, o: Options, events: Finished[], stalled: { label: string; idle: number }[]) {
  for (const { job, kind } of events) {
    const took = duration((job.endedAt ?? job.updatedAt) - job.startedAt)
    const at = job.total !== undefined ? ` at ${job.done}/${job.total}` : ''
    const line =
      kind === 'done'
        ? `✓ ${job.label} finished in ${took}`
        : job.status === 'died'
          ? `✕ ${job.label} died${at} after ${took}: its process is gone`
          : `✕ ${job.label} failed${at} after ${took}${job.msg ? ` (${job.msg})` : ''}`
    $.ui.toast(line)
    await tell($, o, line, kind === 'done' ? 'always' : 'on-failure')
  }
  for (const s of stalled) {
    const line = `⚠ ${s.label} has made no progress for ${duration(s.idle)}`
    $.ui.toast(line)
    await tell($, o, line, 'on-failure')
  }
}

// The person may be away: a native notification, and, only if they chose it, a turn for Claude.
async function tell($: EngineInterface, o: Options, line: string, wakeFor: 'on-failure' | 'always') {
  if (o.notify) {
    try {
      await $.ui.notify(line, { title: 'progress-bar' })
    } catch {
      // No terminal to notify through (-p, the SDK).
    }
  }
  const wake = o.wake_claude === 'always' || (o.wake_claude === 'on-failure' && wakeFor === 'on-failure')
  if (wake) {
    try {
      await $.prompt.submit({ text: `[progress-bar] ${line}. Check on it with ${STATUS_TOOL} and carry on.` })
    } catch {
      // The session is going away.
    }
  }
}

async function receive($: EngineInterface, o: Options, line: string) {
  let message: unknown
  try {
    message = JSON.parse(line)
  } catch {
    return
  }
  const t = await $.clock.now()
  let before: Record<string, Job> = {}
  let after: Record<string, Job> = {}
  await update($, jobs, prev => {
    before = prev ?? {}
    after = ingest(before, message, t)
    return after
  })
  await update($, now, () => t)
  if (after !== before) await announce($, o, transitions(before, after), [])
}

// A socket the hub listens on for the life of the session: scripts, `pbar run`,
// or a plain `nc -U` report to it, and the mod reads what arrives. No port, no HTTP, no model call.
async function startHub($: EngineInterface, o: Options) {
  if (isHubStarted || !o.watch_jobs) return
  isHubStarted = true
  const id = await $.session.id()
  const tmp = ((await $.env.get('TMPDIR')) ?? '/tmp').replace(/\/$/, '')
  const sock = `${tmp}/pbar-${id.slice(0, 8)}.sock`
  const bin = `${$.plugin.root}/bin`
  await $.env.set('PROGRESS_BAR_SOCK', sock)
  await $.env.set('PATH', `${bin}:${(await $.env.get('PATH')) ?? ''}`)

  void (async () => {
    let buffered = ''
    try {
      for await (const piece of $.process.spawn({ argv: ['python3', `${bin}/pbar`, 'hub', '--sock', sock] })) {
        if (piece.stream !== 'stdout') continue
        buffered += piece.text
        const lines = buffered.split('\n')
        buffered = lines.pop() ?? ''
        for (const line of lines) if (line.trim() !== '') await receive($, o, line)
      }
      $.ui.log(SOCKET_WAIT_NOTE, { to: 'debug' })
    } catch {
      $.ui.toast(SOCKET_WAIT_NOTE)
    }
    isHubStarted = false
  })()
}

async function checkJobs($: EngineInterface, o: Options, t: number) {
  const { value: current = {} } = await $.state.get(JOBS)
  const running = Object.values(current).filter(j => j.status === 'running')
  if (running.length === 0) return false

  // Is the process of each job that names one still there? A dead one will never say so.
  beats += 1
  if (beats % 2 === 0) {
    for (const j of running) {
      if (j.pid === undefined) continue
      try {
        const probe = await $.process.run(['kill', '-0', String(j.pid)])
        if (probe.exitCode !== 0 && !/permitted/i.test(probe.stderr)) {
          let before: Record<string, Job> = {}
          let after: Record<string, Job> = {}
          await update($, jobs, prev => {
            before = prev ?? {}
            after = markDied(before, j.id, t)
            return after
          })
          await announce($, o, transitions(before, after), [])
        }
      } catch {
        // Cannot tell; say nothing rather than call a live job dead.
      }
    }
  }

  const { value: latest = {} } = await $.state.get(JOBS)
  const noticed = noticeStalls(latest, t, o)
  if (noticed.stalled.length > 0) {
    await update($, jobs, () => noticed.jobs)
    await announce($, o, [], noticed.stalled.map(j => ({ label: j.label, idle: t - j.updatedAt + j.idleSec * 1000 })))
  }
  return true
}


export const register: Register = (on, options) => {
  const o = readOptions(options as Parameters<typeof readOptions>[0])

  on('session.start', async ($, e, next) => {
    await declareTool($)
    try {
      await startHub($, o)
    } catch {
      // Jobs are an extra: Claude's own bar and tool must load without them.
      isHubStarted = false
    }
    ticker?.cancel()
    ticker = $.clock.every(TICK_MS, async () => {
      const t = await $.clock.now()
      const hasJobs = o.watch_jobs && (await checkJobs($, o, t))
      if (!isActive && !hasJobs) return
      await update($, now, () => t)
    })
    return next(e)
  })

  // /clear and /resume go on under a new session with no session.start: its
  // state is empty again, and the tool must still be there.
  on('classic.SessionStart', async ($, e, next) => {
    const result = await next(e)
    if (e.source === 'clear' || e.source === 'resume') {
      isActive = false
      await declareTool($)
    }
    return result
  })

  on('tool.call', { tool: TOOL }, async ($, e) => {
    if (e.agentId !== undefined) {
      return { deny: 'Only the main agent reports progress; the bar shows the whole task.' }
    }
    const percent = toPercent(e.percent)
    if (percent === undefined) return { deny: 'percent must be a number from 0 to 100.' }

    const t = await $.clock.now()
    const step = toStep(e.step)
    await update($, progress, prev => advance(prev, { percent, step }, t))
    await update($, now, () => t)
    await update($, isHidden, () => false)
    isActive = percent < 100

    const text = percent >= 100 ? 'Progress set to 100% (done).' : `Progress set to ${percent}%.`
    return { result: text }
  })

  on('tool.call', { tool: STATUS_TOOL }, async $ => {
    const { value: current = {} } = await $.state.get(JOBS)
    const t = await $.clock.now()
    const all = Object.values(current).sort((a, b) => b.updatedAt - a.updatedAt)
    return { result: all.length === 0 ? 'No jobs have been reported.' : all.map(j => jobLine(j, t, o)).join('\n') }
  })

  // The tools are for every turn, so keep their schemas in the list rather than
  // behind ToolSearch, and never ask the person to approve a progress update.
  for (const tool of [TOOL, STATUS_TOOL]) {
    on('tool.describe', { tool }, async ($, e, next) => ({ ...(await next(e)), isDeferred: false }))
    on('tool.check', { tool }, () => ({ decision: 'allow' as const }))
  }

  // A finished run is the last task's; the next prompt starts with a clean bar.
  on('prompt.submit', async ($, e, next) => {
    await update($, progress, p => (p !== null && p.percent >= 100 ? null : p))
    await update($, jobs, js => {
      const all = js ?? {}
      return Object.fromEntries(Object.entries(all).filter(([, j]) => j.status !== 'done'))
    })
    return next(e)
  })

  if (o.auto_report) {
    on('prompt.compose', async ($, e, next) => {
      const composed = await next(e)
      // Nothing draws under -p or the SDK, so nobody would see the bar.
      if (e.surfaces.length === 0) return composed
      const added = [{ id: 'progress-bar:report', text: instructions(TOOL), scope: 'session' as const }]
      if (o.watch_jobs) {
        added.push({ id: 'progress-bar:jobs', text: jobInstructions(STATUS_TOOL, `${$.plugin.root}/bin`), scope: 'session' as const })
      }
      return { ...composed, sections: [...composed.sections, ...added] }
    })
  }

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const p = await read($, progress)
    const js = o.watch_jobs ? await read($, jobs) : {}
    const t = await read($, now)
    const { shown, more } = visible(js ?? {}, t, o)
    const hasClaudeRow = p !== null && !(await read($, isHidden))
    if (e.props.hasSurvey || (!hasClaudeRow && shown.length === 0)) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)

    const claudeRow = () => {
      const view = describe(p!, t, e.props.isWorking, o)
      const l = layout(view, e.props.bodyColumns, o)
      const color = view.phase === 'done' ? 'green' : view.phase === 'running' ? 'cyan' : undefined
      const isDim = view.phase === 'paused'
      return (
        <Box flexDirection="row" paddingLeft={2}>
          <Text color={color} dimColor={isDim}>
            {'━'.repeat(l.filled)}
          </Text>
          <Text dimColor>{'─'.repeat(l.cells - l.filled)} </Text>
          <Text color={color} dimColor={isDim} bold>
            {l.head}
          </Text>
          {l.tail ? <Text dimColor>{`  ${l.tail}`}</Text> : null}
          {view.phase === 'running' ? null : (
            <Button key="hide" label=" ✕" onPress={() => update($, isHidden, () => true)} />
          )}
        </Box>
      )
    }

    // Claude's own bar alone is the tree it always was.
    if (shown.length === 0) return claudeRow()

    const jobRows = shown.map(j => {
      const r = jobRow(j, t, e.props.bodyColumns, o)
      const color = COLORS[r.tone]
      const isCalm = r.tone === 'running' || r.tone === 'done'
      const lead = r.slide >= 0 ? r.slide : r.filled
      const block = r.slide >= 0 ? 3 : 0
      return (
        <Box key={`job-${j.id}`} flexDirection="row" paddingLeft={2}>
          <Text color={color}>{`${r.glyph} `}</Text>
          {r.slide >= 0 ? <Text dimColor>{'─'.repeat(lead)}</Text> : null}
          <Text color={color}>{'━'.repeat(r.slide >= 0 ? block : lead)}</Text>
          <Text dimColor>{'─'.repeat(Math.max(0, r.cells - lead - block))} </Text>
          <Text color={color} bold>
            {r.head}
          </Text>
          {r.tail ? (
            <Text color={isCalm ? undefined : color} dimColor={isCalm}>{`  ${r.tail}`}</Text>
          ) : null}
          {r.tone === 'running' ? null : (
            <Button
              key={`x-${j.id}`}
              label=" ✕"
              onPress={() => update($, jobs, prev => Object.fromEntries(Object.entries(prev ?? {}).filter(([id]) => id !== j.id)))}
            />
          )}
        </Box>
      )
    })

    return (
      <Box flexDirection="column">
        {hasClaudeRow ? claudeRow() : null}
        {jobRows}
        {more > 0 ? <Text dimColor>{`  +${more} more job${more === 1 ? '' : 's'}`}</Text> : null}
      </Box>
    )
  })
}
