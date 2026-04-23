import { useEffect, useMemo, useRef, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { useDelayUnmount } from '../hooks/useDelayUnmount.js';
import { useRealtimeTopic } from '../hooks/useRealtime.js';
import { api } from '../api.js';
import { loadAuth } from './AuthPage.jsx';
import {
  MEMBERS,
  CHANNELS,
  DMS,
  SEED_MESSAGES,
  SHAREABLE_TICKETS,
  SHAREABLE_CUSTOMERS,
  REACTION_PICKER,
  CURRENT_USER_ID,
  memberById,
  ticketById,
  customerById,
  loadTeamsData,
  loadConversationMessages,
  refreshTeamsPresence,
  refreshTeamsUnread,
  getTeamsUnread,
} from '../data/teamsData.js';
import './teams.css';

const AVATAR_PALETTE = [
  { bg: '#eef2ff', fg: '#4338ca' },
  { bg: '#fef3c7', fg: '#b45309' },
  { bg: '#dcfce7', fg: '#166534' },
  { bg: '#fce7f3', fg: '#9d174d' },
  { bg: '#e0f2fe', fg: '#075985' },
  { bg: '#ffedd5', fg: '#9a3412' },
  { bg: '#f3e8ff', fg: '#6b21a8' },
  { bg: '#cffafe', fg: '#155e75' },
];

function initials(name = '') {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function colorFor(seed = '') {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

function Avatar({ userId, size = 32, hideStatus = false }) {
  const m = memberById(userId);
  if (!m) return null;
  const c = colorFor(m.name);
  return (
    <span className="team-avatar" style={{ width: size, height: size, background: c.bg, color: c.fg, fontSize: Math.round(size * 0.38) }}>
      {initials(m.name)}
      {!hideStatus && m.status && <span className={`team-avatar-status team-status-${m.status}`} />}
    </span>
  );
}

function dmTitle(dm, currentUserId = CURRENT_USER_ID) {
  const others = dm.members.filter((id) => id !== currentUserId).map((id) => memberById(id)?.name).filter(Boolean);
  return others.join(', ') || 'Direct message';
}

function renderBlocks(blocks = []) {
  return blocks.map((b, i) => {
    if (b.type === 'text') return <span key={i}>{b.value}</span>;
    if (b.type === 'mention') {
      if (b.channel) return <span key={i} className="team-mention team-mention-channel">#{b.name}</span>;
      const m = memberById(b.userId);
      return <span key={i} className="team-mention">@{m?.name || 'unknown'}</span>;
    }
    return null;
  });
}

const LEFT_COLLAPSED_KEY = 'teams-left-collapsed';

export default function Teams() {
  const [dataVersion, setDataVersion] = useState(0);
  const [activeId, setActiveId] = useState(null);

  useEffect(() => {
    let alive = true;
    loadTeamsData().then(async () => {
      if (!alive) return;
      await Promise.all([refreshTeamsPresence(), refreshTeamsUnread()]);
      if (!alive) return;
      setActiveId((cur) => cur || CHANNELS[0]?.id || DMS[0]?.id || null);
      setDataVersion((v) => v + 1);
    });
    return () => { alive = false; };
  }, []);

  // Presence heartbeat + periodic refresh so dots don't lie after 2 minutes of
  // inactivity. The worker reaper flips stale users offline in the background.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      try { await api.post('/teams/presence/heartbeat', { status: 'online' }); } catch {}
      try { await refreshTeamsPresence(); } catch {}
      if (alive) setDataVersion((v) => v + 1);
    };
    tick();
    const id = setInterval(tick, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const searchMount = useDelayUnmount(searchOpen, 220);
  const searchInputRef = useRef(null);
  const [leftCollapsed, setLeftCollapsed] = useState(() => {
    try { return localStorage.getItem(LEFT_COLLAPSED_KEY) === 'true'; } catch { return false; }
  });
  useEffect(() => {
    try { localStorage.setItem(LEFT_COLLAPSED_KEY, String(leftCollapsed)); } catch {}
  }, [leftCollapsed]);
  const [threadsByConv, setThreadsByConv] = useState({});
  const [showInfo, setShowInfo] = useState(true);
  const [replyTo, setReplyTo] = useState(null);
  const [draft, setDraft] = useState('');
  const [draftMentions, setDraftMentions] = useState([]); // [{at, userId, name}]
  const [attachDraft, setAttachDraft] = useState(null); // {kind, ...}
  const [showMentionMenu, setShowMentionMenu] = useState(false);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionAnchor, setMentionAnchor] = useState(null); // position of "@" in input
  const [showPlusMenu, setShowPlusMenu] = useState(false);
  const [showEmojiPicker, setShowEmojiPicker] = useState(null); // {msgId} or 'composer'
  const [attachSheet, setAttachSheet] = useState(null); // 'ticket' | 'customer'
  const [typingNames, setTypingNames] = useState([]);
  const [dialog, setDialog] = useState(null); // 'channel' | 'dm' | null

  const inputRef = useRef(null);
  const chatBodyRef = useRef(null);

  const active = useMemo(() => {
    const ch = CHANNELS.find((c) => c.id === activeId);
    if (ch) return { kind: 'channel', ch };
    const dm = DMS.find((d) => d.id === activeId);
    if (dm) return { kind: 'dm', dm };
    return null;
  }, [activeId, dataVersion]);

  useEffect(() => {
    if (!activeId) return;
    if (threadsByConv[activeId]) return;
    let alive = true;
    loadConversationMessages(activeId).then((msgs) => {
      if (!alive) return;
      setThreadsByConv((prev) => ({ ...prev, [activeId]: msgs }));
    });
    return () => { alive = false; };
  }, [activeId, dataVersion]);

  // Mark the thread read on activation so the unread badge clears.
  useEffect(() => {
    if (!activeId) return;
    api.post(`/teams/conversations/${encodeURIComponent(activeId)}/read`)
      .then(() => refreshTeamsUnread())
      .then(() => setDataVersion((v) => v + 1))
      .catch(() => {});
  }, [activeId]);

  // Typing indicator — fire-and-forget every time the composer changes while
  // there's text. 2.5s debounced-off on the server via the 2.5s decay timer.
  const typingTimer = useRef(null);
  useEffect(() => {
    if (!activeId) return;
    if (!draft.trim()) return;
    if (typingTimer.current) return; // already broadcasting within the 2s window
    api.post(`/teams/typing/${encodeURIComponent(activeId)}`, { on: true }).catch(() => {});
    typingTimer.current = setTimeout(() => { typingTimer.current = null; }, 2000);
  }, [draft, activeId]);

  const channelMessages = threadsByConv[activeId] || [];

  useEffect(() => {
    // Scroll to bottom on active change or new msg.
    const el = chatBodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activeId, channelMessages.length]);

  useEffect(() => {
    if (searchMount.state === 'open') searchInputRef.current?.focus();
    if (!searchOpen) return;
    const onKey = (e) => {
      if (e.key === 'Escape') { setSearchOpen(false); setQuery(''); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [searchOpen, searchMount.state]);

  useEffect(() => {
    setTypingNames([]);
  }, [activeId, active]);

  // Realtime subscription for the currently open teams channel / DM.
  // Server publishes on `teams:<channel_or_dm_id>` (see wire.md §3).
  useRealtimeTopic(
    activeId ? `teams:${activeId}` : null,
    (evt) => {
      if (!evt || evt.topic !== `teams:${activeId}`) return;
      if (evt.type === 'team.message.created' && evt.data) {
        const data = evt.data;
        setThreadsByConv((prev) => {
          const prior = prev[activeId] || [];
          // Dedupe against the optimistic bubble we inserted locally.
          if (data.tempId && prior.some((m) => m.tempId === data.tempId)) {
            return {
              ...prev,
              [activeId]: prior.map((m) => (m.tempId === data.tempId ? { ...data, tempId: m.tempId } : m)),
            };
          }
          if (data.id && prior.some((m) => m.id === data.id)) return prev;
          return { ...prev, [activeId]: [...prior, data] };
        });
      } else if (evt.type === 'team.reaction.updated' && evt.data) {
        const { messageId, reactions } = evt.data;
        if (!messageId) return;
        setThreadsByConv((prev) => {
          const prior = prev[activeId] || [];
          if (!prior.some((m) => m.id === messageId)) return prev;
          return {
            ...prev,
            [activeId]: prior.map((m) => (m.id === messageId ? { ...m, reactions: reactions || [] } : m)),
          };
        });
      } else if (evt.type === 'team.typing' && evt.data) {
        const who = evt.data.userName;
        if (!who || evt.data.userId === CURRENT_USER_ID) return;
        setTypingNames((prev) => (prev.includes(who) ? prev : [...prev, who]));
        setTimeout(() => setTypingNames((prev) => prev.filter((n) => n !== who)), 2500);
      }
    },
  );

  // Presence updates workspace-wide so the left-rail status dots track live.
  const meWorkspaceId = loadAuth()?.workspaceId;
  useRealtimeTopic(
    meWorkspaceId ? `workspace:${meWorkspaceId}` : null,
    (evt) => {
      if (evt?.type !== 'presence.changed' || !evt.data) return;
      const { userId, status } = evt.data;
      const m = MEMBERS.find((x) => x.id === userId);
      if (m) m.status = status;
      setDataVersion((v) => v + 1);
    },
  );

  const filteredChannels = CHANNELS.filter((c) => c.name.toLowerCase().includes(query.toLowerCase()));
  const filteredDMs = DMS.filter((d) => dmTitle(d).toLowerCase().includes(query.toLowerCase()));

  const sendMessage = () => {
    const text = draft.trim();
    if (!text && !attachDraft) return;
    const blocks = parseDraftToBlocks(draft, draftMentions);
    const tempId = 'tmp-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
    const attachments = attachDraft ? [attachDraft] : [];
    const replyToSnap = replyTo
      ? { messageId: replyTo.id, userId: replyTo.userId, preview: previewText(replyTo) }
      : null;
    const optimistic = {
      id: tempId,
      tempId,
      userId: CURRENT_USER_ID,
      at: 'Just now',
      blocks,
      reactions: [],
      replyTo: replyToSnap || undefined,
      attachments: attachments.length ? attachments : undefined,
      _pending: true,
    };
    setThreadsByConv((prev) => ({
      ...prev,
      [activeId]: [...(prev[activeId] || []), optimistic],
    }));
    setDraft('');
    setDraftMentions([]);
    setAttachDraft(null);
    setReplyTo(null);
    setShowMentionMenu(false);

    const targetId = activeId;
    api.post(`/teams/conversations/${encodeURIComponent(targetId)}/messages`, {
      text: text || '',
      blocks,
      attachments,
      replyTo: replyToSnap ? replyToSnap.messageId : null,
      tempId,
    })
      .then((saved) => {
        setThreadsByConv((prev) => ({
          ...prev,
          [targetId]: (prev[targetId] || []).map((m) =>
            m.tempId === tempId ? { ...saved, tempId } : m
          ),
        }));
      })
      .catch(() => {
        setThreadsByConv((prev) => ({
          ...prev,
          [targetId]: (prev[targetId] || []).map((m) =>
            m.tempId === tempId ? { ...m, _pending: false, _failed: true } : m
          ),
        }));
      });
  };

  const onKeyDown = (e) => {
    if (showMentionMenu) {
      if (e.key === 'Escape') { setShowMentionMenu(false); return; }
      if (e.key === 'Enter' || e.key === 'Tab') {
        const pick = filteredMentionList()[0];
        if (pick) { e.preventDefault(); insertMention(pick); return; }
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const onInputChange = (e) => {
    const val = e.target.value;
    setDraft(val);
    // detect @-trigger
    const cursor = e.target.selectionStart ?? val.length;
    const textBefore = val.slice(0, cursor);
    const lastAt = textBefore.lastIndexOf('@');
    if (lastAt !== -1 && (lastAt === 0 || /\s/.test(textBefore[lastAt - 1] || ''))) {
      const qry = textBefore.slice(lastAt + 1);
      if (!/\s/.test(qry) && qry.length <= 30) {
        setShowMentionMenu(true);
        setMentionQuery(qry);
        setMentionAnchor(lastAt);
        return;
      }
    }
    setShowMentionMenu(false);
  };

  const filteredMentionList = () => {
    const q = mentionQuery.toLowerCase();
    return MEMBERS.filter((m) => m.id !== CURRENT_USER_ID && m.name.toLowerCase().includes(q)).slice(0, 6);
  };

  const insertMention = (member) => {
    if (mentionAnchor === null) return;
    const before = draft.slice(0, mentionAnchor);
    const afterStart = mentionAnchor + 1 + mentionQuery.length;
    const after = draft.slice(afterStart);
    const token = `@${member.name} `;
    const newDraft = before + token + after;
    setDraft(newDraft);
    setDraftMentions((prev) => [...prev, { userId: member.id, name: member.name }]);
    setShowMentionMenu(false);
    setMentionQuery('');
    setMentionAnchor(null);
    setTimeout(() => {
      if (inputRef.current) {
        const pos = before.length + token.length;
        inputRef.current.focus();
        inputRef.current.setSelectionRange(pos, pos);
      }
    }, 0);
  };

  const toggleReaction = (msgId, emoji) => {
    // Skip optimistic reactions on pending-send messages (no real id yet).
    if (typeof msgId === 'string' && msgId.startsWith('tmp-')) return;
    // Optimistic: mirror the server-side toggle so the picker feels instant.
    setThreadsByConv((prev) => {
      const list = prev[activeId] || [];
      const next = list.map((m) => {
        if (m.id !== msgId) return m;
        const reactions = [...(m.reactions || [])];
        const idx = reactions.findIndex((r) => r.emoji === emoji);
        if (idx === -1) {
          reactions.push({ emoji, userIds: [CURRENT_USER_ID] });
        } else {
          const r = { ...reactions[idx], userIds: [...reactions[idx].userIds] };
          const uIdx = r.userIds.indexOf(CURRENT_USER_ID);
          if (uIdx === -1) r.userIds.push(CURRENT_USER_ID);
          else r.userIds.splice(uIdx, 1);
          if (r.userIds.length === 0) reactions.splice(idx, 1);
          else reactions[idx] = r;
        }
        return { ...m, reactions };
      });
      return { ...prev, [activeId]: next };
    });
    setShowEmojiPicker(null);
    // Persist server-side; the `team.reaction.updated` broadcast keeps other
    // tabs in sync. We ignore the response (the local optimistic update wins).
    api.post(`/teams/messages/${encodeURIComponent(msgId)}/reactions`, { emoji })
      .catch(() => {});
  };

  const attachImageOrFile = (kind) => {
    // Simulated file picker — we just attach a fake file pill.
    const mock =
      kind === 'image'
        ? { kind: 'image', name: 'screenshot.png', size: '218 KB' }
        : { kind: 'file', name: 'shared-notes.pdf', size: '94 KB' };
    setAttachDraft(mock);
    setShowPlusMenu(false);
  };

  const onPickTicket = (t) => {
    setAttachDraft({ kind: 'ticket', ticketId: t.id });
    setAttachSheet(null);
    setShowPlusMenu(false);
  };
  const onPickCustomer = (c) => {
    setAttachDraft({ kind: 'customer', customerId: c.id });
    setAttachSheet(null);
    setShowPlusMenu(false);
  };

  const totalUnread =
    CHANNELS.reduce((n, c) => n + (c.unread || 0), 0) +
    DMS.reduce((n, d) => n + (d.unread || 0), 0);

  const titleLeft = (
    <div className="top-bar-title-group">
      <div className="top-bar-title">Teams</div>
      <div className="top-bar-sub">Internal chat · {MEMBERS.length} teammates{totalUnread ? ` · ${totalUnread} unread` : ''}</div>
    </div>
  );

  return (
    <>
      <TopBar left={titleLeft} />
      <div className="main team-layout">
        {/* Left pane: channels + DMs */}
        <aside className={'left-pane team-left' + (leftCollapsed ? ' collapsed' : '')}>
          <div className="team-left-header">
            {!leftCollapsed && <div className="team-left-header-title">Chats</div>}
            <button
              className="team-left-toggle"
              onClick={() => {
                setLeftCollapsed((v) => {
                  if (!v) setSearchOpen(false);
                  return !v;
                });
              }}
              title={leftCollapsed ? 'Expand chat list' : 'Collapse chat list'}
              aria-label={leftCollapsed ? 'Expand chat list' : 'Collapse chat list'}
            >
              <Icon.Chevron className={'team-left-toggle-caret' + (leftCollapsed ? ' collapsed' : '')} />
            </button>
          </div>
          {!leftCollapsed && searchMount.mounted && (
            <div className={'team-search-collapse' + (searchMount.state === 'exiting' ? ' closing' : '')}>
              <div className="search-box">
                <Icon.Search />
                <input
                  ref={searchInputRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search channels & people"
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') { setSearchOpen(false); setQuery(''); }
                  }}
                />
                <button
                  className="team-search-close"
                  onClick={() => { setSearchOpen(false); setQuery(''); }}
                  title="Close search"
                  aria-label="Close search"
                >
                  <Icon.Close />
                </button>
              </div>
            </div>
          )}

          <div className="team-left-body">
            <TeamGroup
              label="Channels"
              count={filteredChannels.length}
              onAdd={() => setDialog('channel')}
              onSearch={() => setSearchOpen((v) => !v)}
              searchActive={searchOpen}
            >
              {filteredChannels.map((c) => (
                <button
                  key={c.id}
                  className={'team-row' + (activeId === c.id ? ' active' : '') + (c.unread > 0 ? ' has-unread' : '')}
                  onClick={() => setActiveId(c.id)}
                  title={leftCollapsed ? `#${c.name}${c.unread ? ` · ${c.unread} unread` : ''}` : undefined}
                >
                  <Icon.Hash />
                  <span className="team-row-label">{c.name}</span>
                  {c.unread > 0 && <span className="team-unread">{c.unread}</span>}
                </button>
              ))}
            </TeamGroup>

            <TeamGroup
              label="Direct messages"
              count={filteredDMs.length}
              onAdd={() => setDialog('dm')}
            >
              {filteredDMs.map((d) => {
                const other = d.members.find((id) => id !== CURRENT_USER_ID);
                const m = memberById(other);
                return (
                  <button
                    key={d.id}
                    className={'team-row' + (activeId === d.id ? ' active' : '') + (d.unread > 0 ? ' has-unread' : '')}
                    onClick={() => setActiveId(d.id)}
                    title={leftCollapsed ? `${m?.name || 'Direct'}${d.unread ? ` · ${d.unread} unread` : ''}` : undefined}
                  >
                    <Avatar userId={other} size={22} />
                    <span className="team-row-label">{m?.name || 'Direct'}</span>
                    {d.unread > 0 && <span className="team-unread">{d.unread}</span>}
                  </button>
                );
              })}
            </TeamGroup>
          </div>
        </aside>

        {/* Center pane: chat */}
        <section className="center-pane team-center">
          <ChatHeader active={active} onToggleInfo={() => setShowInfo((v) => !v)} infoOpen={showInfo} />

          <div className="team-chat-body" ref={chatBodyRef}>
            {channelMessages.length === 0 && (
              <div className="team-empty">
                <div className="team-empty-icon"><Icon.Teams /></div>
                <div className="team-empty-title">No messages yet</div>
                <div className="team-empty-sub">Say hello — share a ticket, a customer, or a file.</div>
              </div>
            )}
            {channelMessages.map((m, i) => {
              const prev = channelMessages[i - 1];
              const compact = prev && prev.userId === m.userId && sameTimeGroup(prev.at, m.at);
              return (
                <MessageRow
                  key={m.id}
                  msg={m}
                  compact={compact}
                  onReply={() => setReplyTo(m)}
                  onReact={(emoji) => toggleReaction(m.id, emoji)}
                  onOpenPicker={() => setShowEmojiPicker(m.id)}
                  pickerOpen={showEmojiPicker === m.id}
                  onClosePicker={() => setShowEmojiPicker(null)}
                />
              );
            })}
            {typingNames.length > 0 && (
              <div className="team-typing">
                <span className="team-typing-dots"><span /><span /><span /></span>
                <span>{typingNames.join(', ')} {typingNames.length > 1 ? 'are' : 'is'} typing…</span>
              </div>
            )}
          </div>

          {/* Reply banner */}
          {replyTo && (
            <div className="team-reply-banner">
              <Icon.Reply />
              <div className="team-reply-banner-body">
                <div className="team-reply-banner-name">Replying to {memberById(replyTo.userId)?.name}</div>
                <div className="team-reply-banner-text">{previewText(replyTo)}</div>
              </div>
              <button className="team-reply-banner-close" onClick={() => setReplyTo(null)} aria-label="Cancel reply">
                <Icon.Close />
              </button>
            </div>
          )}

          {/* Attachment preview */}
          {attachDraft && (
            <div className="team-attach-preview">
              <AttachmentCard a={attachDraft} compact />
              <button
                className="team-reply-banner-close"
                onClick={() => setAttachDraft(null)}
                aria-label="Remove attachment"
                title="Remove"
              >
                <Icon.Close />
              </button>
            </div>
          )}

          <div className="team-composer">
            {showMentionMenu && (
              <div className="team-mention-menu anim-slide-down">
                <div className="team-menu-title">Mention someone</div>
                {filteredMentionList().length === 0 && (
                  <div className="team-menu-empty">No teammates match "{mentionQuery}"</div>
                )}
                {filteredMentionList().map((m) => (
                  <button key={m.id} className="team-menu-item" onClick={() => insertMention(m)}>
                    <Avatar userId={m.id} size={26} hideStatus />
                    <div className="team-menu-item-body">
                      <div className="team-menu-item-title">{m.name}</div>
                      <div className="team-menu-item-sub">{m.role}</div>
                    </div>
                    <span className={`team-status-pill team-status-${m.status}`}>{m.status}</span>
                  </button>
                ))}
              </div>
            )}

            {showPlusMenu && !attachSheet && (
              <div className="team-plus-menu anim-slide-down">
                <button className="team-menu-item" onClick={() => setAttachSheet('ticket')}>
                  <span className="team-menu-icon tone-primary"><Icon.Ticket /></span>
                  <div className="team-menu-item-body">
                    <div className="team-menu-item-title">Share a ticket</div>
                    <div className="team-menu-item-sub">Attach a ticket card to this message</div>
                  </div>
                </button>
                <button className="team-menu-item" onClick={() => setAttachSheet('customer')}>
                  <span className="team-menu-icon tone-info"><Icon.User /></span>
                  <div className="team-menu-item-body">
                    <div className="team-menu-item-title">Share a customer</div>
                    <div className="team-menu-item-sub">Send customer contact & profile</div>
                  </div>
                </button>
                <button className="team-menu-item" onClick={() => attachImageOrFile('image')}>
                  <span className="team-menu-icon tone-success"><Icon.Image /></span>
                  <div className="team-menu-item-body">
                    <div className="team-menu-item-title">Image</div>
                    <div className="team-menu-item-sub">Upload a screenshot or photo</div>
                  </div>
                </button>
                <button className="team-menu-item" onClick={() => attachImageOrFile('file')}>
                  <span className="team-menu-icon"><Icon.Document /></span>
                  <div className="team-menu-item-body">
                    <div className="team-menu-item-title">File</div>
                    <div className="team-menu-item-sub">PDFs, docs, spreadsheets</div>
                  </div>
                </button>
              </div>
            )}

            {attachSheet === 'ticket' && (
              <div className="team-plus-menu team-pick-sheet anim-slide-down">
                <div className="team-menu-head">
                  <button className="team-menu-back" onClick={() => setAttachSheet(null)}><Icon.Back /></button>
                  <div className="team-menu-title no-pad">Pick a ticket</div>
                </div>
                {SHAREABLE_TICKETS.map((t) => (
                  <button key={t.id} className="team-menu-item" onClick={() => onPickTicket(t)}>
                    <span className="team-menu-icon tone-primary"><Icon.Ticket /></span>
                    <div className="team-menu-item-body">
                      <div className="team-menu-item-title">{t.id} · {t.customer}</div>
                      <div className="team-menu-item-sub">{t.task} · {t.status}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}

            {attachSheet === 'customer' && (
              <div className="team-plus-menu team-pick-sheet anim-slide-down">
                <div className="team-menu-head">
                  <button className="team-menu-back" onClick={() => setAttachSheet(null)}><Icon.Back /></button>
                  <div className="team-menu-title no-pad">Pick a customer</div>
                </div>
                {SHAREABLE_CUSTOMERS.map((c) => (
                  <button key={c.id} className="team-menu-item" onClick={() => onPickCustomer(c)}>
                    <span className="team-menu-icon tone-info"><Icon.User /></span>
                    <div className="team-menu-item-body">
                      <div className="team-menu-item-title">{c.name}</div>
                      <div className="team-menu-item-sub">{c.phoneDisplay} · {c.orders} orders</div>
                    </div>
                  </button>
                ))}
              </div>
            )}

            {showEmojiPicker === 'composer' && (
              <div className="team-emoji-pop team-emoji-pop-composer anim-slide-down">
                {REACTION_PICKER.map((e) => (
                  <button key={e} className="team-emoji-btn" onClick={() => { setDraft((d) => d + e); setShowEmojiPicker(null); inputRef.current?.focus(); }}>{e}</button>
                ))}
              </div>
            )}

            <div className="team-composer-row">
              <button
                className={'chat-plus-btn' + (showPlusMenu ? ' on' : '')}
                onClick={() => { setShowPlusMenu((v) => !v); setAttachSheet(null); }}
                title="Attach"
                aria-label="Attach"
              >
                <Icon.Plus />
              </button>
              <textarea
                ref={inputRef}
                className="chat-input team-chat-input"
                rows={1}
                placeholder="Enter to send · Shift+Enter for newline · @ to mention"
                value={draft}
                onChange={onInputChange}
                onKeyDown={onKeyDown}
              />
              <button
                className="team-composer-icon"
                title="Mention"
                onClick={() => {
                  const pos = inputRef.current?.selectionStart ?? draft.length;
                  const needSpace = pos > 0 && !/\s/.test(draft[pos - 1] || '');
                  const insert = (needSpace ? ' ' : '') + '@';
                  const newDraft = draft.slice(0, pos) + insert + draft.slice(pos);
                  setDraft(newDraft);
                  setTimeout(() => {
                    inputRef.current?.focus();
                    const newPos = pos + insert.length;
                    inputRef.current?.setSelectionRange(newPos, newPos);
                    setShowMentionMenu(true);
                    setMentionQuery('');
                    setMentionAnchor(pos + insert.length - 1);
                  }, 0);
                }}
              >
                <Icon.At />
              </button>
              <button
                className="team-composer-icon"
                title="Emoji"
                onClick={() => setShowEmojiPicker(showEmojiPicker === 'composer' ? null : 'composer')}
              >
                <Icon.Smile />
              </button>
              <button
                className="chat-send-btn"
                onClick={sendMessage}
                disabled={!draft.trim() && !attachDraft}
                title="Send"
                aria-label="Send message"
              >
                <Icon.Send />
              </button>
            </div>
          </div>
        </section>

        {/* Right pane: info */}
        {showInfo && <InfoPane active={active} />}
      </div>
      {dialog === 'channel' && (
        <CreateChannelDialog
          onClose={() => setDialog(null)}
          onCreated={async (ch) => {
            setDialog(null);
            await loadTeamsData();
            await refreshTeamsUnread();
            setDataVersion((v) => v + 1);
            if (ch?.id) setActiveId(ch.id);
          }}
        />
      )}
      {dialog === 'dm' && (
        <StartDmDialog
          onClose={() => setDialog(null)}
          onCreated={async (dm) => {
            setDialog(null);
            await loadTeamsData();
            await refreshTeamsUnread();
            setDataVersion((v) => v + 1);
            if (dm?.id) setActiveId(dm.id);
          }}
        />
      )}
    </>
  );
}

function TeamModalShell({ title, onClose, children, footer }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="action-modal-overlay" onClick={onClose}>
      <div className="action-modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 440 }}>
        <h3 className="action-modal-title">{title}</h3>
        <div className="action-modal-body">{children}</div>
        <div className="action-modal-footer">
          <button className="btn secondary" onClick={onClose}>Cancel</button>
          {footer}
        </div>
      </div>
    </div>
  );
}

function CreateChannelDialog({ onClose, onCreated }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [privacy, setPrivacy] = useState('private');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const submit = async () => {
    const slug = name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
    if (!slug) { setErr('Name required'); return; }
    setBusy(true); setErr(null);
    try {
      const res = await api.post('/teams/channels', { name: slug, description, privacy });
      onCreated(res);
    } catch (e) {
      setErr(e?.message || 'create failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <TeamModalShell
      title="Create a channel"
      onClose={onClose}
      footer={<button className="btn" onClick={submit} disabled={busy || !name.trim()}>{busy ? 'Creating…' : 'Create'}</button>}
    >
      <div style={{ display: 'grid', gap: 10 }}>
        <label>Name
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="product-launch" />
        </label>
        <label>Description (optional)
          <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What's this channel for?" />
        </label>
        <label>Privacy
          <select value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
            <option value="private">Private — invite only</option>
            <option value="public">Public — anyone in the workspace</option>
          </select>
        </label>
        {err && <div style={{ color: 'var(--danger, #ef4444)', fontSize: 12 }}>{err}</div>}
      </div>
    </TeamModalShell>
  );
}

function StartDmDialog({ onClose, onCreated }) {
  const [picked, setPicked] = useState(new Set());
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const others = MEMBERS.filter((m) => m.id && m.id !== CURRENT_USER_ID);
  const filtered = others.filter((m) => !q || (m.name || '').toLowerCase().includes(q.toLowerCase()));
  const toggle = (id) => setPicked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const submit = async () => {
    if (picked.size === 0) { setErr('Pick at least one teammate'); return; }
    setBusy(true); setErr(null);
    try {
      const res = await api.post('/teams/dms', { members: Array.from(picked) });
      onCreated(res);
    } catch (e) {
      setErr(e?.message || 'create failed');
    } finally {
      setBusy(false);
    }
  };
  return (
    <TeamModalShell
      title="Start a direct message"
      onClose={onClose}
      footer={<button className="btn" onClick={submit} disabled={busy || picked.size === 0}>{busy ? 'Starting…' : 'Start chat'}</button>}
    >
      <div style={{ display: 'grid', gap: 10 }}>
        <input autoFocus placeholder="Search teammates…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div style={{ maxHeight: 260, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 6 }}>
          {filtered.length === 0 && <div style={{ padding: 12, color: 'var(--muted)', fontSize: 13 }}>No teammates match.</div>}
          {filtered.map((m) => (
            <label key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', cursor: 'pointer', borderBottom: '1px solid var(--border)' }}>
              <input type="checkbox" checked={picked.has(m.id)} onChange={() => toggle(m.id)} />
              <Avatar userId={m.id} size={24} hideStatus />
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{m.name}</span>
              <span style={{ color: 'var(--muted)', fontSize: 12 }}>{m.role}</span>
            </label>
          ))}
        </div>
        {err && <div style={{ color: 'var(--danger, #ef4444)', fontSize: 12 }}>{err}</div>}
      </div>
    </TeamModalShell>
  );
}

function TeamGroup({ label, count, onAdd, children, onSearch, searchActive }) {
  const [open, setOpen] = useState(true);
  const listMount = useDelayUnmount(open, 260);
  return (
    <div className="team-group">
      <div className="team-group-head">
        <button className="team-group-toggle" onClick={() => setOpen((v) => !v)}>
          <Icon.Chevron className={'team-group-caret' + (open ? ' open' : '')} />
          <span>{label}</span>
          <span className="team-group-count">{count}</span>
        </button>
        {onSearch && (
          <button
            className={'team-group-add' + (searchActive ? ' active' : '')}
            onClick={onSearch}
            title={searchActive ? 'Close search' : 'Search channels & people'}
            aria-label={searchActive ? 'Close search' : 'Search channels & people'}
          >
            <Icon.Search />
          </button>
        )}
        <button className="team-group-add" onClick={onAdd} title={`Add to ${label.toLowerCase()}`}>
          <Icon.Plus />
        </button>
      </div>
      {listMount.mounted && (
        <div className={'team-group-list' + (listMount.state === 'exiting' ? ' closing' : '')}>
          {children}
        </div>
      )}
    </div>
  );
}

function ChatHeader({ active, onToggleInfo, infoOpen }) {
  if (!active) return null;
  if (active.kind === 'channel') {
    const { ch } = active;
    const chMembers = Array.isArray(ch.members) ? ch.members : [];
    const shownMembers = chMembers.slice(0, 5);
    return (
      <div className="team-chat-head">
        <div className="team-chat-head-left">
          <div className="team-chat-head-icon"><Icon.Hash /></div>
          <div className="team-chat-head-body">
            <div className="team-chat-head-title">{ch.name}</div>
            <div className="team-chat-head-sub">{ch.description}</div>
          </div>
        </div>
        <div className="team-chat-head-right">
          <div className="team-avatar-stack">
            {shownMembers.map((id) => <Avatar key={id} userId={id} size={26} hideStatus />)}
            {chMembers.length > shownMembers.length && (
              <span className="team-avatar-more">+{chMembers.length - shownMembers.length}</span>
            )}
          </div>
          <button className="icon-btn" title="Pinned"><Icon.Pin /></button>
          <button
            className={'icon-btn' + (infoOpen ? ' active' : '')}
            title={infoOpen ? 'Hide details' : 'Show details'}
            onClick={onToggleInfo}
          >
            <Icon.Info />
          </button>
        </div>
      </div>
    );
  }
  const { dm } = active;
  const otherId = dm.members.find((id) => id !== CURRENT_USER_ID);
  const m = memberById(otherId);
  return (
    <div className="team-chat-head">
      <div className="team-chat-head-left">
        <Avatar userId={otherId} size={36} />
        <div className="team-chat-head-body">
          <div className="team-chat-head-title">{m?.name}</div>
          <div className="team-chat-head-sub">
            <span className={`team-status-dot team-status-${m?.status}`} /> {m?.status} · {m?.role}
          </div>
        </div>
      </div>
      <div className="team-chat-head-right">
        <button className="icon-btn" title="Call"><Icon.CallOutline /></button>
        <button
          className={'icon-btn' + (infoOpen ? ' active' : '')}
          title={infoOpen ? 'Hide details' : 'Show details'}
          onClick={onToggleInfo}
        >
          <Icon.Info />
        </button>
      </div>
    </div>
  );
}

function MessageRow({ msg, compact, onReply, onReact, onOpenPicker, pickerOpen, onClosePicker }) {
  const m = memberById(msg.userId);
  const isMe = msg.userId === CURRENT_USER_ID;
  const pickerRef = useRef(null);

  useEffect(() => {
    if (!pickerOpen) return;
    const onDown = (e) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target)) onClosePicker();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [pickerOpen, onClosePicker]);

  return (
    <div className={'team-msg' + (compact ? ' compact' : '') + (isMe ? ' me' : '')}>
      <div className="team-msg-avatar">{!compact && <Avatar userId={msg.userId} size={36} />}</div>
      <div className="team-msg-body">
        {!compact && (
          <div className="team-msg-head">
            <span className="team-msg-name">{m?.name}</span>
            <span className="team-msg-time">{msg.at}</span>
          </div>
        )}
        {msg.replyTo && (
          <div className="team-msg-reply">
            <span className="team-msg-reply-bar" />
            <div className="team-msg-reply-body">
              <div className="team-msg-reply-name">{memberById(msg.replyTo.userId)?.name}</div>
              <div className="team-msg-reply-text">{msg.replyTo.preview}</div>
            </div>
          </div>
        )}
        <div className="team-msg-bubble">
          <div className="team-msg-text">{renderBlocks(msg.blocks)}</div>
          {msg.attachments?.length > 0 && (
            <div className="team-msg-attachments">
              {msg.attachments.map((a, i) => <AttachmentCard key={i} a={a} />)}
            </div>
          )}
        </div>
        {msg.reactions?.length > 0 && (
          <div className="team-reactions">
            {msg.reactions.map((r) => (
              <button
                key={r.emoji}
                className={'team-reaction' + (r.userIds.includes(CURRENT_USER_ID) ? ' mine' : '')}
                onClick={() => onReact(r.emoji)}
                title={r.userIds.map((id) => memberById(id)?.name).join(', ')}
              >
                <span>{r.emoji}</span>
                <span className="team-reaction-count">{r.userIds.length}</span>
              </button>
            ))}
            <button className="team-reaction team-reaction-add" onClick={onOpenPicker} title="Add reaction">
              <Icon.Smile />
              <Icon.Plus />
            </button>
          </div>
        )}

        <div className="team-msg-actions">
          <button className="team-msg-action" onClick={onOpenPicker} title="Add reaction"><Icon.Smile /></button>
          <button className="team-msg-action" onClick={onReply} title="Reply"><Icon.Reply /></button>
          <button className="team-msg-action" title="More"><Icon.More /></button>
        </div>

        {pickerOpen && (
          <div className="team-emoji-pop" ref={pickerRef}>
            {REACTION_PICKER.map((e) => (
              <button key={e} className="team-emoji-btn" onClick={() => onReact(e)}>{e}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AttachmentCard({ a, compact = false }) {
  if (a.kind === 'ticket') {
    const t = ticketById(a.ticketId);
    if (!t) return null;
    const tone = t.status === 'Blocked' ? 'danger' : t.status === 'Resolved' ? 'success' : 'info';
    return (
      <div className={'team-attach-card' + (compact ? ' compact' : '')}>
        <div className={`team-attach-icon tone-${tone}`}><Icon.Ticket /></div>
        <div className="team-attach-body">
          <div className="team-attach-title">Ticket {t.id} · {t.customer}</div>
          <div className="team-attach-sub">{t.task} · {t.channel}</div>
        </div>
        <span className={`team-attach-pill tone-${tone}`}>{t.status}</span>
      </div>
    );
  }
  if (a.kind === 'customer') {
    const c = customerById(a.customerId);
    if (!c) return null;
    return (
      <div className={'team-attach-card' + (compact ? ' compact' : '')}>
        <div className="team-attach-icon tone-primary"><Icon.User /></div>
        <div className="team-attach-body">
          <div className="team-attach-title">{c.name}</div>
          <div className="team-attach-sub">{c.phoneDisplay} · {c.email}</div>
        </div>
        <span className="team-attach-meta">{c.orders} orders</span>
      </div>
    );
  }
  if (a.kind === 'image') {
    return (
      <div className={'team-attach-card team-attach-image' + (compact ? ' compact' : '')}>
        <div className="team-attach-thumb"><Icon.Image /></div>
        <div className="team-attach-body">
          <div className="team-attach-title">{a.name}</div>
          <div className="team-attach-sub">Image · {a.size}</div>
        </div>
      </div>
    );
  }
  if (a.kind === 'file') {
    return (
      <div className={'team-attach-card' + (compact ? ' compact' : '')}>
        <div className="team-attach-icon"><Icon.Document /></div>
        <div className="team-attach-body">
          <div className="team-attach-title">{a.name}</div>
          <div className="team-attach-sub">File · {a.size}</div>
        </div>
      </div>
    );
  }
  return null;
}

function InfoPane({ active }) {
  if (!active) return null;
  if (active.kind === 'channel') {
    const { ch } = active;
    return (
      <aside className="right-sidebar team-info">
        <div className="team-info-head">
          <div className="team-info-icon"><Icon.Hash /></div>
          <div>
            <div className="team-info-title">{ch.name}</div>
            <div className="team-info-sub">{ch.description}</div>
          </div>
        </div>
        <div className="team-info-section">
          <div className="team-info-section-title">Members · {(ch.members || []).length}</div>
          <div className="team-info-members">
            {(ch.members || []).map((id) => {
              const m = memberById(id);
              return (
                <div key={id} className="team-info-member">
                  <Avatar userId={id} size={32} />
                  <div className="team-info-member-body">
                    <div className="team-info-member-name">{m?.name}{m?.me ? ' (you)' : ''}</div>
                    <div className="team-info-member-sub">{m?.role}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        <div className="team-info-section">
          <div className="team-info-section-title">About</div>
          <div className="team-info-row"><span>Privacy</span><b>Private</b></div>
          <div className="team-info-row"><span>Created</span><b>Mar 2026</b></div>
        </div>
      </aside>
    );
  }
  const otherId = active.dm.members.find((id) => id !== CURRENT_USER_ID);
  const m = memberById(otherId);
  return (
    <aside className="right-sidebar team-info">
      <div className="team-info-head tall">
        <Avatar userId={otherId} size={56} />
        <div>
          <div className="team-info-title">{m?.name}</div>
          <div className="team-info-sub">{m?.role}</div>
        </div>
      </div>
      <div className="team-info-section">
        <div className="team-info-section-title">Contact</div>
        <div className="team-info-row"><span>Email</span><b>{m?.email}</b></div>
        <div className="team-info-row"><span>Status</span><b className={`team-status-text team-status-${m?.status}`}>{m?.status}</b></div>
      </div>
      <div className="team-info-section">
        <div className="team-info-section-title">Shared</div>
        <div className="team-info-empty">No shared files yet</div>
      </div>
    </aside>
  );
}

// ---- helpers ----

function previewText(msg) {
  const text = (msg.blocks || [])
    .map((b) => {
      if (b.type === 'text') return b.value;
      if (b.type === 'mention') {
        if (b.channel) return `#${b.name}`;
        return `@${memberById(b.userId)?.name || ''}`;
      }
      return '';
    })
    .join('')
    .trim();
  if (text) return text.length > 120 ? text.slice(0, 117) + '…' : text;
  const a = msg.attachments?.[0];
  if (a?.kind === 'ticket') return `🎫 Ticket ${a.ticketId}`;
  if (a?.kind === 'customer') return `👤 Customer ${customerById(a.customerId)?.name || ''}`;
  if (a?.kind === 'image') return `🖼 ${a.name}`;
  if (a?.kind === 'file') return `📎 ${a.name}`;
  return '[attachment]';
}

function parseDraftToBlocks(draft, mentions) {
  // Walk through draft, finding "@Name " tokens for known mentions and turning them into mention blocks.
  const blocks = [];
  let i = 0;
  let buf = '';
  const seen = new Set();
  while (i < draft.length) {
    if (draft[i] === '@') {
      // Try to match the longest mention that fits at position i.
      let matched = null;
      for (const mention of mentions) {
        const token = '@' + mention.name;
        if (draft.slice(i, i + token.length) === token && !seen.has(`${i}-${mention.name}`)) {
          if (!matched || token.length > ('@' + matched.name).length) matched = mention;
        }
      }
      if (matched) {
        if (buf) { blocks.push({ type: 'text', value: buf }); buf = ''; }
        blocks.push({ type: 'mention', userId: matched.userId });
        seen.add(`${i}-${matched.name}`);
        i += ('@' + matched.name).length;
        continue;
      }
    }
    buf += draft[i];
    i++;
  }
  if (buf) blocks.push({ type: 'text', value: buf });
  return blocks.length ? blocks : [{ type: 'text', value: '' }];
}

function sameTimeGroup(a, b) {
  // Very loose — treat "Just now" and same label as same group.
  return a === b;
}
