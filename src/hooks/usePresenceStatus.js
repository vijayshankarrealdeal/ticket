import { useEffect, useState } from 'react';
import { api } from '../api.js';

const KEY = 'workspacex.presence.status';
const VALID = new Set(['online', 'offline', 'ooo']);
const listeners = new Set();

export function getPresenceStatus() {
  try {
    const v = localStorage.getItem(KEY);
    return VALID.has(v) ? v : 'online';
  } catch {
    return 'online';
  }
}

export function setPresenceStatus(status) {
  if (!VALID.has(status)) return;
  try { localStorage.setItem(KEY, status); } catch {}
  listeners.forEach((fn) => { try { fn(status); } catch {} });
  api.post('/teams/presence/heartbeat', { status }).catch(() => {});
}

export function usePresenceStatus() {
  const [status, setStatus] = useState(getPresenceStatus);
  useEffect(() => {
    const fn = (s) => setStatus(s);
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }, []);
  return [status, setPresenceStatus];
}

export const PRESENCE_LABELS = {
  online: 'Online',
  ooo: 'Out of office',
  offline: 'Offline',
};
