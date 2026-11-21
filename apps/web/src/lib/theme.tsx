import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ThemeContext, useTheme, type Theme } from './themeContext'

const KEY = 'eaas.theme'

function saved(): Theme | null {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : null
  } catch {
    return null
  }
}

function system(): Theme {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** Light or dark. Follows the device until the person picks one; their choice is remembered on this browser. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<Theme | null>(saved)
  const [device, setDevice] = useState<Theme>(system)
  const theme = choice ?? device

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return
    const on = () => setDevice(mq.matches ? 'dark' : 'light')
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (choice) root.dataset.theme = choice
    else delete root.dataset.theme
  }, [choice])

  const toggle = useCallback(() => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setChoice(next)
    try {
      localStorage.setItem(KEY, next)
    } catch {
      /* private browsing: the choice lasts until the page is closed */
    }
  }, [theme])

  return <ThemeContext.Provider value={{ theme, toggle }}>{children}</ThemeContext.Provider>
}

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme()
  const dark = theme === 'dark'
  return (
    <button type="button" className={`theme-toggle ${className ?? ''}`} onClick={toggle}>
      {dark ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      )}
      <span>{dark ? 'Light mode' : 'Dark mode'}</span>
    </button>
  )
}
