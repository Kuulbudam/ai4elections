import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { ClaimCard, ClaimForm, type ClaimRow, type EvidenceRow } from '../components/Claims'
import { CLAIM_STATUS } from '../lib/findings'
import { friendlyError, getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

export default function Claims() {
  const { projectId = '' } = useParams()
  const { session } = useAuth()
  const [project, setProject] = useState<{ org_id: string; election_name: string } | null>(null)
  const [rows, setRows] = useState<ClaimRow[] | null>(null)
  const [evidence, setEvidence] = useState<EvidenceRow[]>([])
  const [people, setPeople] = useState<Record<string, string>>({})
  const [status, setStatus] = useState('')
  const [error, setError] = useState<string | null>(null)
  const role = useRole(project?.org_id)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const p = await sb.from('projects').select('org_id, election_name').eq('id', projectId).maybeSingle()
    setProject(p.data)
    let q = sb
      .from('claims')
      .select('id, statement, status, finding_id, created_by, created_at, verified_by, verified_at, verification_reason')
      .eq('project_id', projectId)
      .order('created_at', { ascending: false })
      .limit(100)
    if (status) q = q.eq('status', status)
    const { data, error: e } = await q
    if (e) setError(friendlyError(e))
    const cl = (data as ClaimRow[]) ?? []
    setRows(cl)
    const ev = cl.length ? (((await sb.from('claim_evidence').select('id, claim_id, kind, description, url, file_sha256, added_by, created_at').in('claim_id', cl.map((c) => c.id)).order('created_at')).data as EvidenceRow[]) ?? []) : []
    setEvidence(ev)
    const ids = [...new Set([...cl.flatMap((c) => [c.created_by, c.verified_by ?? '']), ...ev.map((x) => x.added_by)])].filter(Boolean)
    if (ids.length) {
      const pr = await sb.from('profiles').select('id, email, display_name').in('id', ids)
      setPeople(Object.fromEntries((pr.data ?? []).map((u: { id: string; email: string; display_name: string | null }) => [u.id, u.display_name || u.email])))
    }
  }, [projectId, status])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  const canReview = role === 'admin' || role === 'analyst'
  const canContribute = canReview || role === 'researcher'
  const who = (id: string | null) => (id ? (people[id] ?? 'a member') : 'system')

  return (
    <section className="page" aria-labelledby="claims-heading">
      <p>
        <Link to={`/projects/${projectId}`}>← Project</Link> · <Link to={`/projects/${projectId}/findings`}>Findings</Link>
      </p>
      <h1 id="claims-heading">Claims{project ? `: ${project.election_name}` : ''}</h1>
      <p className="page-intro">
        Things people have reported, such as “results were changed at the ward”. Every claim starts as an <strong>unverified allegation</strong>. It
        becomes a <strong>verified fact</strong> only when evidence is attached and a second admin or analyst signs it off.
      </p>
      <label className="inline-label">
        Status
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option>
          {Object.entries(CLAIM_STATUS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="empty">No claims recorded.</p>
      ) : (
        rows.map((c) => (
          <ClaimCard key={c.id} c={c} items={evidence.filter((e) => e.claim_id === c.id)} who={who} canReview={canReview} canContribute={canContribute} me={session!.user.id} orgId={project!.org_id} onDone={load} />
        ))
      )}
      {canContribute && <ClaimForm projectId={projectId} findingId={null} onDone={load} />}
    </section>
  )
}
