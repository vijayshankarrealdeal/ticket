/* WorkspaceX — Langflow rebrand runtime.
 *
 * Jobs (all idempotent, re-run on every DOM mutation):
 *   1. Force Langflow's dark theme so the embedded editor matches WorkspaceX.
 *   2. Hide the welcome splash except the "Create first flow" CTA —
 *      headline, subtitle, Langflow icon card above it, GitHub/Discord
 *      cards, and the "Already have a flow? Drag and drop" footer.
 *   3. Hide "Version" + "Docs" rows inside the profile dropdown.
 *
 * Deliberately *not* doing:
 *   - No ancestor-walking / sibling-pruning around the avatar. The earlier
 *     version of this script tried that and ended up hiding the flow
 *     editor's left sidebar + save/publish toolbar. CSS in wx-rebrand.css
 *     handles the stable chrome selectors (logo, github/discord/x chips).
 */
(function () {
  'use strict';

  // ---- WorkspaceX handshake ----------------------------------------------
  // `/flow/launch` appends `_wxToken=<session>` + `_wxApi=<backend-origin>`
  // to the redirect URL. Capture both on first paint so later hooks (Export
  // save-as-agent) can call WorkspaceX without CORS cookies. We strip the
  // params from the URL afterwards so they don't leak into history/referer.
  function captureWorkspaceXHandoff() {
    try {
      const url = new URL(window.location.href);
      const tk = url.searchParams.get('_wxToken');
      const api = url.searchParams.get('_wxApi');
      let mutated = false;
      if (tk) {
        sessionStorage.setItem('workspacex.token', tk);
        url.searchParams.delete('_wxToken');
        mutated = true;
      }
      if (api) {
        sessionStorage.setItem('workspacex.api', api);
        url.searchParams.delete('_wxApi');
        mutated = true;
      }
      if (mutated) {
        window.history.replaceState(null, '', url.toString());
      }
    } catch (_) { }
  }
  captureWorkspaceXHandoff();

  function wxToken() { return sessionStorage.getItem('workspacex.token') || ''; }
  function wxApi()   { return sessionStorage.getItem('workspacex.api') || ''; }

  function currentFlowId() {
    // Langflow's editor URL is `/flow/<uuid>` (or `/lf/flow/<uuid>` under
    // the proxy). Pull the last UUID segment.
    const m = /\/flow\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i
      .exec(window.location.pathname);
    return m ? m[1] : null;
  }

  function toast(message, kind) {
    // Minimal toast — reuses Langflow's dark palette. Slides in from bottom
    // right, auto-dismisses after 3s. Multiple toasts stack.
    const el = document.createElement('div');
    el.textContent = message;
    const stack = document.getElementById('wx-toast-stack') || (() => {
      const s = document.createElement('div');
      s.id = 'wx-toast-stack';
      s.style.cssText = [
        'position:fixed', 'right:20px', 'bottom:20px', 'z-index:99999',
        'display:flex', 'flex-direction:column', 'gap:8px',
        'pointer-events:none',
      ].join(';');
      document.body.appendChild(s);
      return s;
    })();
    el.style.cssText = [
      'padding:10px 16px', 'border-radius:8px',
      'background:' + (kind === 'error' ? '#7f1d1d' : '#111827'),
      'color:#f8fafc', 'font:14px system-ui,sans-serif',
      'border:1px solid ' + (kind === 'error' ? '#b91c1c' : '#1f2937'),
      'box-shadow:0 10px 25px rgba(0,0,0,0.35)',
      'pointer-events:auto', 'opacity:0', 'transform:translateY(8px)',
      'transition:opacity .18s, transform .18s',
    ].join(';');
    stack.appendChild(el);
    requestAnimationFrame(() => {
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    });
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      setTimeout(() => el.remove(), 220);
    }, 3000);
  }

  async function saveFlowToWorkspaceX(overrides) {
    const token = wxToken();
    const api = wxApi();
    if (!token || !api) {
      console.warn('[wx] save-as-agent: missing token/api from launch handoff');
      return;
    }
    const flowId = currentFlowId();
    if (!flowId) {
      console.warn('[wx] save-as-agent: no flow id in URL');
      return;
    }
    // Fetch the flow + variables. Both are same-origin calls on Langflow
    // itself, so cookies flow automatically.
    let flow = null;
    try {
      const r = await fetch(`/api/v1/flows/${flowId}`, { credentials: 'include' });
      if (r.ok) flow = await r.json();
    } catch (_) { }
    if (!flow) return;

    let variables = [];
    try {
      const r = await fetch('/api/v1/variables/', { credentials: 'include' });
      if (r.ok) {
        const body = await r.json();
        variables = Array.isArray(body) ? body : (body.items || body.variables || []);
      }
    } catch (_) { }

    // Prefer the values the user typed in the Export dialog (overrides) over
    // the flow's stored editor name/description. Langflow's Export dialog
    // lets users rename the download without persisting the rename back to
    // the flow record, so the dialog inputs are the source of truth for
    // what the operator actually wants to call this snapshot.
    const name = (overrides && overrides.name) || flow.name || null;
    const description = (overrides && overrides.description)
      || flow.description || '';

    const payload = {
      flowId,
      name,
      description,
      data: flow.data || {},
      variables: (variables || [])
        .filter((v) => v && v.name)
        .map((v) => ({ name: v.name, value: v.value, type: v.type || null })),
    };

    try {
      const qs = token ? `?token=${encodeURIComponent(token)}` : '';
      const resp = await fetch(`${api}/flow/save-as-agent${qs}`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        toast(`WorkspaceX save failed (${resp.status})`, 'error');
        console.warn('[wx] save-as-agent response', resp.status, text);
        return;
      }
      const body = await resp.json().catch(() => ({}));
      const secretsMsg = body.variablesStored
        ? ` · ${body.variablesStored} secret${body.variablesStored === 1 ? '' : 's'} stored`
        : '';
      toast(`Saved to WorkspaceX — open the Agent Builder to chat${secretsMsg}`);
    } catch (e) {
      toast('WorkspaceX save failed (network)', 'error');
      console.warn('[wx] save-as-agent', e);
    }
  }

  function readExportDialogOverrides(dialog) {
    // Langflow's dialog renders the Name/Description using form labels
    // rather than stable ids, so look them up by associated <label> text
    // first, then fall back to the first/second input/textarea inside the
    // dialog. The fallback is fine because the dialog structure has been
    // Name→Description top-to-bottom since Langflow 1.x.
    const byLabel = (rx) => {
      for (const lbl of dialog.querySelectorAll('label')) {
        if (!rx.test((lbl.textContent || '').trim())) continue;
        const id = lbl.getAttribute('for');
        if (id) {
          const el = dialog.querySelector(`#${CSS.escape(id)}`);
          if (el) return el;
        }
        const sibling = lbl.parentElement
          && (lbl.parentElement.querySelector('input, textarea'));
        if (sibling) return sibling;
      }
      return null;
    };
    const nameEl = byLabel(/^name\b/i) || dialog.querySelector('input');
    const descEl = byLabel(/^description\b/i) || dialog.querySelector('textarea');
    return {
      name: (nameEl && typeof nameEl.value === 'string')
        ? nameEl.value.trim() : '',
      description: (descEl && typeof descEl.value === 'string')
        ? descEl.value.trim() : '',
    };
  }

  function hookExportButton() {
    // Walk every button with the visible text "Export". Only engage inside
    // Langflow's export dialog, not the Share menu's "Export" row — the
    // dialog's confirm button is what finalises a download, and that's
    // where we want the side-effect.
    //
    // We STOP the native JSON file download. In WorkspaceX, Export is
    // rebranded as "Save to WorkspaceX" — the user's mental model is
    // publish-the-flow, not download-a-file. The Save As dialog confused
    // operators and the JSON file was rarely what they wanted. The flow
    // still persists to WorkspaceX via /flow/save-as-agent below, and the
    // AgentBuilder chat playground picks it up immediately.
    const candidates = document.querySelectorAll('button, [role="button"]');
    candidates.forEach((btn) => {
      if (btn.dataset.wxSaveHooked === '1') return;
      const text = (btn.textContent || '').trim();
      if (text !== 'Export') return;
      const dialog = btn.closest('[role="dialog"], [data-state="open"]');
      if (!dialog) return;
      btn.dataset.wxSaveHooked = '1';

      // Rename the button in-place so the UX matches what actually happens.
      try {
        // Walk text nodes so we don't stomp any child <svg> icons.
        const walker = document.createTreeWalker(btn, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
          if ((node.nodeValue || '').trim() === 'Export') {
            node.nodeValue = 'Save to WorkspaceX';
            break;
          }
        }
      } catch (_) { }

      btn.addEventListener('click', (ev) => {
        // Snapshot the dialog inputs BEFORE Langflow tears the dialog down.
        const overrides = readExportDialogOverrides(dialog);
        // Suppress Langflow's own click handler so the browser doesn't get
        // a Blob URL download + native Save As prompt.
        ev.preventDefault();
        ev.stopImmediatePropagation();
        ev.stopPropagation();
        saveFlowToWorkspaceX(overrides)
          .catch((e) => console.warn('[wx] save', e))
          .finally(() => {
            // Close the Export dialog so the operator sees the toast and
            // lands back on the editor. Prefer the dialog's own close
            // button; fall back to Escape for builds that don't render one.
            const close = dialog.querySelector(
              '[data-testid="close-dialog"], button[aria-label="Close"], [aria-label="Close"]'
            );
            if (close) {
              try { close.click(); return; } catch (_) { }
            }
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
          });
      }, { capture: true });
    });

    // Belt-and-braces: if a download still slips through (e.g. a future
    // Langflow version wires Export to a plain <a download> anchor), swallow
    // the click on any dynamically-created `a[download][href^="blob:"]`
    // issued inside the export dialog.
    document.querySelectorAll('a[download][href^="blob:"]').forEach((a) => {
      if (a.dataset.wxBlockHooked === '1') return;
      if (!a.closest('[role="dialog"], [data-state="open"]')) return;
      a.dataset.wxBlockHooked = '1';
      a.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopImmediatePropagation();
      }, { capture: true });
    });
  }

  function forceDarkTheme() {
    const html = document.documentElement;
    if (!html.classList.contains('dark')) html.classList.add('dark');
    html.classList.remove('light');
    const keys = ['isDark', 'theme', 'langflow-theme', 'color-theme', 'system'];
    try {
      for (const k of keys) {
        if (k === 'system') localStorage.setItem(k, 'false');
        else localStorage.setItem(k, k === 'isDark' ? 'true' : 'dark');
      }
    } catch (_) { }
  }

  function hide(el, reason) {
    if (!el || el.dataset.wxHidden === '1') return;
    el.dataset.wxHidden = '1';
    el.dataset.wxReason = reason || '';
    el.style.setProperty('display', 'none', 'important');
  }

  /** Return elements whose trimmed textContent matches `rx`, bounded to
   *  short strings so we don't sweep up whole pages. */
  function byExactText(rx, maxLen = 80) {
    const out = [];
    const root = document.body || document.documentElement;
    if (!root) return out;
    const walker = document.createTreeWalker(
      root, NodeFilter.SHOW_ELEMENT,
      {
        acceptNode(node) {
          const tag = node.tagName;
          if (tag === 'SCRIPT' || tag === 'STYLE'
              || tag === 'SVG' || tag === 'PATH') {
            return NodeFilter.FILTER_REJECT;
          }
          const t = (node.textContent || '').trim();
          if (!t || t.length > maxLen) return NodeFilter.FILTER_SKIP;
          return rx.test(t) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
        },
      },
    );
    while (walker.nextNode()) {
      out.push(walker.currentNode);
      if (out.length > 12) break;
    }
    return out;
  }

  function hideWelcomeSplash() {
    // Headline. The icon card above it lives in the previous sibling chain;
    // we climb up to 3 siblings back looking for the one that contains an
    // inline SVG/image and hide that too.
    byExactText(/^Welcome to Langflow$/i).forEach((el) => {
      hide(el, 'welcome-headline');
      let prev = el.previousElementSibling;
      for (let i = 0; i < 3 && prev; i++) {
        if (prev.querySelector('svg, img')) {
          hide(prev, 'welcome-icon');
          break;
        }
        prev = prev.previousElementSibling;
      }
    });

    byExactText(/^Your new favorite way to ship Agents$/i)
      .forEach((el) => hide(el, 'welcome-subtitle'));

    // Drag-and-drop footer.
    byExactText(/^Already have a flow\? Drag and drop to upload\.?$/i, 100)
      .forEach((el) => hide(el, 'drag-drop-footer'));

    // GitHub + Discord welcome cards. Match by anchor href, then climb to
    // the enclosing card (height > 50, width 240..900) and hide that.
    document.querySelectorAll(
      'a[href*="github.com/langflow"], a[href*="discord.gg"], a[href*="discord.com/invite"]'
    ).forEach((a) => {
      if (a.dataset.wxHidden === '1') return;
      let node = a;
      for (let i = 0; i < 6 && node && node.parentElement; i++) {
        const r = node.parentElement.getBoundingClientRect();
        if (r.height > 50 && r.width > 240 && r.width < 900) {
          hide(node.parentElement, 'welcome-card');
          return;
        }
        node = node.parentElement;
      }
      hide(a, 'welcome-anchor');
    });
  }

  function hideDropdownRows() {
    // "Version X.Y.Z (latest)" row.
    byExactText(/^Version\b[\s\S]*\d/, 60).forEach((el) => {
      const row = el.closest('[role="menuitem"], li, [data-radix-collection-item]')
                  || el.parentElement;
      hide(row, 'dropdown-version');
    });
    // "Docs" row — short exact-match, must not catch "Documentation" etc.
    byExactText(/^Docs$/, 10).forEach((el) => {
      const row = el.closest('[role="menuitem"], li, [data-radix-collection-item]')
                  || el.parentElement;
      hide(row, 'dropdown-docs');
    });
  }

  function sweep() {
    try { forceDarkTheme(); } catch (_) { }
    try { hideWelcomeSplash(); } catch (_) { }
    try { hideDropdownRows(); } catch (_) { }
    try { hookExportButton(); } catch (_) { }
  }

  let pending = false;
  const mo = new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; sweep(); });
  });

  function start() {
    forceDarkTheme();                         // before first paint
    mo.observe(document.body || document.documentElement, {
      childList: true, subtree: true,
    });
    sweep();
    setTimeout(sweep, 250);
    setTimeout(sweep, 800);
    setTimeout(sweep, 2000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
