import { useMemo, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';

const messaging = [
  {
    id: 'whatsapp',
    name: 'WhatsApp Business',
    category: 'Messaging',
    desc: 'Two-way chats, notifications and broadcasts via the WhatsApp Business API.',
    status: 'connected',
    accent: '#25d366',
    letter: 'W',
    stats: { orders: '142 tickets / 30d', latency: 'Reply < 2m' },
  },
  {
    id: 'email',
    name: 'Email',
    category: 'Messaging',
    desc: 'Turn support emails into tickets with threading and auto-routing.',
    status: 'connected',
    accent: '#3b82f6',
    letter: 'E',
    stats: { orders: '87 tickets / 30d', latency: 'Reply < 4m' },
  },
  {
    id: 'instagram',
    name: 'Instagram DM',
    category: 'Messaging',
    desc: 'Direct messages, story replies and mentions from Instagram.',
    status: 'pending',
    accent: '#e1306c',
    letter: 'IG',
  },
  {
    id: 'web',
    name: 'Web Widget',
    category: 'Messaging',
    desc: 'Embeddable live-chat widget with proactive prompts.',
    status: 'connected',
    accent: '#0ea5e9',
    letter: 'LC',
    stats: { orders: '211 tickets / 30d', latency: 'Reply < 1m' },
  },
  {
    id: 'slack',
    name: 'Slack',
    category: 'Messaging',
    desc: 'Internal team escalations and channel-based alerts.',
    status: 'connected',
    accent: '#4a154b',
    letter: 'SL',
  },
  {
    id: 'telegram',
    name: 'Telegram',
    category: 'Messaging',
    desc: 'Bot-based ticket creation and customer conversations.',
    status: 'available',
    accent: '#229ed9',
    letter: 'TG',
  },
  {
    id: 'messenger',
    name: 'Messenger',
    category: 'Messaging',
    desc: 'Facebook Messenger inbox, saved replies and handoff.',
    status: 'available',
    accent: '#006aff',
    letter: 'FB',
  },
  {
    id: 'sms',
    name: 'SMS',
    category: 'Messaging',
    desc: 'Transactional SMS and two-way promotional campaigns.',
    status: 'available',
    accent: '#f59e0b',
    letter: 'SMS',
  },
];

const ecommerce = [
  {
    id: 'shopify',
    name: 'Shopify',
    category: 'E-commerce platform',
    desc: 'Sync orders, products and refunds in real time.',
    status: 'connected',
    accent: '#95bf47',
    letter: 'S',
    stats: { orders: '1,284 orders / 30d', latency: '210ms avg' },
  },
  {
    id: 'unicommerce',
    name: 'Unicommerce',
    category: 'OMS',
    desc: 'Inventory, warehouse and order management sync.',
    status: 'available',
    accent: '#9ab04a',
    letter: 'U',
  },
  {
    id: 'clickpost',
    name: 'Clickpost',
    category: 'Logistics aggregator',
    desc: 'Unified shipment tracking across carriers.',
    status: 'available',
    accent: '#1670f0',
    letter: 'C',
  },
  {
    id: 'woocommerce',
    name: 'WooCommerce',
    category: 'E-commerce platform',
    desc: 'WordPress store orders, products and customers.',
    status: 'available',
    accent: '#7f54b3',
    letter: 'W',
  },
  {
    id: 'magento',
    name: 'Magento',
    category: 'E-commerce platform',
    desc: 'Adobe Commerce catalog and checkout events.',
    status: 'available',
    accent: '#ee672f',
    letter: 'M',
  },
];

const shipping = [
  {
    id: 'shiprocket',
    name: 'Shiprocket',
    category: 'Shipping aggregator',
    desc: 'Book shipments with 17+ courier partners.',
    status: 'available',
    accent: '#7c3aed',
    letter: 'SR',
  },
  {
    id: 'delhivery',
    name: 'Delhivery',
    category: 'Carrier',
    desc: 'Pan-India express and surface logistics.',
    status: 'available',
    accent: '#111827',
    letter: 'D',
  },
  {
    id: 'shadowfax',
    name: 'Shadowfax',
    category: 'Carrier',
    desc: 'Same-day and hyperlocal delivery network.',
    status: 'available',
    accent: '#f59e0b',
    letter: 'SF',
  },
  {
    id: 'openleaf',
    name: 'OpenLeaf',
    category: 'Carrier',
    desc: 'Reverse logistics and eco-friendly delivery.',
    status: 'available',
    accent: '#6d28d9',
    letter: 'OL',
  },
  {
    id: 'xpressbees',
    name: 'XpressBees',
    category: 'Carrier',
    desc: 'E-commerce focused surface network.',
    status: 'available',
    accent: '#f97316',
    letter: 'XB',
  },
  {
    id: 'bluedart',
    name: 'Blue Dart',
    category: 'Carrier',
    desc: 'Premium express with DHL network reach.',
    status: 'available',
    accent: '#1d4ed8',
    letter: 'BD',
  },
];

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
  const [items, setItems] = useState(() => [
    ...messaging.map((p) => ({ ...p, _group: 'messaging' })),
    ...ecommerce.map((p) => ({ ...p, _group: 'ecommerce' })),
    ...shipping.map((p) => ({ ...p, _group: 'shipping' })),
  ]);

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
        i.name.toLowerCase().includes(q) ||
        i.category.toLowerCase().includes(q) ||
        i.desc.toLowerCase().includes(q)
      );
    });
  }, [items, query, cat]);

  const toggle = (id) => {
    setItems((prev) =>
      prev.map((p) =>
        p.id === id
          ? { ...p, status: p.status === 'connected' ? 'available' : 'connected' }
          : p,
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
