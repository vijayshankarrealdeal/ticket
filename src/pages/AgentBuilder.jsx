import { useEffect, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { api } from '../api.js';

// Build the `/flow/launch` URL the embedded iframe navigates into. The
// backend logs the user into Langflow and 302s into the flow editor; the
// `?token=` fallback is needed because the iframe doesn't carry the
// `wx_session` cookie to a cross-origin backend in every browser.
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

function prettyName(d = new Date()) {
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const mon = d.toLocaleString('en-US', { month: 'short' });
  return `Flow ${mon} ${d.getDate()} · ${hh}:${mm}`;
}

export default function AgentBuilder() {
  const [agents, setAgents] = useState([]);
  const [current, setCurrent] = useState(null);
  const [loadingList, setLoadingList] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await api.get('/agents');
        if (cancelled) return;
        // Only Langflow-backed rows make sense in the recent picker — the
        // default-orchestrator placeholder doesn't have a flow to open.
        const flows = (list || [])
          .filter((a) => (a.kind || 'default') === 'langflow')
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
      setCurrent(optimistic);
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  if (!current) {
    return (
      <>
        <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
        <div className="page-scroll" style={pageWrap}>
          <div style={card}>
            <div style={title}>Build an agent with Langflow</div>
            <div style={sub}>
              Start a new flow, then wire up nodes, tools and prompts in the
              visual editor. Publishing the flow activates the agent for
              inbound conversations automatically.
            </div>
            <button
              className="btn"
              onClick={createFlow}
              disabled={busy}
              style={ctaBtn}
            >
              <Icon.Plus /> {busy ? 'Creating flow…' : 'Create a flow'}
            </button>
            {err && <div style={errLine}>{err}</div>}

            {loadingList ? null : agents.length > 0 && (
              <div style={recentWrap}>
                <div style={recentLabel}>Recent flows</div>
                <div style={recentList}>
                  {agents.slice(0, 6).map((a) => (
                    <button
                      key={a.id}
                      style={recentItem}
                      onClick={() => setCurrent(a)}
                      title={`Open ${a.name || 'flow'}`}
                    >
                      <div style={recentName}>
                        {a.name || 'Untitled flow'}
                        <span style={recentId}>#{String(a.id).slice(0, 6)}</span>
                      </div>
                      <div style={recentMeta}>
                        {a.status === 'published' ? 'Published' : 'Draft'}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
      <div style={chromeRow}>
        <button
          className="btn-ghost"
          onClick={() => setCurrent(null)}
          style={backBtn}
        >
          <Icon.Back /> All flows
        </button>
        <div style={chromeTitle}>{current.name || 'Untitled flow'}</div>
        <a
          className="btn-ghost"
          href={launchUrl(current.id)}
          target="_blank"
          rel="noopener noreferrer"
          style={{ ...backBtn, textDecoration: 'none' }}
          title="Open in new tab"
        >
          <Icon.Expand /> Open
        </a>
        <button
          className="btn"
          onClick={createFlow}
          disabled={busy}
          style={{ padding: '6px 12px', fontSize: 13 }}
        >
          <Icon.Plus /> New flow
        </button>
      </div>
      <iframe
        key={current.id}
        title="Langflow editor"
        src={launchUrl(current.id)}
        style={frame}
        allow="clipboard-read; clipboard-write"
      />
    </>
  );
}

const pageWrap = {
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  minHeight: 'calc(100vh - 120px)', textAlign: 'center',
};
const card = { maxWidth: 460, width: '100%', padding: '0 24px' };
const title = { fontSize: 22, fontWeight: 600, marginBottom: 8 };
const sub = { color: 'var(--muted)', marginBottom: 24, lineHeight: 1.55 };
const ctaBtn = {
  padding: '12px 22px', fontSize: 15, display: 'inline-flex',
  alignItems: 'center', gap: 8,
};
const errLine = { color: 'var(--danger, #b91c1c)', fontSize: 13, marginTop: 14 };
const recentWrap = { marginTop: 36, textAlign: 'left' };
const recentLabel = {
  fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase',
  color: 'var(--muted)', marginBottom: 8,
};
const recentList = { display: 'grid', gap: 8 };
const recentItem = {
  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  gap: 12,
  padding: '10px 14px', border: '1px solid var(--border)',
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

const chromeRow = {
  display: 'flex', alignItems: 'center', gap: 12,
  padding: '8px 16px',
  borderBottom: '1px solid var(--border)',
  background: 'var(--surface, #fff)',
};
const backBtn = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '6px 10px', fontSize: 13,
};
const chromeTitle = {
  flex: 1, fontSize: 14, fontWeight: 500,
  color: 'var(--text)', overflow: 'hidden',
  textOverflow: 'ellipsis', whiteSpace: 'nowrap',
};
const frame = {
  width: '100%',
  height: 'calc(100vh - 112px)',
  border: 'none',
  display: 'block',
};
