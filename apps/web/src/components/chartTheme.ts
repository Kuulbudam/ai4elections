// Chart colours: validated reference palette (dataviz skill). Severity uses the reserved status steps.
export const C = {
  series: '#2a78d6',
  muted: '#b8c4d2',
  grid: '#e4e7eb',
  axis: '#c3c8cf',
  ink: '#1d232b',
  ink2: '#596371',
  sev: { medium: '#fab219', high: '#ec835a', very_high: '#d03b3b' } as Record<string, string>,
}
export const SEV_LABEL: Record<string, string> = { medium: 'Medium', high: 'High', very_high: 'Very high' }
