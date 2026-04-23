import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

const BASE_URL = import.meta.env.VITE_API_URL || '/api';

export default function PublicCsat() {
  const { token } = useParams();
  const [survey, setSurvey] = useState(null);
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const [error, setError] = useState(null);
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`${BASE_URL}/csat/${encodeURIComponent(token)}`)
      .then(async (r) => {
        if (r.status === 410) { setSubmitted(true); return null; }
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((s) => { if (s) setSurvey(s); })
      .catch((e) => setError(e.message || String(e)));
  }, [token]);

  const submit = async () => {
    if (!rating) return;
    setSaving(true);
    try {
      const r = await fetch(`${BASE_URL}/csat/${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, comment }),
      });
      if (!r.ok) throw new Error(`${r.status}`);
      setSubmitted(true);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: '#0f172a',
      padding: 24,
    }}>
      <div style={{
        background: '#fff',
        padding: 32,
        borderRadius: 16,
        boxShadow: '0 30px 60px rgba(15,23,42,0.4)',
        width: 480,
        maxWidth: '100%',
      }}>
        <h2 style={{ margin: '0 0 8px' }}>How was your experience?</h2>
        {submitted ? (
          <div style={{ color: '#475569' }}>
            Thanks — your feedback has been recorded.
          </div>
        ) : error ? (
          <div style={{ color: '#dc2626' }}>Survey unavailable: {error}</div>
        ) : !survey ? (
          <div style={{ color: '#64748b' }}>Loading…</div>
        ) : (
          <>
            {survey.subject && (
              <div style={{ color: '#64748b', fontSize: 13, marginBottom: 20 }}>
                About: <b>{survey.subject}</b>
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setRating(n)}
                  style={{
                    flex: 1, padding: '10px 6px', borderRadius: 10,
                    border: rating === n ? '2px solid #2563eb' : '1px solid #e2e8f0',
                    background: rating === n ? '#eff6ff' : '#fff',
                    fontWeight: 600, cursor: 'pointer',
                  }}
                >
                  {n}
                </button>
              ))}
            </div>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Anything else we should know?"
              rows={3}
              style={{
                width: '100%', padding: 10, border: '1px solid #e2e8f0',
                borderRadius: 10, resize: 'vertical', fontSize: 14,
              }}
            />
            <button
              type="button"
              onClick={submit}
              disabled={!rating || saving}
              style={{
                marginTop: 16, width: '100%', padding: '10px 16px',
                borderRadius: 10, border: 'none',
                background: rating && !saving ? '#2563eb' : '#94a3b8',
                color: '#fff', fontWeight: 600,
                cursor: rating && !saving ? 'pointer' : 'not-allowed',
              }}
            >
              {saving ? 'Sending…' : 'Submit'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
