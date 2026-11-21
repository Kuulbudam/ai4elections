export function Icon({ name }: { name: string }) {
  const d: Record<string, string> = {
    home: 'M3 10.5 12 3l9 7.5V21H3z M9 21v-6h6v6',
    chart: 'M4 20V10 M10 20V4 M16 20v-8 M22 20H2',
    grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    flag: 'M5 21V4 M5 4h11l-2 4 2 4H5',
    quote: 'M4 5h16v11H9l-5 4z',
    up: 'M12 19V5 M5 12l7-7 7 7',
    pulse: 'M2 12h4l3-8 6 16 3-8h4',
    pin: 'M12 21s-7-6.2-7-11a7 7 0 1 1 14 0c0 4.8-7 11-7 11z M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
    doc: 'M6 2h9l5 5v15H6z M14 2v6h6 M9 13h8 M9 17h8',
    people: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M2 21c0-4 3-6 7-6s7 2 7 6 M17 11a3 3 0 1 0 0-6 M22 20c0-3-2-5-5-5',
    chat: 'M4 4h16v12H8l-4 4z M8 9h8 M8 12h5',
    sum: 'M18 4H6l7 8-7 8h12',
    camera: 'M3 7h4l2-3h6l2 3h4v13H3z M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  }
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={d[name] ?? ''} />
    </svg>
  )
}
