import { useEffect, useMemo, useRef, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { Spinner } from '../components/Spinner.jsx';
import { api } from '../api.js';

// Build the `/flow/launch` URL the new tab navigates into. The backend logs
// the user into Langflow and 302s into the flow editor. The `?token=`
// fallback is needed because the new tab is cross-origin in host-mode dev
// (SPA on :5173, backend on :8000) and the `wx_session` cookie won't
// follow.
function launchUrl(agentId) {
  const base = import.meta.env.VITE_API_URL || '/api';
  let token = '';
  try {
    const auth = JSON.parse(localStorage.getItem('workspacex.auth') || '{}');
    token = auth.token || auth.accessToken || '';
  } catch { }
  const params = new URLSearchParams();
  if (agentId) params.set('agent', agentId);
  if (token) params.set('token', token);
  return `${base}/flow/launch?${params.toString()}`;
}

function openFlow(agentId) {
  // `noopener` so the Langflow tab can't reach back into this window via
  // `window.opener`. Also prevents it from becoming a focus-stealing child.
  window.open(launchUrl(agentId), '_blank', 'noopener');
}

function formatRelativeTime(iso) {
  if (!iso) return '';
  const ts = new Date(iso).getTime();
  if (!ts || Number.isNaN(ts)) return '';
  const diff = Date.now() - ts;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  return new Date(iso).toLocaleDateString();
}

function prettyName(d = new Date()) {
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const mon = d.toLocaleString('en-US', { month: 'short' });
  return `Flow ${mon} ${d.getDate()} · ${hh}:${mm}`;
}

export default function AgentBuilder() {
  const [agents, setAgents] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api.get('/agents');
        if (cancelled) return;
        // Show every Langflow-backed flow: both "Save to WorkspaceX"
        // published ones AND drafts that the user created but hasn't saved
        // yet. The chat playground rehydrates the flow from MinIO on
        // demand, so even flows whose Langflow container forgot them
        // still work — we don't want to hide those rows behind a strict
        // `status='published'` filter.
        const flows = (list || [])
          .filter((a) => (a.kind || 'default') === 'langflow' || !!a.langflow_flow_id)
          .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
        setAgents(flows);
        if (flows.length && !selectedId) setSelectedId(flows[0].id);
      } catch (e) {
        if (!cancelled) setErr(String(e?.message || e));
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    })();
    return () => { cancelled = true; };
    // `selectedId` intentionally omitted — we only want to auto-select on
    // the first load, not every time the user changes the selection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function createFlow() {
    setBusy(true);
    setErr(null);
    try {
      const created = await api.post('/agents', {
        name: prettyName(),
        model: 'gpt-4.1',
        persona: '',
        instructions: '',
        temperature: 0.3,
        tools: [],
      });
      const optimistic = { ...created, kind: 'langflow' };
      setAgents((prev) => [optimistic, ...prev]);
      openFlow(created.id);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  const selected = useMemo(
    () => agents.find((a) => a.id === selectedId) || null,
    [agents, selectedId],
  );

  if (loadingList) {
    return (
      <>
        <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
        <div className="page-scroll" style={emptyWrap}>
          <Spinner size={22} />
        </div>
      </>
    );
  }

  // Empty state — centered CTA with the marketing copy. Gets out of the
  // way as soon as the workspace has at least one exported flow.
  if (agents.length === 0) {
    return (
      <>
        <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
        <div className="page-scroll" style={emptyWrap}>
          <div style={emptyCard}>
            <div style={title}>Build an agent with Langflow</div>
            <div style={sub}>
              Start a new flow, then wire up nodes, tools and prompts in the
              visual editor. Saving the flow publishes it to WorkspaceX and
              opens a chat playground right here.
            </div>
            <button
              className="btn"
              onClick={createFlow}
              disabled={busy}
              style={ctaBtn}
            >
              <Icon.Plus /> {busy ? 'Creating flow…' : 'Create a flow'}
            </button>
            <div style={tabHint}>Opens Langflow in a new tab.</div>
            {err && <div style={errLine}>{err}</div>}
          </div>
        </div>
      </>
    );
  }

  // Populated state — two-pane split: flow list on the left, chat playground
  // on the right. Selecting a flow wires the chat to that flow's
  // `langflow_flow_id`; messages post to `/agents/:id/chat` which fans out
  // to Langflow's `/api/v1/run/<flowId>` under the hood.
  return (
    <>
      <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
      <div className="page-scroll" style={splitWrap}>
        <aside style={listPane}>
          <div style={listHeader}>
            <div style={recentLabel}>Your flows</div>
            <button
              className="btn"
              onClick={createFlow}
              disabled={busy}
              style={cornerBtn}
              title="Create a new flow in Langflow"
            >
              <Icon.Plus /> {busy ? '…' : 'New'}
            </button>
          </div>
          {err && <div style={errLine}>{err}</div>}
          <div style={recentList}>
            {agents.map((a) => {
              const isSel = a.id === selectedId;
              return (
                <button
                  key={a.id}
                  style={{
                    ...recentItem,
                    ...(isSel ? recentItemActive : null),
                  }}
                  onClick={() => setSelectedId(a.id)}
                  title={`Chat with ${a.name || 'flow'}`}
                >
                  <div style={recentName}>
                    {a.name || 'Untitled flow'}
                    <span style={recentId}>#{String(a.id).slice(0, 6)}</span>
                  </div>
                  <div style={recentMeta}>
                    {formatRelativeTime(a.last_published_at || a.created_at)}
                  </div>
                </button>
              );
            })}
          </div>
        </aside>
        <section style={chatPane}>
          {selected ? (
            <ChatPlayground
              key={selected.id}
              agent={selected}
              onEdit={() => openFlow(selected.id)}
            />
          ) : (
            <div style={chatEmpty}>Select a flow to start chatting.</div>
          )}
        </section>
      </div>
    </>
  );
}

function ChatPlayground({ agent, onEdit }) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [chatErr, setChatErr] = useState(null);
  const [sessionId] = useState(
    () => `wx-playground-${agent.id.slice(0, 8)}-${Date.now().toString(36)}`,
  );
  const scrollRef = useRef(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  async function send(e) {
    e?.preventDefault?.();
    const text = draft.trim();
    if (!text || sending) return;
    setDraft('');
    setChatErr(null);
    const userMsg = { role: 'user', text, at: Date.now() };
    setMessages((m) => [...m, userMsg]);
    setSending(true);
    try {
      const resp = await api.post(`/agents/${agent.id}/chat`, {
        input: text,
        sessionId,
      });
      setMessages((m) => [...m, {
        role: 'assistant',
        text: resp?.reply || '(empty reply)',
        source: resp?.source,
        at: Date.now(),
      }]);
    } catch (e2) {
      const msg = String(e2?.message || e2);
      setChatErr(msg);
      setMessages((m) => [...m, {
        role: 'assistant',
        text: `⚠️ ${msg}`,
        error: true,
        at: Date.now(),
      }]);
    } finally {
      setSending(false);
    }
  }

  function clear() {
    setMessages([]);
    setChatErr(null);
  }

  return (
    <div style={chatWrap}>
      <header style={chatHeader}>
        <div style={chatHeaderLeft}>
          <div style={chatTitle}>{agent.name || 'Untitled flow'}</div>
          <div style={chatSub}>
            Playground · session <code style={sessMono}>{sessionId.slice(-8)}</code>
          </div>
        </div>
        <div style={chatHeaderRight}>
          <button
            className="btn"
            onClick={clear}
            style={cornerBtn}
            disabled={!messages.length || sending}
            title="Clear the conversation"
          >
            Clear
          </button>
          <button
            className="btn"
            onClick={onEdit}
            style={cornerBtn}
            title="Open this flow in Langflow"
          >
            <Icon.ExternalLink /> Edit
          </button>
        </div>
      </header>
      <div ref={scrollRef} style={chatBody}>
        {messages.length === 0 ? (
          <div style={chatHint}>
            Send a message to test this flow. Responses come from the published
            Langflow flow via <code style={sessMono}>/api/v1/run/{'<flowId>'}</code>.
          </div>
        ) : (
          messages.map((m, i) => (
            <div key={i} style={{
              ...bubbleRow,
              justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
            }}>
              <div style={{
                ...bubble,
                ...(m.role === 'user' ? bubbleUser : bubbleAssistant),
                ...(m.error ? bubbleError : null),
              }}>
                {m.text}
              </div>
            </div>
          ))
        )}
        {sending && (
          <div style={{ ...bubbleRow, justifyContent: 'flex-start' }}>
            <div style={{ ...bubble, ...bubbleAssistant, opacity: 0.7 }}>
              <Spinner size={14} /> <span style={{ marginLeft: 8 }}>Thinking…</span>
            </div>
          </div>
        )}
      </div>
      {chatErr && <div style={errLine}>{chatErr}</div>}
      <form onSubmit={send} style={composer}>
        <textarea
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Message the flow…"
          style={composerInput}
          disabled={sending}
        />
        <button
          type="submit"
          className="btn"
          style={sendBtn}
          disabled={sending || !draft.trim()}
          title="Send (Enter)"
        >
          <Icon.Send />
        </button>
      </form>
    </div>
  );
}

const emptyWrap = {
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  minHeight: 'calc(100vh - 120px)', textAlign: 'center',
};
const emptyCard = { maxWidth: 460, width: '100%', padding: '0 24px' };
const title = { fontSize: 22, fontWeight: 600, marginBottom: 8 };
const sub = { color: 'var(--muted)', marginBottom: 24, lineHeight: 1.55 };
const ctaBtn = {
  padding: '12px 22px', fontSize: 15, display: 'inline-flex',
  alignItems: 'center', gap: 8,
};
const cornerBtn = {
  padding: '6px 12px', fontSize: 13, display: 'inline-flex',
  alignItems: 'center', gap: 6,
};
const tabHint = {
  marginTop: 10, fontSize: 12, color: 'var(--muted-2)',
};
const errLine = { color: 'var(--danger, #b91c1c)', fontSize: 13, margin: '8px 0' };

const splitWrap = {
  display: 'grid',
  gridTemplateColumns: 'minmax(260px, 320px) 1fr',
  gap: 0,
  height: 'calc(100vh - 56px)',
  minHeight: 0,
};
const listPane = {
  borderRight: '1px solid var(--border)',
  padding: '18px 16px',
  overflowY: 'auto',
  background: 'var(--panel)',
  display: 'flex', flexDirection: 'column', gap: 14,
};
const listHeader = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
};
const recentLabel = {
  fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase',
  color: 'var(--muted)',
};
const recentList = { display: 'grid', gap: 6 };
const recentItem = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  gap: 12,
  padding: '10px 12px', border: '1px solid var(--border)',
  borderRadius: 8, background: 'var(--panel-2, var(--panel))',
  color: 'var(--text)',
  cursor: 'pointer', textAlign: 'left',
};
const recentItemActive = {
  borderColor: 'var(--accent, #2563eb)',
  background: 'var(--hover, rgba(37,99,235,0.08))',
};
const recentName = {
  fontSize: 13, fontWeight: 500, color: 'var(--text)',
  display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0,
  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
};
const recentId = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 10, fontWeight: 400, color: 'var(--muted-2)',
};
const recentMeta = { fontSize: 11, color: 'var(--muted)', flexShrink: 0 };

const chatPane = {
  display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0,
  background: 'var(--bg)',
};
const chatEmpty = {
  margin: 'auto', color: 'var(--muted)', fontSize: 14,
};
const chatWrap = {
  display: 'flex', flexDirection: 'column',
  height: '100%', minHeight: 0,
};
const chatHeader = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  padding: '14px 20px', borderBottom: '1px solid var(--border)',
  background: 'var(--panel)',
};
const chatHeaderLeft = { display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 };
const chatHeaderRight = { display: 'flex', gap: 8 };
const chatTitle = { fontSize: 15, fontWeight: 600 };
const chatSub = { fontSize: 12, color: 'var(--muted)' };
const sessMono = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 11, color: 'var(--muted-2)',
};
const chatBody = {
  flex: 1, overflowY: 'auto', padding: '20px 24px',
  display: 'flex', flexDirection: 'column', gap: 10,
};
const chatHint = {
  margin: 'auto', maxWidth: 420, textAlign: 'center',
  color: 'var(--muted)', fontSize: 13, lineHeight: 1.55,
};
const bubbleRow = { display: 'flex', width: '100%' };
const bubble = {
  maxWidth: '70%', padding: '10px 14px', borderRadius: 14,
  fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};
const bubbleUser = {
  background: 'var(--accent, #2563eb)', color: '#fff',
  borderBottomRightRadius: 4,
};
const bubbleAssistant = {
  background: 'var(--panel-2, var(--panel))',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderBottomLeftRadius: 4,
};
const bubbleError = {
  background: 'rgba(185, 28, 28, 0.12)',
  borderColor: 'rgba(185, 28, 28, 0.5)',
  color: 'var(--danger, #b91c1c)',
};
const composer = {
  display: 'flex', gap: 8, alignItems: 'flex-end',
  padding: '12px 16px',
  borderTop: '1px solid var(--border)',
  background: 'var(--panel)',
};
const composerInput = {
  flex: 1, resize: 'none', minHeight: 38, maxHeight: 140,
  padding: '10px 12px',
  borderRadius: 10, border: '1px solid var(--border)',
  background: 'var(--bg)', color: 'var(--text)',
  font: 'inherit', outline: 'none',
};
const sendBtn = {
  padding: '8px 14px', display: 'inline-flex', alignItems: 'center',
  justifyContent: 'center', gap: 6, minHeight: 38,
};
