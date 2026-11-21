import { useTheme } from '../lib/themeContext'

// Chart colours: validated reference palette (dataviz skill), with its dark-mode steps. Severity uses the reserved
// status steps, which stay the same in both themes and always come with a text label.
const SEV = { medium: '#fab219', high: '#ec835a', very_high: '#d03b3b' } as Record<string, string>
const LIGHT = { series: '#2a78d6', muted: '#b8c4d2', grid: '#e4e7eb', axis: '#c3c8cf', ink: '#13201b', ink2: '#55625d', surface: '#fcfcfb', hover: '#f3f5f7', sev: SEV }
const DARK = { series: '#3987e5', muted: '#4a5a6b', grid: '#2c3632', axis: '#46534d', ink: '#e6ebe8', ink2: '#a7b3ae', surface: '#18201d', hover: '#222b27', sev: SEV }
export type ChartColours = typeof LIGHT

/** The chart colours for the current theme (charts re-draw when it changes). */
export function useChartColours(): ChartColours {
  return useTheme().theme === 'dark' ? DARK : LIGHT
}
export const SEV_LABEL: Record<string, string> = { medium: 'Medium', high: 'High', very_high: 'Very high' }
