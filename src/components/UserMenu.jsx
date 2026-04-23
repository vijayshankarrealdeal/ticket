import { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { Icon } from './Icons.jsx';
import { useDelayUnmount } from '../hooks/useDelayUnmount.js';
import { clearAuth, loadAuth } from '../pages/AuthPage.jsx';
import { closeRealtime } from '../hooks/useRealtime.js';

const AVATAR_PALETTE = [
  { bg: '#eef2ff', fg: '#4338ca' },
  { bg: '#fef3c7', fg: '#b45309' },
  { bg: '#dcfce7', fg: '#166534' },
  { bg: '#fce7f3', fg: '#9d174d' },
  { bg: '#e0f2fe', fg: '#075985' },
  { bg: '#ffedd5', fg: '#9a3412' },
  { bg: '#f3e8ff', fg: '#6b21a8' },
  { bg: '#cffafe', fg: '#155e75' },
];

function getInitials(name = '') {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function colorFor(seed = '') {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

export default function UserMenu({
  name,
  email,
  role = 'Admin',
  image,
}) {
  const stored = loadAuth();
  name = name || stored?.name || 'Jane Doe';
  email = email || stored?.email || 'jane@thesleepcompany.in';
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const wrapRef = useRef(null);
  const navigate = useNavigate();
  const panel = useDelayUnmount(open, 200);
  const confirmPanel = useDelayUnmount(confirming, 200);
  const confirmBtnRef = useRef(null);

  const initials = getInitials(name);
  const color = colorFor(name);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!confirming) return;
    const onKey = (e) => {
      if (e.key === 'Escape') setConfirming(false);
      else if (e.key === 'Enter') {
        e.preventDefault();
        closeRealtime();
        clearAuth();
        window.location.reload();
      }
    };
    document.addEventListener('keydown', onKey);
    confirmBtnRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [confirming]);

  const goProfile = () => {
    setOpen(false);
    navigate('/settings');
  };

  const signOut = () => {
    setOpen(false);
    setConfirming(true);
  };

  const confirmSignOut = () => {
    setConfirming(false);
    clearAuth();
    window.location.reload();
  };

  return (
    <div className="user-menu-wrap" ref={wrapRef}>
      <button
        className="user-avatar-btn"
        onClick={() => setOpen((v) => !v)}
        title={name}
        aria-label={`Account menu for ${name}`}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Avatar name={name} initials={initials} image={image} color={color} size={30} />
      </button>
      {panel.mounted && (
        <div
          className={'user-menu' + (panel.state === 'exiting' ? ' closing' : '')}
          role="menu"
          aria-label="Account menu"
        >
          <div className="user-menu-head">
            <Avatar name={name} initials={initials} image={image} color={color} size={40} />
            <div className="user-menu-identity">
              <div className="user-menu-name">{name}</div>
              <div className="user-menu-email" title={email}>{email}</div>
              {role && <div className="user-menu-role">{role}</div>}
            </div>
          </div>
          <div className="user-menu-sep" />
          <button className="user-menu-item" onClick={goProfile} role="menuitem">
            <Icon.User />
            <span>View profile</span>
          </button>
          <button className="user-menu-item danger" onClick={signOut} role="menuitem">
            <SignOutIcon />
            <span>Sign out</span>
          </button>
        </div>
      )}
      {confirmPanel.mounted &&
        createPortal(
          <div
            className={
              'modal-backdrop confirm-backdrop' +
              (confirmPanel.state === 'exiting' ? ' closing' : '')
            }
            onClick={() => setConfirming(false)}
          >
            <div
              className={
                'confirm-dialog' +
                (confirmPanel.state === 'exiting' ? ' anim-exit' : ' anim-enter')
              }
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="signout-confirm-title"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="confirm-icon">
                <SignOutIcon />
              </div>
              <div className="confirm-body">
                <div className="confirm-title" id="signout-confirm-title">
                  Sign out of your account?
                </div>
                <div className="confirm-sub">
                  You'll need to sign in again to access your tickets.
                </div>
              </div>
              <div className="confirm-actions">
                <button
                  className="confirm-btn confirm-btn-ghost"
                  onClick={() => setConfirming(false)}
                >
                  Cancel
                </button>
                <button
                  ref={confirmBtnRef}
                  className="confirm-btn confirm-btn-danger"
                  onClick={confirmSignOut}
                >
                  Sign out
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}

function Avatar({ name, initials, image, color, size }) {
  const style = { width: size, height: size, fontSize: Math.round(size * 0.4) };
  if (image) {
    return (
      <img
        src={image}
        alt={name}
        className="user-avatar-img"
        style={style}
      />
    );
  }
  return (
    <span
      className="user-avatar"
      style={{ ...style, background: color.bg, color: color.fg }}
      aria-label={name}
    >
      {initials}
    </span>
  );
}

function SignOutIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}
