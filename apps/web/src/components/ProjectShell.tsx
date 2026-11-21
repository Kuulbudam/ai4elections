import { createContext, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, NavLink, matchPath, useLocation, useNavigate } from 'react-router-dom'
import { getSupabase, type Role } from '../lib/supabase'
import { Icon } from './Icon'
import { useRole } from '../lib/useRole'

export type ShellVersion = {
  id: string
  original_filename: string
  version_number: number
  status: string
  row_count: number | null
  mapping_saved_at: string | null
  validated_at: string | null
  latest_analysis_run_id: string | null
  validation_summary: { issues_by_rule?: Record<string, number> } | null
  created_at: string
}
type Project = {
  id: string
  org_id: string
  election_name: string
  election_year: number
}
type Counts = {
  toReview: number
  flagged: number
  reports: number
  unverified: number
}
type Ctx = {
  project: Project
  version: ShellVersion | null
  versions: ShellVersion[]
  role: Role | null
  refresh: () => void
}

const ShellContext = createContext<Ctx | null>(null)

/** Works out which project (and dataset version) the current page belongs to. */
function useScope() {
  const { pathname } = useLocation()
  const m = (p: string) => matchPath({ path: p, end: false }, pathname)?.params
  return {
    projectId: m('/projects/:projectId')?.projectId,
    versionId: m('/versions/:versionId')?.versionId,
    findingId: m('/findings/:findingId')?.findingId,
    reportId: m('/reports/:reportId')?.reportId,
  }
}

export default function ProjectShell({ children }: { children: ReactNode }) {
  const scope = useScope()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [projectId, setProjectId] = useState<string | null>(null)
  const [project, setProject] = useState<Project | null>(null)
  const [versions, setVersions] = useState<ShellVersion[]>([])
  const [chosen, setChosen] = useState<string | null>(null)
  const [counts, setCounts] = useState<Counts | null>(null)
  const [tick, setTick] = useState(0)
  const role = useRole(project?.org_id)

  // Resolve the project from whatever the URL names.
  useEffect(() => {
    const sb = getSupabase()
    if (!sb) return
    let live = true
    ;(async () => {
      try {
        let pid = scope.projectId ?? null
        if (!pid && scope.versionId)
          pid = (await sb.from('dataset_versions').select('project_id').eq('id', scope.versionId).maybeSingle()).data?.project_id ?? null
        if (!pid && scope.findingId) pid = (await sb.from('findings').select('project_id').eq('id', scope.findingId).maybeSingle()).data?.project_id ?? null
        if (!pid && scope.reportId) pid = (await sb.from('reports').select('project_id').eq('id', scope.reportId).maybeSingle()).data?.project_id ?? null
        if (live) setProjectId(pid)
      } catch {
        if (live) setProjectId(null)
      }
    })()
    return () => {
      live = false
    }
  }, [scope.projectId, scope.versionId, scope.findingId, scope.reportId])

  const refresh = useCallback(() => setTick((t) => t + 1), [])

  useEffect(() => {
    const sb = getSupabase()
    if (!sb || !projectId) return
    let live = true
    ;(async () => {
      try {
        const [p, v, r, c] = await Promise.all([
          sb.from('projects').select('id, org_id, election_name, election_year').eq('id', projectId).maybeSingle(),
          sb
            .from('dataset_versions')
            .select(
              'id, original_filename, version_number, status, row_count, mapping_saved_at, validated_at, latest_analysis_run_id, validation_summary, created_at',
            )
            .eq('project_id', projectId)
            .is('archived_at', null)
            .order('created_at', { ascending: false }),
          sb.from('reports').select('id').eq('project_id', projectId).limit(500),
          sb.from('claims').select('id').eq('project_id', projectId).eq('status', 'unverified_allegation').limit(500),
        ])
        if (!live) return
        setProject((p.data as Project) ?? null)
        const vs = Array.isArray(v.data) ? (v.data as ShellVersion[]) : []
        setVersions(vs)
        const runs = vs.map((x) => x.latest_analysis_run_id).filter(Boolean) as string[]
        let toReview = 0
        let flagged = 0
        if (runs.length) {
          const f = await sb
            .from('finding_overview')
            .select('analytical_run_id, review_status, severity')
            .in('analytical_run_id', runs)
            .eq('category', 'statistical_anomaly')
            .limit(5000)
          const rows = (Array.isArray(f.data) ? f.data : []) as {
            analytical_run_id: string
            review_status: string
            severity: string
          }[]
          const cur = vs.find((x) => x.id === (scope.versionId ?? chosen)) ?? vs.find((x) => x.latest_analysis_run_id) ?? vs[0]
          const mine = rows.filter((x) => x.analytical_run_id === cur?.latest_analysis_run_id)
          flagged = mine.length
          toReview = mine.filter((x) => x.review_status === 'unreviewed' && (x.severity === 'very_high' || x.severity === 'high')).length
        }
        if (live)
          setCounts({
            toReview,
            flagged,
            reports: Array.isArray(r.data) ? r.data.length : 0,
            unverified: Array.isArray(c.data) ? c.data.length : 0,
          })
      } catch {
        if (live) setCounts({ toReview: 0, flagged: 0, reports: 0, unverified: 0 })
      }
    })()
    return () => {
      live = false
    }
  }, [projectId, tick, pathname, chosen, scope.versionId])

  const version = useMemo(
    () => versions.find((x) => x.id === (scope.versionId ?? chosen)) ?? versions.find((x) => x.latest_analysis_run_id) ?? versions[0] ?? null,
    [versions, scope.versionId, chosen],
  )

  // The page always stays in the same place in the tree (keyed), so it is never rebuilt when the rail appears.
  if (!projectId || !project)
    return (
      <ShellContext.Provider value={null}>
        <div className={projectId ? 'workspace' : 'no-workspace'}>
          <div key="main" className="workspace-main">
            {children}
          </div>
        </div>
      </ShellContext.Provider>
    )

  const pid = project.id
  const vid = version?.id
  const issues = version?.validation_summary?.issues_by_rule ? Object.values(version.validation_summary.issues_by_rule).reduce((a, b) => a + b, 0) : 0
  const canUpload = role === 'admin' || role === 'analyst'
  const steps: {
    n: number
    label: string
    to: string | null
    done: boolean
    note: string
  }[] = [
    {
      n: 1,
      label: 'Upload results',
      to: `/projects/${pid}/upload`,
      done: versions.length > 0,
      note: versions.length ? `${versions.length} version${versions.length > 1 ? 's' : ''}` : 'Add a CSV of results',
    },
    {
      n: 2,
      label: 'Match columns',
      to: vid ? `/versions/${vid}/mapping` : null,
      done: !!version?.mapping_saved_at,
      note: version?.mapping_saved_at ? 'Saved' : 'Say which column is which',
    },
    {
      n: 3,
      label: 'Check data',
      to: vid ? `/versions/${vid}/validation` : null,
      done: !!version?.validated_at,
      note: version?.validated_at ? `${issues.toLocaleString()} issue${issues === 1 ? '' : 's'} found` : 'Find rows with problems',
    },
    {
      n: 4,
      label: 'Analyse',
      to: vid ? `/versions/${vid}/analysis` : null,
      done: !!version?.latest_analysis_run_id,
      note: version?.latest_analysis_run_id ? `${(counts?.flagged ?? 0).toLocaleString()} unusual units` : 'Find unusual polling units',
    },
    {
      n: 5,
      label: 'Review findings',
      to: `/projects/${pid}/findings`,
      done: !!version?.latest_analysis_run_id && (counts?.toReview ?? 1) === 0,
      note: version?.latest_analysis_run_id
        ? counts?.toReview
          ? `${counts.toReview} to review`
          : 'All reviewed'
        : 'After analysis',
    },
    {
      n: 6,
      label: 'Report',
      to: `/projects/${pid}/reports`,
      done: (counts?.reports ?? 0) > 0,
      note: counts?.reports ? `${counts.reports} report${counts.reports > 1 ? 's' : ''}` : 'Share what you found',
    },
  ]
  const nextIndex = steps.findIndex((s) => !s.done)

  const explore = [
    {
      to: `/projects/${pid}`,
      label: 'Project overview',
      icon: 'home',
      end: true,
    },
    ...(vid
      ? [
          {
            to: `/versions/${vid}/analysis`,
            label: 'Dashboard',
            icon: 'chart',
            end: false,
          },
          {
            to: `/versions/${vid}/areas`,
            label: 'Areas',
            icon: 'grid',
            end: false,
          },
        ]
      : []),
    {
      to: `/projects/${pid}/findings`,
      label: 'Findings',
      icon: 'flag',
      end: false,
    },
    {
      to: `/projects/${pid}/sheets`,
      label: 'Result sheets',
      icon: 'camera',
      end: false,
    },
    {
      to: `/projects/${pid}/collation`,
      label: 'Collation check',
      icon: 'sum',
      end: false,
    },
    {
      to: `/projects/${pid}/claims`,
      label: `Claims${counts?.unverified ? ` (${counts.unverified} unverified)` : ''}`,
      icon: 'quote',
      end: false,
    },
    {
      to: `/projects/${pid}/reports`,
      label: 'Reports',
      icon: 'doc',
      end: false,
    },
    {
      to: `/orgs/${project.org_id}/members`,
      label: 'Team',
      icon: 'people',
      end: false,
    },
  ]

  return (
    <ShellContext.Provider value={{ project, version, versions, role, refresh }}>
      <div className="workspace">
        <aside className="rail" aria-label="Project navigation">
          <div className="rail-head">
            <Link to={`/projects/${pid}`} className="rail-project">
              {project.election_name}
            </Link>
            <span className="rail-year">{project.election_year}</span>
            {versions.length > 1 && (
              <label className="rail-version">
                <span>File</span>
                <select
                  value={version?.id ?? ''}
                  onChange={(e) => {
                    const id = e.target.value
                    setChosen(id)
                    const onVersionPage = matchPath({ path: '/versions/:versionId/:page' }, pathname)
                    if (onVersionPage) navigate(`/versions/${id}/${onVersionPage.params.page}`)
                  }}
                >
                  {versions.map((v) => (
                    <option key={v.id} value={v.id}>
                      v{v.version_number} · {v.original_filename}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {versions.length === 1 && version && (
              <p className="rail-file" title={version.original_filename}>
                v{version.version_number} · {version.original_filename}
              </p>
            )}
          </div>

          <nav aria-label="Workflow">
            <p className="rail-title">Workflow</p>
            <ol className="steps">
              {steps.map((s, i) => {
                const state = s.done ? 'done' : i === nextIndex ? 'next' : 'later'
                const disabled = !s.to || (s.n === 1 && !canUpload && !s.done)
                const body = (
                  <>
                    <span className="step-mark" aria-hidden="true">
                      {s.done ? '✓' : String(s.n).padStart(2, '0')}
                    </span>
                    <span className="step-text">
                      <span className="step-label">{s.label}</span>
                      <span className="step-note">{s.note}</span>
                    </span>
                    <span className="sr-only">{state === 'done' ? ' (done)' : state === 'next' ? ' (next step)' : ''}</span>
                  </>
                )
                return (
                  <li key={s.n} className={`step step-${state}`}>
                    {disabled ? (
                      <span className="step-link">{body}</span>
                    ) : (
                      <NavLink to={s.to!} className="step-link" end>
                        {body}
                      </NavLink>
                    )}
                  </li>
                )
              })}
            </ol>
          </nav>

          <nav aria-label="Explore">
            <p className="rail-title">Explore</p>
            <ul className="explore">
              {explore.map((x) => (
                <li key={x.to}>
                  <NavLink to={x.to} end={x.end}>
                    <Icon name={x.icon} />
                    {x.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>

          {(role === 'admin' || role === 'analyst') && (
            <NavLink to={`/projects/${pid}/assistant`} className="ask-cta">
              <Icon name="chat" />
              <span>
                <strong>Ask the data</strong>
                <span className="step-note">Ask questions in your own words</span>
              </span>
            </NavLink>
          )}
        </aside>
        <div key="main" className="workspace-main">
          {children}
        </div>
      </div>
    </ShellContext.Provider>
  )
}
