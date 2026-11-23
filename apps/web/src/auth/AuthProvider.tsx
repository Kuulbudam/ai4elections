import type { Session } from '@supabase/supabase-js'
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { getSupabase } from '../lib/supabase'

type Aal = { current: string | null; next: string | null }
type AuthState = { session: Session | null; loading: boolean; aal: Aal; refreshAal: () => Promise<void> }

const AuthContext = createContext<AuthState>({
  session: null,
  loading: true,
  aal: { current: null, next: null },
  refreshAal: async () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(() => getSupabase() !== null)
  const [aal, setAal] = useState<Aal>({ current: null, next: null })

  const refreshAal = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const { data } = await sb.auth.mfa.getAuthenticatorAssuranceLevel()
    setAal({ current: data?.currentLevel ?? null, next: data?.nextLevel ?? null })
  }, [])

  useEffect(() => {
    const sb = getSupabase()
    if (!sb) return
    const apply = async (s: Session | null) => {
      setSession(s)
      if (s) await refreshAal()
      else setAal({ current: null, next: null })
      setLoading(false)
    }
    sb.auth.getSession().then(({ data }) => void apply(data.session))
    const { data } = sb.auth.onAuthStateChange((event, s) => {
      // Opened from a "reset your password" email: after two-step verification, go to the new-password page.
      if (event === 'PASSWORD_RECOVERY') markRecovery()
      // Defer: calling Supabase from inside this callback can deadlock its auth lock.
      setTimeout(() => void apply(s), 0)
    })
    return () => data.subscription.unsubscribe()
  }, [refreshAal])

  return <AuthContext.Provider value={{ session, loading, aal, refreshAal }}>{children}</AuthContext.Provider>
}

const RECOVERY = 'eaas.recovery'
// eslint-disable-next-line react-refresh/only-export-components
export function markRecovery(): void {
  try {
    sessionStorage.setItem(RECOVERY, '1')
  } catch {
    /* storage blocked: the person can still change the password from Profile */
  }
}
// eslint-disable-next-line react-refresh/only-export-components
export function takeRecovery(): boolean {
  try {
    const v = sessionStorage.getItem(RECOVERY) === '1'
    sessionStorage.removeItem(RECOVERY)
    return v
  } catch {
    return false
  }
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  return useContext(AuthContext)
}
