import { useEffect, useMemo, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';
import { api } from '../../api.js';

const CATEGORIES = [
  { key: 'all', label: 'All channels' },
  { key: 'messaging', label: 'Messaging' },
  { key: 'ecommerce', label: 'E-commerce' },
  { key: 'shipping', label: 'Shipping partners' },
  { key: 'connected', label: 'Connected' },
];

export default function ChannelsOverview() {
  const [query, setQuery] = useState('');
  const [cat, setCat] = useState('all');
  const [items, setItems] = useState([]);

  useEffect(() => {
    let alive = true;
    api.get('/platforms?include=all')
      .then((res) => {
        if (!alive) return;
        const rows = Array.isArray(res) ? res : res?.items || [];
        const normalized = rows.map((r) => ({
          ...r,
          _group: r._group || r.group || (r.category?.toLowerCase().includes('ship') ? 'shipping' : r.category?.toLowerCase().includes('commerce') || r.category?.toLowerCase().includes('oms') ? 'ecommerce' : 'messaging'),
        }));
        setItems(normalized);
      })
      .catch(() => { if (alive) setItems([]); });
    return () => { alive = false; };
  }, []);

  const counts = useMemo(() => {
    const connected = items.filter((i) => i.status === 'connected').length;
    return {
      total: items.length,
      connected,
      msg: items.filter((i) => i._group === 'messaging').length,
      ecom: items.filter((i) => i._group === 'ecommerce').length,
      ship: items.filter((i) => i._group === 'shipping').length,
    };
  }, [items]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((i) => {
      if (cat === 'messaging' && i._group !== 'messaging') return false;
      if (cat === 'ecommerce' && i._group !== 'ecommerce') return false;
      if (cat === 'shipping' && i._group !== 'shipping') return false;
      if (cat === 'connected' && i.status !== 'connected') return false;
      if (!q) return true;
      return (
        (i.name || '').toLowerCase().includes(q) ||
        (i.category || '').toLowerCase().includes(q) ||
        (i.desc || '').toLowerCase().includes(q)
      );
    });
  }, [items, query, cat]);

  const toggle = async (id) => {
    const p = items.find((x) => x.id === id);
    if (!p) return;
    const endpoint = p.status === 'connected' ? 'disconnect' : 'connect';
    try { await api.post(`/platforms/${encodeURIComponent(id)}/${endpoint}`); } catch {}
    setItems((prev) =>
      prev.map((q) =>
        q.id === id
          ? { ...q, status: q.status === 'connected' ? 'available' : 'connected' }
          : q,
      ),
    );
  };

  const msgList = filtered.filter((i) => i._group === 'messaging');
  const ecomList = filtered.filter((i) => i._group === 'ecommerce');
  const shipList = filtered.filter((i) => i._group === 'shipping');

  return (
    <div className="ch-page">
      <div className="ch-page-head ch-page-head-compact">
        <p className="ch-page-sub">
          Connect every way your customers reach you &mdash; messaging apps, storefronts and delivery
          partners &mdash; to sync conversations, orders and shipments automatically.
        </p>
        <div className="ch-page-head-actions">
          <button className="btn"><Icon.Plus /> Request integration</button>
        </div>
      </div>

      <div className="ch-toolbar">
        <div className="ch-tabs">
          {CATEGORIES.map((c) => (
            <button
              key={c.key}
              className={'ch-tab' + (cat === c.key ? ' active' : '')}
              onClick={() => setCat(c.key)}
            >
              {c.label}
              <span className="ch-tab-count">
                {c.key === 'all'
                  ? counts.total
                  : c.key === 'messaging'
                  ? counts.msg
                  : c.key === 'ecommerce'
                  ? counts.ecom
                  : c.key === 'shipping'
                  ? counts.ship
                  : counts.connected}
              </span>
            </button>
          ))}
        </div>
        <div className="ch-toolbar-right">
          <div className="ch-search">
            <Icon.Search />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search channels, categories, features…"
              aria-label="Search channels"
            />
            {query && (
              <button className="ch-search-clear" onClick={() => setQuery('')} aria-label="Clear">
                <Icon.Close />
              </button>
            )}
          </div>
        </div>
      </div>

      {(cat === 'all' || cat === 'messaging' || cat === 'connected') && msgList.length > 0 && (
        <Section
          title="Messaging & Broadcasts"
          icon={Icon.Megaphone}
          count={msgList.length}
          hint="Send broadcasts, capture conversations and route tickets from these channels."
        >
          {msgList.map((it) => (
            <ChannelCard key={it.id} item={it} onToggle={() => toggle(it.id)} />
          ))}
        </Section>
      )}

      {(cat === 'all' || cat === 'ecommerce' || cat === 'connected') && ecomList.length > 0 && (
        <Section title="E-commerce Channels" icon={Icon.Store} count={ecomList.length}>
          {ecomList.map((it) => (
            <ChannelCard key={it.id} item={it} onToggle={() => toggle(it.id)} />
          ))}
        </Section>
      )}

      {(cat === 'all' || cat === 'shipping' || cat === 'connected') && shipList.length > 0 && (
        <Section title="Shipping Partners" icon={Icon.Truck} count={shipList.length}>
          {shipList.map((it) => (
            <ChannelCard key={it.id} item={it} onToggle={() => toggle(it.id)} />
          ))}
        </Section>
      )}

      {filtered.length === 0 && (
        <div className="ch-empty">
          <Icon.InboxEmpty />
          <div className="ch-empty-title">No matching channels</div>
          <div className="ch-empty-sub">
            Try a different search or request a new integration &mdash; we&rsquo;ll add it to the roadmap.
          </div>
          <button className="btn secondary sm" onClick={() => { setQuery(''); setCat('all'); }}>
            Clear filters
          </button>
        </div>
      )}
    </div>
  );
}

function Section({ title, icon: I, count, children, hint }) {
  return (
    <section className="ch-section">
      <div className="ch-section-head">
        <div className="ch-section-title-wrap">
          <div className="ch-section-title">
            <span className="ch-section-icon"><I /></span>
            {title}
            <span className="ch-section-count">{count}</span>
          </div>
          {hint && <div className="ch-section-hint">{hint}</div>}
        </div>
      </div>
      <div className="ch-grid">{children}</div>
    </section>
  );
}

function ChannelCard({ item, onToggle }) {
  const connected = item.status === 'connected';
  return (
    <div className={'ch-card' + (connected ? ' is-connected' : '')}>
      <div className="ch-card-top">
        <div className="ch-card-logo" style={{ background: item.accent }}>
          {item.letter}
        </div>
        <div className="ch-card-headings">
          <div className="ch-card-name">{item.name}</div>
          <div className="ch-card-cat">{item.category}</div>
        </div>
        {connected ? (
          <span className="ch-pill ch-pill-success">
            <span className="ch-pill-dot" /> Integrated
          </span>
        ) : (
          <span className="ch-pill ch-pill-muted">Available</span>
        )}
      </div>
      <div className="ch-card-desc">{item.desc}</div>
      {connected && item.stats && (
        <div className="ch-card-stats">
          <span>
            <Icon.Activity /> {item.stats.latency}
          </span>
          <span>
            <Icon.ShoppingBag /> {item.stats.orders}
          </span>
        </div>
      )}
      <div className="ch-card-actions">
        {connected ? (
          <>
            <button className="btn secondary sm">Manage</button>
            <button className="btn ghost sm" onClick={onToggle}>
              Disconnect
            </button>
          </>
        ) : (
          <>
            <button className="btn sm" onClick={onToggle}>
              Connect
            </button>
            <button className="btn ghost sm">
              <Icon.ExternalLink /> Learn more
            </button>
          </>
        )}
      </div>
    </div>
  );
}
