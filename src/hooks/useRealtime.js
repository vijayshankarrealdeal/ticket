/**
 * Shared WebSocket client + a small pub/sub layer for realtime updates.
 *
 * Why a singleton: one socket per tab is the contract the backend expects
 * (spec §8 `/ws`). Every component that calls `useRealtimeTopic(topic, onEvent)`
 * reuses the same connection and just sends/removes a `subscribe` frame for
 * its topic when it mounts/unmounts.
 *
 * Why WebSocket over SSE: bidirectional subscribe/unsubscribe keeps bandwidth
 * tight as the user navigates pages. SSE is reserved for agent-streaming in
 * Build 2. See wire.md.
 */
import { useEffect, useRef } from 'react';

const BASE_WS = (() => {
  const apiBase = import.meta.env.VITE_API_URL || '/api';
  if (apiBase.startsWith('http')) return apiBase.replace(/^http/, 'ws') + '/ws';
  // Relative base — use current origin.
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${apiBase}/ws`;
})();

let socket = null;
let state = 'closed'; // 'closed' | 'connecting' | 'open'
let reconnectDelay = 1000;
let reconnectTimer = null;
const subs = new Map(); // topic -> Set<(evt)=>void>
const pendingQueue = []; // frames to send once connected

function authToken() {
  try {
    const raw = localStorage.getItem('workspacex.auth');
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.token || parsed?.accessToken || null;
  } catch {
    return null;
  }
}

function sendFrame(obj) {
  const payload = JSON.stringify(obj);
  if (socket && state === 'open') {
    socket.send(payload);
  } else {
    pendingQueue.push(payload);
    connect();
  }
}

function connect() {
  if (socket || state === 'connecting') return;
  const token = authToken();
  if (!token) return; // wait until signed in
  state = 'connecting';
  try {
    socket = new WebSocket(`${BASE_WS}?token=${encodeURIComponent(token)}`);
  } catch {
    state = 'closed';
    scheduleReconnect();
    return;
  }

  socket.onopen = () => {
    state = 'open';
    reconnectDelay = 1000;
    // Re-subscribe to everything components still care about.
    const topics = [...subs.keys()];
    if (topics.length) socket.send(JSON.stringify({ action: 'subscribe', topics }));
    // Flush queued frames (typing, etc.)
    while (pendingQueue.length) socket.send(pendingQueue.shift());
  };

  socket.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (!msg?.topic) return;
    const listeners = subs.get(msg.topic);
    if (!listeners) return;
    for (const l of listeners) {
      try { l(msg); } catch { /* ignore consumer errors */ }
    }
  };

  socket.onclose = () => {
    socket = null;
    state = 'closed';
    scheduleReconnect();
  };
  socket.onerror = () => { /* onclose will handle reconnect */ };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    connect();
  }, reconnectDelay);
}

export function subscribeTopic(topic, handler) {
  let set = subs.get(topic);
  if (!set) {
    set = new Set();
    subs.set(topic, set);
    sendFrame({ action: 'subscribe', topics: [topic] });
  }
  set.add(handler);
  connect();
  return () => {
    const s = subs.get(topic);
    if (!s) return;
    s.delete(handler);
    if (s.size === 0) {
      subs.delete(topic);
      sendFrame({ action: 'unsubscribe', topics: [topic] });
    }
  };
}

/**
 * React helper: subscribe to a topic for the lifetime of a component.
 * Pass null to skip subscribing (e.g. while `id` is still loading).
 */
export function useRealtimeTopic(topic, handler) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  useEffect(() => {
    if (!topic) return undefined;
    return subscribeTopic(topic, (evt) => handlerRef.current?.(evt));
  }, [topic]);
}

/** Disconnect on sign-out so the next user's socket uses their own token. */
export function closeRealtime() {
  try { socket?.close(); } catch { /* noop */ }
  socket = null;
  state = 'closed';
  subs.clear();
  pendingQueue.length = 0;
}
