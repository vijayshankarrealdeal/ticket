import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useSearchParams } from 'react-router-dom';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { Spinner } from '../components/Spinner.jsx';
import { useDelayUnmount } from '../hooks/useDelayUnmount.js';
import { useGlobalSearch } from '../hooks/useGlobalSearch.jsx';
import { api, uploadFile } from '../api.js';
import { useRealtimeTopic } from '../hooks/useRealtime.js';
import { loadAuth } from './AuthPage.jsx';

const CHANNELS = ['All', 'WhatsApp', 'Instagram', 'Live Chat', 'Mails'];

// Backend stores raw channel names (gmail/whatsapp/outlook/...) while the UI
// filter labels are capitalized buckets. Normalize both sides so 'gmail' and
// 'outlook' fall under the 'Mails' filter, 'whatsapp' under 'WhatsApp', etc.
const channelBucket = (raw) => {
  const c = (raw || '').toLowerCase();
  if (c === 'whatsapp') return 'WhatsApp';
  if (c === 'instagram') return 'Instagram';
  if (c === 'voice' || c === 'live chat' || c === 'call') return 'Live Chat';
  if (c === 'gmail' || c === 'outlook' || c === 'email' || c === 'mails') return 'Mails';
  return raw;
};

const avatarColors = ['#1e40af', '#0ea5e9', '#16a34a', '#f97316', '#ec4899', '#64748b', '#dc2626', '#0891b2'];
const initials = (name) =>
  (name || '').split(' ').filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
const colorFor = (seed) => avatarColors[((seed || ' ').charCodeAt(0) || 0) % avatarColors.length];

export default function Conversations() {
  const [params, setParams] = useSearchParams();
  const channel = params.get('channel') || 'All';

  const [tab, setTab] = useState('all');
  const [channelOpen, setChannelOpen] = useState(false);
  const [selectedId, setSelectedId] = useState(() => params.get('id') || null);
  const [draft, setDraft] = useState('');
  const [draftLoading, setDraftLoading] = useState(false);
  const [list, setList] = useState([]);
  const [listLoading, setListLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api.get('/conversations')
      .then((res) => {
        if (!alive) return;
        const items = Array.isArray(res) ? res : res?.items || [];
        setList(items);
        setSelectedId((cur) => cur ?? items[0]?.id ?? null);
      })
      .catch(() => { if (alive) setList([]); })
      .finally(() => { if (alive) setListLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (selectedId == null) return;
    let alive = true;
    api.get(`/conversations/${encodeURIComponent(selectedId)}`)
      .then((detail) => {
        if (!alive || !detail) return;
        setList((prev) => prev.map((t) => t.id === selectedId ? { ...t, ...detail } : t));
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [selectedId]);

  useEffect(() => {
    const raw = params.get('id');
    if (!raw) return;
    if (list.some((t) => t.id === raw)) {
      setSelectedId(raw);
      const next = new URLSearchParams(params);
      next.delete('id');
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, list]);

  // Realtime — inbox list updates for this workspace.
  const workspaceId = loadAuth()?.workspaceId;
  useRealtimeTopic(workspaceId ? `inbox:${workspaceId}` : null, (evt) => {
    if (evt.type === 'conversation.updated') {
      // If the envelope points at a conversation not in our list (brand-new
      // inbound from a webhook / poller), refetch the full list so the new
      // row appears. Otherwise, patch the existing row in place.
      setList((prev) => {
        const known = prev.some((c) => c.id === evt.data.id);
        if (!known) {
          api.get('/conversations').then((rows) => setList(Array.isArray(rows) ? rows : [])).catch(() => {});
          return prev;
        }
        return prev.map((c) => c.id === evt.data.id ? { ...c, ...evt.data } : c);
      });
    } else if (evt.type === 'conversation.deleted') {
      const gone = evt.data?.id;
      if (!gone) return;
      setList((prev) => prev.filter((c) => c.id !== gone));
      setSelectedId((cur) => (cur === gone ? null : cur));
    } else if (evt.type === 'ticket.created' || evt.type === 'ticket.updated') {
      // a ticket change may affect the right-rail ticket list on the selected thread
      setList((prev) => prev.map((c) =>
        evt.data?.conversation_id && c.id === evt.data.conversation_id
          ? { ...c, tickets: [evt.data, ...(c.tickets || []).filter((t) => t.id !== evt.data.id)] }
          : c
      ));
    }
  });

  // Realtime — messages + AI streaming for the currently open thread. The
  // backend's `stream_inbound_reply` pushes:
  //   ai.typing.started {streamId, agent}  → insert a streaming bubble
  //   ai.delta          {streamId, delta}  → append text to that bubble
  //   message.created   {id, streamFinal}  → swap the streaming bubble for
  //                                           the persisted row
  //   ai.typing.stopped {streamId}         → clear any leftover streaming bubble
  useRealtimeTopic(selectedId ? `conv:${selectedId}` : null, (evt) => {
    if (evt.type === 'message.created') {
      setList((prev) => prev.map((c) => {
        if (c.id !== selectedId) return c;
        const existing = c.messages || [];
        const tempId = evt.data?.tempId;
        const withoutTemp = tempId ? existing.filter((m) => m.id !== tempId && m.tempId !== tempId) : existing;
        // If the final arrived for a streaming bubble, replace it. Otherwise
        // just drop the streaming row (the non-stream message row takes over).
        const withoutStream = existing.some((m) => m.streaming)
          ? withoutTemp.filter((m) => !m.streaming)
          : withoutTemp;
        if (evt.data?.id && withoutStream.some((m) => m.id === evt.data.id)) return c;
        return { ...c, messages: [...withoutStream, evt.data], preview: evt.data.text || c.preview };
      }));
      return;
    }
    if (evt.type === 'ai.typing.started') {
      const { streamId, agent } = evt.data || {};
      if (!streamId) return;
      setList((prev) => prev.map((c) => c.id !== selectedId ? c : ({
        ...c,
        messages: [
          ...(c.messages || []).filter((m) => m.streamId !== streamId),
          {
            id: streamId, streamId, streaming: true,
            from: 'me', fromName: agent || 'AI',
            text: '', delivered: false, at: 'now', aiAgentId: evt.data?.agentId,
          },
        ],
      })));
      return;
    }
    if (evt.type === 'ai.delta') {
      const { streamId, delta } = evt.data || {};
      if (!streamId || !delta) return;
      setList((prev) => prev.map((c) => c.id !== selectedId ? c : ({
        ...c,
        messages: (c.messages || []).map((m) =>
          m.streamId === streamId ? { ...m, text: (m.text || '') + delta } : m,
        ),
      })));
      return;
    }
    if (evt.type === 'ai.typing.stopped') {
      const { streamId } = evt.data || {};
      if (!streamId) return;
      setList((prev) => prev.map((c) => c.id !== selectedId ? c : ({
        ...c,
        // We leave the bubble in place — `message.created` with streamFinal
        // swaps it to the persisted row. This handler only cleans up orphan
        // stream rows that never produced a final (errors, guardrails).
        messages: (c.messages || []).filter((m) => !(m.streamId === streamId && m.streaming && !m.text)),
      })));
    }
  });
  const [rightPanel, setRightPanel] = useState('ticket');
  const [viewChannel, setViewChannel] = useState(null);
  const { query: search } = useGlobalSearch();
  const [filterOpen, setFilterOpen] = useState(false);
  const [filters, setFilters] = useState({ assignment: 'any', unreadOnly: false, withAttachments: false });
  const [pending, setPending] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const [plusOpen, setPlusOpen] = useState(false);
  const [plusMode, setPlusMode] = useState('root');
  const [linkDraft, setLinkDraft] = useState('');
  const [ticketFilter, setTicketFilter] = useState('');
  const [replyingTo, setReplyingTo] = useState(null);
  const fileInputRef = useRef(null);
  const imageInputRef = useRef(null);
  const textareaRef = useRef(null);
  const dragDepth = useRef(0);
  const chatBodyRef = useRef(null);

  const rightAnim = useDelayUnmount(rightPanel !== null, 240);
  const lastRightRef = useRef(rightPanel);
  useEffect(() => { if (rightPanel) lastRightRef.current = rightPanel; }, [rightPanel]);
  const visibleRight = rightPanel || lastRightRef.current;
  const rightClosing = rightAnim.state === 'exiting';

  const channelMenu = useDelayUnmount(channelOpen, 180);
  const filterMenu = useDelayUnmount(filterOpen, 180);

  const selected = list.find((t) => t.id === selectedId);

  useEffect(() => {
    setViewChannel(selected?.channel || null);
    setReplyingTo(null);
    closePlus();
  }, [selectedId]);

  // When the channel filter changes, drop the current selection if it doesn't
  // belong to the new channel — otherwise the center pane would keep showing a
  // thread that's filtered out of the left list.
  useEffect(() => {
    if (channel === 'All' || !selected) return;
    if (channelBucket(selected.channel) !== channel) {
      setSelectedId(null);
    }
  }, [channel, selected]);

  const activeChannel = viewChannel || selected?.channel;

  const activeThread = useMemo(() => {
    if (!selected) return null;
    if (!activeChannel || activeChannel === selected.channel) return selected;
    return list.find((t) =>
      t.id !== selected.id &&
      t.channel === activeChannel &&
      ((t.phone && t.phone === selected.phone) ||
        (t.email && t.email === selected.email) ||
        (t.name && t.name === selected.name))
    ) || null;
  }, [list, selected, activeChannel]);

  const threadId = activeThread?.id;
  const msgCount = activeThread?.messages?.length || 0;
  const lastMsg = activeThread?.messages?.[msgCount - 1];
  const lastMsgKey = lastMsg ? `${lastMsg.id || lastMsg.tempId || lastMsg.streamId || ''}:${(lastMsg.text || '').length}:${lastMsg.streaming ? 1 : 0}` : '';
  useEffect(() => {
    const el = chatBodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [threadId, msgCount, lastMsgKey]);

  const activeFilterCount =
    (filters.assignment !== 'any' ? 1 : 0) +
    (filters.unreadOnly ? 1 : 0) +
    (filters.withAttachments ? 1 : 0);

  const scoped = useMemo(() => {
    const q = search.trim().toLowerCase();
    return list.filter((t) => {
      if (channel !== 'All' && channelBucket(t.channel) !== channel) return false;
      if (filters.assignment === 'assigned' && !t.assignee) return false;
      if (filters.assignment === 'unassigned' && t.assignee) return false;
      if (filters.unreadOnly && !t.unread) return false;
      if (filters.withAttachments && !(t.attachments && t.attachments.length > 0)) return false;
      if (q) {
        const hay = `${t.name} ${t.preview || ''} ${t.email || ''} ${t.phone || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [list, channel, filters, search]);

  const counts = useMemo(() => ({
    all: scoped.length,
    pending: scoped.filter((t) => (t.status || '').toLowerCase() === 'pending').length,
    unassigned: scoped.filter((t) => !t.assignee).length,
  }), [scoped]);

  const visible = useMemo(() => scoped.filter((t) => {
    if (tab === 'pending' && (t.status || '').toLowerCase() !== 'pending') return false;
    if (tab === 'unassigned' && t.assignee) return false;
    return true;
  }), [scoped, tab]);

  const setChannel = (c) => {
    const next = new URLSearchParams(params);
    if (c === 'All') next.delete('channel');
    else next.set('channel', c);
    setParams(next, { replace: true });
  };

  const requestDraft = async () => {
    if (!activeThread || draftLoading) return;
    setDraftLoading(true);
    try {
      const res = await api.post('/ai/draft', { conversationId: activeThread.id });
      if (res?.draft) setDraft(res.draft);
    } catch (e) {
      // silent — the chip just won't fill anything
    } finally {
      setDraftLoading(false);
    }
  };

  const send = () => {
    if ((!draft.trim() && pending.length === 0) || !activeThread) return;
    const targetId = activeThread.id;
    const msgAttachments = pending.map(({ id, kind, name, size, mime, url }) => ({ id, kind, name, size, mime, url }));
    const replyTo = replyingTo && replyingTo.threadId === targetId ? replyingTo.ref : null;
    const tempId = `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    const text = draft;

    // Optimistic insert — the WS `message.created` arriving later will replace this by tempId.
    setList((prev) => prev.map((t) => t.id === targetId
      ? { ...t, messages: [...(t.messages || []), { id: tempId, tempId, from: 'me', at: 'now', text, delivered: false, attachments: msgAttachments.length ? msgAttachments : undefined, replyTo: replyTo || undefined }] }
      : t));
    setDraft('');
    setPending([]);
    setReplyingTo(null);

    api.post(`/conversations/${encodeURIComponent(targetId)}/messages`, {
      text, attachments: msgAttachments, replyTo, tempId,
    }).then((saved) => {
      setList((prev) => prev.map((t) => t.id === targetId
        ? { ...t, messages: (t.messages || []).map((m) => m.tempId === tempId ? { ...saved, tempId } : m) }
        : t));
    }).catch(() => {
      // Mark as failed — keep optimistic bubble, flag it
      setList((prev) => prev.map((t) => t.id === targetId
        ? { ...t, messages: (t.messages || []).map((m) => m.tempId === tempId ? { ...m, failed: true, delivered: false } : m) }
        : t));
    });
  };

  const startReply = (m) => {
    if (!activeThread) return;
    const snippet = (m.text || (m.attachments && m.attachments.length ? `${m.attachments.length} attachment${m.attachments.length > 1 ? 's' : ''}` : '')).toString();
    const authorName = m.from === 'me'
      ? 'You'
      : m.from === 'them'
        ? (m.fromName || activeThread.name || 'Customer')
        : 'System';
    setReplyingTo({
      threadId: activeThread.id,
      ref: { from: m.from, author: authorName, text: snippet, at: m.at },
    });
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const addFiles = async (fileList, kindHint) => {
    if (!activeThread || !fileList || fileList.length === 0) return;
    // Optimistic insert with local blob URL so the preview renders immediately,
    // then kick off the presign→PUT upload from api.js (wire.md §5) and swap
    // the url to the public one on success.
    const items = Array.from(fileList).map((f) => ({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      kind: kindHint || (f.type.startsWith('image/') ? 'image' : 'file'),
      name: f.name,
      size: f.size,
      mime: f.type,
      url: URL.createObjectURL(f),
      _file: f,
      uploading: true,
    }));
    setPending((p) => [...p, ...items]);
    for (const it of items) {
      try {
        const out = await uploadFile(it._file);
        setPending((p) => p.map((x) => {
          if (x.id !== it.id) return x;
          try { URL.revokeObjectURL(x.url); } catch {}
          return { ...x, url: out.url, key: out.key, uploading: false, _file: undefined };
        }));
      } catch (err) {
        setPending((p) => p.map((x) => (x.id === it.id ? { ...x, uploading: false, error: String(err?.message || err) } : x)));
      }
    }
  };

  const addLink = (raw) => {
    const url = String(raw || '').trim();
    if (!url) return;
    const normalized = /^https?:\/\//i.test(url) ? url : `https://${url}`;
    setPending((p) => [...p, {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      kind: 'link',
      name: normalized,
      url: normalized,
    }]);
  };

  const removePending = (id) => {
    setPending((p) => {
      const gone = p.find((x) => x.id === id);
      if (gone && gone.kind !== 'link' && gone.url) {
        try { URL.revokeObjectURL(gone.url); } catch {}
      }
      return p.filter((x) => x.id !== id);
    });
  };

  const onPickFiles = (kind) => () => {
    if (!activeThread) return;
    const ref = kind === 'image' ? imageInputRef : fileInputRef;
    ref.current?.click();
  };

  const onFilesSelected = (kind) => (e) => {
    addFiles(e.target.files, kind);
    e.target.value = '';
  };

  const onDropFiles = (e) => {
    e.preventDefault();
    dragDepth.current = 0;
    setDragOver(false);
    if (!activeThread) return;
    const dt = e.dataTransfer;
    if (dt.files && dt.files.length > 0) {
      addFiles(dt.files);
      return;
    }
    const uri = dt.getData('text/uri-list') || dt.getData('text/plain');
    if (uri && /^https?:\/\/\S+$/i.test(uri.trim())) addLink(uri.trim());
  };

  const onDragEnter = (e) => {
    if (!activeThread) return;
    if (!Array.from(e.dataTransfer?.types || []).some((t) => t === 'Files' || t === 'text/uri-list' || t === 'text/plain')) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragOver(true);
  };
  const onDragOver = (e) => { if (activeThread) e.preventDefault(); };
  const onDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragOver(false);
  };

  const onPasteInput = (e) => {
    if (!activeThread) return;
    const items = e.clipboardData?.items || [];
    const files = [];
    for (const it of items) {
      if (it.kind === 'file') {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  };

  const closePlus = () => {
    setPlusOpen(false);
    setPlusMode('root');
    setLinkDraft('');
    setTicketFilter('');
  };

  const [deletePrompt, setDeletePrompt] = useState(null); // { conv } | null
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const deleteModal = useDelayUnmount(deletePrompt !== null, 200);

  const requestDeleteConversation = (conv) => {
    if (!conv?.id) return;
    setDeleteError(null);
    setDeletePrompt({ conv });
  };

  const confirmDeleteConversation = async () => {
    const conv = deletePrompt?.conv;
    if (!conv?.id || deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await api.del(`/conversations/${encodeURIComponent(conv.id)}`);
      setList((cur) => cur.filter((c) => c.id !== conv.id));
      setSelectedId((cur) => (cur === conv.id ? null : cur));
      setDeletePrompt(null);
    } catch (err) {
      setDeleteError(String(err?.message || err || 'Delete failed'));
    } finally {
      setDeleteBusy(false);
    }
  };

  const cancelDeleteConversation = () => {
    if (deleteBusy) return;
    setDeletePrompt(null);
    setDeleteError(null);
  };

  useEffect(() => {
    if (!deletePrompt) return;
    const onKey = (e) => {
      if (e.key === 'Escape') cancelDeleteConversation();
      else if (e.key === 'Enter') {
        e.preventDefault();
        confirmDeleteConversation();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deletePrompt, deleteBusy]);

  const submitLink = () => {
    if (!linkDraft.trim()) return;
    addLink(linkDraft);
    closePlus();
  };

  const attachTicket = (t) => {
    const id = Date.now() + Math.random();
    setPending((prev) => [
      ...prev,
      { id, kind: 'ticket', name: `Ticket ${t.id}`, sub: t.status || t.source || '', ticket: t },
    ]);
    closePlus();
  };

  const addNote = async (threadId, text) => {
    if (!text.trim()) return;
    const now = new Date();
    const at = `${now.toLocaleDateString([], { month: 'short', day: 'numeric' })} · ${now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    const tempId = `tmp-${Date.now()}`;
    setList((prev) => prev.map((t) => t.id === threadId
      ? { ...t, notes: [...(t.notes || []), { id: tempId, at, text, author: 'You' }] }
      : t));
    try {
      const saved = await api.post(`/conversations/${encodeURIComponent(threadId)}/notes`, { text });
      setList((prev) => prev.map((t) => t.id === threadId
        ? { ...t, notes: (t.notes || []).map((n) => (n.id === tempId ? { ...saved } : n)) }
        : t));
    } catch {
      // leave the optimistic note in place so the user can retry by re-adding
    }
  };

  const simulateInbound = async () => {
    if (!activeThread) return;
    const text = window.prompt('Simulate inbound customer message:');
    if (!text || !text.trim()) return;
    try {
      await api.post(`/conversations/${encodeURIComponent(activeThread.id)}/inbound`, {
        text: text.trim(),
        fromName: activeThread.customer?.name || activeThread.name || 'Customer',
      });
      // WebSocket `message.created` + `conversation.updated` handle the rest.
    } catch (e) {
      alert(`Inbound failed: ${e.message || e}`);
    }
  };

  const tickets = selected?.tickets || [];

  return (
    <>
      <TopBar
        left={
          <div className="dropdown">
            <button className="workspace-select" onClick={() => setChannelOpen((v) => !v)}>
              <Icon.Menu />
              <span>{channel}</span>
              <Icon.Chevron />
            </button>
            {channelMenu.mounted && (
              <div
                className={'dropdown-menu' + (channelMenu.state === 'exiting' ? ' closing' : '')}
                style={{ left: 0 }}
              >
                {CHANNELS.map((c) => (
                  <button key={c} className="dropdown-item" onClick={() => { setChannel(c); setChannelOpen(false); }}>
                    {c}
                  </button>
                ))}
              </div>
            )}
          </div>
        }
      />
      <div className="main">
        <div className="pane">
          <div className="left-pane">
            <div className="list-header">
              <div className="list-toolbar">
                <div className="list-toolbar-hint">
                  {search.trim() ? (
                    <>Filtering by <b>"{search}"</b></>
                  ) : (
                    <>{visible.length} {visible.length === 1 ? 'conversation' : 'conversations'}</>
                  )}
                </div>
                <SyncInboxButton onSynced={() => {
                  api.get('/conversations').then((rows) => setList(Array.isArray(rows) ? rows : [])).catch(() => {});
                }} />
                <div className="dropdown" style={{ display: 'flex' }}>
                  <button
                    className={'search-icon-btn boxed' + (filterOpen || activeFilterCount > 0 ? ' active' : '')}
                    title="Filters"
                    onClick={() => setFilterOpen((v) => !v)}
                  >
                    <Icon.Filter />
                    {activeFilterCount > 0 && <span className="search-icon-dot">{activeFilterCount}</span>}
                  </button>
                  {filterMenu.mounted && (
                    <div
                      className={'dropdown-menu filter-menu' + (filterMenu.state === 'exiting' ? ' closing' : '')}
                      style={{ right: 0, left: 'auto' }}
                    >
                      <div className="filter-menu-title">Filters</div>
                      <div className="filter-group">
                        <div className="filter-group-label">Assignment</div>
                        {['any', 'assigned', 'unassigned'].map((v) => (
                          <label key={v} className="filter-option">
                            <input
                              type="radio"
                              name="assignment"
                              checked={filters.assignment === v}
                              onChange={() => setFilters((f) => ({ ...f, assignment: v }))}
                            />
                            <span>{v[0].toUpperCase() + v.slice(1)}</span>
                          </label>
                        ))}
                      </div>
                      <div className="filter-group">
                        <label className="filter-option">
                          <input
                            type="checkbox"
                            checked={filters.unreadOnly}
                            onChange={(e) => setFilters((f) => ({ ...f, unreadOnly: e.target.checked }))}
                          />
                          <span>Unread only</span>
                        </label>
                        <label className="filter-option">
                          <input
                            type="checkbox"
                            checked={filters.withAttachments}
                            onChange={(e) => setFilters((f) => ({ ...f, withAttachments: e.target.checked }))}
                          />
                          <span>Has attachments</span>
                        </label>
                      </div>
                      <div className="filter-menu-foot">
                        <button
                          className="btn ghost sm"
                          onClick={() => setFilters({ assignment: 'any', unreadOnly: false, withAttachments: false })}
                        >
                          Reset
                        </button>
                        <button className="btn sm" onClick={() => setFilterOpen(false)}>Done</button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
              <div className="tabs">
                <button className={'tab' + (tab === 'all' ? ' active' : '')} onClick={() => setTab('all')}>
                  All <span className={'count ' + (tab === 'all' ? '' : 'gray')}>{counts.all}</span>
                </button>
                <button className={'tab' + (tab === 'pending' ? ' active' : '')} onClick={() => setTab('pending')}>
                  Pending <span className={'count ' + (tab === 'pending' ? '' : 'red')}>{counts.pending}</span>
                </button>
                <button className={'tab' + (tab === 'unassigned' ? ' active' : '')} onClick={() => setTab('unassigned')}>
                  Unassigned <span className={'count ' + (tab === 'unassigned' ? '' : 'gray')}>{counts.unassigned}</span>
                </button>
              </div>
            </div>
            <div className="conv-list">
              {listLoading ? (
                <div style={{ padding: '40px 16px', display: 'flex', justifyContent: 'center' }}>
                  <Spinner size={24} />
                </div>
              ) : (
                <>
                  {visible.map((t) => (
                    <ConvItem key={t.id} t={t} selected={t.id === selectedId} onClick={() => setSelectedId(t.id)} />
                  ))}
                  {visible.length === 0 && (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
                      No conversations in {channel}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          <div className="center-pane">
            {selected ? (
              <>
                <ChatHead
                  t={selected}
                  activeChannel={activeChannel}
                  onChannelChange={setViewChannel}
                  onDelete={() => requestDeleteConversation(selected)}
                />
                {activeThread?.orderId && (
                  <div className="order-bar">
                    <button className="order-back"><Icon.Back /> Back</button>
                    <div className="order-number">{activeThread.orderId}</div>
                    <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                      <button className="icon-btn" title="Drag"><Icon.DragHandle /></button>
                      <button className="icon-btn" title="Notes"><Icon.Document /></button>
                      <button className="icon-btn" title="Email"><Icon.Mail /></button>
                      <button className="icon-btn" title="More"><Icon.More /></button>
                    </div>
                  </div>
                )}
                <div className="chat-body" ref={chatBodyRef}>
                  {activeThread ? (
                    (activeThread.messages || []).length === 0 ? (
                      <div className="empty-state" style={{ margin: 'auto' }}>
                        <Icon.InboxEmpty />
                        <div className="empty-state-text">No conversation on {activeChannel}</div>
                      </div>
                    ) : (
                      (activeThread.messages || []).map((m, i) => {
                        if (m.from === 'system') {
                          return (
                            <div key={i} className="system-line">
                              <Icon.Info /> {m.text} • {m.at}
                            </div>
                          );
                        }
                        const isEmailThread = (
                          activeThread.channel === 'Mails' ||
                          activeThread.channel === 'email' ||
                          activeThread.channel === 'gmail' ||
                          activeThread.channel === 'outlook'
                        );
                        if (isEmailThread) {
                          const accent = m.from === 'them' ? '#f97316' : '#16a34a';
                          const displayName = m.fromName || (m.from === 'them' ? activeThread.name : (m.aiAgentId ? 'AI Agent' : 'You'));
                          const { primary, quoted } = splitEmailBody(m.text || '');
                          return (
                            <div key={i} className={'mail-msg' + (m.from === 'me' ? ' mail-msg-mine' : '')}>
                              <div className="mail-head">
                                <div className="avatar" style={{ background: accent }}>
                                  {initials(displayName)}
                                </div>
                                <div style={{ flex: 1 }}>
                                  <div style={{ fontWeight: 700 }}>{displayName}</div>
                                  {m.from === 'them' && (activeThread.email || activeThread.phone) && (
                                    <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                                      {activeThread.email || activeThread.phone}
                                    </div>
                                  )}
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--muted)' }}>{m.at}</div>
                                {!m.streaming && (
                                  <button
                                    type="button"
                                    className="msg-reply-btn inline"
                                    title="Reply"
                                    onClick={() => startReply(m)}
                                  >
                                    <Icon.Reply />
                                  </button>
                                )}
                              </div>
                              <div className="mail-body">
                                <MessageText text={primary} />
                                {m.streaming && <span className="stream-cursor">▍</span>}
                                {quoted && <QuotedBlock text={quoted} />}
                              </div>
                            </div>
                          );
                        }
                        return (
                          <div key={i} className={'msg-row ' + m.from}>
                            {m.from === 'them' && (
                              <div className="avatar sm" style={{ background: '#475569' }}>+8</div>
                            )}
                            {m.from === 'me' && (
                              <div className="avatar sm" style={{ background: m.aiAgentId || m.streaming ? '#7c3aed' : '#16a34a' }}>AI</div>
                            )}
                            <div className="msg-stack">
                              <div className={'bubble ' + m.from + (m.streaming ? ' streaming' : '')}>
                                {m.replyTo && (
                                  <div className="bubble-quote">
                                    <div className="bubble-quote-bar" />
                                    <div className="bubble-quote-body">
                                      <div className="bubble-quote-name">{m.replyTo.author}</div>
                                      <div className="bubble-quote-text">{m.replyTo.text}</div>
                                    </div>
                                  </div>
                                )}
                                {(m.text || m.streaming) && (
                                  <div>
                                    {m.text
                                      ? <MessageText text={m.text} />
                                      : (m.streaming ? <span style={{ opacity: 0.6 }}>drafting…</span> : null)}
                                    {m.streaming && <span className="stream-cursor">▍</span>}
                                  </div>
                                )}
                                {m.attachments && m.attachments.length > 0 && (
                                  <MessageAttachments items={m.attachments} />
                                )}
                              </div>
                              <div className="msg-meta" style={{ justifyContent: m.from === 'me' ? 'flex-end' : 'flex-start' }}>
                                {m.at}
                                {m.streaming && <> • typing…</>}
                                {!m.streaming && m.delivered && <><Icon.Check2 /> Delivered</>}
                              </div>
                            </div>
                            {!m.streaming && (
                              <button
                                type="button"
                                className="msg-reply-btn"
                                title="Reply"
                                onClick={() => startReply(m)}
                              >
                                <Icon.Reply />
                              </button>
                            )}
                          </div>
                        );
                      })
                    )
                  ) : (
                    <div className="empty-state" style={{ margin: 'auto' }}>
                      <Icon.InboxEmpty />
                      <div className="empty-state-text">
                        No conversation on {activeChannel} for {selected.name}
                      </div>
                    </div>
                  )}
                </div>
                <div
                  className={'chat-input-wrap' + (dragOver ? ' drag-over' : '')}
                  onDragEnter={onDragEnter}
                  onDragOver={onDragOver}
                  onDragLeave={onDragLeave}
                  onDrop={onDropFiles}
                >
                  {dragOver && (
                    <div className="chat-drop-overlay">
                      <Icon.Paperclip /> Drop files or a link to attach
                    </div>
                  )}
                  {pending.length > 0 && (
                    <div className="attach-row">
                      {pending.map((a) => (
                        <div key={a.id} className={'attach-chip ' + a.kind}>
                          {a.kind === 'image' ? (
                            <img src={a.url} alt={a.name} />
                          ) : (
                            <span className="attach-icon">
                              {a.kind === 'link' ? <Icon.MailAt />
                                : a.kind === 'ticket' ? <Icon.Ticket />
                                : <Icon.Paperclip />}
                            </span>
                          )}
                          <div className="attach-meta">
                            <div className="attach-name" title={a.name}>{a.name}</div>
                            {a.kind !== 'link' && a.kind !== 'ticket' && a.size != null && (
                              <div className="attach-sub">{formatBytes(a.size)}</div>
                            )}
                            {a.kind === 'link' && (
                              <div className="attach-sub">Link</div>
                            )}
                            {a.kind === 'ticket' && (
                              <div className="attach-sub">{a.sub || 'Ticket'}</div>
                            )}
                          </div>
                          <button
                            type="button"
                            className="attach-remove"
                            title="Remove"
                            onClick={() => removePending(a.id)}
                          >
                            <Icon.Close />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  {replyingTo && replyingTo.threadId === activeThread?.id && (
                    <div className="reply-preview">
                      <div className="reply-preview-icon"><Icon.Reply /></div>
                      <div className="reply-preview-bar" />
                      <div className="reply-preview-body">
                        <div className="reply-preview-name">
                          Replying to <strong>{replyingTo.ref.author}</strong>
                        </div>
                        <div className="reply-preview-text">{replyingTo.ref.text}</div>
                      </div>
                      <button
                        type="button"
                        className="reply-preview-close"
                        title="Cancel reply"
                        onClick={() => setReplyingTo(null)}
                      >
                        <Icon.Close />
                      </button>
                    </div>
                  )}
                  <div className="chat-input-row">
                    <div className="dropdown">
                      <button
                        type="button"
                        className={'chat-plus-btn' + (plusOpen ? ' on' : '')}
                        title="Add"
                        disabled={!activeThread}
                        onClick={() => {
                          if (plusOpen) closePlus();
                          else { setPlusMode('root'); setPlusOpen(true); }
                        }}
                      >
                        <Icon.Plus />
                      </button>
                      {plusOpen && (
                        <div
                          className="dropdown-menu chat-plus-menu"
                          style={{ left: 0, bottom: '100%', top: 'auto', marginBottom: 8 }}
                        >
                          {plusMode === 'root' && (
                            <>
                              <button
                                type="button"
                                className="chat-plus-item"
                                onClick={() => { closePlus(); onPickFiles('image')(); }}
                              >
                                <Icon.Image />
                                <span className="chat-plus-item-text">
                                  <span className="chat-plus-item-title">Photo</span>
                                  <span className="chat-plus-item-sub">Attach an image</span>
                                </span>
                              </button>
                              <button
                                type="button"
                                className="chat-plus-item"
                                onClick={() => { closePlus(); onPickFiles('file')(); }}
                              >
                                <Icon.Paperclip />
                                <span className="chat-plus-item-text">
                                  <span className="chat-plus-item-title">File</span>
                                  <span className="chat-plus-item-sub">Upload a document</span>
                                </span>
                              </button>
                              <button
                                type="button"
                                className="chat-plus-item"
                                onClick={() => setPlusMode('link')}
                              >
                                <Icon.MailAt />
                                <span className="chat-plus-item-text">
                                  <span className="chat-plus-item-title">Link</span>
                                  <span className="chat-plus-item-sub">Paste a URL</span>
                                </span>
                              </button>
                              <button
                                type="button"
                                className="chat-plus-item"
                                disabled={tickets.length === 0}
                                onClick={() => tickets.length && setPlusMode('ticket')}
                              >
                                <Icon.Ticket />
                                <span className="chat-plus-item-text">
                                  <span className="chat-plus-item-title">Ticket</span>
                                  <span className="chat-plus-item-sub">
                                    {tickets.length
                                      ? `Reference one of ${tickets.length}`
                                      : 'No tickets on this contact'}
                                  </span>
                                </span>
                              </button>
                              <button
                                type="button"
                                className="chat-plus-item"
                                disabled={!activeThread || draftLoading}
                                onClick={() => { closePlus(); requestDraft(); }}
                                title="Generate an AI draft for this conversation"
                              >
                                <Icon.Check2 />
                                <span className="chat-plus-item-text">
                                  <span className="chat-plus-item-title">
                                    {draftLoading ? 'Drafting…' : 'AI Draft'}
                                  </span>
                                  <span className="chat-plus-item-sub">
                                    Generate a reply suggestion
                                  </span>
                                </span>
                              </button>
                              <button
                                type="button"
                                className="chat-plus-item"
                                disabled={!activeThread}
                                onClick={() => { closePlus(); simulateInbound(); }}
                                title="Simulate an inbound customer message — hits POST /conversations/:id/inbound (intent classifier, attach/reopen/create)"
                              >
                                <Icon.Reply />
                                <span className="chat-plus-item-text">
                                  <span className="chat-plus-item-title">Simulate inbound</span>
                                  <span className="chat-plus-item-sub">
                                    Post a fake customer message
                                  </span>
                                </span>
                              </button>
                            </>
                          )}
                          {plusMode === 'link' && (
                            <div className="chat-plus-form">
                              <div className="chat-plus-form-head">
                                <button
                                  type="button"
                                  className="chat-plus-back"
                                  onClick={() => setPlusMode('root')}
                                  title="Back"
                                >
                                  <Icon.Back />
                                </button>
                                <span>Add link</span>
                              </div>
                              <input
                                type="url"
                                autoFocus
                                className="chat-plus-input"
                                placeholder="https://example.com"
                                value={linkDraft}
                                onChange={(e) => setLinkDraft(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') { e.preventDefault(); submitLink(); }
                                  if (e.key === 'Escape') { closePlus(); }
                                }}
                              />
                              <div className="chat-plus-form-foot">
                                <button
                                  type="button"
                                  className="chat-plus-cancel"
                                  onClick={closePlus}
                                >
                                  Cancel
                                </button>
                                <button
                                  type="button"
                                  className="chat-plus-submit"
                                  onClick={submitLink}
                                  disabled={!linkDraft.trim()}
                                >
                                  Add
                                </button>
                              </div>
                            </div>
                          )}
                          {plusMode === 'ticket' && (
                            <div className="chat-plus-form">
                              <div className="chat-plus-form-head">
                                <button
                                  type="button"
                                  className="chat-plus-back"
                                  onClick={() => setPlusMode('root')}
                                  title="Back"
                                >
                                  <Icon.Back />
                                </button>
                                <span>Attach ticket</span>
                              </div>
                              <input
                                type="text"
                                autoFocus
                                className="chat-plus-input"
                                placeholder="Search tickets…"
                                value={ticketFilter}
                                onChange={(e) => setTicketFilter(e.target.value)}
                                onKeyDown={(e) => { if (e.key === 'Escape') closePlus(); }}
                              />
                              <div className="chat-plus-ticket-list">
                                {tickets
                                  .filter((t) => {
                                    const q = ticketFilter.trim().toLowerCase();
                                    if (!q) return true;
                                    return (
                                      (t.id || '').toLowerCase().includes(q) ||
                                      (t.status || '').toLowerCase().includes(q) ||
                                      (t.task || '').toLowerCase().includes(q) ||
                                      (t.source || '').toLowerCase().includes(q)
                                    );
                                  })
                                  .map((t) => (
                                    <button
                                      key={t.id}
                                      type="button"
                                      className="chat-plus-ticket"
                                      onClick={() => attachTicket(t)}
                                    >
                                      <Icon.Ticket />
                                      <span className="chat-plus-ticket-text">
                                        <span className="chat-plus-ticket-id">{t.id}</span>
                                        <span className="chat-plus-ticket-sub">
                                          {t.status}{t.source ? ` · ${t.source}` : ''}
                                        </span>
                                      </span>
                                    </button>
                                  ))}
                                {tickets.filter((t) => {
                                  const q = ticketFilter.trim().toLowerCase();
                                  if (!q) return true;
                                  return (
                                    (t.id || '').toLowerCase().includes(q) ||
                                    (t.status || '').toLowerCase().includes(q) ||
                                    (t.task || '').toLowerCase().includes(q) ||
                                    (t.source || '').toLowerCase().includes(q)
                                  );
                                }).length === 0 && (
                                  <div className="chat-plus-ticket-empty">No matches</div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                    <textarea
                      ref={textareaRef}
                      className="chat-input with-hint-placeholder"
                      placeholder={activeThread ? 'Enter to send · Shift+Enter for newline' : `No conversation on ${activeChannel}`}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onPaste={onPasteInput}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape' && replyingTo) {
                          e.preventDefault();
                          setReplyingTo(null);
                          return;
                        }
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault();
                          send();
                        }
                      }}
                      disabled={!activeThread}
                      rows={1}
                    />
                    <button
                      type="button"
                      className="chat-send-btn"
                      onClick={send}
                      disabled={!activeThread || (!draft.trim() && pending.length === 0)}
                      title="Send"
                    >
                      <Icon.Send />
                    </button>
                  </div>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    style={{ display: 'none' }}
                    onChange={onFilesSelected()}
                  />
                  <input
                    ref={imageInputRef}
                    type="file"
                    accept="image/*"
                    multiple
                    style={{ display: 'none' }}
                    onChange={onFilesSelected('image')}
                  />
                </div>
              </>
            ) : (
              <div style={{ margin: 'auto', color: 'var(--muted)' }}>Select a conversation</div>
            )}
          </div>

          {rightAnim.mounted && visibleRight === 'ticket' && selected && (
            <TicketSidePanel
              tickets={tickets}
              closing={rightClosing}
              onClose={() => setRightPanel(null)}
            />
          )}
          {rightAnim.mounted && visibleRight === 'customer' && selected && (
            <CustomerSidePanel
              customer={selected.customer}
              fallback={selected}
              closing={rightClosing}
              onClose={() => setRightPanel(null)}
            />
          )}
          {rightAnim.mounted && visibleRight === 'notes' && selected && (
            <NotesSidePanel
              notes={selected.notes || []}
              onAdd={(text) => addNote(selected.id, text)}
              closing={rightClosing}
              onClose={() => setRightPanel(null)}
            />
          )}
          {rightAnim.mounted && visibleRight === 'recordings' && selected && (
            <RecordingsSidePanel
              recordings={selected.recordings || []}
              closing={rightClosing}
              onClose={() => setRightPanel(null)}
            />
          )}
          {rightAnim.mounted && visibleRight === 'media' && selected && (
            <MediaSidePanel
              attachments={selected.attachments || []}
              closing={rightClosing}
              onClose={() => setRightPanel(null)}
            />
          )}

          <aside className="right-rail">
            <RailButton
              label="Customer"
              icon={<Icon.User />}
              active={rightPanel === 'customer'}
              onClick={() => setRightPanel((p) => (p === 'customer' ? null : 'customer'))}
            />
            <RailButton
              label="Tickets"
              icon={<Icon.Ticket />}
              badge={tickets.length || undefined}
              active={rightPanel === 'ticket'}
              onClick={() => setRightPanel((p) => (p === 'ticket' ? null : 'ticket'))}
            />
            <RailButton
              label="Recordings"
              icon={<Icon.Send />}
              active={rightPanel === 'recordings'}
              onClick={() => setRightPanel((p) => (p === 'recordings' ? null : 'recordings'))}
            />
            <RailButton
              label="Timeline"
              icon={<Icon.Clock />}
              active={rightPanel === 'timeline'}
              onClick={() => setRightPanel((p) => (p === 'timeline' ? null : 'timeline'))}
            />
            <RailButton
              label="Attachments"
              icon={<Icon.Image />}
              active={rightPanel === 'media'}
              onClick={() => setRightPanel((p) => (p === 'media' ? null : 'media'))}
            />
            <RailButton
              label="Notes"
              icon={<Icon.Document />}
              active={rightPanel === 'notes'}
              onClick={() => setRightPanel((p) => (p === 'notes' ? null : 'notes'))}
            />
          </aside>
        </div>
      </div>
      {deleteModal.mounted && createPortal(
        <DeleteConversationDialog
          conv={deletePrompt?.conv}
          busy={deleteBusy}
          error={deleteError}
          exiting={deleteModal.state === 'exiting'}
          onCancel={cancelDeleteConversation}
          onConfirm={confirmDeleteConversation}
        />,
        document.body,
      )}
    </>
  );
}

function DeleteConversationDialog({ conv, busy, error, exiting, onCancel, onConfirm }) {
  const confirmBtnRef = useRef(null);
  useEffect(() => {
    if (!exiting) confirmBtnRef.current?.focus();
  }, [exiting]);
  const name = conv?.name || 'this customer';
  const channel = conv?.channel || 'the source channel';
  return (
    <div
      className={'modal-backdrop confirm-backdrop' + (exiting ? ' closing' : '')}
      onClick={onCancel}
    >
      <div
        className={'confirm-dialog' + (exiting ? ' anim-exit' : ' anim-enter')}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-conv-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="confirm-icon" style={{ background: 'rgba(239,68,68,0.12)', color: '#dc2626' }}>
          <Icon.Trash style={{ width: 22, height: 22 }} />
        </div>
        <div className="confirm-body">
          <div className="confirm-title" id="delete-conv-title">
            Delete conversation with {name}?
          </div>
          <ul className="confirm-sub" style={{ margin: 0, paddingLeft: 18 }}>
            <li>All messages and notes in this thread will be removed.</li>
            <li>Linked tickets (including parent/child tickets) will be removed.</li>
            <li>The source thread on {channel} will also be trashed where supported.</li>
          </ul>
          <div className="confirm-sub" style={{ marginTop: 8, fontWeight: 600 }}>
            This cannot be undone.
          </div>
          {error && (
            <div className="confirm-sub" style={{ marginTop: 8, color: '#dc2626' }}>
              {error}
            </div>
          )}
        </div>
        <div className="confirm-actions">
          <button
            className="confirm-btn confirm-btn-ghost"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            ref={confirmBtnRef}
            className="confirm-btn confirm-btn-danger"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Strip the quoted-reply block (`On <date>, <addr> wrote:` + everything after,
// plus `>`-prefixed lines) out of an email body so the primary text reads
// cleanly. Returns `{primary, quoted}` so the UI can still show the quote in
// a collapsed block.
function splitEmailBody(raw) {
  if (!raw) return { primary: '', quoted: '' };
  const lines = String(raw).split(/\r?\n/);
  // Find the quote header line: "On <date>, <addr> wrote:"
  let cut = -1;
  for (let i = 0; i < lines.length; i++) {
    if (/^on .+ wrote:\s*$/i.test(lines[i].trim())) { cut = i; break; }
    if (/^-----original message-----/i.test(lines[i].trim())) { cut = i; break; }
  }
  if (cut < 0) {
    // Also treat a block of `>`-prefixed lines at the end as quoted.
    let j = lines.length - 1;
    while (j >= 0 && lines[j].trim() === '') j--;
    let start = j;
    while (start >= 0 && /^\s*>/.test(lines[start])) start--;
    if (start < j - 1) cut = start + 1;
  }
  if (cut < 0) return { primary: raw.trim(), quoted: '' };
  const primary = lines.slice(0, cut).join('\n').trim();
  const quoted = lines.slice(cut).join('\n').trim();
  return { primary, quoted };
}

// Render a plain-text body with newlines preserved, URLs linkified, and light
// markdown support (**bold**, `code`). Intentionally minimal — we're rendering
// text from customer/agent/AI, and we never want to let raw HTML through.
function MessageText({ text }) {
  if (!text) return null;
  const withLinks = linkify(text);
  return <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{withLinks}</span>;
}

function linkify(text) {
  const parts = [];
  const re = /(https?:\/\/[^\s<>]+|www\.[^\s<>]+)/gi;
  let last = 0;
  let m;
  let idx = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(renderMd(text.slice(last, m.index), idx++));
    const url = m[0].startsWith('http') ? m[0] : `http://${m[0]}`;
    parts.push(
      <a key={`l-${idx++}`} href={url} target="_blank" rel="noreferrer"
         style={{ color: 'var(--accent, #60a5fa)', textDecoration: 'underline' }}>
        {m[0]}
      </a>
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(renderMd(text.slice(last), idx++));
  return parts;
}

function renderMd(chunk, key) {
  // **bold** and `code` only — keep scope tight, no HTML injection.
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m;
  let idx = 0;
  while ((m = re.exec(chunk)) !== null) {
    if (m.index > last) out.push(chunk.slice(last, m.index));
    const token = m[0];
    if (token.startsWith('**')) {
      out.push(<strong key={`b-${key}-${idx++}`}>{token.slice(2, -2)}</strong>);
    } else {
      out.push(<code key={`c-${key}-${idx++}`} style={{
        background: 'var(--surface-2, #1f2937)', padding: '1px 5px',
        borderRadius: 4, fontSize: '0.92em',
      }}>{token.slice(1, -1)}</code>);
    }
    last = m.index + token.length;
  }
  if (last < chunk.length) out.push(chunk.slice(last));
  return <span key={`t-${key}`}>{out}</span>;
}

function QuotedBlock({ text }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginTop: 10 }}>
      <button type="button" onClick={() => setOpen((v) => !v)}
              style={{
                fontSize: 12, color: 'var(--muted)', background: 'transparent',
                border: 'none', cursor: 'pointer', padding: '2px 0',
              }}>
        {open ? '▾ hide quoted' : '▸ show quoted'}
      </button>
      {open && (
        <div style={{
          marginTop: 6, paddingLeft: 10, borderLeft: '3px solid var(--border)',
          color: 'var(--muted)', fontSize: 13, whiteSpace: 'pre-wrap',
        }}>
          {text}
        </div>
      )}
    </div>
  );
}

function SyncInboxButton({ onSynced }) {
  // Forces a Gmail pull through `POST /platforms/gmail/sync`. The background
  // worker runs the same poll every 30 s, but clicking here gives the operator
  // an immediate signal that inbound email pulled into the inbox — and works
  // even if they never started the worker process.
  const [state, setState] = useState('idle');
  const [tip, setTip] = useState(null);
  const run = async () => {
    setState('syncing'); setTip(null);
    try {
      const r = await api.post('/platforms/gmail/sync');
      const ingested = r?.ingested ?? 0;
      const fetched = r?.fetched ?? 0;
      const accounts = r?.accounts ?? 0;
      // accounts=0 has two very different causes: the user never connected
      // Gmail, or they did connect but the token now needs a reauth. The
      // backend tells us which via `connected`/`needs_reauth` so the nudge
      // actually matches what the operator needs to do.
      let msg;
      if (accounts > 0) {
        msg = `Pulled ${ingested} new / ${fetched} unread across ${accounts} account(s).`;
      } else if (r?.needs_reauth) {
        msg = 'Gmail token expired — reconnect from Platforms.';
      } else if (r?.connected) {
        msg = 'Gmail is connecting — try again in a moment.';
      } else {
        msg = 'No Gmail connected — connect it from Platforms.';
      }
      setTip(msg);
      setState(ingested > 0 ? 'done' : 'idle');
      onSynced?.();
    } catch (e) {
      setTip(`Sync failed: ${e?.message || e}`);
      setState('idle');
    } finally {
      setTimeout(() => setTip(null), 4000);
    }
  };
  return (
    <div style={{ position: 'relative' }}>
      <button
        className="search-icon-btn boxed"
        title="Pull inbound from connected Gmail"
        onClick={run}
        disabled={state === 'syncing'}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          width: 'auto',
          height: 32,
          padding: '0 10px',
          flexShrink: 0,
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{
          display: 'inline-block', width: 14, height: 14,
          animation: state === 'syncing' ? 'spin 0.8s linear infinite' : 'none',
        }}>↻</span>
        <span style={{ fontSize: 12, fontWeight: 600 }}>
          {state === 'syncing' ? 'Syncing…' : 'Sync'}
        </span>
      </button>
      {tip && (
        <div style={{
          position: 'absolute', top: '110%', right: 0, zIndex: 5,
          background: 'var(--card, #111827)', color: 'var(--text, #e5e7eb)',
          border: '1px solid var(--border, #374151)', borderRadius: 8,
          padding: '8px 10px', fontSize: 12, whiteSpace: 'nowrap',
          boxShadow: '0 10px 24px rgba(0,0,0,0.25)',
        }}>{tip}</div>
      )}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

function ConvItem({ t, selected, onClick }) {
  const bucket = channelBucket(t.channel);
  const channelClass = bucket === 'WhatsApp' ? 'whatsapp'
    : bucket === 'Mails' ? 'mail'
    : bucket === 'Instagram' ? 'instagram' : 'call';
  return (
    <div className={'conv-item' + (selected ? ' selected' : '')} onClick={onClick}>
      <div className="avatar" style={{ background: t.isPhone ? '#475569' : colorFor(t.name) }}>
        {t.isPhone ? '+8' : initials(t.name)}
        <span className={'avatar-ch ' + channelClass}>
          {bucket === 'WhatsApp' && <Icon.WhatsApp />}
          {bucket === 'Mails' && <Icon.Mail style={{ width: 10, height: 10 }} />}
          {bucket === 'Instagram' && <Icon.Instagram style={{ width: 10, height: 10 }} />}
          {bucket === 'Live Chat' && <Icon.Phone style={{ width: 10, height: 10 }} />}
        </span>
      </div>
      <div className="conv-body">
        <div className="conv-top">
          <span className="name">{t.name}</span>
          <span className="time">{t.date}</span>
        </div>
        <div className="conv-preview">{t.preview}</div>
        {(t.assignee || t.unread) && (
          <div className="conv-meta">
            {t.assignee && <span className="assignee-chip">{t.assignee}</span>}
            {t.unread && <span className="unread-chip">{t.unread}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function ChatHead({ t, activeChannel, onChannelChange, onDelete }) {
  const channels = [];
  if (t.phone) channels.push('WhatsApp');
  if (t.email) channels.push('Mails');
  if (t.phone) channels.push('Call');
  const showSwitcher = channels.length > 1;
  const current = activeChannel || t.channel;

  return (
    <div className="chat-head">
      <div className="chat-head-left">
        <div className="avatar" style={{ background: t.isPhone ? '#475569' : colorFor(t.name) }}>
          {t.isPhone ? '+8' : initials(t.name)}
        </div>
        <div>
          <div className="chat-head-name">{t.name}</div>
          {current !== 'Mails' && t.phone && (
            <div className="chat-head-sub"><Icon.Phone style={{ width: 12, height: 12 }} /> {t.phone}</div>
          )}
          {current === 'Mails' && t.email && (
            <div className="chat-head-sub"><Icon.Mail style={{ width: 12, height: 12 }} /> {t.email}</div>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {(current === 'WhatsApp' || current === 'Live Chat') && t.phone && (
          <button className="call-btn solid">
            <Icon.CallOutline /> Call
          </button>
        )}
        {showSwitcher && (
          <div className="chat-channel-switcher">
            {channels.includes('WhatsApp') && (
              <button
                className={'icon-btn circle' + (current === 'WhatsApp' ? ' on' : '')}
                title="WhatsApp"
                onClick={() => onChannelChange?.('WhatsApp')}
              >
                <Icon.WhatsApp style={{ width: 18, height: 18 }} />
              </button>
            )}
            {channels.includes('Mails') && (
              <button
                className={'icon-btn circle' + (current === 'Mails' ? ' on' : '')}
                title="Email"
                onClick={() => onChannelChange?.('Mails')}
              >
                <Icon.Mail style={{ width: 16, height: 16 }} />
              </button>
            )}
            {channels.includes('Call') && (
              <button
                className={'icon-btn circle' + (current === 'Call' ? ' on' : '')}
                title="Call"
                onClick={() => onChannelChange?.('Call')}
              >
                <Icon.Phone style={{ width: 16, height: 16 }} />
              </button>
            )}
          </div>
        )}
        {onDelete && (
          <button
            className="icon-btn circle chat-head-delete"
            title="Delete conversation"
            onClick={onDelete}
          >
            <Icon.Trash style={{ width: 16, height: 16 }} />
          </button>
        )}
      </div>
    </div>
  );
}

function RailButton({ label, icon, active, onClick, badge }) {
  return (
    <button
      className={'icon-btn' + (active ? ' active' : '')}
      title={label}
      onClick={onClick}
    >
      {icon}
      {badge !== undefined && <span className="icon-badge">{badge}</span>}
    </button>
  );
}

function CustomerSidePanel({ customer, fallback, onClose, closing }) {
  const [recentOpen, setRecentOpen] = useState(true);
  const c = customer || {
    name: fallback?.name,
    phone: fallback?.phone,
    email: fallback?.email,
    orders: 0,
    returns: 0,
    address: null,
    recentOrders: [],
  };

  return (
    <aside className={'right-sidebar ticket-panel' + (closing ? ' closing' : '')}>
      <div className="ticket-panel-head">
        <button className="icon-btn" onClick={onClose}>
          <Icon.Chevron style={{ transform: 'rotate(-90deg)' }} />
        </button>
        <div style={{ fontWeight: 700, fontSize: 16 }}>Customer Information</div>
        <div style={{ marginLeft: 'auto' }} />
        <button className="icon-btn" title="Expand"><Icon.Expand /></button>
      </div>

      <div className="customer-body">
        <div className="customer-head">
          <div className="avatar lg" style={{ background: colorFor(c.name || 'C') }}>
            {initials(c.name || 'C')}
          </div>
          <div style={{ fontWeight: 700, fontSize: 16, marginTop: 10 }}>{c.name}</div>
          {c.phone && (
            <div className="customer-contact">
              <Icon.Phone style={{ width: 14, height: 14 }} />
              <span>{c.phone}</span>
            </div>
          )}
          {c.email && (
            <div className="customer-contact">
              <Icon.Mail style={{ width: 14, height: 14 }} />
              <span>{c.email}</span>
            </div>
          )}
        </div>

        <div className="customer-stats">
          <div className="customer-stat stat-orders">
            <div className="customer-stat-label">ORDERS</div>
            <div className="customer-stat-row">
              <div className="customer-stat-value">{c.orders ?? 0}</div>
              <Icon.Ticket style={{ opacity: 0.45 }} />
            </div>
          </div>
          <div className="customer-stat stat-returns">
            <div className="customer-stat-label">RETURNS</div>
            <div className="customer-stat-row">
              <div className="customer-stat-value">{c.returns ?? 0}</div>
              <Icon.Document style={{ opacity: 0.45 }} />
            </div>
          </div>
        </div>

        {c.address && (
          <div className="customer-section">
            <div className="customer-section-head">
              <Icon.MapPin />
              <span>Primary Address</span>
            </div>
            <div className="customer-address">{c.address}</div>
          </div>
        )}

        {c.otherAddresses && c.otherAddresses.length > 0 && (
          <div className="customer-section">
            <div className="customer-section-head">
              <Icon.MapPin />
              <span>Other Addresses ({c.otherAddresses.length})</span>
            </div>
            <div className="customer-other-addresses">
              {c.otherAddresses.map((addr, i) => (
                <div key={i} className="customer-address alt">{addr}</div>
              ))}
            </div>
          </div>
        )}

        {c.recentOrders && c.recentOrders.length > 0 && (
          <div className="customer-section">
            <button
              className="customer-section-head toggle"
              onClick={() => setRecentOpen((v) => !v)}
            >
              <Icon.Chevron style={{ transform: recentOpen ? 'none' : 'rotate(-90deg)' }} />
              <Icon.ShoppingBag />
              <span>Recent Orders</span>
              <span className="order-count">{c.recentOrders.length}</span>
            </button>
            {recentOpen && (
              <div className="recent-orders">
                {c.recentOrders.map((o) => (
                  <RecentOrder key={o.id} order={o} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function RecentOrder({ order }) {
  return (
    <div className="recent-order">
      <div className="recent-order-top">
        <div className="recent-order-id">{order.id}</div>
        <div className="recent-order-time">{order.time}</div>
      </div>
      {order.status && (
        <div className={'recent-order-status tone-' + (order.statusTone || 'info')}>
          {order.status}
        </div>
      )}
      <div className="recent-order-meta">
        <span className="recent-order-total"><Icon.Info style={{ width: 12, height: 12 }} /> ₹{order.total.toLocaleString()}</span>
        <span className="recent-order-items">
          <Icon.Ticket style={{ width: 12, height: 12 }} /> {order.itemsCount} {order.itemsCount === 1 ? 'item' : 'items'}
        </span>
      </div>
      {order.items && order.items.length > 0 && (
        <div className="recent-order-items-list">
          {order.items.map((it, i) => (
            <div key={i} className="recent-order-item">
              <span className="recent-order-item-name">{it.name}</span>
              <span className="recent-order-item-qty">× {it.qty}</span>
              <span className="recent-order-item-price">₹{it.price.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NotesSidePanel({ notes, onAdd, onClose, closing }) {
  const [text, setText] = useState('');
  return (
    <aside className={'right-sidebar ticket-panel' + (closing ? ' closing' : '')}>
      <div className="ticket-panel-head">
        <div style={{ fontWeight: 700, fontSize: 16 }}>Private Notes</div>
        <div style={{ marginLeft: 'auto' }} />
        <button className="icon-btn" title="Expand"><Icon.Expand /></button>
        <button className="icon-btn" onClick={onClose} title="Close"><Icon.Close /></button>
      </div>
      <div className="notes-body">
        {notes.length === 0 ? (
          <div className="empty-state">
            <Icon.InboxEmpty />
            <div className="empty-state-text">No notes yet</div>
          </div>
        ) : (
          <div className="notes-list">
            {notes.map((n) => (
              <div key={n.id} className="note-card">
                <div className="note-meta">
                  <span className="note-author">{n.author}</span>
                  <span className="note-at">{n.at}</span>
                </div>
                <div className="note-text">{n.text}</div>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="notes-footer">
        <div className="notes-footer-label">Add New Note</div>
        <textarea
          className="notes-input"
          placeholder="Enter a new note..."
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <button
          className="btn"
          disabled={!text.trim()}
          onClick={() => {
            onAdd(text);
            setText('');
          }}
        >
          Add Note
        </button>
      </div>
    </aside>
  );
}

function RecordingsSidePanel({ recordings, onClose, closing }) {
  return (
    <aside className={'right-sidebar ticket-panel' + (closing ? ' closing' : '')}>
      <div className="ticket-panel-head">
        <div style={{ fontWeight: 700, fontSize: 16 }}>Call Recordings</div>
        <div style={{ marginLeft: 'auto' }} />
        <button className="btn secondary sm" title="Refresh">
          <Icon.Refresh /> Refresh
        </button>
        <button className="icon-btn" onClick={onClose} title="Close"><Icon.Close /></button>
      </div>
      <div className="panel-body">
        {recordings.length === 0 ? (
          <div className="empty-state">
            <Icon.InboxEmpty />
            <div className="empty-state-text">No call recordings found</div>
          </div>
        ) : (
          <div className="recordings-list">
            {recordings.map((r) => (
              <div key={r.id} className="recording-card">
                <div className="recording-title">{r.title || r.id}</div>
                <div className="recording-date">{r.date}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

function MediaSidePanel({ attachments, onClose, closing }) {
  return (
    <aside className={'right-sidebar ticket-panel' + (closing ? ' closing' : '')}>
      <div className="ticket-panel-head">
        <div style={{ fontWeight: 700, fontSize: 16 }}>Attachments</div>
        <div style={{ marginLeft: 'auto' }} />
        <button className="btn secondary sm" title="Refresh">
          <Icon.Refresh /> Refresh
        </button>
        <button className="icon-btn" onClick={onClose} title="Close"><Icon.Close /></button>
      </div>
      <div className="panel-body">
        {attachments.length === 0 ? (
          <div className="empty-state">
            <Icon.InboxEmpty />
            <div className="empty-state-text">No attachments found</div>
          </div>
        ) : (
          <div className="media-grid">
            {attachments.map((a) => (
              <div key={a.id} className="media-card">
                <div className="media-thumb">{a.id}</div>
                <div className="media-title">{a.title || a.id}</div>
                {a.date && <div className="media-date">{a.date}</div>}
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}

function TicketSidePanel({ tickets, onClose, closing }) {
  // Hooks must run on every render in the same order — keep them above any
  // early return, otherwise React flags a mismatched hook count and tears
  // down the whole tree.
  const navigate = useNavigate();
  if (!tickets || tickets.length === 0) {
    return (
      <aside className={'right-sidebar ticket-panel' + (closing ? ' closing' : '')}>
        <div className="ticket-panel-head">
          <button className="icon-btn" onClick={onClose}><Icon.Chevron style={{ transform: 'rotate(-90deg)' }} /></button>
          <div style={{ fontWeight: 700 }}>Tickets</div>
          <div style={{ marginLeft: 'auto' }} />
          <button className="icon-btn" onClick={onClose} title="Close"><Icon.Close /></button>
        </div>
        <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
          No tickets linked to this conversation.
        </div>
      </aside>
    );
  }
  const t = tickets[0];
  const openFullTicket = () => {
    const raw = String(t.id || '').replace(/^#/, '');
    if (!raw) return;
    navigate(`/tickets?id=${encodeURIComponent(raw)}`);
  };
  return (
    <aside className={'right-sidebar ticket-panel' + (closing ? ' closing' : '')}>
      <div className="ticket-panel-head">
        <button className="icon-btn" onClick={onClose}>
          <Icon.Chevron style={{ transform: 'rotate(-90deg)' }} />
        </button>
        <div style={{ fontWeight: 700, fontSize: 16 }}>{t.id}</div>
        <div style={{ marginLeft: 'auto' }} />
        <button className="icon-btn" title="Open in Tickets" onClick={openFullTicket}>
          <Icon.Expand />
        </button>
      </div>

      <div className="ticket-panel-body">
      <div className="side-section">
        <div className="side-row">
          <div className="side-col">
            <span className="side-label">Status</span>
            <span className={'value-chip ' + (t.statusClass || 'info')}>{t.status}</span>
          </div>
          <div className="side-col">
            <span className="side-label">Source</span>
            <span className="value-chip whatsapp">{t.source}</span>
          </div>
          <div className="side-col">
            <span className="side-label">Task</span>
            <span className="value-chip info">{t.task}</span>
          </div>
        </div>

        <div className="side-row">
          <div className="side-col">
            <span className="side-label">Team</span>
            <span className="value-chip primary">{t.team}</span>
          </div>
        </div>

        {t.orderPlacedOn && (
          <div style={{ marginTop: 10 }}>
            <span className="side-label">Order Placed On</span>
            <div style={{ fontWeight: 600, marginTop: 6 }}>{t.orderPlacedOn}</div>
          </div>
        )}
      </div>

      {t.pendingChild && (
        <div className="side-section">
          <div style={{ fontWeight: 700, marginBottom: 10 }}>Pending Child Ticket</div>
          <div className="child-ticket-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <Icon.User />
              <div style={{ fontWeight: 600, fontSize: 13 }}>Assigned to:</div>
              <button className="icon-btn" style={{ width: 24, height: 24, marginLeft: 'auto' }}><Icon.Pencil /></button>
            </div>
            <div style={{ fontWeight: 700, color: 'var(--primary)', fontSize: 13, marginBottom: 12, wordBreak: 'break-all' }}>
              {t.pendingChild.assigneeEmail}
            </div>
            <div style={{ fontSize: 13, marginBottom: 10 }}>
              Team: <span className="value-chip info" style={{ padding: '2px 8px', marginLeft: 4 }}>{t.pendingChild.team}</span>
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{t.pendingChild.objective}:</div>
            <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.55, marginBottom: 12 }}>
              {t.pendingChild.prompt}
            </div>
            <button className="select-input"><span>Choose an option</span> <Icon.Chevron /></button>

            {t.pendingChild.resolution && (
              <>
                <div style={{ fontSize: 13, fontWeight: 600, margin: '14px 0 6px' }}>Add resolution:</div>
                <div style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.55 }}>
                  {t.pendingChild.resolution}
                </div>
              </>
            )}
          </div>
        </div>
      )}
      </div>
    </aside>
  );
}

function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function MessageAttachments({ items }) {
  const images = items.filter((a) => a.kind === 'image');
  const others = items.filter((a) => a.kind !== 'image');
  return (
    <div className="msg-attachments">
      {images.length > 0 && (
        <div className="msg-attach-images">
          {images.map((a) => (
            <a key={a.id} href={a.url} target="_blank" rel="noreferrer" className="msg-attach-image">
              <img src={a.url} alt={a.name} />
            </a>
          ))}
        </div>
      )}
      {others.map((a) => (
        <a
          key={a.id}
          href={a.url}
          target="_blank"
          rel="noreferrer"
          download={a.kind === 'file' ? a.name : undefined}
          className={'msg-attach-file ' + a.kind}
        >
          <span className="attach-icon">
            {a.kind === 'link' ? <Icon.MailAt /> : <Icon.Paperclip />}
          </span>
          <div className="msg-attach-meta">
            <div className="msg-attach-name">{a.name}</div>
            <div className="msg-attach-sub">
              {a.kind === 'link' ? 'Link' : formatBytes(a.size)}
            </div>
          </div>
        </a>
      ))}
    </div>
  );
}
