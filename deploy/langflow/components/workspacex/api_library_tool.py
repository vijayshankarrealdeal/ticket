"""WorkspaceX API Library tool for Langflow.

Adds a sidebar component ("WorkspaceX → API Library") that lets the signed-in
operator pick a folder + saved endpoint from their API Playground and exposes
it as a LangChain `Tool` an Agent can consume.

Authentication is service-to-service, not per-user bearer. The component
presents `Authorization: Internal <WORKSPACEX_INTERNAL_KEY>` (shared between
the langflow and api containers via compose) plus `X-LF-User: <self.user_id>`.
The backend resolves that Langflow user id back to the matching WorkspaceX
user through `users.langflow_user_id`, which was populated during SSO — so
the user never has to paste an API key.

The file lives inside the custom Langflow image (see ../Dockerfile); Langflow
picks it up via `LANGFLOW_COMPONENTS_PATH=/app/custom_components`. The parent
directory name ("workspacex") becomes the category shown in the sidebar.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import httpx
from langchain_core.tools import StructuredTool

from langflow.custom import Component
from langflow.field_typing import Tool
from langflow.io import DropdownInput, Output, StrInput

log = logging.getLogger("workspacex.api_library")


def _debug(msg: str) -> None:
    """Mirror to stderr so `docker logs workspacex-langflow-1` shows the
    diagnostic breadcrumbs regardless of Langflow's logger config."""
    try:
        print(f"[wx api-library] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass
    log.info(msg)


DEFAULT_BACKEND = (
    os.environ.get("WORKSPACEX_API_URL")
    or os.environ.get("WORKSPACEX_BACKEND_URL")
    or "http://api:8000"
)
INTERNAL_KEY = os.environ.get("WORKSPACEX_INTERNAL_KEY") or ""

_ALL_FOLDERS = "(All folders)"
_NO_ENDPOINTS_PLACEHOLDER = "(no endpoints found — check API Playground)"


class WorkspaceXApiLibraryTool(Component):
    display_name = "API Library"
    description = (
        "Expose a saved WorkspaceX API Playground endpoint as a tool the "
        "agent can call when answering."
    )
    documentation = "https://workspacex.local/docs/api-library"
    icon = "Bolt"
    name = "WorkspaceXApiLibraryTool"

    inputs = [
        DropdownInput(
            name="folder",
            display_name="Folder",
            info="API Playground folder to scope the picker to.",
            options=[_ALL_FOLDERS],
            value=_ALL_FOLDERS,
            real_time_refresh=True,
            refresh_button=True,
        ),
        DropdownInput(
            name="endpoint",
            display_name="Endpoint",
            info="Saved API endpoint inside the folder.",
            options=[],
            real_time_refresh=True,
            refresh_button=True,
            required=True,
        ),
        StrInput(
            name="tool_name_override",
            display_name="Tool name override",
            info="Optional. Defaults to the endpoint's name.",
            advanced=True,
        ),
        StrInput(
            name="tool_description_override",
            display_name="Tool description override",
            info=(
                "Optional. Defaults to the endpoint's stored description, which "
                "is what the LLM reads when deciding whether to call this tool."
            ),
            advanced=True,
        ),
        StrInput(
            name="backend_url",
            display_name="Backend URL",
            value=DEFAULT_BACKEND,
            info=(
                "WorkspaceX API origin. Defaults to `http://api:8000` inside "
                "docker-compose. Only touch this if you moved the backend."
            ),
            advanced=True,
            real_time_refresh=True,
        ),
    ]

    outputs = [
        Output(display_name="Tool", name="tool", method="build_tool"),
    ]

    # ----- backend helpers -------------------------------------------------

    def _backend(self, build_config: dict | None = None) -> str:
        value: str | None = None
        if build_config and isinstance(build_config, dict):
            value = (build_config.get("backend_url") or {}).get("value")
        value = value or getattr(self, "backend_url", None) or DEFAULT_BACKEND
        return value.rstrip("/")

    def _lf_user_id(self) -> str | None:
        """Langflow versions differ on where the current user lives. We try,
        in order: the public property, the private attribute, nested vertex
        / graph references. First non-empty value wins."""
        for probe in (
            lambda: getattr(self, "user_id", None),
            lambda: getattr(self, "_user_id", None),
            lambda: getattr(getattr(self, "_vertex", None), "_user_id", None),
            lambda: getattr(getattr(self, "_vertex", None), "user_id", None),
            lambda: getattr(getattr(self, "graph", None), "user_id", None),
            lambda: getattr(getattr(self, "_graph", None), "user_id", None),
        ):
            try:
                v = probe()
            except Exception:
                v = None
            if v:
                return str(v)
        return None

    def _internal_headers(self) -> dict:
        return {
            "Authorization": f"Internal {INTERNAL_KEY}",
            "X-LF-User": self._lf_user_id() or "",
        }

    def _fetch_tools_payload(self, build_config: dict | None = None) -> dict:
        """Pull `{folders, tools}` in one request. On failure we stash a
        readable explanation in `self.status` so the operator sees why the
        dropdowns are empty right on the component card — no need to dig
        through container logs."""
        url = f"{self._backend(build_config)}/flow/api-library/tools"
        lf_uid = self._lf_user_id()
        _debug(f"fetch_tools url={url} lf_user_id={lf_uid!r} "
               f"internal_key_set={bool(INTERNAL_KEY)}")
        if not INTERNAL_KEY:
            self.status = ("WORKSPACEX_INTERNAL_KEY is not set inside the "
                           "Langflow container. Recreate it with compose.")
            return {"folders": [], "tools": []}
        if not lf_uid:
            self.status = ("Langflow user id unavailable to this component. "
                           "Re-open the flow from WorkspaceX so SSO sets it.")
            return {"folders": [], "tools": []}
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, headers=self._internal_headers())
            if resp.status_code >= 400:
                self.status = (f"Backend {resp.status_code} at {url}: "
                               f"{resp.text[:200]}")
                _debug(f"fetch_tools non-2xx: {resp.status_code} {resp.text[:200]}")
                return {"folders": [], "tools": []}
            data = resp.json() or {}
        except Exception as e:
            self.status = f"Could not reach backend at {url}: {e!r}"
            _debug(f"fetch_tools exception: {e!r}")
            return {"folders": [], "tools": []}
        folders = data.get("folders") or []
        tools = data.get("tools") or []
        self.status = (f"{len(folders)} folder(s), {len(tools)} endpoint(s) "
                       f"loaded for workspace {data.get('workspaceId')}.")
        return {"folders": folders, "tools": tools}

    # ----- dynamic dropdowns ----------------------------------------------

    def update_build_config(self, build_config, field_value, field_name=None):
        """Called by Langflow whenever a `real_time_refresh=True` input
        changes. Repopulates Folder + Endpoint options from the backend."""
        if field_name == "backend_url":
            self.backend_url = field_value

        payload = self._fetch_tools_payload(build_config if isinstance(build_config, dict) else None)
        folders = payload["folders"]
        tools = payload["tools"]

        folder_names = [_ALL_FOLDERS] + [f["name"] for f in folders if f.get("name")]
        current_folder = _ALL_FOLDERS
        if isinstance(build_config, dict) and "folder" in build_config:
            build_config["folder"]["options"] = folder_names
            stored = build_config["folder"].get("value")
            if field_name == "folder":
                stored = field_value
            if stored in folder_names:
                current_folder = stored
            build_config["folder"]["value"] = current_folder

        # Filter endpoints by folderId after resolving the chosen folder's id.
        folder_id = None
        if current_folder != _ALL_FOLDERS:
            folder_id = next(
                (f["id"] for f in folders if f.get("name") == current_folder),
                None,
            )
        filtered = [
            t for t in tools
            if not folder_id or t.get("folderId") == folder_id
        ]
        endpoint_names = [t["name"] for t in filtered if t.get("name")]

        if isinstance(build_config, dict) and "endpoint" in build_config:
            build_config["endpoint"]["options"] = endpoint_names or [_NO_ENDPOINTS_PLACEHOLDER]
            current_endpoint = build_config["endpoint"].get("value") or ""
            if current_endpoint not in endpoint_names:
                build_config["endpoint"]["value"] = endpoint_names[0] if endpoint_names else ""

        return build_config

    # ----- tool build ------------------------------------------------------

    def build_tool(self) -> Tool:
        endpoint_name = (self.endpoint or "").strip()
        if not endpoint_name or endpoint_name == _NO_ENDPOINTS_PLACEHOLDER:
            raise ValueError("Pick a saved endpoint before using this tool.")

        display_name = (self.tool_name_override or "").strip() or endpoint_name
        tool_name = _sanitize_tool_name(display_name)

        description = (self.tool_description_override or "").strip()
        if not description:
            payload = self._fetch_tools_payload()
            match = next(
                (t for t in payload["tools"] if t.get("name") == endpoint_name),
                None,
            )
            description = (match or {}).get("description") or ""
        if not description:
            description = (
                f"Calls the WorkspaceX API Library endpoint '{endpoint_name}'. "
                "Pass template variables as JSON keys."
            )

        backend = self._backend()
        headers = self._internal_headers()

        def _invoke(**kwargs: Any) -> str:
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(
                        f"{backend}/flow/api-library/invoke/{endpoint_name}",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"args": _coerce_args(kwargs)},
                    )
                ctype = (resp.headers.get("content-type") or "").lower()
                if "json" in ctype:
                    try:
                        return json.dumps(resp.json(), default=str)[:16000]
                    except Exception:
                        pass
                return resp.text[:16000]
            except Exception as e:
                return json.dumps({"ok": False, "error": repr(e)[:500]})

        return StructuredTool.from_function(
            func=_invoke,
            name=tool_name,
            description=description[:1024],
        )


# ----- helpers -------------------------------------------------------------


def _sanitize_tool_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    cleaned = cleaned.strip("_") or "workspacex_api"
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned[:64]


def _coerce_args(kwargs: dict) -> dict:
    """The invoke route does `{{token}}` substitution on strings, so every
    arg value needs to be JSON-encodable as a scalar. Dicts/lists survive as
    JSON strings — the backend side can `json.loads` if it wants structure."""
    out: dict[str, Any] = {}
    for k, v in (kwargs or {}).items():
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(v)
        elif v is None:
            continue
        else:
            out[k] = v
    return out
