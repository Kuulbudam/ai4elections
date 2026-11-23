import type { Schemas } from './api'

export type Finding = Schemas['Finding']
export type FindingPage = Schemas['FindingPage']
export type Claim = Schemas['Claim']

export const CATEGORY: Record<string, string> = {
  statistical_anomaly: 'Unusual result',
  data_quality_issue: 'Data problem',
  cross_check: 'Doesn’t match sheet or collation',
  observation: 'Observation',
}
export const INDICATOR: Record<string, string> = { turnout_rate: 'Turnout', leading_party_share: 'Winning share' }
export const SEVERITY: Record<string, string> = { very_high: 'Very high', high: 'High', medium: 'Medium', low: 'Low', info: 'Info' }
export const DECISION: Record<string, string> = {
  unreviewed: 'Not reviewed',
  acknowledged: 'Acknowledged',
  needs_more_evidence: 'Needs more evidence',
  explained_by_data_error: 'Explained by a data error',
  explained_by_context: 'Explained by events on the day',
  escalated: 'Escalated',
}
/** Wording for the decision menu: what the reviewer does (stored as the same values). */
export const DECISION_ACTION: Record<string, string> = {
  acknowledged: 'Acknowledge',
  needs_more_evidence: 'Needs more evidence',
  explained_by_data_error: 'Explained by a data error',
  explained_by_context: 'Explained by events on the day',
  escalated: 'Escalate',
}
export const LEVEL: Record<string, string> = { ward: 'Ward coordinators', lga: 'LGA coordinators', state: 'State coordinators', admin: 'Admins' }
export const CLAIM_STATUS: Record<string, string> = {
  unverified_allegation: 'Unverified allegation',
  verified_fact: 'Verified fact',
}
export const EVIDENCE_KIND: Record<string, string> = { document_reference: 'Document reference', link: 'Link', file: 'File' }

export const pct = (s: string | null | undefined) => (s == null ? '—' : `${(Number(s) * 100).toFixed(1)}%`)

/** SHA-256 of a file, computed in the browser. */
export async function sha256File(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}
