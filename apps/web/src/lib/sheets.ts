import type { Schemas } from './api'

export type Sheet = Schemas['ResultSheet']
export type Figures = Schemas['SheetFigures']
export type Check = Schemas['SheetCheck']

export const COUNT_FIELDS: { key: 'registered_voters' | 'accredited_voters' | 'rejected_votes' | 'valid_votes' | 'total_cast'; label: string }[] = [
  { key: 'registered_voters', label: 'Voters on the register' },
  { key: 'accredited_voters', label: 'Accredited voters' },
  { key: 'rejected_votes', label: 'Rejected ballots' },
  { key: 'valid_votes', label: 'Total valid votes' },
  { key: 'total_cast', label: 'Total votes cast (used ballot papers)' },
]

export const TEXT_FIELDS: { key: 'pu_code' | 'state' | 'lga' | 'ward' | 'polling_unit_name'; label: string }[] = [
  { key: 'pu_code', label: 'Polling unit code' },
  { key: 'state', label: 'State' },
  { key: 'lga', label: 'LGA' },
  { key: 'ward', label: 'Ward' },
  { key: 'polling_unit_name', label: 'Polling unit name' },
]

/** One-line outcome of a sheet for lists. */
export function sheetOutcome(s: Sheet): { text: string; tone: 'todo' | 'ok' | 'flag' | 'muted' } {
  if (s.status === 'read') return { text: 'Needs a person to confirm', tone: 'todo' }
  const failed = (s.checks ?? []).filter((c) => c.ok === false).length
  if (failed) return { text: `${failed} check${failed > 1 ? 's' : ''} need a look`, tone: 'flag' }
  const skipped = (s.checks ?? []).filter((c) => c.ok === null).length
  return skipped ? { text: `Passed (${skipped} not checked)`, tone: 'muted' } : { text: 'All checks passed', tone: 'ok' }
}

/**
 * Shrink a photo to at most `max` px on the long side and return JPEG base64 (no data: prefix).
 * Re-drawing through a canvas also drops the phone's location data before the photo leaves the device.
 * Falls back to the original bytes where the browser cannot decode images (the server re-encodes anyway).
 */
export async function photoToBase64(file: File, max = 1600): Promise<string> {
  if (typeof createImageBitmap === 'function') {
    try {
      const bmp = await createImageBitmap(file)
      const scale = Math.min(1, max / Math.max(bmp.width, bmp.height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(bmp.width * scale)
      canvas.height = Math.round(bmp.height * scale)
      const ctx = canvas.getContext('2d')
      if (ctx) {
        ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height)
        return canvas.toDataURL('image/jpeg', 0.85).split(',', 2)[1]
      }
    } catch {
      /* fall through to the raw file */
    }
  }
  const buf = new Uint8Array(await file.arrayBuffer())
  let bin = ''
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000))
  return btoa(bin)
}

export const sumParties = (f: Figures) => (f.party_votes ?? []).reduce((a, p) => a + (Number.isFinite(p.votes) ? p.votes : 0), 0)

export function readingLabel(status: Sheet['reading']['status']): string {
  return { read: 'Read by AI', unreadable: 'AI could not read it', no_model: 'Typed in (no AI set up)', failed: 'AI unavailable, typed in' }[status]
}
