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
