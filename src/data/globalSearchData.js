import { api, qs } from '../api.js';

const EMPTY = { conversations: [], tickets: [], contacts: [], total: 0 };

export async function runGlobalSearch(query, limit = 8, { signal } = {}) {
  const q = (query || '').trim();
  if (!q) return EMPTY;
  try {
    const data = await api.get(`/search${qs({ q, limit })}`, { signal });
    const conversations = data?.conversations || [];
    const tickets = data?.tickets || [];
    const contacts = data?.contacts || [];
    return {
      conversations,
      tickets,
      contacts,
      total: conversations.length + tickets.length + contacts.length,
    };
  } catch {
    return EMPTY;
  }
}
