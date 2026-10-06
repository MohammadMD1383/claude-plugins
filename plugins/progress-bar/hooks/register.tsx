import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, Timer } from 'claude-code'

import { advance, describe, instructions, layout, readOptions, toPercent, toStep } from './format'

const progress = atom({ plugin: 'progress-bar', key: 'progress' } as const, null)
const isHidden = atom({ plugin: 'progress-bar', key: 'isHidden' } as const, false)
const now = atom({ plugin: 'progress-bar', key: 'now' } as const, 0)

const NAME = 'set_progress'
const TOOL = `mcp__progress-bar__${NAME}`

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

const TICK_MS = 5_000

let ticker: Timer | undefined
// Whether a run is under way, so the clock redraws only while there is something to count.
let isActive = false

async function declareTool($: EngineInterface) {
  try {
    await $.tool.register(SPEC)
  } catch {
    // The session is not bound yet; session.start tries again.
  }
}

export const register: Register = (on, options) => {
  const o = readOptions(options as Parameters<typeof readOptions>[0])

  on('session.start', async ($, e, next) => {
    await declareTool($)
    ticker?.cancel()
    ticker = $.clock.every(TICK_MS, async () => {
      if (!isActive) return
      const t = await $.clock.now()
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

  // The tool is for every turn, so keep its schema in the list rather than
  // behind ToolSearch, and never ask the person to approve a progress update.
  on('tool.describe', { tool: TOOL }, async ($, e, next) => ({ ...(await next(e)), isDeferred: false }))
  on('tool.check', { tool: TOOL }, () => ({ decision: 'allow' as const }))

  // A finished run is the last task's; the next prompt starts with a clean bar.
  on('prompt.submit', async ($, e, next) => {
    await update($, progress, p => (p !== null && p.percent >= 100 ? null : p))
    return next(e)
  })

  if (o.auto_report) {
    on('prompt.compose', async ($, e, next) => {
      const composed = await next(e)
      // Nothing draws under -p or the SDK, so nobody would see the bar.
      if (e.surfaces.length === 0) return composed
      return {
        ...composed,
        sections: [...composed.sections, { id: 'progress-bar:report', text: instructions(TOOL), scope: 'session' as const }],
      }
    })
  }

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const p = await read($, progress)
    if (e.props.hasSurvey || p === null || (await read($, isHidden))) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    const view = describe(p, await read($, now), e.props.isWorking, o)
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
  })
}
