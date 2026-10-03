export type UsageBarLimit = { kind: string; percentUsed: number; resetsAt?: string }

export type UsageBarSnapshot = {
  tokens?: number
  window: number
  percent?: number
  limits: UsageBarLimit[]
}

declare module 'claude-code' {
  interface PluginState {
    'usage-bar': { snapshot: UsageBarSnapshot | null; now: number }
  }
}
