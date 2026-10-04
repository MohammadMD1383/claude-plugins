import http from 'node:http'
import fs from 'node:fs'
const port = Number(process.env.PORT || 8799)
const log = process.env.LOG || './mock-gateway.log'
const reset5 = Math.floor(Date.now()/1000) + 3600*3
const reset7 = Math.floor(Date.now()/1000) + 86400*4
const rl = {
  'anthropic-ratelimit-unified-status': 'allowed',
  'anthropic-ratelimit-unified-5h-status': 'allowed',
  'anthropic-ratelimit-unified-5h-utilization': '0.42',
  'anthropic-ratelimit-unified-5h-reset': String(reset5),
  'anthropic-ratelimit-unified-7d-status': 'allowed',
  'anthropic-ratelimit-unified-7d-utilization': '0.11',
  'anthropic-ratelimit-unified-7d-reset': String(reset7),
  'anthropic-ratelimit-unified-representative-claim': 'five_hour',
  'anthropic-ratelimit-unified-reset': String(reset5),
}
const send = (res, ev, data) => res.write(`event: ${ev}\ndata: ${JSON.stringify(data)}\n\n`)
http.createServer((req, res) => {
  let body = ''
  req.on('data', c => body += c)
  req.on('end', () => {
    const h = req.headers
    // never log credentials: only whether the header is the dummy one
    const entry = { method: req.method, url: req.url, authIsDummy: h.authorization === 'Bearer sk-gw-dummy' || h['x-api-key'] === 'sk-gw-dummy', hasAuth: !!(h.authorization || h['x-api-key']), beta: h['anthropic-beta'], sid: !!h['x-claude-code-session-id'] }
    if (body && req.url.startsWith('/v1/messages')) {
      try { const j = JSON.parse(body); entry.model = j.model; entry.stream = j.stream; entry.tools = (j.tools||[]).length; entry.systemBlocks = Array.isArray(j.system) ? j.system.length : typeof j.system; entry.keys = Object.keys(j); entry.thinking = j.thinking; entry.output_config = j.output_config } catch {}
    }
    fs.appendFileSync(log, JSON.stringify(entry) + '\n')
    if (req.url.startsWith('/api/usage/om-usage')) {
      if (h.authorization !== 'Bearer sk-gw-dummy') { res.writeHead(401, {'content-type':'application/json'}); return res.end('{"allowed":false}') }
      const row = (left, reset) => ({ used: 1000 - left*10, total: 1000, remainingPercentage: left, resetAt: reset })
      res.writeHead(200, {'content-type':'application/json'})
      return res.end(JSON.stringify({ allowed: true, personal: null, provider: null, providers: [{ connectionId: 'c', provider: 'antigravity', plan: 'Google AI Pro', quotas: {
        claude_gpt_session: row(62, new Date(Date.now()+8040000).toISOString()), claude_gpt_weekly: row(88, new Date(Date.now()+4*86400000).toISOString()) } }] }))
    }
    if (req.url.startsWith('/v1/models')) {
      res.writeHead(200, {'content-type':'application/json'})
      return res.end(JSON.stringify({ data: [
        { id: 'agy/claude-sonnet-5-5', display_name: 'AGY Sonnet 5.5', description: 'Sonnet 5.5 via Antigravity' },
        { id: 'agy/claude-opus-5-5', display_name: 'AGY Opus 5.5', description: 'Opus 5.5 via Antigravity' } ] }))
    }
    if (req.url.startsWith('/v1/messages')) {
      const stream = (() => { try { return JSON.parse(body).stream } catch { return false } })()
      const model = (() => { try { return JSON.parse(body).model } catch { return 'x' } })()
      if (!stream) { res.writeHead(200, {'content-type':'application/json', ...rl}); return res.end(JSON.stringify({ id:'msg_1', type:'message', role:'assistant', model, content:[{type:'text', text:'MOCK'}], stop_reason:'end_turn', stop_sequence:null, usage:{input_tokens:10, output_tokens:2} })) }
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', ...rl })
      send(res, 'message_start', { type:'message_start', message:{ id:'msg_1', type:'message', role:'assistant', model, content:[], stop_reason:null, stop_sequence:null, usage:{ input_tokens: 1200, output_tokens: 1, cache_read_input_tokens:0, cache_creation_input_tokens:0 } } })
      send(res, 'content_block_start', { type:'content_block_start', index:0, content_block:{ type:'text', text:'' } })
      send(res, 'content_block_delta', { type:'content_block_delta', index:0, delta:{ type:'text_delta', text:'Hello from the mock gateway.' } })
      send(res, 'content_block_stop', { type:'content_block_stop', index:0 })
      send(res, 'message_delta', { type:'message_delta', delta:{ stop_reason:'end_turn', stop_sequence:null }, usage:{ output_tokens: 8 } })
      send(res, 'message_stop', { type:'message_stop' })
      return res.end()
    }
    res.writeHead(404); res.end('{}')
  })
}).listen(port, '127.0.0.1', () => console.log('mock gateway on', port))
