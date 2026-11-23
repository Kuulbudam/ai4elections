import { api, ApiError, type Schemas } from './api'

/**
 * Field reports are saved on the phone first, then sent. If there is no signal they wait here and go out when the
 * connection returns. Each has a client_id made on the phone, so sending the same report twice is harmless.
 */
export type Kind = Schemas['FieldReportKind']
export type Pending = {
  client_id: string
  project_id: string
  kind: Kind
  pu_code: string
  captured_at: string
  location: { lat: number; lng: number; accuracy_m: number | null } | null
  payload: Record<string, unknown>
  error?: string
}
const KEY = 'eaas.outbox'

export function pending(): Pending[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(v) ? (v as Pending[]) : []
  } catch {
    return []
  }
}

function save(items: Pending[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(items))
  } catch {
    /* storage full or blocked: the report stays in memory for this page */
  }
}

export const newClientId = () => (crypto.randomUUID?.() ?? `${Date.now()}${Math.random()}`).replace(/[^A-Za-z0-9]/g, '').slice(0, 32)

/** Where the phone is, if it can tell within a few seconds (never blocks a report). */
export function locate(timeoutMs = 6000): Promise<Pending['location']> {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null)
    const t = setTimeout(() => resolve(null), timeoutMs + 500)
    navigator.geolocation.getCurrentPosition(
      (p) => {
        clearTimeout(t)
        resolve({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy_m: Math.round(p.coords.accuracy) })
      },
      () => {
        clearTimeout(t)
        resolve(null)
      },
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 120_000 },
    )
  })
}

export function enqueue(item: Pending): void {
  save([...pending(), item])
}

/** Try to send everything waiting. Reports the server refuses are kept with the reason, not retried. */
export async function flush(): Promise<{ sent: number; waiting: number }> {
  let sent = 0
  const left: Pending[] = []
  for (const item of pending()) {
    if (item.error) {
      left.push(item)
      continue
    }
    try {
      const { error: _e, ...body } = item
      await api('POST', '/field-reports', body, { 'Idempotency-Key': item.client_id })
      sent++
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 401 && err.status !== 429) left.push({ ...item, error: err.message })
      else left.push(item) // no signal, server busy or session expired: try again later
    }
  }
  save(left)
  return { sent, waiting: left.filter((x) => !x.error).length }
}

export function discard(clientId: string): void {
  save(pending().filter((x) => x.client_id !== clientId))
}
