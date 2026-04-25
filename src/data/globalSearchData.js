import { api, qs } from '../api.js';

const EMPTY = { conversations: [], tickets: [], contacts: [], teammates: [], total: 0 };

export async function runGlobalSearch(query, limit = 8, { signal } = {}) {
  const q = (query || '').trim();
  if (!q) return EMPTY;
  try {
    const data = await api.get(`/search${qs({ q, limit })}`, { signal });
    const conversations = data?.conversations || [];
    const tickets = data?.tickets || [];
    const contacts = data?.contacts || [];
    const teammates = data?.teammates || [];
    return {
      conversations,
      tickets,
      contacts,
      teammates,
      total: conversations.length + tickets.length + contacts.length + teammates.length,
    };
  } catch {
    return EMPTY;
  }
}
