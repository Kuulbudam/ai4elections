import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ThemeProvider, ThemeToggle } from './theme'

describe('light and dark mode', () => {
  afterEach(() => {
    localStorage.clear()
    delete document.documentElement.dataset.theme
  })

  it('switches between light and dark and remembers the choice', () => {
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Dark mode' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('eaas.theme')).toBe('dark')
    fireEvent.click(screen.getByRole('button', { name: 'Light mode' }))
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('starts in the theme chosen last time', () => {
    localStorage.setItem('eaas.theme', 'dark')
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    )
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(screen.getByRole('button', { name: 'Light mode' })).toBeTruthy()
  })
})
