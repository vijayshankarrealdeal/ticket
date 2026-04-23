import { useEffect, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { api } from '../api.js';

const MODEL_OPTIONS = [
  { value: 'gpt-4.1', label: 'GPT-4.1' },
  { value: 'gpt-4.1-mini', label: 'GPT-4.1 mini' },
  { value: 'gpt-4o', label: 'GPT-4o' },
  { value: 'o4-mini', label: 'o4-mini' },
  { value: 'claude-opus-4-7', label: 'Claude Opus 4.7' },
  { value: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
  { value: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
];

export default function AgentBuilder() {
  const [agentId, setAgentId] = useState(null);
  const [name, setName] = useState('');
  const [model, setModel] = useState('gpt-4.1');
  const [persona, setPersona] = useState('');
  const [instructions, setInstructions] = useState('');
  const [tools, setTools] = useState([]);
  const [temperature, setTemperature] = useState(0.3);
  const [saveState, setSaveState] = useState({ status: 'idle', error: null });
  const [testState, setTestState] = useState({ status: 'idle', output: null, error: null });
  const [testInput, setTestInput] = useState('My order hasn\'t arrived — can you check?');

  useEffect(() => {
    let alive = true;
    api.get('/agents')
      .then((res) => {
        if (!alive) return;
        const items = Array.isArray(res) ? res : res?.items || [];
        const first = items[0];
        if (!first) return;
        setAgentId(first.id || null);
        setName(first.name || '');
        if (first.model) setModel(first.model);
        setPersona(first.persona || '');
        setInstructions(first.instructions || '');
        setTools(Array.isArray(first.tools) ? first.tools : []);
        if (typeof first.temperature === 'number') setTemperature(first.temperature);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const toggleTool = (key) =>
    setTools((prev) => prev.map((t) => (t.key === key ? { ...t, on: !t.on } : t)));

  async function onSave() {
    const body = { name, model, persona, instructions, temperature: Number(temperature) || 0, tools };
    setSaveState({ status: 'saving', error: null });
    try {
      if (agentId) {
        await api.patch(`/agents/${agentId}`, body);
      } else {
        const created = await api.post('/agents', body);
        if (created?.id) setAgentId(created.id);
      }
      setSaveState({ status: 'saved', error: null });
      setTimeout(() => setSaveState((s) => (s.status === 'saved' ? { status: 'idle', error: null } : s)), 1500);
    } catch (e) {
      setSaveState({ status: 'error', error: String(e?.message || e) });
    }
  }

  async function onTest() {
    if (!agentId) {
      setTestState({ status: 'error', output: null, error: 'Save the agent first' });
      return;
    }
    setTestState({ status: 'running', output: null, error: null });
    try {
      const res = await api.post(`/agents/${agentId}/test`, { input: testInput });
      setTestState({ status: 'done', output: res, error: null });
    } catch (e) {
      setTestState({ status: 'error', output: null, error: String(e?.message || e) });
    }
  }

  return (
    <>
      <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
      <div className="page-scroll">
        <div className="page-header">
          <div>
            <div className="page-title">{name || 'New agent'}</div>
            <div className="page-sub">Design how your AI handles incoming tickets.</div>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {saveState.status === 'saved' && <span style={{ color: 'var(--success, #059669)', fontSize: 13 }}>Saved</span>}
            {saveState.status === 'error' && <span style={{ color: 'var(--danger, #b91c1c)', fontSize: 13 }}>{saveState.error}</span>}
            <button className="btn secondary" onClick={onTest} disabled={testState.status === 'running'}>
              {testState.status === 'running' ? 'Running…' : 'Test agent'}
            </button>
            <button className="btn" onClick={onSave} disabled={saveState.status === 'saving'}>
              {saveState.status === 'saving' ? 'Saving…' : 'Save agent'}
            </button>
          </div>
        </div>

        <div className="builder-layout">
          <div className="card">
            <div className="form-group">
              <label>Agent name</label>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="form-group">
              <label>Model</label>
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {MODEL_OPTIONS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label>Persona</label>
              <input value={persona} onChange={(e) => setPersona(e.target.value)} />
            </div>
            <div className="form-group">
              <label>System instructions</label>
              <textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                rows={5}
              />
            </div>
            <div className="form-group">
              <label>Temperature: {Number(temperature).toFixed(2)}</label>
              <input
                type="range"
                min="0" max="1" step="0.05"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
              />
            </div>

            <div style={{ borderTop: '1px solid var(--border)', marginTop: 16, paddingTop: 16 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Sandbox</div>
              <div className="form-group">
                <label>Test prompt</label>
                <textarea value={testInput} onChange={(e) => setTestInput(e.target.value)} rows={2} />
              </div>
              {testState.status === 'done' && testState.output && (
                <div style={{
                  background: 'var(--hover)', borderRadius: 8, padding: 12, fontSize: 13,
                  whiteSpace: 'pre-wrap', marginTop: 8,
                }}>
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>Output</div>
                  <div>{testState.output.output || JSON.stringify(testState.output, null, 2)}</div>
                  {testState.output.latencyMs != null && (
                    <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 6 }}>
                      {testState.output.latencyMs}ms · trace {testState.output.traceId}
                    </div>
                  )}
                </div>
              )}
              {testState.status === 'error' && (
                <div style={{ color: 'var(--danger, #b91c1c)', fontSize: 13, marginTop: 8 }}>{testState.error}</div>
              )}
            </div>
          </div>

          <div className="card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <div className="platform-icon" style={{ width: 32, height: 32, background: 'var(--primary)' }}>
                <Icon.Puzzle />
              </div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>Tools</div>
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 14 }}>
              Actions the agent can take on behalf of users.
            </div>
            {tools.length === 0 && (
              <div style={{ color: 'var(--muted)', fontSize: 13 }}>
                No tools configured. Save the agent to wire in the default tool set.
              </div>
            )}
            {tools.map((t) => (
              <div key={t.key} className="tool-row">
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{t.label}</div>
                  <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 2 }}>{t.desc}</div>
                </div>
                <div className={'switch' + (t.on ? ' on' : '')} onClick={() => toggleTool(t.key)} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
