import { useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, sleep, uploadToSignedUrl, type Job, type Schemas } from '../lib/api'
import { friendlyError, getSupabase } from '../lib/supabase'
import { canUpload, useRole } from '../lib/useRole'
import { formatBytes } from '../lib/format'

const MAX_BYTES = 25 * 1024 * 1024
type Step = 'idle' | 'registering' | 'uploading' | 'finalizing' | 'ingesting' | 'done' | 'failed'

const STEP_TEXT: Record<Step, string> = {
  idle: '',
  registering: 'Registering the upload…',
  uploading: 'Uploading the file to private storage…',
  finalizing: 'Computing the SHA-256 fingerprint on the server…',
  ingesting: 'Ingesting rows (a worker picks this up, usually within a minute)…',
  done: 'Done.',
  failed: 'Stopped.',
}

const JOB_ERRORS: Record<string, string> = {
  HEADER_NOT_FOUND: 'No header row was found. The first row must contain column names.',
  HEADER_DUPLICATE_COLUMNS: 'Two columns have the same name. Rename them and upload again.',
  HEADER_EMPTY_COLUMN: 'A column in the header row has no name.',
  NO_DATA_ROWS: 'The file has a header but no data rows.',
  MALFORMED_CSV: 'The file is not valid CSV (check for an unclosed quote).',
  ENCODING_NOT_UTF8: 'The file is not UTF-8 text. Save it as "CSV UTF-8" and upload again.',
  BINARY_CONTENT: 'The file is not a text CSV file.',
  CELL_TOO_LONG: 'A cell is too long (over 1,000 characters).',
  TOO_MANY_COLUMNS: 'The file has more than 200 columns.',
  TOO_MANY_ROWS: 'The file has more than 200,000 rows.',
  INTEGRITY_MISMATCH: 'The stored file no longer matches its fingerprint. Upload it again.',
}

export default function Upload() {
  const { projectId = '' } = useParams()
  const [project, setProject] = useState<{ org_id: string; election_name: string } | null>(null)
  const [datasets, setDatasets] = useState<{ id: string; name: string }[]>([])
  const role = useRole(project?.org_id)
  const [datasetId, setDatasetId] = useState('')
  const [datasetName, setDatasetName] = useState('')
  const [sourceType, setSourceType] = useState('synthetic')
  const [sourceDescription, setSourceDescription] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [step, setStep] = useState<Step>('idle')
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ versionId: string; sha256: string; duplicate: boolean; rows?: number } | null>(null)

  useEffect(() => {
    const sb = getSupabase()
    if (!sb) return
    void Promise.all([
      sb.from('projects').select('org_id, election_name').eq('id', projectId).maybeSingle(),
      sb.from('datasets').select('id, name').eq('project_id', projectId).order('created_at'),
    ]).then(([p, ds]) => {
      setProject(p.data ?? null)
      setDatasets(ds.data ?? [])
    })
  }, [projectId])

  function chooseFile(f: File | null) {
    setError(null)
    if (f && !f.name.toLowerCase().endsWith('.csv')) {
      setError('Choose a .csv file.')
      return setFile(null)
    }
    if (f && f.size > MAX_BYTES) {
      setError('The file is larger than 25 MB.')
      return setFile(null)
    }
    setFile(f)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setError(null)
    setResult(null)
    try {
      setStep('registering')
      const up = await api<Schemas['UploadResponse']>(
        'POST',
        '/uploads',
        {
          project_id: projectId,
          dataset_id: datasetId || null,
          dataset_name: datasetId ? null : datasetName.trim(),
          original_filename: file.name,
          size_bytes: file.size,
          content_type: 'text/csv',
          source_type: sourceType,
          source_description: sourceDescription.trim(),
        },
        { 'Idempotency-Key': crypto.randomUUID() },
      )
      setStep('uploading')
      await uploadToSignedUrl(up.upload_url, file, setProgress)
      setStep('finalizing')
      const fin = await api<Schemas['FinalizeResponse']>('POST', `/dataset-versions/${up.dataset_version_id}/finalize`)
      setResult({ versionId: up.dataset_version_id, sha256: fin.sha256, duplicate: !!fin.duplicate_of_version_id })
      setStep('ingesting')
      let job: Job = fin.ingestion_job
      const deadline = Date.now() + 10 * 60 * 1000
      while ((job.status === 'queued' || job.status === 'running') && Date.now() < deadline) {
        await sleep(3000)
        job = await api<Job>('GET', `/jobs/${job.id}`)
      }
      if (job.status === 'succeeded') {
        const { data } = await getSupabase()!.from('dataset_versions').select('row_count').eq('id', up.dataset_version_id).maybeSingle()
        setResult((r) => (r ? { ...r, rows: data?.row_count ?? undefined } : r))
        setStep('done')
      } else if (job.status === 'failed') {
        setError(JOB_ERRORS[job.error_code ?? ''] ?? 'Ingestion failed. Please try again.')
        setStep('failed')
      } else {
        setError('Ingestion is taking longer than expected. It will continue in the background; check the project page.')
        setStep('failed')
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : friendlyError(err as { message?: string }))
      setStep('failed')
    }
  }

  const busy = step !== 'idle' && step !== 'done' && step !== 'failed'
  if (project && role && !canUpload(role)) {
    return <p className="empty">Only admins and analysts can upload datasets.</p>
  }

  return (
    <section className="page" aria-labelledby="upload-heading">
      <p>
        <Link to={`/projects/${projectId}`}>← {project?.election_name ?? 'Project'}</Link>
      </p>
      <h1 id="upload-heading">Upload results CSV</h1>
      <p className="muted small">
        The original file is kept unchanged in private storage and fingerprinted with SHA-256. Every upload creates a new version; nothing is
        overwritten. Use synthetic data only.
      </p>
      <form onSubmit={submit} className="form card" aria-label="Upload dataset">
        <label>
          Dataset
          <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} disabled={busy}>
            <option value="">New dataset…</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} (new version)
              </option>
            ))}
          </select>
        </label>
        {!datasetId && (
          <label>
            New dataset name
            <input required maxLength={200} value={datasetName} onChange={(e) => setDatasetName(e.target.value)} disabled={busy} />
          </label>
        )}
        <div className="grid2">
          <label>
            Source type
            <select value={sourceType} onChange={(e) => setSourceType(e.target.value)} disabled={busy}>
              <option value="synthetic">Synthetic</option>
              <option value="official_publication">Official publication</option>
              <option value="observer_collation">Observer collation</option>
              <option value="other">Other</option>
            </select>
          </label>
          <label>
            Source description (provenance)
            <input required maxLength={2000} value={sourceDescription} onChange={(e) => setSourceDescription(e.target.value)} disabled={busy} placeholder="Where this file came from" />
          </label>
        </div>
        <label>
          CSV file (max 25 MB)
          <input type="file" accept=".csv,text/csv" required onChange={(e) => chooseFile(e.target.files?.[0] ?? null)} disabled={busy} />
        </label>
        {file && (
          <p className="muted small">
            {file.name} ({formatBytes(file.size)})
          </p>
        )}
        <button type="submit" className="primary" disabled={busy || !file}>
          {busy ? 'Working…' : 'Upload and ingest'}
        </button>
      </form>

      {step !== 'idle' && (
        <div className="card" aria-live="polite">
          <p>{STEP_TEXT[step]}</p>
          {step === 'uploading' && <progress max={1} value={progress} aria-label="Upload progress" />}
          {result && (
            <dl className="facts">
              <dt>SHA-256</dt>
              <dd>
                <code>{result.sha256}</code>
              </dd>
              {result.rows !== undefined && (
                <>
                  <dt>Rows preserved</dt>
                  <dd>{result.rows.toLocaleString()}</dd>
                </>
              )}
            </dl>
          )}
          {result?.duplicate && <p className="notice warn">This file is identical to an earlier version of this dataset.</p>}
          {step === 'done' && result && (
            <Link className="button primary" to={`/versions/${result.versionId}/mapping`}>
              Map columns
            </Link>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  )
}
