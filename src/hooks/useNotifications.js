import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api.js';
import { subscribeTopic } from './useRealtime.js';

let state = [];
let loaded = false;
let currentUnsub = null;
const listeners = new Set();

const emit = () => listeners.forEach((l) => l());
const subscribe = (l) => { listeners.add(l); return () => listeners.delete(l); };
const getSnapshot = () => state;

function attachRealtime() {
  if (currentUnsub) return;
  try {
    const raw = localStorage.getItem('workspacex.auth');
    const me = raw ? JSON.parse(raw) : null;
    const userId = me?.id;
    if (!userId) return;
    currentUnsub = subscribeTopic(`notif:${userId}`, (evt) => {
      if (evt.type === 'notification.created') {
        state = [evt.data, ...state];
        emit();
      }
    });
  } catch { /* ignore */ }
}

async function load() {
  if (loaded) return;
  loaded = true;
  try {
    const res = await api.get('/notifications');
    const items = Array.isArray(res) ? res : res?.items || [];
    state = items;
    emit();
  } catch {
    state = [];
    emit();
  }
  attachRealtime();
}

export function useNotifications() {
  useEffect(() => { load(); }, []);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export const notificationsApi = {
  async markRead(id) {
    state = state.map((n) => (n.id === id ? { ...n, unread: false } : n));
    emit();
    try { await api.post(`/notifications/${encodeURIComponent(id)}/read`); } catch {}
  },
  async markAllRead() {
    state = state.map((n) => ({ ...n, unread: false }));
    emit();
    try { await api.post('/notifications/read-all'); } catch {}
  },
  async dismiss(id) {
    state = state.filter((n) => n.id !== id);
    emit();
    try { await api.del(`/notifications/${encodeURIComponent(id)}`); } catch {}
  },
  async clearAll() {
    state = [];
    emit();
    try { await api.del('/notifications'); } catch {}
  },
};
