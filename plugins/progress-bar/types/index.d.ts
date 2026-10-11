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

export type JobStatus = 'running' | 'done' | 'failed' | 'died'

/** A job some script or command reports through `pbar` (see bin/pbar). */
export type Job = {
  id: string
  label: string
  status: JobStatus
  /** Items finished, when the job counts items. */
  done?: number
  total?: number
  /** 0-100, from the items or reported directly; undefined when the job cannot say. */
  percent?: number
  /** The latest thing the job said it was doing (its last output line). */
  step?: string
  /** The process to check is still there. */
  pid?: number
  startedAt: number
  /** When the latest report arrived (ms). */
  updatedAt: number
  /** Seconds the job said it had been quiet when it last reported. */
  idleSec: number
  /** When the percentage last moved (ms), and its first value: the baseline of the estimate. */
  movedAt: number
  startFraction: number
  /** Smoothed time between moves (ms); a job that is slow by nature gets more patience. */
  gapMs: number
  /** Reports received: the spinner turns once per report, so a frozen spinner is a frozen job. */
  seq: number
  endedAt?: number
  /** Why it failed or died, or the closing note. */
  msg?: string
  /** Stall already announced for this stretch of silence. */
  isStallNoticed?: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'progress-bar': {
      progress: ProgressState | null
      /** The person dismissed the bar; the next report shows it again. */
      isHidden: boolean
      now: number
      /** Jobs reported through `pbar`, by id. */
      jobs: Record<string, Job>
    }
  }
}
