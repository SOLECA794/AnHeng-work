export interface Schedule {
  id: number
  name: string
  scopeId: number
  scopeName?: string
  intervalType: 'DAILY' | 'WEEKLY' | 'CRON'
  cron?: string
  enabled: boolean
  nextRunAt?: string
}

export type TaskStatus = 'QUEUED' | 'DISCOVERING' | 'SCANNING' | 'ANALYZING' | 'DONE' | 'FAILED'

export interface Task {
  id: number
  scopeId: number
  scopeName?: string
  status: TaskStatus
  progress?: number
  createdAt: string
  finishedAt?: string
}