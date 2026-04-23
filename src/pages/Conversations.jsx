import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { useDelayUnmount } from '../hooks/useDelayUnmount.js';
import { useGlobalSearch } from '../hooks/useGlobalSearch.jsx';
import { api, uploadFile } from '../api.js';
import { useRealtimeTopic } from '../hooks/useRealtime.js';
import { loadAuth } from './AuthPage.jsx';

const CHANNELS = ['All', 'WhatsApp', 'Instagram', 'Live Chat', 'Mails'];

const avatarColors = ['#1e40af', '#0ea5e9', '#16a34a', '#f97316', '#ec4899', '#64748b', '#dc2626', '#0891b2'];
const initials = (name) =>
  (name || '').split(' ').filter(Boolean).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
const colorFor = (seed) => avatarColors[((seed || ' ').charCodeAt(0) || 0) % avatarColors.length];

export default function Conversations() {
  const [params, setParams] = useSearchParams();
  const channel = params.get('channel') || 'All';

  const [tab, setTab] = useState('all');
  const [channelOpen, setChannelOpen] = useState(false);
  const [selectedId, setSelectedId] = useState(() => {
    const raw = params.get('id');
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) ? n : null;
  });
  const [draft, setDraft] = useState('');
  const [draftLoading, setDraftLoading] = useState(false);
  const [list, setList] = useState([]);

  useEffect(() => {
    let alive = true;
    api.get('/conversations')
      .then((res) => {
        if (!alive) return;
        const items = Array.isArray(res) ? res : res?.items || [];
        setList(items);
        setSelectedId((cur) => cur ?? items[0]?.id ?? null);
      })
      .catch(() => { if (alive) setList([]); });
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
    const n = Number(raw);
    if (Number.isFinite(n) && list.some((t) => t.id === n)) {
      setSelectedId(n);
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
      setList((prev) => prev.map((c) => c.id === evt.data.id ? { ...c, ...evt.data } : c));
    } else if (evt.type === 'ticket.created' || evt.type === 'ticket.updated') {
      // a ticket change may affect the right-rail ticket list on the selected thread
      setList((prev) => prev.map((c) =>
        evt.data?.conversation_id && c.id === evt.data.conversation_id
          ? { ...c, tickets: [evt.data, ...(c.tickets || []).filter((t) => t.id !== evt.data.id)] }
          : c
      ));
    }
  });

  // Realtime — messages for the currently open thread.
  useRealtimeTopic(selectedId ? `conv:${selectedId}` : null, (evt) => {
    if (evt.type !== 'message.created') return;
    setList((prev) => prev.map((c) => {
      if (c.id !== selectedId) return c;
      const existing = c.messages || [];
      // De-dupe vs the optimistic bubble (matched on tempId → id).
      const tempId = evt.data?.tempId;
      const withoutTemp = tempId ? existing.filter((m) => m.id !== tempId && m.tempId !== tempId) : existing;
      // Guard against double-inserts when the server also returns the message from POST.
      if (evt.data?.id && withoutTemp.some((m) => m.id === evt.data.id)) return c;
      return { ...c, messages: [...withoutTemp, evt.data], preview: evt.data.text || c.preview };
    }));
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

  const activeFilterCount =
    (filters.assignment !== 'any' ? 1 : 0) +
    (filters.unreadOnly ? 1 : 0) +
    (filters.withAttachments ? 1 : 0);

  const scoped = useMemo(() => {
    const q = search.trim().toLowerCase();
    return list.filter((t) => {
      if (channel !== 'All' && t.channel !== channel) return false;
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

  const addNote = (threadId, text) => {
    if (!text.trim()) return;
    const now = new Date();
    const at = `${now.toLocaleDateString([], { month: 'short', day: 'numeric' })} · ${now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;
    setList((prev) => prev.map((t) => t.id === threadId
      ? { ...t, notes: [...(t.notes || []), { id: Date.now(), at, text, author: 'You' }] }
      : t));
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
              {visible.map((t) => (
                <ConvItem key={t.id} t={t} selected={t.id === selectedId} onClick={() => setSelectedId(t.id)} />
              ))}
              {visible.length === 0 && (
                <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
                  No conversations in {channel}
                </div>
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
                <div className="chat-body">
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
                        if (activeThread.channel === 'Mails' && m.from === 'them') {
                          return (
                            <div key={i} className="mail-msg">
                              <div className="mail-head">
                                <div className="avatar" style={{ background: '#f97316' }}>
                                  {initials(m.fromName || activeThread.name)}
                                </div>
                                <div style={{ flex: 1 }}>
                                  <div style={{ fontWeight: 700 }}>{m.fromName || activeThread.name}</div>
                                  {m.to && (
                                    <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                                      to {m.to} <Icon.Chevron style={{ verticalAlign: 'middle' }} />
                                    </div>
                                  )}
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--muted)' }}>{m.at}</div>
                                <button
                                  type="button"
                                  className="msg-reply-btn inline"
                                  title="Reply"
                                  onClick={() => startReply(m)}
                                >
                                  <Icon.Reply />
                                </button>
                              </div>
                              <div className="mail-body">{m.text}</div>
                            </div>
                          );
                        }
                        return (
                          <div key={i} className={'msg-row ' + m.from}>
                            {m.from === 'them' && (
                              <div className="avatar sm" style={{ background: '#475569' }}>+8</div>
                            )}
                            {m.from === 'me' && (
                              <div className="avatar sm" style={{ background: '#16a34a' }}>AI</div>
                            )}
                            <div className="msg-stack">
                              <div className={'bubble ' + m.from}>
                                {m.replyTo && (
                                  <div className="bubble-quote">
                                    <div className="bubble-quote-bar" />
                                    <div className="bubble-quote-body">
                                      <div className="bubble-quote-name">{m.replyTo.author}</div>
                                      <div className="bubble-quote-text">{m.replyTo.text}</div>
                                    </div>
                                  </div>
                                )}
                                {m.text && <div>{m.text}</div>}
                                {m.attachments && m.attachments.length > 0 && (
                                  <MessageAttachments items={m.attachments} />
                                )}
                              </div>
                              <div className="msg-meta" style={{ justifyContent: m.from === 'me' ? 'flex-end' : 'flex-start' }}>
                                {m.at} {m.delivered && <><Icon.Check2 /> Delivered</>}
                              </div>
                            </div>
                            <button
                              type="button"
                              className="msg-reply-btn"
                              title="Reply"
                              onClick={() => startReply(m)}
                            >
                              <Icon.Reply />
                            </button>
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
                    {activeThread && (
                      <button
                        type="button"
                        className="ai-draft-chip inline"
                        title="Generate an AI draft for this conversation"
                        onClick={requestDraft}
                        disabled={draftLoading}
                        style={{ cursor: draftLoading ? 'progress' : 'pointer' }}
                      >
                        <Icon.Check2 /> {draftLoading ? 'Drafting…' : 'AI Draft'}
                      </button>
                    )}
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
    </>
  );
}

function ConvItem({ t, selected, onClick }) {
  const channelClass = t.channel === 'WhatsApp' ? 'whatsapp'
    : t.channel === 'Mails' ? 'mail'
    : t.channel === 'Instagram' ? 'instagram' : 'call';
  return (
    <div className={'conv-item' + (selected ? ' selected' : '')} onClick={onClick}>
      <div className="avatar" style={{ background: t.isPhone ? '#475569' : colorFor(t.name) }}>
        {t.isPhone ? '+8' : initials(t.name)}
        <span className={'avatar-ch ' + channelClass}>
          {t.channel === 'WhatsApp' && <Icon.WhatsApp />}
          {t.channel === 'Mails' && <Icon.Mail style={{ width: 10, height: 10 }} />}
          {t.channel === 'Instagram' && <Icon.Instagram style={{ width: 10, height: 10 }} />}
          {t.channel === 'Live Chat' && <Icon.Phone style={{ width: 10, height: 10 }} />}
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

function ChatHead({ t, activeChannel, onChannelChange }) {
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
  return (
    <aside className={'right-sidebar ticket-panel' + (closing ? ' closing' : '')}>
      <div className="ticket-panel-head">
        <button className="icon-btn" onClick={onClose}>
          <Icon.Chevron style={{ transform: 'rotate(-90deg)' }} />
        </button>
        <div style={{ fontWeight: 700, fontSize: 16 }}>{t.id}</div>
        <div style={{ marginLeft: 'auto' }} />
        <button className="icon-btn" title="Expand"><Icon.Expand /></button>
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
