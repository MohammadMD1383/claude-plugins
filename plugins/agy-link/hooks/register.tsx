import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, Timer } from 'claude-code'

import type { AgyQuota } from '../types'
import { countdown, current, filled, isGateway, level, parseUsage, readOptions, summary, usageUrl } from './quota'
import type { Level, Options } from './quota'

const quota = atom({ plugin: 'agy-link', key: 'quota' } as const, null)
const problem = atom({ plugin: 'agy-link', key: 'problem' } as const, null)
const active = atom({ plugin: 'agy-link', key: 'isActive' } as const, false)
const now = atom({ plugin: 'agy-link', key: 'now' } as const, 0)

const COLOR: Record<Level, string | undefined> = { ok: undefined, warn: 'yellow', critical: 'red' }
const LABEL = 'AGY'
const SEPARATOR = '  ·  '

async function tick($: EngineInterface) {
  const t = await $.clock.now()
  await update($, now, () => t)
}

async function refreshActive($: EngineInterface, o: Options): Promise<boolean> {
  const isActive = isGateway(await $.env.get('ANTHROPIC_BASE_URL'), o)
  await update($, active, () => isActive)
  return isActive
}

// Reads the gateway's usage endpoint into the quota atom. Never throws: a
// gateway that is down or refuses the key leaves the last reading in place
// and records why.
async function poll($: EngineInterface, o: Options): Promise<void> {
  if (o.show_when === 'gateway' && !(await refreshActive($, o))) return
  try {
    const headers: Record<string, string> = o.gateway_key ? { authorization: `Bearer ${o.gateway_key}` } : {}
    const res = await $.http.fetch(usageUrl(o), { headers })
    if (!res.ok) {
      const why = res.status === 401 ? 'gateway key rejected' : res.status === 403 ? 'key may not read usage' : `HTTP ${res.status}`
      await update($, problem, () => why)
      return
    }
    let body: unknown
    try {
      body = JSON.parse(res.text)
    } catch {
      await update($, problem, () => 'usage endpoint did not return JSON')
      return
    }
    const parsed = parseUsage(body, o.family)
    if (!parsed) {
      await update($, problem, () => 'no Antigravity quota in the response')
      return
    }
    await update($, quota, () => parsed)
    await update($, problem, () => null)
  } catch {
    await update($, problem, () => 'gateway unreachable')
  }
}

// A timer can fire as the mod unloads, when its state writes are refused; there is nobody to tell.
const ignore = () => {}

let ticker: Timer | undefined
let poller: Timer | undefined

export const register: Register = (on, options) => {
  const o = readOptions(options as Parameters<typeof readOptions>[0])

  on('session.start', async ($, e, next) => {
    const result = await next(e)

    if (o.activate) {
      // Without a gateway credential Claude Code would keep sending the claude.ai
      // login to whatever ANTHROPIC_BASE_URL names, so refuse to activate.
      if (o.gateway_key) {
        await $.env.set('ANTHROPIC_BASE_URL', o.gateway_url.replace(/\/+$/, ''))
        await $.env.set('ANTHROPIC_AUTH_TOKEN', o.gateway_key)
      } else {
        $.ui.toast('agy-link: set the gateway API key to point Claude Code at the gateway.')
      }
    }

    await $.command.register({ name: 'agy', description: 'Show the gateway’s Antigravity quota' })
    await tick($)
    ticker?.cancel()
    ticker = $.clock.every(30_000, () => void tick($).catch(ignore))
    poller?.cancel()
    poller = $.clock.every(o.poll_seconds * 1000, () => void poll($, o).catch(ignore))
    void poll($, o).catch(ignore)
    return result
  })

  on('command.run', { command: 'agy' }, async $ => {
    await poll($, { ...o, show_when: 'always' })
    const q = await read($, quota)
    if (q) return { text: summary(q, await $.clock.now()) }
    return { text: `No Antigravity quota yet: ${(await read($, problem)) ?? 'waiting for the first reading'}.` }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // Another band (usage-bar) may already draw here: stack under it, never replace it.
    const below = await next(e)
    if (e.props.hasSurvey) return below

    const isActive = await read($, active)
    if (o.show_when === 'gateway' && !isActive) return below

    const q: AgyQuota | null = await read($, quota)
    const t = await read($, now)
    const { Box, Text } = $.ui.resolve(e)

    let mine
    if (!q) {
      const why = await read($, problem)
      if (!why) return below
      mine = (
        <Box flexDirection="row" paddingLeft={2}>
          <Text dimColor>{LABEL} · {why}</Text>
        </Box>
      )
    } else {
      const windows = q.windows.map(w => current(w, t))
      // Shed the meters first, then the countdowns, when the line would not fit.
      const text = (meters: boolean, resets: boolean) =>
        `${LABEL} ` +
        windows
          .map(w => {
            const reset = resets && o.show_reset ? countdown(w.resetsAt, t) : undefined
            return `${w.label} ${meters ? '━'.repeat(7) + ' ' : ''}${Math.round(w.percentUsed)}%${reset ? ` ↻ ${reset}` : ''}`
          })
          .join(SEPARATOR)
      const room = e.props.bodyColumns - 4
      const meters = text(true, true).length <= room
      const resets = meters || text(false, true).length <= room

      const stale = await read($, problem)
      const children = []
      for (const [i, w] of windows.entries()) {
        if (i > 0) children.push(<Text dimColor>{SEPARATOR}</Text>)
        const color = COLOR[level(w.percentUsed, o)]
        const n = filled(w.percentUsed)
        const reset = resets && o.show_reset ? countdown(w.resetsAt, t) : undefined
        children.push(
          <Box key={w.id} flexDirection="row" flexShrink={0}>
            <Text dimColor>{w.label} </Text>
            {meters ? <Text color={color}>{'━'.repeat(n)}</Text> : null}
            {meters ? <Text dimColor>{'─'.repeat(6 - n)} </Text> : null}
            <Text color={color} bold={level(w.percentUsed, o) !== 'ok'}>{Math.round(w.percentUsed)}%</Text>
            {reset ? <Text dimColor> ↻ {reset}</Text> : null}
          </Box>,
        )
      }
      mine = (
        <Box flexDirection="row" flexWrap="wrap" paddingLeft={2}>
          <Text dimColor>{LABEL} </Text>
          {children}
          {stale ? <Text dimColor> ({stale})</Text> : null}
        </Box>
      )
    }

    if (!below) return mine
    return (
      <Box flexDirection="column">
        {below}
        {mine}
      </Box>
    )
  })
}
