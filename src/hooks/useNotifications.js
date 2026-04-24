import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api.js';
import { subscribeTopic } from './useRealtime.js';

let state = [];
let loaded = false;
let loading = false;
let currentUnsub = null;
const listeners = new Set();

const emit = () => listeners.forEach((l) => l());
const subscribe = (l) => { listeners.add(l); return () => listeners.delete(l); };
const getSnapshot = () => state;
const getLoadingSnapshot = () => loading;

let audioCtx = null;
function playNotificationSound() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    if (!audioCtx) audioCtx = new Ctx();
    // Some browsers suspend the context until a user gesture; resume best-effort.
    if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
    const now = audioCtx.currentTime;
    const gain = audioCtx.createGain();
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.18, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
    gain.connect(audioCtx.destination);
    const tone = (freq, start, dur) => {
      const osc = audioCtx.createOscillator();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + start);
      osc.connect(gain);
      osc.start(now + start);
      osc.stop(now + start + dur);
    };
    tone(880, 0, 0.18);
    tone(1320, 0.12, 0.22);
  } catch { /* ignore */ }
}

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
        playNotificationSound();
      }
    });
  } catch { /* ignore */ }
}

async function load() {
  if (loaded) return;
  loaded = true;
  loading = true;
  emit();
  try {
    const res = await api.get('/notifications');
    const items = Array.isArray(res) ? res : res?.items || [];
    state = items;
  } catch {
    state = [];
  } finally {
    loading = false;
    emit();
  }
  attachRealtime();
}

export function useNotifications() {
  useEffect(() => { load(); }, []);
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function useNotificationsLoading() {
  return useSyncExternalStore(subscribe, getLoadingSnapshot, getLoadingSnapshot);
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
