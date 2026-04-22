import { useSyncExternalStore } from 'react';

const INITIAL = [
  { id: 1, type: 'assign',    title: 'New ticket assigned',  body: 'TCKT-2481 • Refund request from Priya S.',           time: '2m ago',   ts: Date.now() - 2 * 60 * 1000,       unread: true,  link: '/tickets?id=3770' },
  { id: 2, type: 'reply',     title: 'Customer replied',     body: 'Rahul M. replied on "Order not delivered"',          time: '18m ago',  ts: Date.now() - 18 * 60 * 1000,      unread: true,  link: '/tickets?id=3765' },
  { id: 3, type: 'sla',       title: 'SLA breach warning',   body: 'Ticket TCKT-2402 is 30 min from breach',             time: '1h ago',   ts: Date.now() - 60 * 60 * 1000,      unread: true,  link: '/tickets?id=3764' },
  { id: 4, type: 'broadcast', title: 'Broadcast sent',       body: '"Festive sale" delivered to 12,430 contacts',        time: '3h ago',   ts: Date.now() - 3 * 60 * 60 * 1000,  unread: false, link: '/platforms' },
  { id: 5, type: 'member',    title: 'New teammate joined',  body: 'Ananya K. joined the Support team',                  time: 'Yesterday',ts: Date.now() - 26 * 60 * 60 * 1000, unread: false, link: '/settings' },
  { id: 6, type: 'reply',     title: 'Customer replied',     body: 'Sara T. shared screenshots on TCKT-2370',            time: '2d ago',   ts: Date.now() - 2 * 24 * 60 * 60 * 1000, unread: false, link: '/tickets?id=3759' },
  { id: 7, type: 'assign',    title: 'Ticket reassigned',    body: 'TCKT-2355 moved to Billing queue',                   time: '3d ago',   ts: Date.now() - 3 * 24 * 60 * 60 * 1000, unread: false, link: '/tickets?id=3770' },
];

let state = INITIAL;
const listeners = new Set();

const emit = () => listeners.forEach((l) => l());
const subscribe = (l) => { listeners.add(l); return () => listeners.delete(l); };
const getSnapshot = () => state;

export function useNotifications() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export const notificationsApi = {
  markRead(id) {
    state = state.map((n) => (n.id === id ? { ...n, unread: false } : n));
    emit();
  },
  markAllRead() {
    state = state.map((n) => ({ ...n, unread: false }));
    emit();
  },
  dismiss(id) {
    state = state.filter((n) => n.id !== id);
    emit();
  },
  clearAll() {
    state = [];
    emit();
  },
};
