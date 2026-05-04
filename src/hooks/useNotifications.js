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

let lastError = null;
const errorListeners = new Set();
const subscribeError = (l) => { errorListeners.add(l); return () => errorListeners.delete(l); };
const getErrorSnapshot = () => lastError;
const setError = (err) => {
  lastError = err;
  errorListeners.forEach((l) => l());
};

function reportError(scope, err) {
  // Surface for the UI to render an inline banner; also log so devtools
  // shows the original stack/status. Silent `.catch(() => {})` makes broken
  // endpoints look like "no data" which is impossible to debug.
  console.error(`[notifications:${scope}]`, err);
  setError({ scope, message: err?.message || String(err), status: err?.status });
}

function attachRealtime() {
  if (currentUnsub) return;
  const me = (() => {
    try {
      const raw = localStorage.getItem('workspacex.auth');
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      reportError('parse-profile', err);
      return null;
    }
  })();
  const userId = me?.id;
  if (!userId) return;
  currentUnsub = subscribeTopic(`notif:${userId}`, (evt) => {
    if (evt.type === 'notification.created') {
      state = [evt.data, ...state];
      emit();
      playNotificationSound();
    }
  });
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
    setError(null);
  } catch (err) {
    state = [];
    reportError('load', err);
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

export function useNotificationsError() {
  return useSyncExternalStore(subscribeError, getErrorSnapshot, getErrorSnapshot);
}

export const notificationsApi = {
  async markRead(id) {
    const prev = state;
    state = state.map((n) => (n.id === id ? { ...n, unread: false } : n));
    emit();
    try {
      await api.post(`/notifications/${encodeURIComponent(id)}/read`);
    } catch (err) {
      // Roll back the optimistic update so the UI doesn't lie about server state.
      state = prev;
      emit();
      reportError('markRead', err);
    }
  },
  async markAllRead() {
    const prev = state;
    state = state.map((n) => ({ ...n, unread: false }));
    emit();
    try {
      await api.post('/notifications/read-all');
    } catch (err) {
      state = prev;
      emit();
      reportError('markAllRead', err);
    }
  },
  async dismiss(id) {
    const prev = state;
    state = state.filter((n) => n.id !== id);
    emit();
    try {
      await api.del(`/notifications/${encodeURIComponent(id)}`);
    } catch (err) {
      state = prev;
      emit();
      reportError('dismiss', err);
    }
  },
  async clearAll() {
    const prev = state;
    state = [];
    emit();
    try {
      await api.del('/notifications');
    } catch (err) {
      state = prev;
      emit();
      reportError('clearAll', err);
    }
  },
};
