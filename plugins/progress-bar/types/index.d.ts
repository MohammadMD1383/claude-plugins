/** What Claude last reported, and when the run it belongs to began. */
export type ProgressState = {
  /** 0-100, whole numbers. */
  percent: number
  /** What Claude is doing now, as it phrased it. */
  step?: string
  /** When the first report of this run arrived (ms). */
  startedAt: number
  /** The percentage of that first report: the baseline the time-left estimate measures from. */
  startPercent: number
  /** When the latest report arrived (ms). */
  updatedAt: number
}

declare module 'claude-code' {
  interface PluginState {
    'progress-bar': {
      progress: ProgressState | null
      /** The person dismissed the bar; the next report shows it again. */
      isHidden: boolean
      now: number
    }
  }
}
