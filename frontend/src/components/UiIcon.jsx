import './UiIcon.css'

const ICONS = {
  hexagon: <path d="M12 2 20 6.5v11L12 22l-8-4.5v-11L12 2Z" />,
  sparkles: (
    <>
      <path d="m12 3-1.1 3.2a3 3 0 0 1-1.9 1.9L5.8 9.2 9 10.3a3 3 0 0 1 1.9 1.9L12 15.4l1.1-3.2a3 3 0 0 1 1.9-1.9l3.2-1.1L15 8.1a3 3 0 0 1-1.9-1.9L12 3Z" />
      <path d="m5 15-.6 1.7a2 2 0 0 1-1.2 1.2l-1.7.6 1.7.6a2 2 0 0 1 1.2 1.2L5 22l.6-1.7a2 2 0 0 1 1.2-1.2l1.7-.6-1.7-.6a2 2 0 0 1-1.2-1.2L5 15Z" />
    </>
  ),
  'book-open': (
    <>
      <path d="M3 5.5A3.5 3.5 0 0 1 6.5 2H11v17H6.5A3.5 3.5 0 0 0 3 22V5.5Z" />
      <path d="M21 5.5A3.5 3.5 0 0 0 17.5 2H13v17h4.5A3.5 3.5 0 0 1 21 22V5.5Z" />
    </>
  ),
  'clipboard-list': (
    <>
      <rect x="5" y="4" width="14" height="18" rx="2" />
      <path d="M9 4.5V3a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v1.5M9 10h6M9 14h6M9 18h4" />
    </>
  ),
  'calendar-check': (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M16 3v4M8 3v4M3 10h18m-12 5 2 2 4-4" />
    </>
  ),
  'file-search': (
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h7" />
      <path d="M14 2v6h6m-1 6a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm3 7 2 2" />
    </>
  ),
  receipt: (
    <>
      <path d="M5 3v18l3-2 4 2 4-2 3 2V3l-3 2-4-2-4 2-3-2Z" />
      <path d="M9 9h6m-6 4h6m-6 4h3" />
    </>
  ),
  'shield-check': (
    <>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5m0-8h.01" />
    </>
  ),
  sources: (
    <>
      <path d="M4 5a2 2 0 0 1 2-2h9l5 5v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5Z" />
      <path d="M14 3v6h6M8 13h8m-8 4h6" />
    </>
  ),
  code: (
    <>
      <path d="m8 9-3 3 3 3m8-6 3 3-3 3m-3-8-2 10" />
    </>
  ),
}

export default function UiIcon({ name, size = 20, className = '' }) {
  return (
    <svg
      className={`ui-icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name] || ICONS.hexagon}
    </svg>
  )
}
