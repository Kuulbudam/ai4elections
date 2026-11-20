import { useCallback, useEffect, useState, type ChangeEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { photoToBase64, readingLabel, sheetOutcome, type Sheet } from '../lib/sheets'
import { getSupabase } from '../lib/supabase'
import { useRole } from '../lib/useRole'

type Version = { id: string; original_filename: string; version_number: number }

export default function Sheets() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState<{ org_id: string; election_name: string } | null>(null)
  const [versions, setVersions] = useState<Version[]>([])
  const [version, setVersion] = useState('')
  const [sheets, setSheets] = useState<Sheet[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const role = useRole(project?.org_id)

  const load = useCallback(async () => {
    const sb = getSupabase()
    if (!sb) return
    const [p, v] = await Promise.all([
      sb.from('projects').select('org_id, election_name').eq('id', projectId).maybeSingle(),
      sb
        .from('dataset_versions')
        .select('id, original_filename, version_number')
        .eq('project_id', projectId)
        .not('validated_at', 'is', null)
        .is('archived_at', null)
        .order('created_at', { ascending: false }),
    ])
    setProject(p.data)
    const vs = Array.isArray(v.data) ? (v.data as Version[]) : []
    setVersions(vs)
    setVersion((cur) => cur || vs[0]?.id || '')
    try {
      setSheets((await api<{ items: Sheet[] }>('GET', `/sheets?project_id=${projectId}`)).items)
    } catch (err) {
      setSheets([])
      setError(err instanceof ApiError ? err.message : 'Could not load the result sheets.')
    }
  }, [projectId])

  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
  }, [load])

  async function onPhoto(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || !version) return
    setBusy(true)
    setError(null)
    try {
      const image_base64 = await photoToBase64(file)
      const s = await api<Sheet>('POST', '/sheets', { project_id: projectId, dataset_version_id: version, image_base64 }, { 'Idempotency-Key': crypto.randomUUID() })
      navigate(`/projects/${projectId}/sheets/${s.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The photo could not be uploaded. Try again.')
      setBusy(false)
    }
  }

  const canUpload = role === 'admin' || role === 'analyst' || role === 'researcher'
  const toConfirm = (sheets ?? []).filter((s) => s.status === 'read').length
  return (
    <section className="page" aria-labelledby="sheets-heading">
      <h1 id="sheets-heading">Result sheets{project ? `: ${project.election_name}` : ''}</h1>
      <p className="page-intro">
        Upload a photo of a polling-unit result sheet (Form EC8A). The AI reads the figures; you check them against the photo and confirm. The platform then
        checks the sheet's own sums and compares it with the uploaded results. Any mismatch goes to the findings queue for review.
      </p>

      {canUpload && (
        <div className="card sheet-upload">
          <h2>Check a result sheet</h2>
          {versions.length === 0 ? (
            <p className="muted">Upload and validate the results first. Sheets are compared with a validated dataset.</p>
          ) : (
            <>
              {versions.length > 1 && (
                <label>
                  Compare with
                  <select value={version} onChange={(e) => setVersion(e.target.value)}>
                    {versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number}: {v.original_filename}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="file-drop">
                <span>{busy ? 'Reading the sheet… this takes a few seconds' : 'Take a photo or choose one'}</span>
                <span className="small muted">JPEG, PNG or WebP. Get the whole sheet in the frame, flat and in good light. Location data is removed.</span>
                <input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" disabled={busy} onChange={(e) => void onPhoto(e)} />
              </label>
            </>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <h2>
        Uploaded sheets{sheets ? ` (${sheets.length})` : ''}
        {toConfirm > 0 && <span className="badge badge-unverified">{toConfirm} to confirm</span>}
      </h2>
      {sheets === null ? (
        <p className="muted">Loading…</p>
      ) : sheets.length === 0 ? (
        <p className="empty">No result sheets yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Uploaded</th>
                <th scope="col">Polling unit</th>
                <th scope="col">AI reading</th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {sheets.map((s) => {
                const o = sheetOutcome(s)
                return (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/projects/${projectId}/sheets/${s.id}`}>{new Date(s.created_at).toLocaleString()}</Link>
                    </td>
                    <td>{(s.confirmed ?? s.reading.figures).pu_code ?? <span className="muted">not read</span>}</td>
                    <td>{readingLabel(s.reading.status)}</td>
                    <td>
                      <span className={`outcome outcome-${o.tone}`}>{o.text}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

