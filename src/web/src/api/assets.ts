import client from './client'
import type { Asset, AssetListParams, PageResult } from '../types/asset'

// 后端联调后改成 false
const USE_MOCK = true

let mockList: Asset[] = [
  { id: 1, domain: 'demo.example.com', scopeType: 'FULL', owner: '安全部', status: 'ACTIVE', pageCount: 128, createdAt: '2026-10-08 10:00:00' },
  { id: 2, domain: 'test.example.com', scopeType: 'PATH', scopeValue: '/app', owner: '测试部', status: 'ACTIVE', pageCount: 42, createdAt: '2026-10-09 14:20:00' },
  { id: 3, domain: 'old.example.com', scopeType: 'FULL', owner: '运维组', status: 'DISABLED', pageCount: 0, createdAt: '2026-10-01 09:00:00' },
]

export async function listAssets(params: AssetListParams): Promise<PageResult<Asset>> {
  if (USE_MOCK) {
    const { page = 1, size = 20, status } = params
    const filtered = status ? mockList.filter(a => a.status === status) : mockList
    return new Promise(r => setTimeout(() => r({
      list: filtered.slice((page - 1) * size, page * size),
      total: filtered.length, page, size,
    }), 200))
  }
  return client.get('/assets', { params })
}

export async function createAsset(data: Partial<Asset>) {
  if (USE_MOCK) {
    const now = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    const createdAt = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
    const item: Asset = {
      id: Date.now(),
      domain: data.domain || '',
      scopeType: data.scopeType || 'FULL',
      scopeValue: data.scopeValue,
      owner: data.owner,
      authNote: data.authNote,
      status: 'ACTIVE',
      pageCount: 0,
      createdAt,
    }
    mockList = [item, ...mockList]
    return item
  }
  return client.post('/assets', data)
}

export async function updateAsset(id: number, data: Partial<Asset>) {
  if (USE_MOCK) {
    mockList = mockList.map(a => a.id === id ? { ...a, ...data } : a)
    return mockList.find(a => a.id === id)
  }
  return client.patch(`/assets/${id}`, data)
}

export async function disableAsset(id: number) {
  if (USE_MOCK) {
    mockList = mockList.map(a => a.id === id ? { ...a, status: 'DISABLED' as const } : a)
    return { id }
  }
  return client.delete(`/assets/${id}`)
}

export async function enableAsset(id: number) {
  if (USE_MOCK) {
    mockList = mockList.map(a => a.id === id ? { ...a, status: 'ACTIVE' as const } : a)
    return { id }
  }
  return client.patch(`/assets/${id}`, { status: 'ACTIVE' })
}