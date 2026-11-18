export const LEVELS = [
  ['presidential', 'Presidential'],
  ['governorship', 'Governorship'],
  ['senate', 'Senate'],
  ['house_of_representatives', 'House of Representatives'],
  ['state_assembly', 'State Assembly'],
  ['local_government', 'Local Government'],
  ['other', 'Other'],
] as const

export function formatBytes(n: number | null): string {
  if (n == null) return '—'
  return n >= 1e6 ? `${(n / 1e6).toFixed(2)} MB` : `${(n / 1e3).toFixed(1)} kB`
}
