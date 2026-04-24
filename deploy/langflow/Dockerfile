# Custom Langflow image — extends upstream + overlays a rebrand injection
# script into the served index.html. Keeps us on the official Python wheels
# (fast rebuilds, CVE patches free) while letting us strip GitHub/Discord
# chips, the Langflow logo, the welcome splash, and substitute "Version X"
# in the profile dropdown with the logged-in user's email + name.
#
# The rebrand assets live in `/app/.venv/lib/python3.12/site-packages/langflow/frontend/`
# which is where upstream's static files get baked. We sed-inject a
# `<script src="/rebrand/wx.js"></script>` tag into index.html so the file
# loads on every SPA route. No Langflow code changes, no fork.

FROM langflowai/langflow:latest

USER root

# Copy the rebrand bundle into Langflow's static frontend directory so it's
# served under the same origin as the rest of the app.
RUN mkdir -p /app/.venv/lib/python3.12/site-packages/langflow/frontend/rebrand
COPY wx-rebrand.css /app/.venv/lib/python3.12/site-packages/langflow/frontend/rebrand/wx.css
COPY wx-rebrand.js  /app/.venv/lib/python3.12/site-packages/langflow/frontend/rebrand/wx.js

# Splice the <link>/<script> references just before </head>. The `sed -i`
# guard keeps this idempotent — re-running doesn't duplicate the inject.
RUN set -eux; \
    INDEX=/app/.venv/lib/python3.12/site-packages/langflow/frontend/index.html; \
    if ! grep -q 'wx-rebrand' "$INDEX"; then \
      sed -i 's#</head>#<link data-wx-rebrand rel="stylesheet" href="/rebrand/wx.css"><script data-wx-rebrand defer src="/rebrand/wx.js"></script></head>#' "$INDEX"; \
    fi; \
    # Sanity-check the inject landed.
    grep -c 'wx-rebrand' "$INDEX"

# Install our custom components into /app/custom_components and advertise
# that path via LANGFLOW_COMPONENTS_PATH. The parent folder name under
# custom_components becomes the sidebar category, so `workspacex/` shows up
# as its own group (alongside OpenAI, LangChain, etc.).
COPY components /app/custom_components
ENV LANGFLOW_COMPONENTS_PATH=/app/custom_components

# Langflow runs as root by default; keep that so the image starts cleanly.
# (The upstream entrypoint handles dropping privileges if configured.)
