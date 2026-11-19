import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ErrorBoundary from './ErrorBoundary'

function Boom(): never {
  throw new Error('kaboom')
}

afterEach(() => vi.restoreAllMocks())

describe('ErrorBoundary', () => {
  it('shows a message instead of a blank page when a page crashes', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary resetKey="/a">
        <Boom />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('heading', { name: 'This page hit a problem' })).toBeTruthy()
    expect(screen.getByText(/kaboom/)).toBeTruthy()
  })

  it('clears the message when the user moves to another page', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(
      <ErrorBoundary resetKey="/a">
        <Boom />
      </ErrorBoundary>,
    )
    rerender(
      <ErrorBoundary resetKey="/b">
        <p>fine</p>
      </ErrorBoundary>,
    )
    expect(screen.getByText('fine')).toBeTruthy()
  })
})
