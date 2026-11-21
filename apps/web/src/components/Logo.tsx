/** Election as a System (EaaS): a voter's inked thumbprint on a ballot paper. Same drawing as /logo.svg. */
export default function Logo({ size = 40, title }: { size?: number; title?: string }) {
  return (
    <svg
      className="logo"
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <rect width="64" height="64" rx="14" fill="#0b6b4f" />
      <rect x="12" y="7" width="40" height="50" rx="5" fill="#ffffff" />
      <path d="M18 14h16M18 19h10" stroke="#b9d8cb" strokeWidth="2.6" strokeLinecap="round" />
      <path d="M33.20 40.95A2.40 2.83 0 1 0 30.46 40.67 M30.19 32.62A5.30 6.25 0 1 0 34.65 33.08 M39.71 41.81A8.20 9.68 0 1 0 24.29 41.81 M27.30 46.43A8.20 9.68 0 0 0 36.10 46.88 M37.55 27.16A11.10 13.10 0 1 0 28.20 50.81 M32.97 51.55A11.10 13.10 0 0 0 41.09 30.99" fill="none" stroke="#5b3fa0" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}
