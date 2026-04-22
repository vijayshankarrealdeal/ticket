import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../components/Icons.jsx';
import { BrandMark } from '../components/Sidebar.jsx';
import { PRODUCT_NAME } from '../config.js';
import { useTheme } from '../hooks/useTheme.js';

const AUTH_KEY = 'workspacex-auth';
const HERO_IMAGE =
  'https://images.pexels.com/photos/30770668/pexels-photo-30770668.jpeg';

export function loadAuth() {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveAuth(user) {
  try {
    localStorage.setItem(AUTH_KEY, JSON.stringify(user));
  } catch {}
}

export function clearAuth() {
  try {
    localStorage.removeItem(AUTH_KEY);
  } catch {}
}

export default function AuthPage({ onAuthenticated }) {
  const [mode, setMode] = useState('signin');
  const [showPassword, setShowPassword] = useState(false);
  const [values, setValues] = useState({
    name: '',
    email: '',
    password: '',
    confirm: '',
  });
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(null);
  const [banner, setBanner] = useState(null);
  const firstFieldRef = useRef(null);
  const { theme, toggle } = useTheme();

  useEffect(() => {
    firstFieldRef.current?.focus();
  }, [mode]);

  const isSignUp = mode === 'signup';

  const passwordStrength = useMemo(() => {
    const pw = values.password;
    if (!pw) return { score: 0, label: '' };
    let score = 0;
    if (pw.length >= 8) score++;
    if (pw.length >= 12) score++;
    if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) score++;
    if (/\d/.test(pw)) score++;
    if (/[^A-Za-z0-9]/.test(pw)) score++;
    const label = ['Too short', 'Weak', 'Fair', 'Good', 'Strong', 'Very strong'][score];
    return { score, label };
  }, [values.password]);

  const setField = (field) => (e) => {
    const val = e.target.value;
    setValues((v) => ({ ...v, [field]: val }));
    if (errors[field]) setErrors((prev) => ({ ...prev, [field]: undefined }));
  };

  const validate = () => {
    const next = {};
    if (isSignUp && !values.name.trim()) next.name = 'Please enter your name';
    if (!values.email.trim()) {
      next.email = 'Email is required';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim())) {
      next.email = 'Enter a valid email address';
    }
    if (!values.password) {
      next.password = 'Password is required';
    } else if (isSignUp && values.password.length < 8) {
      next.password = 'Use at least 8 characters';
    }
    if (isSignUp && values.confirm !== values.password) {
      next.confirm = 'Passwords do not match';
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setBanner(null);
    if (!validate()) return;
    setSubmitting(true);
    await new Promise((r) => setTimeout(r, 650));
    setSubmitting(false);
    const name =
      values.name.trim() ||
      values.email.split('@')[0].replace(/[._-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    onAuthenticated({
      name,
      email: values.email.trim(),
      provider: 'password',
      signedInAt: new Date().toISOString(),
    });
  };

  const handleSso = (provider) => async () => {
    setBanner(null);
    setSsoLoading(provider);
    await new Promise((r) => setTimeout(r, 800));
    setSsoLoading(null);
    const profiles = {
      outlook: { name: 'Outlook User', email: 'you@outlook.com' },
      google: { name: 'Google User', email: 'you@gmail.com' },
    };
    onAuthenticated({
      ...profiles[provider],
      provider,
      signedInAt: new Date().toISOString(),
    });
  };

  const switchMode = (next) => {
    setMode(next);
    setErrors({});
    setBanner(null);
  };

  return (
    <div className="auth-shell">
        <aside
          className="auth-hero-side"
          style={{ backgroundImage: `url(${HERO_IMAGE})` }}
          aria-hidden="true"
        />


        <section className="auth-form-side">
          <div className="auth-brand">
            <span className="auth-brand-mark"><BrandMark /></span>
            <span className="auth-brand-text">
              {PRODUCT_NAME.slice(0, -1)}
              <span className="auth-brand-accent">{PRODUCT_NAME.slice(-1)}</span>
            </span>
            <button
              className="auth-theme-toggle"
              onClick={toggle}
              aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
            >
              {theme === 'dark' ? <Icon.Sun /> : <Icon.Moon />}
            </button>
          </div>

          <div className="auth-form-scroll">
            {banner && <div className="auth-banner">{banner}</div>}

            <form className="auth-form" onSubmit={handleSubmit} noValidate>
              {isSignUp && (
                <div className={'auth-float' + (errors.name ? ' has-error' : '')}>
                  <input
                    id="auth-name"
                    ref={firstFieldRef}
                    type="text"
                    autoComplete="name"
                    placeholder=" "
                    value={values.name}
                    onChange={setField('name')}
                  />
                  <label htmlFor="auth-name">Full name</label>
                  {errors.name && <div className="auth-error">{errors.name}</div>}
                </div>
              )}

              <div className={'auth-float' + (errors.email ? ' has-error' : '')}>
                <input
                  id="auth-email"
                  ref={isSignUp ? null : firstFieldRef}
                  type="email"
                  autoComplete="email"
                  placeholder=" "
                  value={values.email}
                  onChange={setField('email')}
                />
                <label htmlFor="auth-email">Email ID</label>
                {errors.email && <div className="auth-error">{errors.email}</div>}
              </div>

              <div className={'auth-float' + (errors.password ? ' has-error' : '')}>
                <input
                  id="auth-password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete={isSignUp ? 'new-password' : 'current-password'}
                  placeholder=" "
                  value={values.password}
                  onChange={setField('password')}
                />
                <label htmlFor="auth-password">Password</label>
                <button
                  type="button"
                  className="auth-eye"
                  onClick={() => setShowPassword((s) => !s)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOffIcon /> : <EyeIcon />}
                </button>
                {errors.password && <div className="auth-error">{errors.password}</div>}
                {isSignUp && values.password && !errors.password && (
                  <div className="auth-strength" aria-live="polite">
                    <div className={'auth-strength-track strength-' + passwordStrength.score}>
                      <div className="auth-strength-bar" />
                    </div>
                    <span className="auth-strength-label">{passwordStrength.label}</span>
                  </div>
                )}
              </div>

              {isSignUp && (
                <div className={'auth-float' + (errors.confirm ? ' has-error' : '')}>
                  <input
                    id="auth-confirm"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="new-password"
                    placeholder=" "
                    value={values.confirm}
                    onChange={setField('confirm')}
                  />
                  <label htmlFor="auth-confirm">Confirm password</label>
                  {errors.confirm && <div className="auth-error">{errors.confirm}</div>}
                </div>
              )}

              {!isSignUp && (
                <div className="auth-row-between">
                  <span />
                  <button
                    type="button"
                    className="auth-inline-link"
                    onClick={() =>
                      setBanner(
                        'Password reset is not wired up in this demo — contact your admin.'
                      )
                    }
                  >
                    Forgot password?
                  </button>
                </div>
              )}

              <button
                type="submit"
                className="auth-submit"
                disabled={submitting || !!ssoLoading}
              >
                {submitting ? (
                  <span className="auth-spinner light" aria-hidden="true" />
                ) : (
                  <span>{isSignUp ? 'Sign Up' : 'Login'}</span>
                )}
              </button>
            </form>

            <div className="auth-divider"><span>Or continue with</span></div>

            <div className="auth-socials" role="group" aria-label="Sign in with a provider">
              <button
                type="button"
                className="auth-social outlook"
                onClick={handleSso('outlook')}
                disabled={!!ssoLoading || submitting}
                aria-label="Continue with Outlook"
                title="Continue with Outlook"
              >
                {ssoLoading === 'outlook' ? (
                  <span className="auth-spinner" aria-hidden="true" />
                ) : (
                  <OutlookIcon />
                )}
              </button>
              <button
                type="button"
                className="auth-social google"
                onClick={handleSso('google')}
                disabled={!!ssoLoading || submitting}
                aria-label="Continue with Google"
                title="Continue with Google"
              >
                {ssoLoading === 'google' ? (
                  <span className="auth-spinner" aria-hidden="true" />
                ) : (
                  <GoogleIcon />
                )}
              </button>
            </div>

            <div className="auth-foot">
              {isSignUp ? (
                <>
                  Already have an account?{' '}
                  <button
                    type="button"
                    className="auth-inline-link strong"
                    onClick={() => switchMode('signin')}
                  >
                    Sign In
                  </button>
                </>
              ) : (
                <>
                  Don&apos;t have an account?{' '}
                  <button
                    type="button"
                    className="auth-inline-link strong"
                    onClick={() => switchMode('signup')}
                  >
                    Register Now
                  </button>
                </>
              )}
            </div>
          </div>
        </section>
    </div>
  );
}

function EyeIcon(p) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon(p) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
      <path d="M3 3l18 18" />
      <path d="M10.6 6.1A10.4 10.4 0 0 1 12 6c6.5 0 10 6 10 6a14.5 14.5 0 0 1-3.2 3.9" />
      <path d="M6.1 6.1C3.6 7.8 2 12 2 12s3.5 7 10 7a10.7 10.7 0 0 0 4.9-1.2" />
      <path d="M9.9 9.9A3 3 0 0 0 12 15a3 3 0 0 0 2.1-.9" />
    </svg>
  );
}

function OutlookIcon(p) {
  return (
    <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true" {...p}>
      <path fill="#0A2767" d="M19.5 6v4l-1.5.9L13 13.5 8 11V7a1 1 0 0 1 1-1h10.5z" />
      <path fill="#0364B8" d="M29 11.3v10.3c0 .6-.3 1.2-.9 1.4l-9.6 3.8v-7.2l6-3.7V12l4.5-.7z" />
      <path fill="#0078D4" d="M19.5 10v5.9l-6 3.7-6-3.7V10H5.3c-.7 0-1.3.6-1.3 1.3v11.4c0 .7.6 1.3 1.3 1.3h13.2V19l6-3.7 4.5-3v-.9L19.5 10z" />
      <path fill="#28A8EA" d="M13.5 19.6l-9.5-4.8v-3.5c0-.7.6-1.3 1.3-1.3h13.2v6l-5 3.6z" />
      <path fill="#50D9FF" d="M4 14.8l9.5 4.8 6-3.6V10h-14c-.8 0-1.5.6-1.5 1.5v3.3z" />
      <path fill="#0A2767" opacity=".5" d="M19.5 19.6l-6 4v2.2l6-2.4v-3.8z" />
      <rect x="3" y="11" width="10" height="10" rx="1.5" fill="#0078D4" />
      <text x="8" y="18.2" textAnchor="middle" fontSize="7.2" fontWeight="700" fontFamily="Segoe UI, Inter, sans-serif" fill="#fff">O</text>
    </svg>
  );
}

function GoogleIcon(p) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" {...p}>
      <path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.4h6.5a5.6 5.6 0 0 1-2.4 3.7v3h3.9c2.3-2.1 3.5-5.2 3.5-8.8z" />
      <path fill="#34A853" d="M12 24c3.2 0 5.9-1.1 7.9-2.9l-3.9-3a7.3 7.3 0 0 1-11-3.8H1v3A12 12 0 0 0 12 24z" />
      <path fill="#FBBC05" d="M5 14.3a7.2 7.2 0 0 1 0-4.6v-3H1a12 12 0 0 0 0 10.6l4-3z" />
      <path fill="#EA4335" d="M12 4.8a6.5 6.5 0 0 1 4.6 1.8l3.4-3.4A12 12 0 0 0 1 6.7l4 3A7.2 7.2 0 0 1 12 4.8z" />
    </svg>
  );
}

