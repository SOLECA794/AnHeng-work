import client from './client'
import type { SensitiveRule, RuleListParams, PageResult } from '../types/sensitive'

const USE_MOCK = true

let mockList: SensitiveRule[] = [
  { id: 1, name: '密钥特征', ruleType: 'REGEX', pattern: '(?i)(api[_-]?key|secret)[\\s:=\'"]+[A-Za-z0-9]{16,}', severity: 'HIGH', enabled: true, createdAt: '2026-10-09 11:00:00' },
  { id: 2, name: '内部邮箱', ruleType: 'KEYWORD', pattern: '@internal.com', severity: 'MEDIUM', enabled: true, createdAt: '2026-10-09 11:10:00' },
  { id: 3, name: '配置文件', ruleType: 'FILE_TYPE', pattern: '.env,.conf,.bak', severity: 'HIGH', enabled: false, createdAt: '2026-10-09 11:20:00' },
]

export async function listRules(params: RuleListParams): Promise<PageResult<SensitiveRule>> {
  if (USE_MOCK) {
    const { page = 1, size = 20, ruleType, enabled } = params
    let filtered = mockList
    if (ruleType) filtered = filtered.filter(r => r.ruleType === ruleType)
    if (enabled !== undefined) filtered = filtered.filter(r => r.enabled === enabled)
    return new Promise(r => setTimeout(() => r({
      list: filtered.slice((page - 1) * size, page * size),
      total: filtered.length, page, size,
    }), 200))
  }
  return client.get('/sensitive/rules', { params })
}

export async function createRule(data: Partial<SensitiveRule>) {
  if (USE_MOCK) {
    const item: SensitiveRule = {
      id: Date.now(), enabled: true,
      name: data.name || '', ruleType: data.ruleType || 'KEYWORD',
      pattern: data.pattern || '', scopeBind: data.scopeBind,
      severity: data.severity || 'MEDIUM',
      createdAt: new Date().toISOString().slice(0, 19).replace('T', ' '),
    }
    mockList = [item, ...mockList]
    return item
  }
  return client.post('/sensitive/rules', data)
}

export async function updateRule(id: number, data: Partial<SensitiveRule>) {
  if (USE_MOCK) {
    mockList = mockList.map(r => r.id === id ? { ...r, ...data } : r)
    return mockList.find(r => r.id === id)
  }
  return client.put(`/sensitive/rules/${id}`, data)
}

export async function toggleRule(id: number, enabled: boolean) {
  if (USE_MOCK) {
    mockList = mockList.map(r => r.id === id ? { ...r, enabled } : r)
    return { id, enabled }
  }
  return client.patch(`/sensitive/rules/${id}/enabled`, { enabled })
}