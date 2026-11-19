import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { friendlyError, getSupabase } from '../lib/supabase'
import { canUpload, useRole } from '../lib/useRole'
import ReferenceList from './ReferenceList'
import { formatBytes, LEVELS } from '../lib/format'

type Project = {
  id: string
  org_id: string
  election_name: string
  election_year: number
  country: string
  election_level: string
  description: string | null
}
type Version = {
  id: string
  dataset_id: string
  version_number: number
  status: string
  original_filename: string
  size_bytes: number | null
  sha256: string | null
  row_count: number | null
  created_at: string
  mapping_saved_at: string | null
  duplicate_of_version_id: string | null
  archived_at: string | null
  validated_at: string | null
}

const STATUS_LABEL: Record<string, string> = {
  awaiting_upload: 'Waiting for file',
  uploaded: 'Queued for ingestion',
  ingesting: 'Ingesting…',
  ingested: 'Ingested',
  ingest_failed: 'Ingestion failed',
}

export default function ProjectOverview() {
  const { projectId = '' } = useParams()
  const [project, setProject] = useState<Project | null | undefined>(undefined)
  const [datasets, setDatasets] = useState<{ id: string; name: string }[]>([])
  const [versions, setVersions] = useState<Version[]>([])
  const [error, setError] = useState<string | null>(null)
  const role = useRole(project?.org_id)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const [p, ds, vs] = await Promise.all([
      sb.from('projects').select('id, org_id, election_name, election_year, country, election_level, description').eq('id', projectId).maybeSingle(),
      sb.from('datasets').select('id, name').eq('project_id', projectId).order('created_at'),
      sb
        .from('dataset_versions')
        .select('id, dataset_id, version_number, status, original_filename, size_bytes, sha256, row_count, created_at, mapping_saved_at, duplicate_of_version_id, archived_at, validated_at')
        .eq('project_id', projectId)
        .order('version_number', { ascending: false }),
    ])
    const err = p.error ?? ds.error ?? vs.error
    if (err) setError(friendlyError(err))
    setProject((p.data as Project) ?? null)
    setDatasets(ds.data ?? [])
    setVersions((vs.data as Version[]) ?? [])
  }, [projectId])

  useEffect(() => {
    // load() only sets state after awaiting the network, never synchronously
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  if (project === undefined) return <p className="muted">Loading…</p>
  if (project === null)
    return (
      <section className="page">
        <p className="empty">Project not found, or you are not a member of its organisation.</p>
        <Link to="/">Back to organisations</Link>
      </section>
    )

  return (
    <section className="page" aria-labelledby="project-heading">
      <p>
        <Link to={`/orgs/${project.org_id}/projects`}>← Projects</Link>
      </p>
      <div className="row-between">
        <h1 id="project-heading">{project.election_name}</h1>
        <span>
          <Link to={`/projects/${project.id}/findings`}>Findings</Link> · <Link to={`/projects/${project.id}/claims`}>Claims register</Link>
          {(role === 'admin' || role === 'analyst') && (
            <>
              {' '}
              · <Link to={`/projects/${project.id}/assistant`}>Ask the data</Link>
            </>
          )}
        </span>
        {canUpload(role) && (
          <Link className="button primary" to={`/projects/${project.id}/upload`}>
            Upload dataset
          </Link>
        )}
      </div>
      <dl className="facts card">
        <dt>Year</dt>
        <dd>{project.election_year}</dd>
        <dt>Country</dt>
        <dd>{project.country}</dd>
        <dt>Level</dt>
        <dd>{LEVELS.find(([v]) => v === project.election_level)?.[1] ?? project.election_level}</dd>
        {project.description && (
          <>
            <dt>Description</dt>
            <dd>{project.description}</dd>
          </>
        )}
      </dl>

      <ReferenceList projectId={project.id} orgId={project.org_id} canEdit={canUpload(role)} />

      <h3>Datasets and versions</h3>
      {datasets.length === 0 ? (
        <p className="empty">No datasets yet.{canUpload(role) ? ' Upload a CSV of polling-unit results to start.' : ''}</p>
      ) : (
        datasets.map((d) => (
          <div key={d.id} className="card">
            <h4>{d.name}</h4>
            <table className="table compact">
              <thead>
                <tr>
                  <th scope="col">Version</th>
                  <th scope="col">File</th>
                  <th scope="col">Status</th>
                  <th scope="col">Rows</th>
                  <th scope="col">Size</th>
                  <th scope="col">SHA-256</th>
                  <th scope="col">Uploaded</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {versions
                  .filter((v) => v.dataset_id === d.id)
                  .map((v) => (
                    <tr key={v.id} className={v.archived_at ? 'archived' : undefined}>
                      <td>v{v.version_number}</td>
                      <td>
                        {v.original_filename}
                        {v.duplicate_of_version_id && <span className="badge warn" title="Same SHA-256 as an earlier version">duplicate file</span>}
                        {v.archived_at && <span className="badge">archived</span>}
                      </td>
                      <td>
                        <span className={`status status-${v.status}`}>{STATUS_LABEL[v.status] ?? v.status}</span>
                      </td>
                      <td>{v.row_count?.toLocaleString() ?? '—'}</td>
                      <td>{formatBytes(v.size_bytes)}</td>
                      <td>
                        {v.sha256 ? (
                          <code title={v.sha256}>{v.sha256.slice(0, 12)}…</code>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td>{new Date(v.created_at).toLocaleString()}</td>
                      <td>
                        {v.status === 'ingested' && !v.archived_at && (
                          <>
                            <Link to={`/versions/${v.id}/mapping`}>{v.mapping_saved_at ? 'Mapping' : 'Map columns'}</Link>
                            {v.mapping_saved_at && (
                              <>
                                <span aria-hidden="true">&ensp;</span>
                                <Link to={`/versions/${v.id}/validation`}>{v.validated_at ? 'Validation' : 'Validate'}</Link>
                                {v.validated_at && (
                                  <>
                                    <span aria-hidden="true">&ensp;</span>
                                    <Link to={`/versions/${v.id}/analysis`}>Analysis</Link>
                                    <span aria-hidden="true">&ensp;</span>
                                    <Link to={`/versions/${v.id}/areas`}>Areas</Link>
                                  </>
                                )}
                              </>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ))
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  )
}
