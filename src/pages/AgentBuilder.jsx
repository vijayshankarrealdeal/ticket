import { useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { api } from '../api.js';

// Single-button page. Agents live in Langflow — click "Create agent", we
// POST /agents with a blank "new agent" row, then full-tab-redirect the
// browser into the Langflow editor for that row. Publishing in Langflow
// fires /webhooks/langflow/published which fills in the rest.

export default function AgentBuilder() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function createAgent() {
    setBusy(true);
    setErr(null);
    try {
      const created = await api.post('/agents', {
        name: 'new agent',
        model: 'gpt-4.1',
        persona: '',
        instructions: '',
        temperature: 0.3,
        tools: [],
      });

      const base = import.meta.env.VITE_API_URL || '/api';
      let token = '';
      try {
        const auth = JSON.parse(localStorage.getItem('workspacex.auth') || '{}');
        token = auth.token || auth.accessToken || '';
      } catch {}
      const params = new URLSearchParams();
      if (created?.id) params.set('agent', created.id);
      if (token) params.set('token', token);
      // Open Langflow in a new tab so the AgentBuilder landing stays behind;
      // `noopener` is set so the Langflow tab can't reach back into this one.
      window.open(`${base}/flow/launch?${params.toString()}`, '_blank', 'noopener');
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
      <div className="page-scroll" style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        minHeight: 'calc(100vh - 120px)', textAlign: 'center',
      }}>
        <div style={{ maxWidth: 420 }}>
          <div className="page-title" style={{ marginBottom: 8 }}>Build an agent</div>
          <div className="page-sub" style={{ marginBottom: 24, color: 'var(--muted)' }}>
            Agents are authored in Langflow. Click below to create a fresh
            agent and open it in the visual editor.
          </div>
          <button
            className="btn"
            onClick={createAgent}
            disabled={busy}
            style={{ padding: '12px 22px', fontSize: 15 }}
          >
            {busy ? 'Opening Langflow…' : 'Create agent'}
          </button>
          {err && (
            <div style={{
              color: 'var(--danger, #b91c1c)', fontSize: 13, marginTop: 14,
            }}>
              {err}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
