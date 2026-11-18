import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError, type Schemas } from '../lib/api'
import { getSupabase } from '../lib/supabase'
import { canUpload, useRole } from '../lib/useRole'

type Field = Schemas['CanonicalField']
const REQUIRED: Field[] = ['pu_code', 'state', 'lga', 'ward', 'registered_voters', 'accredited_voters', 'valid_votes']
const OPTIONAL: Field[] = ['rejected_votes', 'total_cast', 'latitude', 'longitude']
const LABELS: Record<Field, string> = {
  pu_code: 'Polling unit code',
  state: 'State',
  lga: 'LGA',
  ward: 'Ward',
  registered_voters: 'Registered voters',
  accredited_voters: 'Accredited voters',
  valid_votes: 'Valid votes',
  rejected_votes: 'Rejected votes (optional)',
  total_cast: 'Total votes cast (optional)',
  latitude: 'Latitude (optional)',
  longitude: 'Longitude (optional)',
}
const SYNONYMS: Record<Field, string[]> = {
  pu_code: ['pucode', 'pollingunitcode', 'pollingunit', 'puid', 'pu'],
  state: ['state'],
  lga: ['lga', 'localgovernment', 'localgovernmentarea'],
  ward: ['ward', 'registrationarea'],
  registered_voters: ['registeredvoters', 'registered', 'regvoters'],
  accredited_voters: ['accreditedvoters', 'accredited'],
  valid_votes: ['validvotes', 'totalvalidvotes'],
  rejected_votes: ['rejectedvotes', 'rejected'],
  total_cast: ['totalvotescast', 'totalcast', 'totalvotes', 'votescast'],
  latitude: ['latitude', 'lat'],
  longitude: ['longitude', 'lon', 'lng', 'long'],
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')

// eslint-disable-next-line react-refresh/only-export-components
export function suggestMapping(columns: string[]) {
  const fields: Partial<Record<Field, string>> = {}
  const used = new Set<string>()
  for (const f of [...REQUIRED, ...OPTIONAL]) {
    const hit = columns.find((c) => !used.has(c) && SYNONYMS[f].includes(norm(c)))
    if (hit) {
      fields[f] = hit
      used.add(hit)
    }
  }
  const parties = columns
    .filter((c) => !used.has(c))
    .map((c) => {
      const m = c.trim().match(/^(?:votes?\s*(?:for\s*)?)?([A-Za-z0-9_]{1,16})$/i)
      return m && /^votes?\b/i.test(c.trim()) ? { source_column: c, party_code: m[1].toUpperCase(), include: true } : { source_column: c, party_code: '', include: false }
    })
  return { fields, parties }
}

type PartyRow = { source_column: string; party_code: string; include: boolean }

export default function Mapping() {
  const { versionId = '' } = useParams()
  const [version, setVersion] = useState<{
    org_id: string
    project_id: string
    original_filename: string
    version_number: number
    status: string
    header: { columns?: string[] } | null
    mapping: Schemas['ColumnMapping'] | null
  } | null | undefined>(undefined)
  const role = useRole(version?.org_id)
  const [fields, setFields] = useState<Partial<Record<Field, string>>>({})
  const [parties, setParties] = useState<PartyRow[]>([])
  const [templates, setTemplates] = useState<{ id: string; name: string; mapping: Schemas['ColumnMapping'] }[]>([])
  const [allParties, setAllParties] = useState(true)
  const [templateName, setTemplateName] = useState('')
  const [issues, setIssues] = useState<Schemas['MappingIssue'][] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const columns = useMemo(() => version?.header?.columns ?? [], [version])

  useEffect(() => {
    const sb = getSupabase()
    if (!sb) return
    sb.from('dataset_versions')
      .select('org_id, project_id, original_filename, version_number, status, header, mapping')
      .eq('id', versionId)
      .maybeSingle()
      .then(({ data }) => {
        setVersion(data ?? null)
        if (!data) return
        const cols: string[] = data.header?.columns ?? []
        if (data.mapping) applyMapping(data.mapping, cols)
        else {
          const s = suggestMapping(cols)
          setFields(s.fields)
          setParties(s.parties)
        }
        sb.from('mapping_templates')
          .select('id, name, mapping')
          .eq('org_id', data.org_id)
          .order('name')
          .then(({ data: t }) => setTemplates(t ?? []))
      })
  }, [versionId])

  function applyMapping(m: Schemas['ColumnMapping'], cols: string[]) {
    const f: Partial<Record<Field, string>> = {}
    for (const [k, v] of Object.entries(m.fields ?? {})) if (cols.includes(v as string)) f[k as Field] = v as string
    const byCol = new Map(m.party_columns.map((p) => [p.source_column, p.party_code]))
    setFields(f)
    setAllParties(m.party_columns_complete !== false)
    setParties(cols.filter((c) => !Object.values(f).includes(c)).map((c) => ({ source_column: c, party_code: byCol.get(c) ?? '', include: byCol.has(c) })))
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setIssues(null)
    try {
      const res = await api<Schemas['ColumnMappingSaved']>('PUT', `/dataset-versions/${versionId}/mapping`, {
        schema_version: '1.1.0',
        fields: Object.fromEntries(Object.entries(fields).filter(([, v]) => v)),
        party_columns: parties.filter((p) => p.include).map((p) => ({ source_column: p.source_column, party_code: p.party_code.trim().toUpperCase() })),
        party_columns_complete: allParties,
        save_as_template_name: templateName.trim() || null,
      })
      setIssues(res.issues)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Saving failed. Please try again.')
    }
    setBusy(false)
  }

  if (version === undefined) return <p className="muted">Loading…</p>
  if (version === null) return <p className="empty">Dataset version not found, or you are not a member of its organisation.</p>
  if (version.status !== 'ingested') return <p className="empty">This version has not finished ingesting yet.</p>
  const readOnly = !canUpload(role)
  const partyCodeInvalid = (p: PartyRow) => p.include && !/^[A-Z0-9_]{1,16}$/.test(p.party_code.trim().toUpperCase())

  return (
    <section className="page" aria-labelledby="mapping-heading">
      <p>
        <Link to={`/projects/${version.project_id}`}>← Project</Link>
      </p>
      <h1 id="mapping-heading">
        Map columns — {version.original_filename} (v{version.version_number})
      </h1>
      <p className="muted small">Tell the system which column in your file holds each standard field. Nothing in the original file changes.</p>
      {templates.length > 0 && !readOnly && (
        <label className="inline-label">
          Apply a saved template
          <select defaultValue="" onChange={(e) => {
            const t = templates.find((x) => x.id === e.target.value)
            if (t) applyMapping(t.mapping, columns)
          }}>
            <option value="" disabled>
              Choose…
            </option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <form onSubmit={save} className="form" aria-label="Column mapping">
        <fieldset className="card" disabled={readOnly || busy}>
          <legend>Standard fields</legend>
          <div className="grid2">
            {[...REQUIRED, ...OPTIONAL].map((f) => (
              <label key={f}>
                {LABELS[f]}
                <select value={fields[f] ?? ''} onChange={(e) => setFields({ ...fields, [f]: e.target.value || undefined })}>
                  <option value="">— not mapped —</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="card" disabled={readOnly || busy}>
          <legend>Party vote columns (at least two)</legend>
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Include</th>
                <th scope="col">Column in file</th>
                <th scope="col">Party code</th>
              </tr>
            </thead>
            <tbody>
              {parties.map((p, i) => (
                <tr key={p.source_column}>
                  <td>
                    <input type="checkbox" aria-label={`Include ${p.source_column}`} checked={p.include} onChange={(e) => setParties(parties.map((x, j) => (j === i ? { ...x, include: e.target.checked } : x)))} />
                  </td>
                  <td>{p.source_column}</td>
                  <td>
                    <input aria-label={`Party code for ${p.source_column}`} aria-invalid={partyCodeInvalid(p)} value={p.party_code} maxLength={16} onChange={(e) => setParties(parties.map((x, j) => (j === i ? { ...x, party_code: e.target.value } : x)))} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <label className="check">
            <input type="checkbox" checked={allParties} onChange={(e) => setAllParties(e.target.checked)} />
            These columns cover every party on the result sheet
          </label>
          <p className="muted small">
            {allParties
              ? 'Party votes must add up exactly to valid votes.'
              : 'Some parties are not in this file, so the listed parties’ votes only need to stay within valid votes.'}
          </p>
        </fieldset>
        {!readOnly && (
          <div className="grid2">
            <label>
              Save as template (optional)
              <input value={templateName} maxLength={120} onChange={(e) => setTemplateName(e.target.value)} placeholder="e.g. Standard results layout" />
            </label>
            <button type="submit" className="primary" disabled={busy || parties.some(partyCodeInvalid)}>
              {busy ? 'Saving…' : 'Save mapping'}
            </button>
          </div>
        )}
        {readOnly && <p className="muted small">Only admins and analysts can change the mapping.</p>}
      </form>
      {issues && issues.length === 0 && (
        <p className="notice">
          Mapping saved. All required fields are mapped. <Link to={`/versions/${versionId}/validation`}>Go to validation</Link>
        </p>
      )}
      {issues && issues.length > 0 && (
        <div className="card" role="alert">
          <p>Mapping saved, but it is not complete yet:</p>
          <ul>
            {issues.map((i, n) => (
              <li key={n}>
                <code>{i.rule_id}</code> {i.message}
              </li>
            ))}
          </ul>
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
