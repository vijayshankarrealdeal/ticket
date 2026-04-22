// Flat search index across conversations, tickets and contacts.
// Kept separate from each page's local state so TopBar can search
// globally without importing every page module.

export const searchIndex = [
  // ---------- Conversations ----------
  { type: 'conversation', id: 1,  title: '+91 88 7931 7596',  subtitle: 'WhatsApp · Hey! I\'d love t...',        channel: 'WhatsApp' },
  { type: 'conversation', id: 2,  title: 'Miloni Boricha',    subtitle: 'WhatsApp · For a hotel-lik...',         channel: 'WhatsApp' },
  { type: 'conversation', id: 3,  title: 'Hamzah',            subtitle: 'WhatsApp · I understand yo...',         channel: 'WhatsApp' },
  { type: 'conversation', id: 4,  title: 'VINAY M',           subtitle: 'Live Chat · Waiting for callback',      channel: 'Live Chat' },
  { type: 'conversation', id: 5,  title: 'Soumya Kundena',    subtitle: 'WhatsApp · Thanks, will check',         channel: 'WhatsApp' },
  { type: 'conversation', id: 6,  title: 'rajendra .',        subtitle: 'WhatsApp · Order not received',         channel: 'WhatsApp' },
  { type: 'conversation', id: 7,  title: 'Ashwini Sawant',    subtitle: 'WhatsApp · Please call back',           channel: 'WhatsApp' },
  { type: 'conversation', id: 8,  title: 'aftab sayyed',      subtitle: 'Mails · Please i wantt to return...',   channel: 'Mails',    email: 'lionafsay@gmail.com' },
  { type: 'conversation', id: 9,  title: 'Fiona puthur',      subtitle: 'WhatsApp · Let me check fo...',         channel: 'WhatsApp' },
  { type: 'conversation', id: 10, title: 'Anudeep Baidya',    subtitle: 'WhatsApp · For neck pain, ...',         channel: 'WhatsApp' },
  { type: 'conversation', id: 11, title: 'SHAURYA SINGH',     subtitle: 'WhatsApp · Hi there! Your ...',         channel: 'WhatsApp' },
  { type: 'conversation', id: 12, title: 'Naveen K',          subtitle: 'Instagram · DM about recliner',         channel: 'Instagram' },
  { type: 'conversation', id: 13, title: 'Akshay Mukim',      subtitle: 'Live Chat · Chat escalation',           channel: 'Live Chat' },
  { type: 'conversation', id: 14, title: 'Harish',            subtitle: 'WhatsApp · Hi there! I see...',         channel: 'WhatsApp' },

  // ---------- Tickets ----------
  { type: 'ticket', id: '#3770', title: '#3770 · not_delivered_agent_handoff', subtitle: 'rajendra . · WhatsApp · Assigned',  status: 'assigned' },
  { type: 'ticket', id: '#3765', title: '#3765 · refund_pending',              subtitle: 'Dixit Shah · Call · Queued',        status: 'queued' },
  { type: 'ticket', id: '#3764', title: '#3764 · delivery_followup',           subtitle: 'Dixit Shah · Call · Queued',        status: 'queued' },
  { type: 'ticket', id: '#3759', title: '#3759 · delay_delivery',              subtitle: 'aftab sayyed · WhatsApp · Blocked', status: 'blocked' },
  { type: 'ticket', id: '#3754', title: '#3754 · delivery_followup',           subtitle: 'SHAURYA SINGH · WhatsApp · In Progress', status: 'in_progress' },
  { type: 'ticket', id: '#3777', title: '#3777 · product_issue_report',        subtitle: 'aftab sayyed · WhatsApp · In Progress',  status: 'in_progress' },

  // ---------- Contacts (customers) ----------
  { type: 'contact', id: 'c-aftab',    title: 'aftab sayyed',    subtitle: '+91 88 9840 0709 · lionafsay@gmail.com',  phone: '918898400709', email: 'lionafsay@gmail.com' },
  { type: 'contact', id: 'c-rajendra', title: 'rajendra .',      subtitle: '+91 98 7023 1100 · rajendra@example.com', phone: '919870231100', email: 'rajendra@example.com' },
  { type: 'contact', id: 'c-miloni',   title: 'Miloni Boricha',  subtitle: '+91 91 6753 4112 · miloni.b@example.com', phone: '919167534112', email: 'miloni.b@example.com' },
  { type: 'contact', id: 'c-dixit',    title: 'Dixit Shah',      subtitle: '+91 91 2312 3123 · dixit@example.com',    phone: '919123123123', email: 'dixit@example.com' },
  { type: 'contact', id: 'c-ashwini',  title: 'Ashwini Sawant',  subtitle: '+91 98 2034 5612 · ashwini2186@gmail.com', phone: '919820345612', email: 'ashwini2186@gmail.com' },
  { type: 'contact', id: 'c-shaurya',  title: 'SHAURYA SINGH',   subtitle: '+91 76 7812 3456 · shaurya@example.com',  phone: '917678123456', email: 'shaurya@example.com' },
  { type: 'contact', id: 'c-hamzah',   title: 'Hamzah',          subtitle: '+91 98 1234 5678 · hamzah@example.com',   phone: '919812345678', email: 'hamzah@example.com' },
  { type: 'contact', id: 'c-fiona',    title: 'Fiona puthur',    subtitle: '+91 90 9080 7060 · fiona.p@example.com',  phone: '919090807060', email: 'fiona.p@example.com' },
];

export function runGlobalSearch(query, limit = 8) {
  const q = query.trim().toLowerCase();
  if (!q) return { conversations: [], tickets: [], contacts: [], total: 0 };

  const matches = (r) => {
    const hay = [r.title, r.subtitle, r.phone, r.email, r.channel, r.status]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return hay.includes(q);
  };

  const hits = searchIndex.filter(matches);
  const conversations = hits.filter((r) => r.type === 'conversation').slice(0, limit);
  const tickets = hits.filter((r) => r.type === 'ticket').slice(0, limit);
  const contacts = hits.filter((r) => r.type === 'contact').slice(0, limit);
  return {
    conversations,
    tickets,
    contacts,
    total: conversations.length + tickets.length + contacts.length,
  };
}
