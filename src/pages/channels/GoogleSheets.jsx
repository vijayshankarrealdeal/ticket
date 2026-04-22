import { useState } from 'react';
import { Icon } from '../../components/Icons.jsx';

const SERVICE_EMAIL = 'local-255@ai-chat-1c879.iam.gserviceaccount.com';

const initialSheets = [
  {
    id: 'sh1',
    name: 'Customer FAQ knowledge base',
    tabs: ['FAQs', 'Macros'],
    rows: 842,
    lastSync: '8 minutes ago',
    url: 'https://docs.google.com/spreadsheets/d/1abc',
    status: 'connected',
  },
  {
    id: 'sh2',
    name: 'Product catalog overrides',
    tabs: ['Products', 'Offers'],
    rows: 2104,
    lastSync: '1 hour ago',
    url: 'https://docs.google.com/spreadsheets/d/2def',
    status: 'connected',
  },
  {
    id: 'sh3',
    name: 'Pincode-to-store mapping',
    tabs: ['Pincodes'],
    rows: 19437,
    lastSync: 'Paused',
    url: 'https://docs.google.com/spreadsheets/d/3ghi',
    status: 'paused',
  },
];

export default function GoogleSheets() {
  const [sheets, setSheets] = useState(initialSheets);
  const [url, setUrl] = useState('');
  const [validating, setValidating] = useState(false);
  const [copyFlash, setCopyFlash] = useState(false);

  const copyEmail = async () => {
    try {
      await navigator.clipboard.writeText(SERVICE_EMAIL);
      setCopyFlash(true);
      setTimeout(() => setCopyFlash(false), 1200);
    } catch {
      /* ignore */
    }
  };

  const onValidate = (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    setValidating(true);
    setTimeout(() => {
      const id = 'sh-' + Date.now();
      setSheets((prev) => [
        {
          id,
          name: 'Imported sheet',
          tabs: ['Sheet1'],
          rows: 0,
          lastSync: 'Just now',
          url: url.trim(),
          status: 'connected',
        },
        ...prev,
      ]);
      setUrl('');
      setValidating(false);
    }, 900);
  };

  const remove = (id) => setSheets((prev) => prev.filter((s) => s.id !== id));
  const togglePause = (id) =>
    setSheets((prev) =>
      prev.map((s) =>
        s.id === id
          ? { ...s, status: s.status === 'paused' ? 'connected' : 'paused', lastSync: s.status === 'paused' ? 'Just now' : 'Paused' }
          : s,
      ),
    );

  return (
    <div className="ch-page">
      <div className="ch-page-head">
        <div>
          <div className="ch-breadcrumb">
            <span>Channels</span>
            <Icon.Chevron className="ch-breadcrumb-sep" />
            <span className="ch-breadcrumb-current">Google Sheets</span>
          </div>
          <h1 className="ch-page-title">Google Sheets</h1>
          <p className="ch-page-sub">
            Use spreadsheets as live data sources for your workflows, agents and automations.
          </p>
        </div>
        <div className="ch-page-head-actions">
          <a
            className="btn secondary"
            href="https://developers.google.com/sheets"
            target="_blank"
            rel="noreferrer"
          >
            <Icon.ExternalLink /> Setup guide
          </a>
        </div>
      </div>

      <div className="ch-sheet-callout">
        <div className="ch-sheet-callout-icon"><Icon.Info /></div>
        <div className="ch-sheet-callout-body">
          <div className="ch-sheet-callout-title">Share your sheet with this service account</div>
          <div className="ch-sheet-callout-sub">
            Grant <b>read &amp; edit</b> access to the address below before validating a sheet URL.
          </div>
          <div className={'ch-copy-field' + (copyFlash ? ' flash' : '')}>
            <code>{SERVICE_EMAIL}</code>
            <button className="ch-copy-btn" onClick={copyEmail}>
              {copyFlash ? (
                <>
                  <Icon.Check /> Copied
                </>
              ) : (
                <>
                  <Icon.Copy /> Copy
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      <div className="ch-two-col">
        <section className="ch-section">
          <div className="ch-section-head">
            <div className="ch-section-title">
              <span className="ch-section-icon"><Icon.Plus /></span>
              Add a Google Sheet
            </div>
          </div>
          <form className="ch-form" onSubmit={onValidate}>
            <label className="ch-field">
              <span className="ch-field-label">Google Sheet URL</span>
              <div className="ch-field-input">
                <Icon.Sheet />
                <input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://docs.google.com/spreadsheets/d/…"
                />
              </div>
              <span className="ch-field-hint">
                We&rsquo;ll read the tab names and row counts to confirm access.
              </span>
            </label>
            <button className="btn" disabled={validating || !url.trim()}>
              {validating ? 'Validating…' : 'Validate sheet'}
            </button>
          </form>
        </section>

        <section className="ch-section">
          <div className="ch-section-head">
            <div className="ch-section-title">
              <span className="ch-section-icon"><Icon.Sheet /></span>
              Saved sheets
              <span className="ch-section-count">{sheets.length}</span>
            </div>
          </div>
          {sheets.length === 0 ? (
            <div className="ch-empty inline">
              <div className="ch-empty-title">No sheets connected</div>
              <div className="ch-empty-sub">Paste a URL above to add your first sheet.</div>
            </div>
          ) : (
            <div className="ch-sheet-list">
              {sheets.map((s) => (
                <div key={s.id} className="ch-sheet-row">
                  <div className="ch-sheet-row-left">
                    <div className="ch-sheet-icon">
                      <Icon.Sheet />
                    </div>
                    <div>
                      <div className="ch-sheet-name">{s.name}</div>
                      <div className="ch-sheet-meta">
                        {s.tabs.length} tab{s.tabs.length === 1 ? '' : 's'} &middot; {s.rows.toLocaleString()} rows &middot; {s.lastSync}
                      </div>
                    </div>
                  </div>
                  <div className="ch-sheet-row-right">
                    <span
                      className={
                        'ch-pill ch-pill-' + (s.status === 'connected' ? 'success' : 'muted')
                      }
                    >
                      <span className="ch-pill-dot" />
                      {s.status === 'connected' ? 'Live' : 'Paused'}
                    </span>
                    <a className="ch-row-action" href={s.url} target="_blank" rel="noreferrer">
                      <Icon.ExternalLink /> Open
                    </a>
                    <button className="ch-row-action" onClick={() => togglePause(s.id)}>
                      {s.status === 'connected' ? 'Pause' : 'Resume'}
                    </button>
                    <button className="ch-row-action danger" onClick={() => remove(s.id)}>
                      <Icon.Trash /> Remove
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
