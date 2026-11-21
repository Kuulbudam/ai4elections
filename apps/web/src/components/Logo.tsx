/** EaaS lettermark: an "E" made of three result bars, with one blue square. The bars take the text colour, so the
 *  mark works on light and dark backgrounds. /logo.svg is the same mark on a charcoal tile (favicon, sharing). */
export default function Logo({ size = 32, title }: { size?: number; title?: string }) {
  return (
    <svg
      className="logo"
      width={size}
      height={size}
      viewBox="12 11 40 42"
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <g fill="currentColor">
        <rect x="15" y="14" width="34" height="7" rx="1.5" />
        <rect x="15" y="28.5" width="24" height="7" rx="1.5" />
        <rect x="15" y="43" width="30" height="7" rx="1.5" />
        <rect x="15" y="14" width="7" height="36" rx="1.5" />
      </g>
      <rect x="42" y="28.5" width="7" height="7" rx="1.5" fill="#3e6ae1" />
    </svg>
  )
}

/** The wordmark: the lettermark beside the spaced letters EAAS, with the full name underneath when asked. */
export function Wordmark({ size = 30, full = true }: { size?: number; full?: boolean }) {
  return (
    <span className="wordmark">
      <Logo size={size} />
      <span className="wordmark-text">
        <span className="wordmark-letters">EAAS</span>
        {full && <span className="wordmark-name">Election as a System</span>}
      </span>
    </span>
  )
}
