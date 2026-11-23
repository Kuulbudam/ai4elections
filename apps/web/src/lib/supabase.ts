import { createClient, type SupabaseClient } from '@supabase/supabase-js'

let client: SupabaseClient | null | undefined

/** Browser client. Uses only the public anon/publishable key; every query is filtered by RLS. */
export function getSupabase(): SupabaseClient | null {
  if (client !== undefined) return client
  const url = (import.meta.env.VITE_SUPABASE_URL as string | undefined)?.trim()
  const key = (import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined)?.trim()
  client = url && key ? createClient(url, key) : null
  return client
}

/** For tests only. */
export function setSupabaseForTests(c: SupabaseClient | null | undefined): void {
  client = c
}

export type Role = 'admin' | 'analyst' | 'researcher' | 'viewer' | 'coordinator' | 'field_agent'
export const ROLES: Role[] = ['admin', 'analyst', 'researcher', 'viewer', 'coordinator', 'field_agent']
export const ROLE_LABEL: Record<Role, string> = {
  admin: 'Admin',
  analyst: 'Analyst',
  researcher: 'Researcher',
  viewer: 'Viewer',
  coordinator: 'Coordinator',
  field_agent: 'Field agent',
}

/** Turn database/API errors into short messages that never expose internals. */
export function friendlyError(err: { message?: string; code?: string } | null | undefined): string {
  const m = (err?.message ?? '').toLowerCase()
  if (m.includes('invalid login credentials')) return 'Email or password is incorrect.'
  if (m.includes('user already registered')) return 'An account with this email already exists. Sign in instead.'
  if (m.includes('password should be')) return 'Password is too weak. Use at least 8 characters.'
  if (m.startsWith('not_found')) return err!.message!.replace(/^not_found:\s*/i, '')
  if (m.startsWith('conflict')) return err!.message!.replace(/^conflict:\s*/i, '')
  if (m.startsWith('last_admin')) return 'An organisation must keep at least one admin.'
  if (m.startsWith('forbidden') || err?.code === '42501') return 'You do not have permission to do that.'
  if (err?.code === '23514') return 'Some values are not allowed. Check the form and try again.'
  return 'Something went wrong. Please try again.'
}
