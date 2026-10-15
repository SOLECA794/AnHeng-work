import client from './client'
import type { Task } from '../types/schedule'

const USE_MOCK = true

const mockTasks: Task[] = [
  { id: 101, scopeId: 1, scopeName: 'demo.example.com', status: 'DONE', progress: 100, createdAt: '2026-10-12 02:00:00', finishedAt: '2026-10-12 02:08:21' },
  { id: 102, scopeId: 1, scopeName: 'demo.example.com', status: 'SCANNING', progress: 62, createdAt: '2026-10-12 09:30:00' },
  { id: 103, scopeId: 2, scopeName: 'test.example.com', status: 'FAILED', progress: 30, createdAt: '2026-10-12 08:00:00' },
  { id: 104, scopeId: 1, scopeName: 'demo.example.com', status: 'QUEUED', createdAt: '2026-10-12 10:00:00' },
]

export async function listTasks(): Promise<Task[]> {
  if (USE_MOCK) return new Promise(r => setTimeout(() => r([...mockTasks]), 200))
  return client.get('/tasks')
}

export async function retryTask(id: number) {
  if (USE_MOCK) return { id, status: 'QUEUED' as const }
  return client.post(`/tasks/${id}/retry`)
}