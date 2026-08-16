import { describe, it, expect, vi, beforeEach } from 'vitest'
import { getApiBase, buildApiUrl, apiFetch } from '../api'

describe('api helpers', () => {
  beforeEach(() => {
    // @ts-ignore override
    delete (window as any).location
    // minimal location mock
    ;(window as any).location = { protocol: 'http:', hostname: 'localhost', port: '5173' }
  ;(globalThis as any).process = { env: {} }
  })

  it('getApiBase falls back to host:8000', () => {
    expect(getApiBase()).toBe('http://localhost:8000')
  })

  it('getApiBase uses VITE_API_URL when set', () => {
    ;(globalThis as any).process.env.VITE_API_URL = 'http://example:9000/'
    expect(getApiBase()).toBe('http://example:9000')
  })

  it('getApiBase uses same origin when running on port 8000', () => {
    ;(globalThis as any).process.env.VITE_API_URL = ''
    // @ts-ignore override
    delete (window as any).location
    ;(window as any).location = { protocol: 'http:', hostname: 'localhost', port: '8000' }
    expect(getApiBase()).toBe('http://localhost:8000')
  })

  it('buildApiUrl joins correctly', () => {
    ;(globalThis as any).process.env.VITE_API_URL = 'http://api:8000'
    expect(buildApiUrl('/x')).toBe('http://api:8000/x')
    expect(buildApiUrl('y')).toBe('http://api:8000/y')
  })

  it('apiFetch prefixes relative URLs', async () => {
    const spy = vi.spyOn(globalThis, 'fetch' as any).mockResolvedValue(new Response('{}'))
    ;(globalThis as any).process.env.VITE_API_URL = 'http://api:8000'
    await apiFetch('/z')
    expect(spy).toHaveBeenCalledWith('http://api:8000/z', undefined)
    spy.mockRestore()
  })

  it('apiFetch passes through absolute URLs and Request objects', async () => {
    const spy = vi.spyOn(globalThis, 'fetch' as any).mockResolvedValue(new Response('{}'))
    const req = new Request('http://example.com/abs')
    await apiFetch('http://example.com/a')
    await apiFetch(req)
    expect(spy).toHaveBeenNthCalledWith(1, 'http://example.com/a', undefined)
    expect(spy).toHaveBeenNthCalledWith(2, req, undefined)
    spy.mockRestore()
  })
})
