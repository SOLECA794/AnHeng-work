export type RuleType = 'KEYWORD' | 'REGEX' | 'FILE_TYPE' | 'SAMPLE'
export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO'

export interface SensitiveRule {
  id: number
  name: string
  ruleType: RuleType
  pattern: string
  scopeBind?: string
  severity: Severity
  enabled: boolean
  createdAt?: string
}

export interface RuleListParams {
  page?: number
  size?: number
  ruleType?: RuleType
  enabled?: boolean
}

export interface PageResult<T> {
  list: T[]
  total: number
  page: number
  size: number
}