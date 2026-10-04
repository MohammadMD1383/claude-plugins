export type AgyWindow = {
  /** Stable id: `session`, `weekly`, or `model:<key>` (family-prefixed when several families show). */
  id: string
  label: string
  /** 0-100. */
  percentUsed: number
  resetsAt?: string
}

export type AgyQuota = {
  plan?: string
  windows: AgyWindow[]
}

declare module 'claude-code' {
  interface PluginState {
    'agy-link': {
      /** The last good reading of the gateway's Antigravity quota. */
      quota: AgyQuota | null
      /** Why the last read failed; null when it worked. */
      problem: string | null
      /** Whether this session's requests go to the gateway. */
      isActive: boolean
      now: number
    }
  }
}
