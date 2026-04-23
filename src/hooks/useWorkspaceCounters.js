/**
 * Sidebar badge counters. Reads `GET /workspace/counters` (pre-aggregated by
 * the counter-refresh worker every 30s) and reconciles with live WS events on
 * `inbox:<ws>` + `notif:<user>` so the number on the rail stays honest
 * between refreshes.
 *
 * Contract (backend/app/routers/me.py::workspace_counters):
 *   { ticketsOpen, conversationsUnread, notificationsUnread }
 */
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { subscribeTopic } from './useRealtime.js';

function loadAuth() {
  try {
    const raw = localStorage.getItem('workspacex.auth');
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function useWorkspaceCounters() {
  const [counters, setCounters] = useState({
    ticketsOpen: 0,
    conversationsUnread: 0,
    notificationsUnread: 0,
  });

  useEffect(() => {
    let alive = true;
    let refreshTimer = null;
    const auth = loadAuth();
    const wsId = auth?.user?.workspace_id;
    const userId = auth?.user?.id;
    if (!wsId) return undefined;

    const refresh = () => {
      api.get('/workspace/counters')
        .then((r) => { if (alive) setCounters(r); })
        .catch(() => {});
    };
    refresh();
    // Poll as a safety net — backend refresh runs every 30 s so 60 s is
    // plenty to keep the badges aligned with persisted state even if a WS
    // event is missed over a flaky network.
    refreshTimer = setInterval(refresh, 60_000);

    // Live nudge on the topics that move the counters. We re-fetch instead
    // of incrementing locally so we never drift from the server aggregate.
    const unsub1 = subscribeTopic(`inbox:${wsId}`, (evt) => {
      if (['conversation.updated', 'ticket.created', 'ticket.updated'].includes(evt?.type)) {
        refresh();
      }
    });
    const unsub2 = userId
      ? subscribeTopic(`notif:${userId}`, (evt) => {
          if (evt?.type === 'notification.created') refresh();
        })
      : () => {};

    return () => {
      alive = false;
      if (refreshTimer) clearInterval(refreshTimer);
      unsub1?.();
      unsub2?.();
    };
  }, []);

  return counters;
}
