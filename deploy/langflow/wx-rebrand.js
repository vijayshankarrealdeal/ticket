/* WorkspaceX — Langflow rebrand runtime (minimal).
 *
 * Langflow is now embedded in an iframe inside the WorkspaceX AgentBuilder
 * shell — the parent page already supplies the user icon, "All flows"
 * breadcrumb, and "New flow" CTA, so hiding Langflow's own chrome would
 * leave users with no node library, no save/publish controls, and no flow
 * settings. We keep this script tiny:
 *
 *   1. Force Langflow's dark theme so the editor's palette matches the
 *      WorkspaceX shell around it.
 *
 * Everything else (avatar swap, app-bar pruning, welcome-splash hiding,
 * dropdown row swaps) was removed — it either duplicates chrome we now own
 * outside the iframe, or was over-hiding and leaving the editor unusable.
 */
(function () {
  'use strict';

  function forceDarkTheme() {
    const html = document.documentElement;
    if (!html.classList.contains('dark')) html.classList.add('dark');
    html.classList.remove('light');
    // Langflow stores the user's theme choice in a handful of places
    // depending on version. Setting all common keys is cheap and covers
    // 1.x + 2.x without further upstream-sniffing.
    const keys = ['isDark', 'theme', 'langflow-theme', 'color-theme', 'system'];
    try {
      for (const k of keys) {
        if (k === 'system') localStorage.setItem(k, 'false');
        else localStorage.setItem(k, k === 'isDark' ? 'true' : 'dark');
      }
    } catch (_) { }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', forceDarkTheme);
  } else {
    forceDarkTheme();
  }
})();
