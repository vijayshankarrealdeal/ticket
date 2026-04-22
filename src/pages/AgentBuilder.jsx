import { useState } from 'react';
import TopBar from '../components/TopBar.jsx';
import { Icon } from '../components/Icons.jsx';

const defaultTools = [
  { key: 'kb_search', label: 'Knowledge Base Search', desc: 'Search help articles & FAQs', on: true },
  { key: 'order_lookup', label: 'Order Lookup', desc: 'Fetch order details by ID or phone', on: true },
  { key: 'refund', label: 'Issue Refund', desc: 'Trigger refund up to ₹10,000', on: false },
  { key: 'escalate', label: 'Escalate to Human', desc: 'Hand off conversation to an agent', on: true },
  { key: 'web_search', label: 'Web Search', desc: 'Look up public info', on: false },
  { key: 'shipping_status', label: 'Shipping Status', desc: 'Track parcels via courier APIs', on: true },
];

export default function AgentBuilder() {
  const [name, setName] = useState('Support Agent v1');
  const [model, setModel] = useState('claude-opus-4-7');
  const [persona, setPersona] = useState('Friendly, concise, solutions-first.');
  const [instructions, setInstructions] = useState(
    'You are a customer support agent for The Sleep Company. Always greet the user, verify the issue, consult the knowledge base, and escalate to a human when unsure.',
  );
  const [tools, setTools] = useState(defaultTools);
  const [temperature, setTemperature] = useState(0.3);

  const toggleTool = (key) =>
    setTools((prev) => prev.map((t) => (t.key === key ? { ...t, on: !t.on } : t)));

  return (
    <>
      <TopBar left={<div className="top-bar-title">Agent Builder</div>} />
      <div className="page-scroll">
        <div className="page-header">
          <div>
            <div className="page-title">Support Agent v1</div>
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
