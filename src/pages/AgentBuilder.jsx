import { useEffect, useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';
import { api } from '../api.js';

export default function AgentBuilder() {
  const [agentId, setAgentId] = useState(null);
  const [name, setName] = useState('');
  const [model, setModel] = useState('claude-opus-4-7');
  const [persona, setPersona] = useState('');
  const [instructions, setInstructions] = useState('');
  const [tools, setTools] = useState([]);
  const [temperature, setTemperature] = useState(0.3);

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

  return (
    <>
      <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
      <div className="page-scroll">
        <div className="page-header">
          <div>
            <div className="page-title">{name || 'New agent'}</div>
            <div className="page-sub">Design how your AI handles incoming tickets.</div>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn secondary">Test agent</button>
            <button className="btn">Save agent</button>
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
                <option value="claude-opus-4-7">Claude Opus 4.7</option>
                <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                <option value="claude-haiku-4-5">Claude Haiku 4.5</option>
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
              <label>Temperature: {temperature.toFixed(2)}</label>
              <input
                type="range"
                min="0" max="1" step="0.05"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
              />
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
