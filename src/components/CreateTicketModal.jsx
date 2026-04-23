import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from './Icons.jsx';
import { api, qs } from '../api.js';

const CHANNELS = [
  { key: 'whatsapp',  label: 'WhatsApp',    icon: <Icon.WhatsApp />,                       color: 'var(--whatsapp)', hint: 'Requires phone' },
  { key: 'chat',      label: 'Chat Widget', icon: <Icon.Inbox style={{ width: 14, height: 14 }} />, color: 'var(--primary)', hint: 'In-app chat' },
  { key: 'instagram', label: 'Instagram',   icon: <Icon.Instagram style={{ width: 14, height: 14 }} />, color: 'var(--instagram)', hint: 'DM thread' },
  { key: 'email',     label: 'Email',       icon: <Icon.Mail style={{ width: 14, height: 14 }} />, color: 'var(--mail)', hint: 'Gmail / Outlook' },
  { key: 'voice',     label: 'Voice Call',  icon: <Icon.Phone style={{ width: 14, height: 14 }} />, color: 'var(--call)', hint: 'Requires phone' },
];

const PRIORITIES = [
  { key: 'low',    label: 'Low',    tone: 'muted' },
  { key: 'normal', label: 'Normal', tone: 'info' },
  { key: 'high',   label: 'High',   tone: 'warning' },
  { key: 'urgent', label: 'Urgent', tone: 'danger' },
];

function detectLookupField(query) {
  const q = query.trim();
  if (!q) return null;
  if (/^\+?\d[\d\s-]{6,}$/.test(q)) return 'phone';
  if (/\S+@\S+\.\S+/.test(q)) return 'email';
  if (/^\d{5,}$/.test(q)) return 'shopify';
  return 'name';
}

const statusTone = {
  assigned: 'info',
  queued:   'warning',
  blocked:  'danger',
  resolved: 'success',
  closed:   'muted',
};

/* -----------------------------------------------------------------
   Main modal
   ----------------------------------------------------------------- */

export default function CreateTicketModal({ onClose, onCreated }) {
  const [customer, setCustomer] = useState(null);
  const [lookup, setLookup] = useState('');
  const [subject, setSubject] = useState('');
  const [description, setDescription] = useState('');
  const [channel, setChannel] = useState('');
  const [priority, setPriority] = useState('normal');
  const [orderId, setOrderId] = useState(null);
  const [tags, setTags] = useState([]);
  const [tagDraft, setTagDraft] = useState('');
  const [shareWithCustomer, setShareWithCustomer] = useState(false);

  const subjectRef = useRef(null);

  // close on Escape, Cmd/Ctrl+Enter to submit, lock body scroll
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose?.();
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit();
    }
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customer, subject, description, channel, priority, orderId, tags]);

  // focus subject when customer is selected
  useEffect(() => {
    if (customer && subjectRef.current) {
      const t = setTimeout(() => subjectRef.current?.focus(), 120);
      return () => clearTimeout(t);
    }
  }, [customer]);

  const [lookupHits, setLookupHits] = useState([]);
  const lookupKind = useMemo(() => detectLookupField(lookup), [lookup]);

  useEffect(() => {
    const q = lookup.trim();
    if (!q) { setLookupHits([]); return; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      api.get(`/customers/search${qs({ q, limit: 5 })}`, { signal: ctrl.signal })
        .then((res) => {
          const items = Array.isArray(res) ? res : res?.items || [];
          setLookupHits(items);
        })
        .catch(() => setLookupHits([]));
    }, 180);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [lookup]);

  const [similar, setSimilar] = useState([]);
  useEffect(() => {
    const text = `${subject} ${description}`.trim();
    if (text.length < 4) { setSimilar([]); return; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      api.post('/tickets/similar', { text, customerId: customer?.id }, { signal: ctrl.signal })
        .then((res) => {
          const items = Array.isArray(res) ? res : res?.items || [];
          setSimilar(items);
        })
        .catch(() => setSimilar([]));
    }, 220);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [subject, description, customer]);

  const canSubmit = customer && subject.trim().length >= 4 && channel;

  function submit() {
    if (!canSubmit) return;
    const payload = {
      customerId: customer?.id,
      customer: customer?.name,
      subject: subject.trim(),
      description: description.trim(),
      source: channel,
      channel,
      priority,
      orderId,
      tags,
      shareWithCustomer,
      task: 'general_enquiry',
      team: 'Support',
    };
    api.post('/tickets', payload)
      .then((created) => { onCreated?.(created); onClose?.(); })
      .catch(() => { onCreated?.(payload); onClose?.(); });
  }

  function addTag(raw) {
    const v = raw.trim().replace(/\s+/g, '-').toLowerCase();
    if (!v) return;
    if (tags.includes(v)) return;
    if (tags.length >= 8) return;
    setTags((ts) => [...ts, v]);
    setTagDraft('');
  }

  return (
    <div className="modal-backdrop ct-backdrop anim-fade-in" onClick={onClose}>
      <div
        className="ct-shell anim-slide-up"
        role="dialog"
        aria-modal="true"
        aria-label="Create new ticket"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="ct-head">
          <div className="ct-head-left">
            <span className="ct-head-icon"><Icon.Plus /></span>
            <div>
              <div className="ct-head-title">Create ticket</div>
              <div className="ct-head-sub">
                {customer
                  ? `Composing for ${customer.name}`
                  : 'Start by finding the customer this ticket is for'}
              </div>
            </div>
          </div>
          <div className="ct-head-right">
            <kbd className="ct-kbd">Esc</kbd>
            <button className="icon-btn ct-close" onClick={onClose} title="Close">
              <Icon.Close />
            </button>
          </div>
        </header>

        {!customer ? (
          <CustomerFinder
            lookup={lookup}
            onLookup={setLookup}
            hits={lookupHits}
            kind={lookupKind}
            onPick={setCustomer}
          />
        ) : (
          <div className="ct-body">
            {/* LEFT: context */}
            <aside className="ct-col ct-col-left">
              <CustomerCard
                customer={customer}
                onChange={() => {
                  setCustomer(null);
                  setOrderId(null);
                }}
              />
              <OrderPicker
                orders={customer.recentOrders}
                selected={orderId}
                onSelect={setOrderId}
              />
              <TipsCard />
            </aside>

            {/* RIGHT: composer */}
            <section className="ct-col ct-col-main">
              <div className="ct-panel">
                <div className="ct-field">
                  <label className="ct-label">
                    Subject
                    <span className="ct-req">*</span>
                    <span className="ct-hint">Short, search-friendly line</span>
                  </label>
                  <div className="ct-subject-wrap">
                    <input
                      ref={subjectRef}
                      className="ct-input ct-subject"
                      placeholder="e.g. Where is my SmartGRID mattress order?"
                      value={subject}
                      onChange={(e) => setSubject(e.target.value)}
                      maxLength={120}
                    />
                    <span className="ct-counter">{subject.length}/120</span>
                  </div>
                </div>

                <SimilarTicketsPanel
                  query={subject}
                  matches={similar}
                  currentCustomerId={customer.id}
                />

                <div className="ct-field">
                  <label className="ct-label">
                    Description
                    <span className="ct-hint">What happened, when, and any error details</span>
                  </label>
                  <textarea
                    className="ct-input ct-textarea"
                    placeholder="Add context for the agent who will pick this up…"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={4}
                  />
                </div>

                <div className="ct-field">
                  <label className="ct-label">
                    Channel
                    <span className="ct-req">*</span>
                    <span className="ct-hint">How did the customer reach out?</span>
                  </label>
                  <div className="ct-channel-grid">
                    {CHANNELS.map((c) => {
                      const disabled =
                        (c.key === 'whatsapp' || c.key === 'voice') && !customer.phone;
                      const active = channel === c.key;
                      return (
                        <button
                          key={c.key}
                          type="button"
                          className={
                            'ct-channel' +
                            (active ? ' active' : '') +
                            (disabled ? ' disabled' : '')
                          }
                          onClick={() => !disabled && setChannel(c.key)}
                          disabled={disabled}
                          title={disabled ? 'Phone number needed for this channel' : c.hint}
                        >
                          <span className="ct-channel-icon" style={{ background: c.color }}>
                            {c.icon}
                          </span>
                          <span className="ct-channel-body">
                            <span className="ct-channel-name">{c.label}</span>
                            <span className="ct-channel-hint">{c.hint}</span>
                          </span>
                          {active && (
                            <span className="ct-channel-tick">
                              <Icon.Check2 />
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="ct-row-2">
                  <div className="ct-field">
                    <label className="ct-label">Priority</label>
                    <div className="ct-priority-row">
                      {PRIORITIES.map((p) => (
                        <button
                          key={p.key}
                          type="button"
                          className={
                            'ct-priority tone-' + p.tone + (priority === p.key ? ' active' : '')
                          }
                          onClick={() => setPriority(p.key)}
                        >
                          <span className="status-dot" />
                          {p.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="ct-field">
                    <label className="ct-label">Tags</label>
                    <div className="ct-tags">
                      {tags.map((tg) => (
                        <span key={tg} className="ct-tag">
                          {tg}
                          <button
                            type="button"
                            className="ct-tag-x"
                            onClick={() => setTags((ts) => ts.filter((x) => x !== tg))}
                            aria-label={`remove ${tg}`}
                          >
                            <Icon.Close />
                          </button>
                        </span>
                      ))}
                      <input
                        className="ct-tag-input"
                        placeholder={tags.length ? '' : 'add a tag…'}
                        value={tagDraft}
                        onChange={(e) => setTagDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ',') {
                            e.preventDefault();
                            addTag(tagDraft);
                          } else if (e.key === 'Backspace' && !tagDraft && tags.length) {
                            setTags((ts) => ts.slice(0, -1));
                          }
                        }}
                      />
                    </div>
                  </div>
                </div>

                <div className="ct-share">
                  <label className="ct-check">
                    <input
                      type="checkbox"
                      checked={shareWithCustomer}
                      onChange={(e) => setShareWithCustomer(e.target.checked)}
                    />
                    <span className="ct-check-box" />
                    <span>Share initial response with the customer on the selected channel</span>
                  </label>
                </div>
              </div>
            </section>
          </div>
        )}

        <footer className="ct-foot">
          <div className="ct-foot-left">
            {customer && (
              <>
                <span className="ct-kbd">⌘</span>
                <span className="ct-kbd">↵</span>
                <span className="ct-foot-hint">to create ticket</span>
              </>
            )}
          </div>
          <div className="ct-foot-right">
            <button className="btn secondary" onClick={onClose}>Cancel</button>
            {customer && (
              <button
                className={'btn ct-submit' + (canSubmit ? '' : ' disabled')}
                onClick={submit}
                disabled={!canSubmit}
              >
                <Icon.Plus /> Create ticket
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

/* -----------------------------------------------------------------
   Sub-components
   ----------------------------------------------------------------- */

function CustomerFinder({ lookup, onLookup, hits, kind, onPick }) {
  const kindLabel = {
    phone:   'phone number',
    email:   'email address',
    shopify: 'Shopify customer ID',
    name:    'customer name',
  }[kind];
  return (
    <div className="ct-body ct-body-finder">
      <div className="ct-finder-card">
        <div className="ct-finder-head">
          <span className="ct-finder-head-icon"><Icon.Search /></span>
          <div>
            <div className="ct-finder-title">Who is this ticket for?</div>
            <div className="ct-finder-sub">
              Search by <b>phone</b>, <b>email</b>, <b>Shopify ID</b>, or <b>name</b>
              {kindLabel ? <> — detected as <b>{kindLabel}</b></> : null}
            </div>
          </div>
        </div>

        <div className="ct-finder-input-wrap">
          <Icon.Search className="ct-finder-input-icon" />
          <input
            autoFocus
            className="ct-input ct-finder-input"
            placeholder="Start typing — e.g. 9187… or name@gmail.com"
            value={lookup}
            onChange={(e) => onLookup(e.target.value)}
          />
          {lookup && (
            <button
              type="button"
              className="ct-finder-clear"
              onClick={() => onLookup('')}
              title="Clear"
            >
              <Icon.Close />
            </button>
          )}
        </div>

        {lookup.trim().length === 0 ? (
          <div className="ct-finder-empty">
            <Icon.InboxEmpty />
            <div className="ct-finder-empty-title">Search for a customer</div>
            <div className="ct-finder-empty-sub">
              We match against existing profiles first — avoids duplicate records.
            </div>
          </div>
        ) : hits.length === 0 ? (
          <div className="ct-finder-empty ct-finder-empty-nohit">
            <div className="ct-finder-empty-title">No customer matches "{lookup}"</div>
            <div className="ct-finder-empty-sub">
              You can proceed as a guest — we'll create a new profile on save.
            </div>
            <button
              className="btn secondary"
              onClick={() =>
                onPick({
                  id: 'new',
                  name: lookup.trim(),
                  phone: kind === 'phone' ? lookup.trim() : '',
                  email: kind === 'email' ? lookup.trim() : '',
                  shopifyId: '',
                  since: 'New',
                  orders: 0,
                  returns: 0,
                  address: '—',
                  recentOrders: [],
                  isGuest: true,
                })
              }
            >
              <Icon.Plus /> Create as new customer
            </button>
          </div>
        ) : (
          <div className="ct-finder-hits">
            <div className="ct-finder-hits-title">
              {hits.length} {hits.length === 1 ? 'match' : 'matches'}
            </div>
            {hits.map((c) => (
              <button key={c.id} className="ct-finder-hit" onClick={() => onPick(c)}>
                <div className="avatar sm" style={{ background: '#1e40af' }}>
                  {c.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
                </div>
                <div className="ct-finder-hit-body">
                  <div className="ct-finder-hit-name">{c.name}</div>
                  <div className="ct-finder-hit-sub">
                    <span>{c.phone}</span>
                    <span className="ct-dot-sep">·</span>
                    <span className="ct-finder-hit-email">{c.email}</span>
                  </div>
                </div>
                <div className="ct-finder-hit-stats">
                  <span className="ct-finder-hit-stat">{c.orders} orders</span>
                  <span className="ct-finder-hit-stat">{c.returns} returns</span>
                </div>
                <Icon.Chevron style={{ transform: 'rotate(-90deg)', color: 'var(--muted)' }} />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function CustomerCard({ customer, onChange }) {
  return (
    <div className="ct-panel ct-customer">
      <div className="ct-panel-head">
        <span>Customer</span>
        <button className="link link-btn" onClick={onChange}>
          Change <Icon.Chevron style={{ transform: 'rotate(-90deg)', width: 12, height: 12 }} />
        </button>
      </div>
      <div className="customer-top-row">
        <div className="avatar" style={{ background: '#1e40af' }}>
          {customer.name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}
        </div>
        <div style={{ minWidth: 0 }}>
          <div className="customer-top-name">{customer.name}</div>
          <div className="customer-top-sub">
            {customer.isGuest ? 'New customer — will be created' : `Customer since ${customer.since}`}
          </div>
        </div>
      </div>
      <div className="kv-list">
        {customer.phone && (
          <div className="kv-row"><Icon.Phone className="icon" /><span>{customer.phone}</span></div>
        )}
        {customer.email && (
          <div className="kv-row"><Icon.Mail className="icon" /><span>{customer.email}</span></div>
        )}
        {customer.address && customer.address !== '—' && (
          <div className="kv-row" style={{ alignItems: 'flex-start' }}>
            <Icon.MapPin className="icon" style={{ marginTop: 2 }} />
            <span>{customer.address}</span>
          </div>
        )}
      </div>
      {!customer.isGuest && (
        <div className="customer-metric-row">
          <div className="customer-metric">
            <div className="metric-value">{customer.orders}</div>
            <div className="metric-label">Orders</div>
          </div>
          <div className="customer-metric">
            <div className="metric-value">{customer.returns}</div>
            <div className="metric-label">Returns</div>
          </div>
        </div>
      )}
    </div>
  );
}

function OrderPicker({ orders, selected, onSelect }) {
  if (!orders || orders.length === 0) return null;
  return (
    <div className="ct-panel">
      <div className="ct-panel-head">
        <span>Link an order</span>
        <span className="muted-small">Optional</span>
      </div>
      <div className="ct-orders">
        {orders.map((o) => {
          const isSel = selected === o.id;
          return (
            <button
              key={o.id}
              type="button"
              className={'ct-order' + (isSel ? ' active' : '')}
              onClick={() => onSelect(isSel ? null : o.id)}
            >
              <div className="ct-order-top">
                <span className="ct-order-id">#{o.id}</span>
                <span className="ct-order-date">{o.placedAt}</span>
              </div>
              <div className="ct-order-items">
                {o.items.slice(0, 2).map((it, i) => (
                  <span key={i} className="ct-order-item">
                    {it.name} <span className="ct-order-qty">×{it.qty}</span>
                  </span>
                ))}
                {o.items.length > 2 && (
                  <span className="ct-order-item muted">+{o.items.length - 2} more</span>
                )}
              </div>
              <div className="ct-order-foot">
                <span className="ct-order-total">₹{o.total.toLocaleString()}</span>
                <span className={'ct-order-tick' + (isSel ? ' on' : '')}>
                  {isSel ? <Icon.Check2 /> : <span className="ct-order-tick-empty" />}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TipsCard() {
  return (
    <div className="ct-panel ct-tips">
      <div className="ct-panel-head"><span>Tips</span></div>
      <ul className="ct-tips-list">
        <li>A descriptive subject helps our AI route the ticket faster.</li>
        <li>Link the relevant order when the issue is order-specific.</li>
        <li>Similar open tickets will appear — merge instead of duplicating.</li>
      </ul>
    </div>
  );
}

function SimilarTicketsPanel({ query, matches, currentCustomerId }) {
  if (query.trim().length < 4) {
    return (
      <div className="ct-similar ct-similar-idle">
        <Icon.Info />
        <span>We'll check for similar existing tickets as you type the subject.</span>
      </div>
    );
  }
  if (matches.length === 0) {
    return (
      <div className="ct-similar ct-similar-none">
        <span className="ct-similar-ok">
          <Icon.Check2 /> No similar tickets found — safe to create
        </span>
      </div>
    );
  }
  const topPct = Math.round(matches[0].score * 100);
  const severe = topPct >= 60;
  return (
    <div className={'ct-similar' + (severe ? ' severe' : '')}>
      <div className="ct-similar-head">
        <span className="ct-similar-dot" />
        <span className="ct-similar-title">
          {severe ? 'Likely duplicate detected' : 'Similar tickets found'}
        </span>
        <span className="ct-similar-count">{matches.length}</span>
      </div>
      <div className="ct-similar-list">
        {matches.map(({ t, score }) => {
          const pct = Math.round(score * 100);
          const sameCustomer = t.customerId === currentCustomerId;
          return (
            <div key={t.id} className="ct-similar-row">
              <div className="ct-similar-score">
                <div
                  className="ct-similar-score-ring"
                  style={{ '--pct': pct + '%' }}
                  title={`${pct}% match`}
                >
                  <span className="ct-similar-score-num">{pct}</span>
                </div>
              </div>
              <div className="ct-similar-meta">
                <div className="ct-similar-row-top">
                  <span className="ct-similar-id">{t.id}</span>
                  <span className={'status-pill tone-' + statusTone[t.status]}>
                    <span className="status-dot" />
                    {t.status}
                  </span>
                  {sameCustomer && (
                    <span className="ct-similar-same">same customer</span>
                  )}
                </div>
                <div className="ct-similar-subject">{t.subject}</div>
                <div className="ct-similar-sub">
                  <span>{t.customer}</span>
                  <span className="ct-dot-sep">·</span>
                  <span>{t.channel}</span>
                  <span className="ct-dot-sep">·</span>
                  <span>{t.ago} ago</span>
                </div>
              </div>
              <div className="ct-similar-actions">
                <button className="btn ghost sm" title="Open ticket in new pane">
                  Open
                </button>
                <button className="btn secondary sm" title="Add my note to this ticket instead">
                  Merge
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
