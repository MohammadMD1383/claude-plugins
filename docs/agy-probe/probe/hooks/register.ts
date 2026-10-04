import type { Register } from 'claude-code'

const rec = async ($: any, kind: string, data: unknown) => {
  try { await $.fs.write(`/tmp/probe/${kind.replace(/[^a-z.]/gi, '_')}.json`, JSON.stringify(data)) } catch (e) { $.ui.log(`probe write failed: ${String(e)}`, { to: 'debug' }) }
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    const r = await next(e)
    const base = await $.env.get('PROBE_SET_BASE_URL')
    if (base) {
      await $.env.set('ANTHROPIC_BASE_URL', base)
      await rec($, 'env.set base url', await $.env.get('ANTHROPIC_BASE_URL'))
    }
    const url = await $.env.get('PROBE_FETCH')
    if (url) {
      try {
        const res = await $.http.fetch(url)
        await rec($, 'http.fetch', { status: res.status, ok: res.ok, len: res.text.length, head: res.text.slice(0, 80) })
      } catch (err) { await rec($, 'http.fetch', { error: String(err) }) }
    }
    await rec($, 'session.start', { base: await $.env.get('ANTHROPIC_BASE_URL') })
    return r
  })
  on('session.measure', async ($, e, next) => {
    await rec($, 'session.measure rateLimits', e.rateLimits)
    return next(e)
  })
  on('turn.step', async function* ($, e, next) {
    await rec($, 'turn.step model', e.model)
    if (await $.env.get('PROBE_PROVIDER')) {
      // answer the step ourselves: nothing beneath runs, so the engine makes no API request
      yield { kind: 'text', index: 0, text: 'ANSWER-FROM-MOD' }
      yield { kind: 'stop', stopReason: 'end_turn', usage: null }
      return { turnId: e.turnId, index: e.index, answer: 'ANSWER-FROM-MOD', toolUses: [], stopReason: 'end_turn', usage: null }
    }
    const r = yield* next(e)
    await rec($, 'turn.step result', { answer: r.answer, usage: r.usage })
    return r
  })
}
