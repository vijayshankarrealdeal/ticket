import { useEffect, useState } from 'react';
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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api.get('/agents');
        if (cancelled) return;
        // Only surface flows that have been Exported out of Langflow —
        // draft rows are placeholders created at first `/flow/launch` and
        // clutter the list until the user actually saves something. The
        // `/flow/save-as-agent` hook (deploy/langflow/wx-rebrand.js) flips
        // `agents.status` to `published` via `apply_flow_publish`.
        const flows = (list || [])
          .filter((a) => (a.kind || 'default') === 'langflow')
          .filter((a) => (a.status || '').toLowerCase() === 'published')
          .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
        setAgents(flows);
      } catch (e) {
        if (!cancelled) setErr(String(e?.message || e));
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    })();
    return () => { cancelled = true; };
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
      // Optimistically flag as langflow — the /flow/launch bridge will
      // stamp `kind='langflow'` server-side when it provisions the flow.
      const optimistic = { ...created, kind: 'langflow' };
      setAgents((prev) => [optimistic, ...prev]);
      openFlow(created.id);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

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
              visual editor. Exporting the flow publishes it to WorkspaceX
              automatically.
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

  // Populated state — list is the primary content; Create a flow sits in
  // the top-right corner so the user can keep adding more without hunting
  // for the CTA.
  return (
    <>
      <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
      <div className="page-scroll" style={listWrap}>
        <div style={listInner}>
          <div style={listHeader}>
            <div style={recentLabel}>Your flows</div>
            <button
              className="btn"
              onClick={createFlow}
              disabled={busy}
              style={cornerBtn}
            >
              <Icon.Plus /> {busy ? 'Creating…' : 'Create a flow'}
            </button>
          </div>
          {err && <div style={errLine}>{err}</div>}
          <div style={recentList}>
            {agents.map((a) => (
              <button
                key={a.id}
                style={recentItem}
                onClick={() => openFlow(a.id)}
                title={`Open ${a.name || 'flow'} in a new tab`}
              >
                <div style={recentName}>
                  {a.name || 'Untitled flow'}
                  <span style={recentId}>#{String(a.id).slice(0, 6)}</span>
                </div>
                <div style={recentMeta}>
                  {formatRelativeTime(a.last_published_at || a.created_at)}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

const emptyWrap = {
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  minHeight: 'calc(100vh - 120px)', textAlign: 'center',
};
const emptyCard = { maxWidth: 460, width: '100%', padding: '0 24px' };
const listWrap = { display: 'flex', justifyContent: 'center', padding: '28px 24px' };
const listInner = { width: '100%', maxWidth: 760 };
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
const errLine = { color: 'var(--danger, #b91c1c)', fontSize: 13, marginTop: 14 };
const listHeader = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  marginBottom: 16,
};
const recentLabel = {
  fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase',
  color: 'var(--muted)',
};
const recentList = { display: 'grid', gap: 8 };
const recentItem = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  gap: 12,
  padding: '12px 16px', border: '1px solid var(--border)',
  borderRadius: 8, background: 'var(--panel)',
  color: 'var(--text)',
  cursor: 'pointer', textAlign: 'left',
};
const recentName = {
  fontSize: 14, fontWeight: 500, color: 'var(--text)',
  display: 'flex', alignItems: 'baseline', gap: 8,
};
const recentId = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 11, fontWeight: 400, color: 'var(--muted-2)',
};
const recentMeta = { fontSize: 12, color: 'var(--muted)' };
