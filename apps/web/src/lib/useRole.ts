import { useEffect, useState } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { getSupabase, type Role } from './supabase'

/** The signed-in user's role in an organisation (null while loading or if not a member). */
export function useRole(orgId: string | undefined): Role | null {
  const { session } = useAuth()
  const [role, setRole] = useState<Role | null>(null)
  useEffect(() => {
    const sb = getSupabase()
    if (!sb || !orgId || !session) return
    sb.from('memberships')
      .select('role')
      .eq('org_id', orgId)
      .eq('user_id', session.user.id)
      .maybeSingle()
      .then(({ data }) => setRole((data?.role as Role) ?? null))
  }, [orgId, session])
  return role
}

export const canUpload = (r: Role | null) => r === 'admin' || r === 'analyst'
