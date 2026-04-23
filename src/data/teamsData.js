// Mock data for the Teams feature (internal chat, channels, DMs).

export const MEMBERS = [
  { id: 'u-jane', name: 'Jane Doe', email: 'jane@thesleepcompany.in', role: 'Admin', status: 'online', me: true },
  { id: 'u-aniket', name: 'Aniket Singh', email: 'aniket.s@thesleepcompany.in', role: 'Agent · CRM L1', status: 'online' },
  { id: 'u-aftab', name: 'Aftab Sayyed', email: 'aftab.s@thesleepcompany.in', role: 'Agent · CRM L2', status: 'away' },
  { id: 'u-bharat', name: 'Bharat M', email: 'bharat.m@thesleepcompany.in', role: 'Supervisor', status: 'online' },
  { id: 'u-priya', name: 'Priya Rao', email: 'priya.r@thesleepcompany.in', role: 'Logistics', status: 'offline' },
  { id: 'u-karan', name: 'Karan Shah', email: 'karan.s@thesleepcompany.in', role: 'Agent · Returns', status: 'online' },
  { id: 'u-meera', name: 'Meera Iyer', email: 'meera.i@thesleepcompany.in', role: 'QA', status: 'away' },
  { id: 'u-rohit', name: 'Rohit Kulkarni', email: 'rohit.k@thesleepcompany.in', role: 'Engineer', status: 'online' },
];

export const CHANNELS = [
  {
    id: 'c-general',
    name: 'general',
    description: 'Company-wide announcements and chatter',
    members: ['u-jane', 'u-aniket', 'u-aftab', 'u-bharat', 'u-priya', 'u-karan', 'u-meera', 'u-rohit'],
    unread: 0,
  },
  {
    id: 'c-support',
    name: 'support-floor',
    description: 'Live support coordination',
    members: ['u-jane', 'u-aniket', 'u-aftab', 'u-bharat', 'u-karan'],
    unread: 3,
  },
  {
    id: 'c-escalations',
    name: 'escalations',
    description: 'Critical tickets that need immediate eyes',
    members: ['u-jane', 'u-bharat', 'u-aftab', 'u-meera'],
    unread: 1,
  },
  {
    id: 'c-logistics',
    name: 'logistics',
    description: 'Returns, pickups, delivery updates',
    members: ['u-jane', 'u-priya', 'u-karan', 'u-aniket'],
    unread: 0,
  },
  {
    id: 'c-product',
    name: 'product-feedback',
    description: 'Customer signals and product wins',
    members: ['u-jane', 'u-meera', 'u-rohit', 'u-bharat'],
    unread: 0,
  },
];

export const DMS = [
  { id: 'd-aniket', members: ['u-jane', 'u-aniket'], unread: 0 },
  { id: 'd-bharat', members: ['u-jane', 'u-bharat'], unread: 2 },
  { id: 'd-priya', members: ['u-jane', 'u-priya'], unread: 0 },
];

// Small curated set of tickets users can share in chat.
export const SHAREABLE_TICKETS = [
  { id: '#3777', customer: 'aftab sayyed', task: 'product_issue_report', status: 'In Progress', channel: 'WhatsApp' },
  { id: '#3770', customer: 'rajendra .', task: 'not_delivered_agent_handoff', status: 'Assigned', channel: 'WhatsApp' },
  { id: '#3759', customer: 'aftab sayyed', task: 'delay_delivery', status: 'Blocked', channel: 'WhatsApp' },
  { id: '#3752', customer: 'VINAY M', task: 'callback_requested', status: 'Queued', channel: 'Live Chat' },
  { id: '#3741', customer: 'Miloni Boricha', task: 'product_recommendation', status: 'Resolved', channel: 'WhatsApp' },
];

export const SHAREABLE_CUSTOMERS = [
  { id: 'c-aftab', name: 'aftab sayyed', phoneDisplay: '+91 88 9840 0709', email: 'lionafsay@gmail.com', orders: 2 },
  { id: 'c-rajendra', name: 'rajendra .', phoneDisplay: '+91 98 7023 1100', email: 'rajendra@example.com', orders: 1 },
  { id: 'c-miloni', name: 'Miloni Boricha', phoneDisplay: '+91 91 6753 4112', email: 'miloni@example.com', orders: 4 },
  { id: 'c-vinay', name: 'VINAY M', phoneDisplay: '+91 99 4421 1200', email: 'vinay.m@example.com', orders: 0 },
];

// Seed conversation threads. Keyed by conversation id (channel id or dm id).
export const SEED_MESSAGES = {
  'c-general': [
    {
      id: 'm1',
      userId: 'u-bharat',
      at: '9:12 AM',
      blocks: [{ type: 'text', value: 'Morning team — great run on backlog this week. Shoutout to ' }, { type: 'mention', userId: 'u-aniket' }, { type: 'text', value: ' and ' }, { type: 'mention', userId: 'u-karan' }, { type: 'text', value: ' for closing 18 tickets yesterday 🎉' }],
      reactions: [{ emoji: '🎉', userIds: ['u-jane', 'u-priya', 'u-meera'] }, { emoji: '🙌', userIds: ['u-aniket'] }],
    },
    {
      id: 'm2',
      userId: 'u-aniket',
      at: '9:15 AM',
      blocks: [{ type: 'text', value: 'Thanks! Team effort 🙏' }],
      reactions: [{ emoji: '❤️', userIds: ['u-bharat', 'u-jane'] }],
    },
    {
      id: 'm3',
      userId: 'u-meera',
      at: '9:42 AM',
      blocks: [{ type: 'text', value: "Heads up — seeing intermittent 500s on the shipping callback. Logging it in " }, { type: 'mention', userId: 'u-rohit', channel: true, name: 'engineering' }, { type: 'text', value: '.' }],
      reactions: [],
    },
  ],
  'c-support': [
    {
      id: 'm1',
      userId: 'u-aftab',
      at: '10:04 AM',
      blocks: [{ type: 'text', value: 'Need a second pair of eyes on this one — customer is upset and we may need a refund override.' }],
      attachments: [{ kind: 'ticket', ticketId: '#3759' }],
      reactions: [{ emoji: '👀', userIds: ['u-bharat', 'u-jane'] }],
    },
    {
      id: 'm2',
      userId: 'u-bharat',
      at: '10:06 AM',
      blocks: [{ type: 'mention', userId: 'u-aftab' }, { type: 'text', value: ' I can take it. Assigning to myself.' }],
      reactions: [{ emoji: '👍', userIds: ['u-aftab'] }],
    },
    {
      id: 'm3',
      userId: 'u-aftab',
      at: '10:09 AM',
      blocks: [{ type: 'text', value: "Here's the customer record for context:" }],
      attachments: [{ kind: 'customer', customerId: 'c-aftab' }],
      reactions: [],
    },
    {
      id: 'm4',
      userId: 'u-karan',
      at: '10:14 AM',
      blocks: [{ type: 'text', value: 'Also sharing the return label we issued earlier.' }],
      attachments: [{ kind: 'image', name: 'return-label.png', size: '184 KB' }],
      reactions: [{ emoji: '🔥', userIds: ['u-bharat'] }],
    },
  ],
  'c-escalations': [
    {
      id: 'm1',
      userId: 'u-jane',
      at: 'Yesterday',
      blocks: [{ type: 'text', value: 'Escalation from the mail channel — please look when free ' }, { type: 'mention', userId: 'u-bharat' }],
      attachments: [{ kind: 'ticket', ticketId: '#3777' }],
      reactions: [{ emoji: '👀', userIds: ['u-bharat'] }],
    },
  ],
  'c-logistics': [
    {
      id: 'm1',
      userId: 'u-priya',
      at: '8:30 AM',
      blocks: [{ type: 'text', value: 'Pickup schedule for today has shifted — Mumbai routes moved to afternoon slots.' }],
      reactions: [],
    },
  ],
  'c-product': [
    {
      id: 'm1',
      userId: 'u-meera',
      at: 'Tue',
      blocks: [{ type: 'text', value: 'Great quote from a customer about the SmartGRID mattress — feedback is overwhelmingly positive this week.' }],
      reactions: [{ emoji: '❤️', userIds: ['u-jane', 'u-bharat', 'u-rohit'] }],
    },
  ],
  'd-aniket': [
    {
      id: 'm1',
      userId: 'u-aniket',
      at: '11:00 AM',
      blocks: [{ type: 'text', value: 'Hey Jane, free for 5 mins? Wanted your take on ticket #3770 — the customer is not responding.' }],
      reactions: [],
    },
  ],
  'd-bharat': [
    {
      id: 'm1',
      userId: 'u-bharat',
      at: '7:48 AM',
      blocks: [{ type: 'text', value: 'Reviewed the Q1 support deck — sharing notes in a bit.' }],
      reactions: [],
    },
    {
      id: 'm2',
      userId: 'u-bharat',
      at: '7:49 AM',
      blocks: [{ type: 'text', value: 'Also, please look at this customer ↓' }],
      attachments: [{ kind: 'customer', customerId: 'c-miloni' }],
      reactions: [],
    },
  ],
  'd-priya': [
    {
      id: 'm1',
      userId: 'u-priya',
      at: 'Mon',
      blocks: [{ type: 'text', value: 'Pickup report attached.' }],
      attachments: [{ kind: 'file', name: 'pickup-report-apr-21.pdf', size: '412 KB' }],
      reactions: [{ emoji: '👍', userIds: ['u-jane'] }],
    },
  ],
};

export const REACTION_PICKER = ['👍', '❤️', '😂', '🎉', '🙌', '😮', '😢', '👀', '🔥', '✅'];

export const CURRENT_USER_ID = 'u-jane';

export function memberById(id) {
  return MEMBERS.find((m) => m.id === id);
}

export function ticketById(id) {
  return SHAREABLE_TICKETS.find((t) => t.id === id);
}

export function customerById(id) {
  return SHAREABLE_CUSTOMERS.find((c) => c.id === id);
}
