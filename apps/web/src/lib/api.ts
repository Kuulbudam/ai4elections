import type { components } from '../api/schema'
import { getSupabase } from './supabase'

export type Schemas = components['schemas']
export type Job = Schemas['Job']

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

function base(): string {
  const value = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim()
  if (!value) throw new ApiError(0, 'NOT_CONFIGURED', 'The API address is not configured for this deployment.')
  return value.replace(/\/$/, '')
}

/** Calls the FastAPI service with the signed-in user's access token. */
export async function api<T>(method: string, path: string, body?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const { data } = (await getSupabase()?.auth.getSession()) ?? { data: { session: null } }
  const token = data.session?.access_token
  if (!token) throw new ApiError(401, 'UNAUTHENTICATED', 'Your session has expired. Sign in again.')
  let res: Response
  try {
    res = await fetch(`${base()}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...extraHeaders,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'NETWORK', 'Could not reach the server. Check your connection and try again.')
  }
  if (!res.ok) {
    let err: Partial<Schemas['Error']> = {}
    try {
      err = await res.json()
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, err.code ?? 'INTERNAL', err.message ?? 'Request failed. Please try again.')
  }
  return (await res.json()) as T
}

/** PUT the file to the signed upload URL, reporting progress (0..1). */
export function uploadToSignedUrl(url: string, file: Blob, onProgress: (p: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    xhr.setRequestHeader('Content-Type', 'text/csv')
    xhr.setRequestHeader('x-upsert', 'false')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new ApiError(xhr.status, 'UPLOAD_FAILED', 'The file upload failed. Please try again.'))
    xhr.onerror = () => reject(new ApiError(0, 'NETWORK', 'The file upload was interrupted. Please try again.'))
    xhr.send(file)
  })
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
