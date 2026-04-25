import { api } from '../api.js';

export const MEMBERS = [];
export const CHANNELS = [];
export const DMS = [];
export const SHAREABLE_TICKETS = [];
export const SHAREABLE_CUSTOMERS = [];
export const SEED_MESSAGES = {};

// Keyed like the /teams/unread response — { channels:{id:count}, dms:{id:count}, total }.
let TEAMS_UNREAD = { channels: {}, dms: {}, total: 0 };

export const REACTION_PICKER = ['👍', '❤️', '😂', '🎉', '🙌', '😮', '😢', '👀', '🔥', '✅'];

export let CURRENT_USER_ID = null;

function replaceArray(target, next) {
  target.splice(0, target.length, ...(next || []));
}

function replaceObject(target, next) {
  for (const k of Object.keys(target)) delete target[k];
  Object.assign(target, next || {});
}

export async function loadTeamsData() {
  const [members, channels, dms, tickets, customers, me] = await Promise.all([
    api.get('/teams/members').catch(() => []),
    api.get('/teams/channels').catch(() => []),
    api.get('/teams/dms').catch(() => []),
    api.get('/tickets?shareable=1').catch(() => []),
    api.get('/customers?shareable=1').catch(() => []),
    api.get('/auth/me').catch(() => null),
  ]);
  const rawMembers = Array.isArray(members) ? members : members?.items || [];
  CURRENT_USER_ID = me?.id || me?.userId || null;

  const meId = CURRENT_USER_ID;
  let meEntry = meId ? rawMembers.find((m) => m.id === meId) : null;
  const others = meId ? rawMembers.filter((m) => m.id !== meId) : rawMembers.slice();
  if (!meEntry && me) {
    meEntry = {
      id: meId,
      name: me.name || 'You',
      email: me.email,
      role: me.role || 'You',
      status: me.status || 'online',
      avatar_url: me.avatar_url,
    };
  }
  const ordered = meEntry ? [{ ...meEntry, me: true }, ...others] : others;

  replaceArray(MEMBERS, ordered);
  replaceArray(CHANNELS, Array.isArray(channels) ? channels : channels?.items || []);

  // The backend guarantees a self-DM (members = [me]) and returns it with
  // `isSelf: true`. Pin it to the top so the current user always sees their
  // "(You)" scratchpad first, matching the MS Teams sidebar.
  const rawDms = Array.isArray(dms) ? dms : dms?.items || [];
  const orderedDms = rawDms.slice().sort((a, b) => (b.isSelf ? 1 : 0) - (a.isSelf ? 1 : 0));
  replaceArray(DMS, orderedDms);

  replaceArray(SHAREABLE_TICKETS, Array.isArray(tickets) ? tickets : tickets?.items || []);
  replaceArray(SHAREABLE_CUSTOMERS, Array.isArray(customers) ? customers : customers?.items || []);
  return { members: MEMBERS, channels: CHANNELS, dms: DMS };
}

export async function loadConversationMessages(conversationId) {
  if (!conversationId) return [];
  try {
    const res = await api.get(`/teams/conversations/${encodeURIComponent(conversationId)}/messages`);
    const items = Array.isArray(res) ? res : res?.items || [];
    SEED_MESSAGES[conversationId] = items;
    return items;
  } catch {
    SEED_MESSAGES[conversationId] = [];
    return [];
  }
}

/** GET /teams/presence — refresh the `status` field on every MEMBER in place. */
export async function refreshTeamsPresence() {
  const rows = await api.get('/teams/presence').catch(() => []);
  const byId = new Map((Array.isArray(rows) ? rows : []).map((r) => [r.id, r]));
  for (const m of MEMBERS) {
    const p = byId.get(m.id);
    if (p) m.status = p.status;
  }
  return MEMBERS;
}

/** GET /teams/unread — map each channel/DM into { ...entry, unread }. */
export async function refreshTeamsUnread() {
  const res = await api.get('/teams/unread').catch(() => ({ channels: {}, dms: {}, total: 0 }));
  TEAMS_UNREAD = {
    channels: res?.channels || {},
    dms: res?.dms || {},
    total: res?.total || 0,
  };
  for (const c of CHANNELS) c.unread = TEAMS_UNREAD.channels[c.id] || 0;
  for (const d of DMS) d.unread = TEAMS_UNREAD.dms[d.id] || 0;
  return TEAMS_UNREAD;
}

export function getTeamsUnread() {
  return TEAMS_UNREAD;
}

export function setSeedMessages(next) {
  replaceObject(SEED_MESSAGES, next);
}

export function memberById(id) {
  return MEMBERS.find((m) => m.id === id);
}

export function ticketById(id) {
  return SHAREABLE_TICKETS.find((t) => t.id === id);
}

export function customerById(id) {
  return SHAREABLE_CUSTOMERS.find((c) => c.id === id);
}
