import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { getSupabase } from '../lib/supabase'

/** Inbox link with the number of unread notifications; refreshes every 30 seconds and on page changes. */
export default function NotificationBell() {
  const [unread, setUnread] = useState(0)
  const { pathname } = useLocation()
  useEffect(() => {
    const sb = getSupabase()
    if (!sb) return
    let live = true
    const check = async () => {
      try {
        const { count } = await sb.from('notifications').select('id', { count: 'exact', head: true }).is('read_at', null)
        if (live) setUnread(count ?? 0)
      } catch {
        /* offline: keep the last count */
      }
    }
    void check()
    const t = setInterval(() => void check(), 30_000)
    return () => {
      live = false
      clearInterval(t)
    }
  }, [pathname])
  return (
    <NavLink to="/inbox" className="bell" aria-label={unread ? `Inbox, ${unread} unread` : 'Inbox'}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
        <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
      </svg>
      {unread > 0 && <span className="bell-count">{unread > 99 ? '99+' : unread}</span>}
    </NavLink>
  )
}
