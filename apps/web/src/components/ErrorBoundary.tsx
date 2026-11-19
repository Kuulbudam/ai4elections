import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode; resetKey?: string }
type State = { error: Error | null; key?: string }

/**
 * Catches a crash in any page so the user sees a message and a way out, never a blank screen.
 * It resets itself when `resetKey` (the current path) changes.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, key: this.props.resetKey }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page crashed:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <section className="page" role="alert" aria-labelledby="crash-heading">
        <h1 id="crash-heading">This page hit a problem</h1>
        <p>Something on this page failed to display. Your data is safe; nothing was changed.</p>
        <p className="small muted">Details: {this.state.error.message}</p>
        <p>
          <button type="button" className="primary" onClick={() => this.setState({ error: null })}>
            Try again
          </button>{' '}
          <a href="/">Go to the dashboard</a>
        </p>
      </section>
    )
  }
}
