import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('App foundation shell', () => {
  it('says plainly when the API is not configured instead of faking a status', () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    render(<App />)
    expect(screen.getByTestId('api-status').textContent).toMatch(/not configured/i)
  })

  it('shows the real health response from the API', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.test/')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', version: '0.1.0' }) })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByText(/API online \(version 0\.1\.0\)/)).toBeTruthy()
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.test/health')
  })

  it('reports an unreachable API', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')))
    render(<App />)
    expect(await screen.findByText(/unreachable/i)).toBeTruthy()
  })

  it('never uses verdict language', () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    const { container } = render(<App />)
    expect(container.textContent?.toLowerCase()).not.toMatch(/fraud|rigged/)
  })
})
