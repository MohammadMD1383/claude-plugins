export type UsageBarLimit = { kind: string; percentUsed: number; resetsAt?: string }

export type UsageBarSnapshot = {
  tokens?: number
  window: number
  percent?: number
  limits: UsageBarLimit[]
  /** The model the main thread's last step named; tells a subagent on the same model apart. */
  model?: string
}

/** A subagent's context as of its last model request. */
export type UsageBarAgent = { tokens: number; model: string }

declare module 'claude-code' {
  interface PluginState {
    'usage-bar': {
      snapshot: UsageBarSnapshot | null
      agents: Record<string, UsageBarAgent>
      now: number
    }
  }
}
