import { api } from '../api.js';

export const MEMBERS = [];
export const CHANNELS = [];
export const DMS = [];
export const SHAREABLE_TICKETS = [];
export const SHAREABLE_CUSTOMERS = [];
export const SEED_MESSAGES = {};

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
  replaceArray(MEMBERS, Array.isArray(members) ? members : members?.items || []);
  replaceArray(CHANNELS, Array.isArray(channels) ? channels : channels?.items || []);
  replaceArray(DMS, Array.isArray(dms) ? dms : dms?.items || []);
  replaceArray(SHAREABLE_TICKETS, Array.isArray(tickets) ? tickets : tickets?.items || []);
  replaceArray(SHAREABLE_CUSTOMERS, Array.isArray(customers) ? customers : customers?.items || []);
  CURRENT_USER_ID = me?.id || me?.userId || null;
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
