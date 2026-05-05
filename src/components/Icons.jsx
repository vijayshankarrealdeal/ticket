const stroke = {
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  fill: 'none',
};

export const Icon = {
  Inbox: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M3 12h5l2 3h4l2-3h5" />
      <path d="M3 12l2.5-7h13L21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  ),
  Ticket: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M4 8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z" />
      <path d="M10 6v12" strokeDasharray="2 2" />
    </svg>
  ),
  Megaphone: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M3 11v2a2 2 0 0 0 2 2h1l3 4h2v-4h2l7 4V5l-7 4h-2z" />
    </svg>
  ),
  Chart: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M3 3v18h18" />
      <path d="M7 14l4-4 4 3 5-7" />
    </svg>
  ),
  Puzzle: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M10 3h4v3a2 2 0 1 0 4 0h3v4a2 2 0 1 1 0 4v4h-4a2 2 0 1 0-4 0H6v-4a2 2 0 1 1 0-4V6a3 3 0 0 1 3-3z" />
    </svg>
  ),
  Plug: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M9 3v5M15 3v5" />
      <path d="M7 9h10v3a5 5 0 0 1-10 0z" />
      <path d="M12 17v4" />
    </svg>
  ),
  Gear: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.8l-.1-.1A2 2 0 1 1 7 4.3l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  ),
  Speaker: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M11 5 6 9H3v6h3l5 4z" />
      <path d="M15 9a4 4 0 0 1 0 6" />
      <path d="M18 6a8 8 0 0 1 0 12" />
    </svg>
  ),
  Bell: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8" />
      <path d="M10 21a2 2 0 0 0 4 0" />
    </svg>
  ),
  Headset: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M3 14v-2a9 9 0 0 1 18 0v2" />
      <path d="M21 14v4a2 2 0 0 1-2 2h-2v-6h4zM3 14v4a2 2 0 0 0 2 2h2v-6H3z" />
    </svg>
  ),
  Chevron: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M6 9l6 6 6-6" />
    </svg>
  ),
  Search: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4-4" />
    </svg>
  ),
  Filter: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M4 5h16l-6 8v6l-4 2v-8z" />
    </svg>
  ),
  Sort: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M7 4v16M4 7l3-3 3 3" />
      <path d="M17 20V4M14 17l3 3 3-3" />
    </svg>
  ),
  Plus: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  ),
  Phone: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2 4.2 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c1 .3 1.9.6 2.9.7a2 2 0 0 1 1.7 2z" />
    </svg>
  ),
  WhatsApp: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" {...p}>
      <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.272-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.134.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.58-.487-.501-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.075-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.71.306 1.263.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 0 0-3.48-8.413z" />
    </svg>
  ),
  Mail: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7l9 6 9-6" />
    </svg>
  ),
  Instagram: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.5" cy="6.5" r="0.8" fill="currentColor" stroke="none" />
    </svg>
  ),
  Send: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M22 2 11 13" />
      <path d="M22 2l-7 20-4-9-9-4z" />
    </svg>
  ),
  Clock: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  ),
  Image: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-5-5L5 21" />
    </svg>
  ),
  Document: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
    </svg>
  ),
  User: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </svg>
  ),
  Users: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2 21a7 7 0 0 1 14 0" />
      <path d="M16 4.5a3.5 3.5 0 0 1 0 7" />
      <path d="M17 21a7 7 0 0 0-2-4.9" />
    </svg>
  ),
  Expand: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M15 3h6v6M21 3l-7 7M9 21H3v-6M3 21l7-7" />
    </svg>
  ),
  Pencil: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M17 3 21 7 8 20H4v-4z" />
    </svg>
  ),
  Check2: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="m1 12 5 5 8-10M10 17l2 2 9-11" />
    </svg>
  ),
  Back: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M19 12H5M12 19l-7-7 7-7" />
    </svg>
  ),
  Reply: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <polyline points="9 17 4 12 9 7" />
      <path d="M20 18v-2a4 4 0 0 0-4-4H4" />
    </svg>
  ),
  Menu: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  ),
  More: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" stroke="none" {...p}>
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </svg>
  ),
  Info: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v.01M11 12h1v5h1" />
    </svg>
  ),
  Sun: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
    </svg>
  ),
  Moon: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  ),
  SidebarToggle: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M15 4v16" />
    </svg>
  ),
  Close: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M6 6l12 12M18 6l-12 12" />
    </svg>
  ),
  DragHandle: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M8 6v12M16 6v12" />
      <path d="M5 9l-3 3 3 3M19 9l3 3-3 3" />
    </svg>
  ),
  MailAt: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <circle cx="12" cy="12" r="4" />
      <path d="M16 8v5a3 3 0 0 0 6 0 10 10 0 1 0-4 7.5" />
    </svg>
  ),
  Paperclip: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="M21 12.5 12.5 21a5 5 0 0 1-7-7L14 5.5a3.5 3.5 0 1 1 5 5L10.5 19" />
    </svg>
  ),
  Refresh: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5" />
    </svg>
  ),
  MapPin: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M12 22s8-7.5 8-13a8 8 0 1 0-16 0c0 5.5 8 13 8 13z" />
      <circle cx="12" cy="9" r="2.5" />
    </svg>
  ),
  InboxEmpty: (p) => (
    <svg viewBox="0 0 48 48" width="52" height="52" {...stroke} strokeWidth={1.4} {...p}>
      <path d="M8 28l6-14a2 2 0 0 1 1.8-1.2h16.4a2 2 0 0 1 1.8 1.2l6 14" />
      <path d="M8 28v10a2 2 0 0 0 2 2h28a2 2 0 0 0 2-2V28h-10l-2 4h-8l-2-4H8z" />
    </svg>
  ),
  ShoppingBag: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M3 7h18l-1.5 12a2 2 0 0 1-2 1.8h-11a2 2 0 0 1-2-1.8z" />
      <path d="M8 7V5a4 4 0 1 1 8 0v2" />
    </svg>
  ),
  CallOutline: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2 4.2 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c1 .3 1.9.6 2.9.7a2 2 0 0 1 1.7 2z" />
    </svg>
  ),
  Code: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="m8 6-6 6 6 6M16 6l6 6-6 6M14 4l-4 16" />
    </svg>
  ),
  Activity: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="M3 12h4l2-7 4 14 2-7h6" />
    </svg>
  ),
  Sheet: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
    </svg>
  ),
  Truck: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="M3 7h11v9H3zM14 10h4l3 3v3h-7z" />
      <circle cx="7" cy="18" r="1.8" />
      <circle cx="17" cy="18" r="1.8" />
    </svg>
  ),
  Store: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="M3 9l2-5h14l2 5" />
      <path d="M3 9a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0" />
      <path d="M4 10v10h16V10" />
    </svg>
  ),
  Copy: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <rect x="8" y="8" width="13" height="13" rx="2" />
      <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
    </svg>
  ),
  Trash: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M6 6l1 14a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-14" />
    </svg>
  ),
  ExternalLink: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M14 4h6v6M10 14 20 4M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6" />
    </svg>
  ),
  Play: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M6 4v16l14-8z" />
    </svg>
  ),
  Check: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="m5 12 5 5 9-11" />
    </svg>
  ),
  Dot: (p) => (
    <svg viewBox="0 0 24 24" width="10" height="10" fill="currentColor" stroke="none" {...p}>
      <circle cx="12" cy="12" r="5" />
    </svg>
  ),
  Bolt: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M13 2 3 14h8l-1 8 10-12h-8z" />
    </svg>
  ),
  Calendar: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 9h18M8 3v4M16 3v4" />
    </svg>
  ),
  AlertTriangle: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M12 3 2 20h20zM12 10v4M12 17v.01" />
    </svg>
  ),
  Eye: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  Book: (p) => (
    <svg viewBox="0 0 24 24" width="14" height="14" {...stroke} {...p}>
      <path d="M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 0-2 2zM4 19a2 2 0 0 1 2-2h13" />
    </svg>
  ),
  Heartbeat: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="M3 12h4l2-4 3 8 2-5 2 3 2-2h3" />
    </svg>
  ),
  Shield: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M12 3 4 6v6c0 5 3.5 8.5 8 9 4.5-.5 8-4 8-9V6z" />
    </svg>
  ),
  AiAgent: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M12 2v3" />
      <circle cx="12" cy="2" r="0.9" fill="currentColor" stroke="none" />
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <circle cx="9" cy="13" r="1.2" fill="currentColor" stroke="none" />
      <circle cx="15" cy="13" r="1.2" fill="currentColor" stroke="none" />
      <path d="M2 12v3M22 12v3" />
    </svg>
  ),
  Teams: (p) => (
    <svg viewBox="0 0 24 24" width="20" height="20" {...stroke} {...p}>
      <path d="M4 11a4 4 0 0 1 8 0v1a4 4 0 0 1-4 4H4z" />
      <path d="M14 7a3 3 0 1 1 3 3h-3" />
      <path d="M14 15a3 3 0 0 1 3-3h1a3 3 0 0 1 3 3v4" />
    </svg>
  ),
  Smile: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 14s1.5 2 4 2 4-2 4-2" />
      <path d="M9 9v.01M15 9v.01" />
    </svg>
  ),
  At: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <circle cx="12" cy="12" r="4" />
      <path d="M16 8v5a3 3 0 0 0 6 0 10 10 0 1 0-4 7.5" />
    </svg>
  ),
  Hash: (p) => (
    <svg viewBox="0 0 24 24" width="18" height="18" {...stroke} {...p}>
      <path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16" />
    </svg>
  ),
  Pin: (p) => (
    <svg viewBox="0 0 24 24" width="16" height="16" {...stroke} {...p}>
      <path d="M12 17v5" />
      <path d="M9 3h6l-1 5 3 3v3H7v-3l3-3z" />
    </svg>
  ),
};
