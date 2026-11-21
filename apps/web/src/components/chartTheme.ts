import { useTheme } from '../lib/themeContext'

// Chart colours: validated reference palette (dataviz skill), with its dark-mode steps. Severity uses the reserved
// status steps, which stay the same in both themes and always come with a text label.
const SEV = { medium: '#fab219', high: '#ec835a', very_high: '#d03b3b' } as Record<string, string>
const LIGHT = { series: '#3e6ae1', muted: '#c4cbd9', grid: '#ececec', axis: '#d0d1d2', ink: '#171a20', ink2: '#5c5e62', surface: '#ffffff', hover: '#f4f4f4', sev: SEV }
const DARK = { series: '#5b82ee', muted: '#3d4556', grid: '#2a2d33', axis: '#45484f', ink: '#eeeeee', ink2: '#a2a3a5', surface: '#171a20', hover: '#22252b', sev: SEV }
export type ChartColours = typeof LIGHT

/** The chart colours for the current theme (charts re-draw when it changes). */
export function useChartColours(): ChartColours {
  return useTheme().theme === 'dark' ? DARK : LIGHT
}
export const SEV_LABEL: Record<string, string> = { medium: 'Medium', high: 'High', very_high: 'Very high' }
