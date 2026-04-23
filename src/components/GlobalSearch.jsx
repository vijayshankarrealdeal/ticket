import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from './Icons.jsx';
import { useDelayUnmount } from '../hooks/useDelayUnmount.js';
import { useGlobalSearch } from '../hooks/useGlobalSearch.jsx';
import { runGlobalSearch } from '../data/globalSearchData.js';

const EMPTY_RESULTS = { conversations: [], tickets: [], contacts: [], total: 0 };

const TYPE_META = {
  conversation: { label: 'Conversations', icon: Icon.Inbox,  tone: 'info'    },
  ticket:       { label: 'Tickets',       icon: Icon.Ticket, tone: 'primary' },
  contact:      { label: 'Contacts',      icon: Icon.User,   tone: 'success' },
};

export default function GlobalSearch() {
  const { query, setQuery } = useGlobalSearch();
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const wrapRef = useRef(null);
  const inputRef = useRef(null);
  const navigate = useNavigate();
  const panel = useDelayUnmount(open && query.trim().length > 0, 160);

  const [results, setResults] = useState(EMPTY_RESULTS);
  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults(EMPTY_RESULTS); return; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      runGlobalSearch(q, 8, { signal: ctrl.signal }).then(setResults).catch(() => {});
    }, 120);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [query]);

  const flat = useMemo(() => {
    return [
      ...results.conversations.map((r) => ({ ...r, _group: 'conversation' })),
      ...results.tickets.map((r) => ({ ...r, _group: 'ticket' })),
      ...results.contacts.map((r) => ({ ...r, _group: 'contact' })),
    ];
  }, [results]);

  useEffect(() => { setActiveIdx(0); }, [query]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
        setOpen(true);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const go = (item) => {
    setOpen(false);
    setQuery('');
    if (item.type === 'conversation') navigate(`/conversations?id=${encodeURIComponent(item.id)}`);
    else if (item.type === 'ticket')  navigate(`/tickets?id=${encodeURIComponent(item.id)}`);
    else if (item.type === 'contact') navigate(`/customers?id=${encodeURIComponent(item.id)}`);
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      setOpen(false);
      e.currentTarget.blur();
      return;
    }
    if (!flat.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % flat.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + flat.length) % flat.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const item = flat[activeIdx];
      if (item) go(item);
    }
  };

  let runningIdx = -1;
  const renderGroup = (type, items) => {
    if (items.length === 0) return null;
    const meta = TYPE_META[type];
    const IconCmp = meta.icon;
    return (
      <div className="gs-group" key={type}>
        <div className="gs-group-head">
          <span className={'gs-group-label tone-' + meta.tone}><IconCmp /> {meta.label}</span>
          <span className="gs-group-count">{items.length}</span>
        </div>
        {items.map((r) => {
          runningIdx += 1;
          const isActive = runningIdx === activeIdx;
          return (
            <button
              key={type + '-' + r.id}
              type="button"
              className={'gs-item' + (isActive ? ' active' : '')}
              onMouseEnter={(() => {
                const captured = runningIdx;
                return () => setActiveIdx(captured);
              })()}
              onClick={() => go(r)}
            >
              <span className={'gs-item-icon tone-' + meta.tone}><IconCmp /></span>
              <span className="gs-item-body">
                <span className="gs-item-title">{r.title}</span>
                {r.subtitle && <span className="gs-item-sub">{r.subtitle}</span>}
              </span>
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div className="global-search" ref={wrapRef}>
      <div className={'gs-box' + (open ? ' focus' : '')}>
        <Icon.Search />
        <input
          ref={inputRef}
          type="text"
          placeholder="Search conversations, tickets, contacts…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          aria-label="Global search"
        />
        {query ? (
          <button
            type="button"
            className="gs-clear"
            title="Clear"
            onClick={() => { setQuery(''); inputRef.current?.focus(); }}
          >
            <Icon.Close />
          </button>
        ) : (
          <span className="gs-kbd">⌘K</span>
        )}
      </div>

      {panel.mounted && (
        <div
          className={'gs-panel' + (panel.state === 'exiting' ? ' closing' : '')}
          role="listbox"
          aria-label="Search results"
        >
          {results.total === 0 ? (
            <div className="gs-empty">
              <Icon.Search />
              <div>No matches for <b>"{query}"</b></div>
            </div>
          ) : (
            <>
              {renderGroup('conversation', results.conversations)}
              {renderGroup('ticket',       results.tickets)}
              {renderGroup('contact',      results.contacts)}
            </>
          )}
        </div>
      )}
    </div>
  );
}
