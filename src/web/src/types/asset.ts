export interface Asset {
  id: number
  domain: string
  scopeType: 'FULL' | 'PATH' | 'SUBDOMAIN'
  scopeValue?: string
  authNote?: string
  owner?: string
  status: 'ACTIVE' | 'DISABLED'
  pageCount?: number
  createdAt: string
  updatedAt?: string
}

export interface AssetListParams {
  page?: number
  size?: number
  status?: 'ACTIVE' | 'DISABLED'
}

export interface PageResult<T> {
  list: T[]
  total: number
  page: number
  size: number
}