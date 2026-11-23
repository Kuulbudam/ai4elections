import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { setSupabaseForTests } from '../lib/supabase'

function query(result: unknown) {
  const q: Record<string, unknown> = {}
  for (const m of ['select', 'eq', 'order', 'in', 'range', 'limit', 'maybeSingle']) q[m] = () => q
  q.then = (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve)
  return q
}
const session = { user: { id: 'u1', email: 'a@example.test' }, access_token: 'tok' }
function client(role: string) {
  return {
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session } }),
      onAuthStateChange: vi.fn().mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } }),
      signOut: vi.fn(),
      mfa: { getAuthenticatorAssuranceLevel: vi.fn().mockResolvedValue({ data: { currentLevel: 'aal2', nextLevel: 'aal2' } }) },
    },
    from: vi.fn((t: string) => query({ data: t === 'memberships' ? { role } : { org_id: 'o1', election_name: 'Plateau 2023' }, error: null })),
    rpc: vi.fn(),
  }
}

/** A stand-in microphone: start() records, stop() hands over one chunk of audio. */
class FakeRecorder {
  static isTypeSupported = (m: string) => m === 'audio/webm;codecs=opus'
  static last: FakeRecorder | null = null
  state = 'inactive'
  mimeType = 'audio/webm;codecs=opus'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  stream: { getTracks: () => { stop: () => void }[] }
  constructor(stream: { getTracks: () => { stop: () => void }[] }) {
    this.stream = stream
    FakeRecorder.last = this
  }
  start() {
    this.state = 'recording'
  }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['opus'], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

const spoken: { text: string; lang: string }[] = []
function stub(calls: string[], voices: { lang: string }[] = [{ lang: 'en-GB' }]) {
  vi.stubGlobal('crypto', { randomUUID: () => 'k1' })
  vi.stubGlobal('MediaRecorder', FakeRecorder)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] })) },
  })
  vi.stubGlobal('SpeechSynthesisUtterance', class { voice: unknown = null; lang = ''; rate = 1; onend: (() => void) | null = null; onerror = null; text: string; constructor(text: string) { this.text = text } })  // prettier-ignore
  vi.stubGlobal('speechSynthesis', {
    getVoices: () => voices,
    cancel: vi.fn(),
    speak: vi.fn((u: { text: string; lang: string; onend: () => void }) => {
      spoken.push({ text: u.text, lang: u.lang })
      u.onend()
    }),
  })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url} ${init?.body ?? ''}`)
      const json = (b: unknown) => ({ ok: true, status: 200, json: async () => b })
      if (url.endsWith('/assistant/status')) return json({ enabled: true, model: 'llama' })
      if (url.endsWith('/assistant/transcribe')) return json({ text: 'Which LGA has the most flags?', language: 'en', model: 'whisper' })
      if (url.endsWith('/assistant/ask'))
        return json({ id: 'q1', answer: 'JOS NORTH has the most flags (12).', citations: [], lookups: [], grounded: true, note: null, model: 'llama', language: JSON.parse(String(init?.body)).language })
      return json({ status: 'ok' })
    }),
  )
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://api.test')
  window.history.pushState({}, '', '/projects/p1/assistant')
  spoken.length = 0
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  setSupabaseForTests(undefined)
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe('voice', () => {
  it('a spoken question is turned into text, asked, and the answer is read back', async () => {
    const calls: string[] = []
    stub(calls)
    setSupabaseForTests(client('analyst') as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Speak' }))
    const stop = await screen.findByRole('button', { name: /Stop \(\d+s\)/ })
    expect(stop.getAttribute('aria-pressed')).toBe('true')
    await act(async () => fireEvent.click(stop))
    expect(await screen.findByText('JOS NORTH has the most flags (12).')).toBeTruthy()
    const t = calls.find((c) => c.startsWith('POST https://api.test/assistant/transcribe'))!
    expect(t).toContain('"mime_type":"audio/webm"')
    expect(t).toContain(`"audio_base64":"${btoa('opus')}"`)
    expect(calls.some((c) => c.startsWith('POST https://api.test/assistant/ask') && c.includes('Which LGA has the most flags?'))).toBe(true)
    await waitFor(() => expect(spoken).toEqual([{ text: 'JOS NORTH has the most flags (12).', lang: 'en-GB' }]))
    expect(screen.getByRole('button', { name: 'Read aloud' })).toBeTruthy()
  })

  it('says plainly when the phone has no voice for the answer language', async () => {
    stub([], [{ lang: 'en-US' }])
    localStorage.setItem('ai4e.answerLanguage', 'ha')
    setSupabaseForTests(client('admin') as never)
    render(<App />)
    fireEvent.change(await screen.findByLabelText('Your question'), { target: { value: 'Wane LGA?' } })
    fireEvent.submit(screen.getByRole('form', { name: 'Ask a question' }))
    expect(await screen.findByText('This device has no Hausa voice to read it aloud.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Read aloud' })).toBeNull()
  })

  it('a blocked microphone gives a clear message', async () => {
    stub([])
    ;(navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('NotAllowedError'))
    setSupabaseForTests(client('analyst') as never)
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Speak' }))
    expect(await screen.findByText('Allow the microphone for this site to speak your question.')).toBeTruthy()
  })
})
