import { useEffect, useState } from 'react';
import { Icon } from '../../components/Icons.jsx';
import { Spinner } from '../../components/Spinner.jsx';
import { api } from '../../api.js';

export default function GoogleSheets() {
  const [sheets, setSheets] = useState([]);
  const [serviceEmail, setServiceEmail] = useState('');
  const [url, setUrl] = useState('');
  const [validating, setValidating] = useState(false);
  const [copyFlash, setCopyFlash] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api.get('/sheets')
      .then((res) => {
        if (!alive) return;
        const items = Array.isArray(res) ? res : res?.items || [];
        setSheets(items);
        if (res?.serviceEmail) setServiceEmail(res.serviceEmail);
      })
      .catch(() => { if (alive) setSheets([]); })
      .finally(() => { if (alive) setInitialLoading(false); });
    return () => { alive = false; };
  }, []);

  const copyEmail = async () => {
    if (!serviceEmail) return;
    try {
      await navigator.clipboard.writeText(serviceEmail);
      setCopyFlash(true);
      setTimeout(() => setCopyFlash(false), 1200);
    } catch { /* ignore */ }
  };

  const onValidate = async (e) => {
    e.preventDefault();
    if (!url.trim()) return;
    setValidating(true);
    try {
      const validated = await api.post('/sheets/validate', { url: url.trim() });
      const created = await api.post('/sheets', validated || { url: url.trim() });
      if (created) setSheets((prev) => [created, ...prev]);
    } catch { /* ignore */ }
    setUrl('');
    setValidating(false);
  };

  const remove = async (id) => {
    try { await api.del(`/sheets/${encodeURIComponent(id)}`); } catch {}
    setSheets((prev) => prev.filter((s) => s.id !== id));
  };

  const togglePause = async (id) => {
    const sheet = sheets.find((s) => s.id === id);
    if (!sheet) return;
    const nextStatus = sheet.status === 'paused' ? 'connected' : 'paused';
    try { await api.patch(`/sheets/${encodeURIComponent(id)}`, { status: nextStatus }); } catch {}
    setSheets((prev) =>
      prev.map((s) =>
        s.id === id
          ? { ...s, status: nextStatus, lastSync: nextStatus === 'paused' ? 'Paused' : 'Just now' }
          : s,
      ),
    );
  };

  const sync = async (id) => {
    try {
      const updated = await api.post(`/sheets/${encodeURIComponent(id)}/sync`);
      setSheets((prev) => prev.map((s) => (s.id === id ? { ...s, ...updated } : s)));
    } catch { /* ignore */ }
  };

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
            <code>{serviceEmail || '—'}</code>
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
          {initialLoading ? (
            <div style={{ padding: '40px 16px', display: 'flex', justifyContent: 'center' }}>
              <Spinner size={24} />
            </div>
          ) : sheets.length === 0 ? (
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
                    <button className="ch-row-action" onClick={() => sync(s.id)}>
                      Sync now
                    </button>
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
