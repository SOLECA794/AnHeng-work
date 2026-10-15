import client from './client'
import type { Schedule } from '../types/schedule'

const USE_MOCK = true

let mockList: Schedule[] = [
  { id: 1, name: 'demo 每日巡检', scopeId: 1, scopeName: 'demo.example.com', intervalType: 'DAILY', enabled: true, nextRunAt: '2026-10-13 02:00:00' },
  { id: 2, name: 'test 每周巡检', scopeId: 2, scopeName: 'test.example.com', intervalType: 'WEEKLY', enabled: false },
]

export async function listSchedules(): Promise<Schedule[]> {
  if (USE_MOCK) return new Promise(r => setTimeout(() => r([...mockList]), 200))
  return client.get('/schedules')
}

export async function toggleSchedule(id: number, enabled: boolean) {
  if (USE_MOCK) {
    mockList = mockList.map(s => s.id === id ? { ...s, enabled } : s)
    return { id, enabled }
  }
  return client.patch(`/schedules/${id}`, { enabled })
}