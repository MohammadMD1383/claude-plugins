import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, SessionContextUsage, SessionRateLimit } from 'claude-code'

import type { UsageBarSnapshot } from '../types'
import { contextTokens, fit, filled, lineText, METER_CELLS, readOptions, segments, SEPARATOR, withAgent } from './format'
import type { Level, Segment } from './format'

const snapshot = atom({ plugin: 'usage-bar', key: 'snapshot' } as const, null)
const agents = atom({ plugin: 'usage-bar', key: 'agents' } as const, {})
const now = atom({ plugin: 'usage-bar', key: 'now' } as const, 0)

const COLOR: Record<Level, string | undefined> = { ok: undefined, warn: 'yellow', critical: 'red' }

function toSnapshot(
  context: SessionContextUsage,
  rateLimits: readonly SessionRateLimit[],
  previous: UsageBarSnapshot | null,
): UsageBarSnapshot {
  return {
    tokens: context.tokens,
    window: context.window,
    percent: context.percent,
    limits: rateLimits.map(l => ({ kind: l.kind, percentUsed: l.percentUsed, resetsAt: l.resetsAt })),
    model: previous?.model,
  }
}

async function tick($: EngineInterface) {
  const t = await $.clock.now()
  await update($, now, () => t)
}

export const register: Register = (on, options) => {
  const o = readOptions(options as Parameters<typeof readOptions>[0])

  on('session.start', async ($, e, next) => {
    const result = await next(e)
    try {
      const usage = await $.session.usage()
      await update($, snapshot, prev => toSnapshot(usage.context, usage.rateLimits, prev))
    } catch {
      // No figures yet; the first session.measure fills them in.
    }
    await tick($)
    // Countdowns move on their own, so redraw them every half minute.
    $.clock.every(30_000, () => void tick($))
    return result
  })

  on('session.measure', async ($, e, next) => {
    await update($, snapshot, prev => toSnapshot(e.context, e.rateLimits, prev))
    await tick($)
    return next(e)
  })

  // session.measure comes once a turn ends; each model request inside the turn
  // reports its own usage, so the context figures move step by step.
  on('turn.step', async function* ($, e, next) {
    const result = yield* next(e)
    if (result.usage) {
      const used = contextTokens(result.usage)
      const { agentId } = e
      if (agentId !== undefined) {
        await update($, agents, all => withAgent(all, agentId, { tokens: used, model: e.model }))
      } else {
        await update($, snapshot, s =>
          s && { ...s, tokens: used, percent: s.window > 0 ? Math.round((used / s.window) * 100) : s.percent, model: e.model },
        )
      }
    }
    return result
  })

  if (o.placement === 'footer') {
    on('ui.render', { component: 'PromptHint' }, async ($, e, next) => {
      const segs = segments(await read($, snapshot), await read($, now), o)
      if (segs.length === 0) return next(e)
      const text = lineText(segs, { meters: false, resets: true }).replaceAll(SEPARATOR, ' · ')
      const tail = e.props.tail ? `${e.props.tail} · ${text}` : text
      return next({ ...e, props: { ...e.props, tail } })
    })
    return
  }

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const { agentId } = e.props.view
    const agent = agentId !== undefined ? (await read($, agents))[agentId] : undefined
    const segs = segments(await read($, snapshot), await read($, now), o, { agentId, agent })
    if (segs.length === 0) return next(e)

    const { Box, Text } = $.ui.resolve(e)
    const density = fit(segs, e.props.bodyColumns, o)

    const draw = (seg: Segment) => {
      const color = COLOR[seg.level]
      const n = filled(seg.pct)
      const meter = density.meters && seg.pct !== undefined
      return (
        <Box key={seg.id} flexDirection="row" flexShrink={0}>
          <Text dimColor>{seg.label} </Text>
          {meter ? <Text color={color}>{'━'.repeat(n)}</Text> : null}
          {meter ? <Text dimColor>{'─'.repeat(METER_CELLS - n)} </Text> : null}
          <Text color={color} bold={seg.level !== 'ok'}>{seg.value}</Text>
          {density.resets && seg.reset ? <Text dimColor> ↻ {seg.reset}</Text> : null}
        </Box>
      )
    }

    const children = []
    for (const [i, seg] of segs.entries()) {
      if (i > 0) children.push(<Text dimColor>{SEPARATOR}</Text>)
      children.push(draw(seg))
    }
    return (
      <Box flexDirection="row" flexWrap="wrap" paddingLeft={2}>
        {children}
      </Box>
    )
  })
}
