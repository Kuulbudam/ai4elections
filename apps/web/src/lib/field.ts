import type { Kind } from './outbox'

export const KIND_LABEL: Record<Kind, string> = {
  arrival: 'Polls opened',
  accreditation: 'Accreditation count',
  incident: 'Incident',
  result: 'Result sheet',
  closing: 'Closing',
}
export const CATEGORY: Record<string, string> = {
  bvas_failure: 'BVAS not working',
  late_opening: 'Polls opened late',
  materials_missing: 'Materials missing',
  violence: 'Violence',
  intimidation: 'Intimidation of voters',
  vote_buying: 'Vote buying',
  ballot_snatching: 'Ballot box snatched',
  agents_denied: 'Party agents denied entry',
  observers_denied: 'Observers denied entry',
  other: 'Something else',
}

