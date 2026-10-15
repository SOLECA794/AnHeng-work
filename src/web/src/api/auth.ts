import client from './client'

export function login(data: { username: string; password: string }) {
  return client.post('/auth/login', data)
}

export function logout() {
  return client.post('/auth/logout')
}

export function getMe() {
  return client.get('/auth/me')
}