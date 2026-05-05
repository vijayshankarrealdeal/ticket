import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SimpleTreeView } from '@mui/x-tree-view/SimpleTreeView';
import { TreeItem } from '@mui/x-tree-view/TreeItem';
import { Icon } from '../../components/Icons.jsx';
import TopBar from '../../components/TopBar.jsx';
import { api as apiClient } from '../../api.js';
import './channels.css';

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
const HISTORY_KEY = 'workspacex.apiPlayground.history';
const ACTIVE_ENV_KEY = 'workspacex.apiPlayground.activeEnv';
const HISTORY_LIMIT = 20;

const EMPTY_REQ = {
  endpointId: null,
  folderId: null,
  name: '',
  description: '',
  method: 'GET',
  url: '',
  headers: [{ key: '', value: '' }],
  body: '',
};

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, HISTORY_LIMIT) : [];
  } catch { return []; }
}
function saveHistory(entries) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_LIMIT))); } catch {}
}

export default function ApiPlayground() {
  const [req, setReq] = useState(EMPTY_REQ);
  const [tab, setTab] = useState('body');
  const [responseTab, setResponseTab] = useState('body');
  const [responseFilter, setResponseFilter] = useState('filtered'); // 'filtered' | 'raw'
  const [result, setResult] = useState(null);
  const [sending, setSending] = useState(false);
  const [history, setHistory] = useState(() => loadHistory());
  const [unresolvedVars, setUnresolvedVars] = useState([]);
  const abortRef = useRef(null);

  const [folders, setFolders] = useState([]);
  const [endpoints, setEndpoints] = useState([]);
  const [environments, setEnvironments] = useState([]);
  const [activeEnvId, setActiveEnvId] = useState(() => localStorage.getItem(ACTIVE_ENV_KEY) || '');
  const [sideTab, setSideTab] = useState('collections');

  // Inline UI state (no native prompts)
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renamingFolderId, setRenamingFolderId] = useState(null);
  const [creatingEnv, setCreatingEnv] = useState(false);
  const [savePickerOpen, setSavePickerOpen] = useState(false);
  const [schemaDialogOpen, setSchemaDialogOpen] = useState(false);
  const [toast, setToast] = useState(null);
  const [expandedFolders, setExpandedFolders] = useState([]);
  const [descOpen, setDescOpen] = useState(false);
  const [curlOpen, setCurlOpen] = useState(false);
  const [curlText, setCurlText] = useState('');
  const [curlError, setCurlError] = useState('');

  useEffect(() => {
    if (activeEnvId) localStorage.setItem(ACTIVE_ENV_KEY, activeEnvId);
  }, [activeEnvId]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  const activeEnv = useMemo(
    () => environments.find((e) => e.id === activeEnvId) || null,
    [environments, activeEnvId],
  );

  const refreshCollections = useCallback(async () => {
    const [f, e] = await Promise.all([
      apiClient.get('/api-playground/folders').catch(() => []),
      apiClient.get('/api-playground/endpoints').catch(() => []),
    ]);
    setFolders(Array.isArray(f) ? f : []);
    setEndpoints(Array.isArray(e) ? e : []);
  }, []);

  const refreshEnvironments = useCallback(async () => {
    const envs = await apiClient.get('/api-playground/environments').catch(() => []);
    setEnvironments(Array.isArray(envs) ? envs : []);
  }, []);

  useEffect(() => { refreshCollections(); refreshEnvironments(); }, [refreshCollections, refreshEnvironments]);

  useEffect(() => {
    if (environments.length === 0) return;
    if (!environments.find((e) => e.id === activeEnvId)) {
      setActiveEnvId(environments[0].id);
    }
  }, [environments, activeEnvId]);

  // --- variable substitution ----------------------------------------------
  const varMap = useMemo(() => {
    const m = new Map();
    (activeEnv?.variables || []).forEach((v) => { m.set(v.key, v.value ?? ''); });
    return m;
  }, [activeEnv]);

  const substitute = useCallback((text) => {
    if (!text) return { out: text || '', missing: [] };
    const missing = new Set();
    const out = String(text).replace(/\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}/g, (_m, key) => {
      if (varMap.has(key)) return varMap.get(key);
      missing.add(key);
      return `{{${key}}}`;
    });
    return { out, missing: Array.from(missing) };
  }, [varMap]);

  useEffect(() => {
    const all = new Set();
    const fields = [req.url, req.body, ...req.headers.flatMap((h) => [h.key, h.value])];
    for (const f of fields) {
      const { missing } = substitute(f);
      missing.forEach((k) => all.add(k));
    }
    setUnresolvedVars(Array.from(all));
  }, [req, substitute]);

  // --- helpers -------------------------------------------------------------
  const setField = (k, v) => setReq((r) => ({ ...r, [k]: v }));
  const setHeader = (idx, patch) =>
    setReq((r) => ({ ...r, headers: r.headers.map((h, i) => (i === idx ? { ...h, ...patch } : h)) }));
  const addHeader = () =>
    setReq((r) => ({ ...r, headers: [...r.headers, { key: '', value: '' }] }));
  const removeHeader = (idx) =>
    setReq((r) => {
      const next = r.headers.filter((_, i) => i !== idx);
      return { ...r, headers: next.length ? next : [{ key: '', value: '' }] };
    });

  const buildHeadersObject = () => {
    const out = {};
    for (const h of req.headers) {
      const { out: k } = substitute((h.key || '').trim());
      if (!k) continue;
      out[k] = substitute(h.value ?? '').out;
    }
    return out;
  };

  const canSend = useMemo(() => !sending && !!req.url.trim(), [sending, req.url]);

  // --- send ----------------------------------------------------------------
  async function send() {
    if (!canSend) return;
    setSending(true);
    setResult(null);
    const started = performance.now();

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const resolvedUrl = substitute(req.url.trim()).out;
      if (!/^https?:\/\//i.test(resolvedUrl)) {
        throw new Error('URL must start with http:// or https://');
      }
      let parsedBody = null;
      if (!['GET', 'HEAD'].includes(req.method) && req.body && req.body.trim()) {
        const rawBody = substitute(req.body).out;
        try { parsedBody = JSON.parse(rawBody); } catch { parsedBody = rawBody; }
      }
      const res = await apiClient.post('/api-playground/send', {
        method: req.method,
        url: resolvedUrl,
        headers: buildHeadersObject(),
        body: parsedBody,
      }, { signal: controller.signal });

      const status = typeof res?.status === 'number' ? res.status : 0;
      const durationMs = typeof res?.latencyMs === 'number'
        ? res.latencyMs
        : Math.round(performance.now() - started);
      const final = {
        ok: !!res?.ok,
        status,
        durationMs,
        headers: res?.headers || {},
        body: res?.body,
        traceId: res?.traceId || null,
        error: res?.error || null,
      };
      setResult(final);

      pushHistory({
        id: crypto.randomUUID(),
        at: new Date().toISOString(),
        method: req.method,
        url: req.url,
        status,
        durationMs,
      });

      if (req.endpointId && final.ok && final.body !== undefined) {
        apiClient
          .post(`/api-playground/endpoints/${encodeURIComponent(req.endpointId)}/infer-schema`, {
            sample: final.body,
            status,
          })
          .then((updated) => {
            if (updated?.id) {
              setEndpoints((prev) => prev.map((e) => (e.id === updated.id ? updated : e)));
            }
          })
          .catch(() => {});
      }
    } catch (e) {
      const ms = Math.round(performance.now() - started);
      setResult({ ok: false, error: e.message || String(e), durationMs: ms });
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }

  function cancel() { if (abortRef.current) abortRef.current.abort(); }

  function pushHistory(entry) {
    setHistory((prev) => {
      const next = [entry, ...prev].slice(0, HISTORY_LIMIT);
      saveHistory(next);
      return next;
    });
  }
  function clearHistory() { saveHistory([]); setHistory([]); }
  function replay(entry) {
    setReq((r) => ({
      ...EMPTY_REQ,
      method: entry.method || 'GET',
      url: entry.url || '',
      headers: r.headers,
    }));
  }

  // --- endpoints -----------------------------------------------------------
  const loadEndpoint = (ep) => {
    const headers = Array.isArray(ep.headers) && ep.headers.length
      ? ep.headers
      : [{ key: '', value: '' }];
    setReq({
      endpointId: ep.id,
      folderId: ep.folderId || null,
      name: ep.name,
      description: ep.description || '',
      method: ep.method,
      url: ep.url,
      headers,
      body: ep.body || '',
    });
    setResult(null);
    if (ep.description) setDescOpen(true);
  };

  async function saveCurrentAsEndpoint(folderId) {
    const desc = (req.description || '').trim();
    if (!desc) {
      setToast({ tone: 'danger', msg: 'Add a description before saving — the LLM needs it to know when to call this tool.' });
      setTab('body'); // keep user in main editor where description field lives
      return;
    }
    try {
      const payload = {
        folderId: folderId || req.folderId || null,
        name: req.name?.trim() || suggestName(req),
        description: desc,
        method: req.method,
        url: req.url,
        headers: req.headers,
        body: req.body,
      };
      const created = await apiClient.post('/api-playground/endpoints', payload);
      setEndpoints((prev) => [...prev, created]);
      setReq((r) => ({
        ...r,
        endpointId: created.id,
        folderId: created.folderId,
        name: created.name,
        description: created.description || '',
      }));
      if (folderId) {
        setExpandedFolders((prev) => prev.includes('f-' + folderId) ? prev : [...prev, 'f-' + folderId]);
      }
      setToast({ tone: 'success', msg: 'Saved' });
    } catch (e) {
      setToast({ tone: 'danger', msg: e.message || String(e) });
    }
  }

  async function updateCurrentEndpoint() {
    if (!req.endpointId) return;
    const desc = (req.description || '').trim();
    if (!desc) {
      setToast({ tone: 'danger', msg: 'Description can\'t be empty — it\'s what the LLM sees.' });
      return;
    }
    try {
      const updated = await apiClient.patch(`/api-playground/endpoints/${req.endpointId}`, {
        name: req.name || undefined,
        description: desc,
        method: req.method,
        url: req.url,
        headers: req.headers,
        body: req.body,
        folderId: req.folderId,
      });
      setEndpoints((prev) => prev.map((e) => (e.id === updated.id ? updated : e)));
      setToast({ tone: 'success', msg: 'Saved' });
    } catch (e) {
      setToast({ tone: 'danger', msg: e.message || String(e) });
    }
  }

  async function deleteEndpoint(id) {
    await apiClient.del(`/api-playground/endpoints/${id}`);
    setEndpoints((prev) => prev.filter((e) => e.id !== id));
    if (req.endpointId === id) setReq(EMPTY_REQ);
  }

  // --- folders -------------------------------------------------------------
  async function createFolderInline(name) {
    const trimmed = (name || '').trim();
    if (!trimmed) { setCreatingFolder(false); return; }
    const created = await apiClient.post('/api-playground/folders', { name: trimmed });
    setFolders((prev) => [...prev, created]);
    setExpandedFolders((prev) => [...prev, 'f-' + created.id]);
    setCreatingFolder(false);
  }

  async function renameFolderInline(id, name) {
    const trimmed = (name || '').trim();
    setRenamingFolderId(null);
    if (!trimmed) return;
    const updated = await apiClient.patch(`/api-playground/folders/${id}`, { name: trimmed });
    setFolders((prev) => prev.map((f) => (f.id === id ? updated : f)));
  }

  async function deleteFolder(id) {
    await apiClient.del(`/api-playground/folders/${id}`);
    setFolders((prev) => prev.filter((f) => f.id !== id));
    setEndpoints((prev) => prev.filter((e) => e.folderId !== id));
    if (req.folderId === id) setReq(EMPTY_REQ);
  }

  // --- environments --------------------------------------------------------
  async function createEnvInline(name) {
    const trimmed = (name || '').trim();
    if (!trimmed) { setCreatingEnv(false); return; }
    const created = await apiClient.post('/api-playground/environments', { name: trimmed });
    setEnvironments((prev) => [...prev, created]);
    setActiveEnvId(created.id);
    setCreatingEnv(false);
  }

  async function deleteEnv(id) {
    await apiClient.del(`/api-playground/environments/${id}`);
    setEnvironments((prev) => prev.filter((e) => e.id !== id));
    if (activeEnvId === id) setActiveEnvId('');
  }

  async function addVariable(key, value) {
    if (!activeEnvId) return;
    const k = (key || '').trim();
    if (!k) return;
    try {
      const v = await apiClient.post(
        `/api-playground/environments/${activeEnvId}/variables`,
        { key: k, value: value || '' },
      );
      setEnvironments((prev) => prev.map((e) => e.id === activeEnvId
        ? { ...e, variables: [...(e.variables || []), v] }
        : e));
    } catch (e) {
      setToast({ tone: 'danger', msg: `Could not add: ${e.message || e}` });
    }
  }

  async function updateVariable(variableId, patch) {
    const v = await apiClient.patch(`/api-playground/variables/${variableId}`, patch);
    setEnvironments((prev) => prev.map((e) => e.id === activeEnvId
      ? { ...e, variables: (e.variables || []).map((x) => x.id === variableId ? v : x) }
      : e));
  }

  async function deleteVariable(variableId) {
    await apiClient.del(`/api-playground/variables/${variableId}`);
    setEnvironments((prev) => prev.map((e) => e.id === activeEnvId
      ? { ...e, variables: (e.variables || []).filter((x) => x.id !== variableId) }
      : e));
  }

  // --- derived -------------------------------------------------------------
  const endpointsByFolder = useMemo(() => {
    const m = new Map();
    for (const e of endpoints) {
      const k = e.folderId || '__unfiled__';
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(e);
    }
    return m;
  }, [endpoints]);

  const activeEndpoint = useMemo(
    () => endpoints.find((e) => e.id === req.endpointId) || null,
    [endpoints, req.endpointId],
  );

  // --- render --------------------------------------------------------------
  return (
    <>
      <TopBar left={<div className="top-bar-title">API Playground</div>} />
      <div className="pg-layout">
        <aside className="pg-side pg-side-left">
          <div className="pg-side-tabs">
            <button
              className={'pg-side-tab' + (sideTab === 'collections' ? ' active' : '')}
              onClick={() => setSideTab('collections')}
            >
              <Icon.Book /> Collections
            </button>
            <button
              className={'pg-side-tab' + (sideTab === 'environments' ? ' active' : '')}
              onClick={() => setSideTab('environments')}
            >
              <Icon.Bolt /> Environments
            </button>
          </div>

          {sideTab === 'collections' ? (
            <div className="pg-side-body">
              <div className="pg-side-toolbar">
                <span className="pg-side-label">Collections</span>
                <div className="pg-side-toolbar-actions">
                  <button
                    className="pg-icon-btn"
                    title="New folder"
                    onClick={() => setCreatingFolder(true)}
                  >
                    <Icon.Plus />
                  </button>
                  <button
                    className="pg-icon-btn"
                    title="Refresh"
                    onClick={() => refreshCollections()}
                  >
                    <RefreshIcon />
                  </button>
                  <button
                    className="pg-icon-btn"
                    title="Collapse all"
                    onClick={() => setExpandedFolders([])}
                    disabled={expandedFolders.length === 0}
                  >
                    <CollapseIcon />
                  </button>
                </div>
              </div>

              {creatingFolder && (
                <InlineInput
                  placeholder="Folder name"
                  onSubmit={createFolderInline}
                  onCancel={() => setCreatingFolder(false)}
                  autoFocus
                />
              )}

              {folders.length === 0 && !creatingFolder && (
                <div className="pg-side-empty">
                  <Icon.Book />
                  <div className="pg-side-empty-title">No folders yet</div>
                  <div className="pg-side-empty-sub">Create one to group related requests.</div>
                </div>
              )}

              {folders.length > 0 && (
                <SimpleTreeView
                  className="pg-mui-tree"
                  expandedItems={expandedFolders}
                  onExpandedItemsChange={(_e, items) => setExpandedFolders(items)}
                  selectedItems={req.endpointId ? 'e-' + req.endpointId : null}
                  slots={{
                    expandIcon: ChevronRight,
                    collapseIcon: ChevronDown,
                  }}
                >
                  {folders.map((f) => {
                    const eps = endpointsByFolder.get(f.id) || [];
                    const isRenaming = renamingFolderId === f.id;
                    return (
                      <TreeItem
                        key={f.id}
                        itemId={'f-' + f.id}
                        label={
                          isRenaming ? (
                            <InlineInput
                              initial={f.name}
                              onSubmit={(n) => renameFolderInline(f.id, n)}
                              onCancel={() => setRenamingFolderId(null)}
                              autoFocus
                            />
                          ) : (
                            <div className="pg-tree-folder">
                              <span className="pg-tree-folder-name">{f.name}</span>
                              <span className="pg-tree-count">{eps.length}</span>
                              <span className="pg-tree-actions">
                                <span
                                  role="button"
                                  className="pg-icon-btn"
                                  title="Save current as new request"
                                  onClick={(e) => { e.stopPropagation(); saveCurrentAsEndpoint(f.id); }}
                                >
                                  <Icon.Plus />
                                </span>
                                <span
                                  role="button"
                                  className="pg-icon-btn"
                                  title="Rename folder"
                                  onClick={(e) => { e.stopPropagation(); setRenamingFolderId(f.id); }}
                                >
                                  <Icon.Pencil />
                                </span>
                                <ConfirmButton
                                  title="Delete folder"
                                  onConfirm={() => deleteFolder(f.id)}
                                />
                              </span>
                            </div>
                          )
                        }
                      >
                        {eps.length === 0 ? (
                          <TreeItem
                            itemId={'f-' + f.id + '-empty'}
                            disabled
                            label={<span className="pg-hint pg-tree-empty">Empty</span>}
                          />
                        ) : (
                          eps.map((ep) => (
                            <TreeItem
                              key={ep.id}
                              itemId={'e-' + ep.id}
                              onClick={() => loadEndpoint(ep)}
                              label={
                                <div className="pg-tree-endpoint">
                                  <span className={'ch-method ch-method-' + (ep.method || '').toLowerCase()}>
                                    {ep.method}
                                  </span>
                                  <span className="pg-tree-endpoint-name">{ep.name}</span>
                                  {ep.outputSchema && (
                                    <span className="pg-endpoint-badge" title="Has output schema">
                                      schema
                                    </span>
                                  )}
                                  {!ep.description && (
                                    <span
                                      className="pg-endpoint-badge pg-endpoint-warn"
                                      title="Missing description — add one so the LLM knows when to call it"
                                    >
                                      needs desc
                                    </span>
                                  )}
                                  <span className="pg-tree-actions">
                                    <ConfirmButton
                                      title="Delete request"
                                      onConfirm={() => deleteEndpoint(ep.id)}
                                    />
                                  </span>
                                </div>
                              }
                            />
                          ))
                        )}
                      </TreeItem>
                    );
                  })}

                  {(endpointsByFolder.get('__unfiled__') || []).length > 0 && (
                    <TreeItem
                      itemId="f-unfiled"
                      label={<div className="pg-tree-folder"><span className="pg-tree-folder-name">Unfiled</span></div>}
                    >
                      {(endpointsByFolder.get('__unfiled__') || []).map((ep) => (
                        <TreeItem
                          key={ep.id}
                          itemId={'e-' + ep.id}
                          onClick={() => loadEndpoint(ep)}
                          label={
                            <div className="pg-tree-endpoint">
                              <span className={'ch-method ch-method-' + (ep.method || '').toLowerCase()}>
                                {ep.method}
                              </span>
                              <span className="pg-tree-endpoint-name">{ep.name}</span>
                              {ep.outputSchema && (
                                <span className="pg-endpoint-badge" title="Has output schema">
                                  schema
                                </span>
                              )}
                              <span className="pg-tree-actions">
                                <ConfirmButton
                                  title="Delete request"
                                  onConfirm={() => deleteEndpoint(ep.id)}
                                />
                              </span>
                            </div>
                          }
                        />
                      ))}
                    </TreeItem>
                  )}
                </SimpleTreeView>
              )}
            </div>
          ) : (
            <div className="pg-side-body">
              <div className="pg-side-toolbar">
                <span className="pg-side-label">Environments</span>
                <div className="pg-side-toolbar-actions">
                  <EnvHelpButton />
                  <button
                    className="pg-icon-btn"
                    title="New environment"
                    onClick={() => setCreatingEnv(true)}
                  >
                    <Icon.Plus />
                  </button>
                  <button
                    className="pg-icon-btn"
                    title="Refresh"
                    onClick={() => refreshEnvironments()}
                  >
                    <RefreshIcon />
                  </button>
                </div>
              </div>

              {environments.length > 0 && (
                <div className="pg-side-row">
                  <label className="ch-dropdown grow">
                    <span className="ch-dropdown-label">Active env</span>
                    <select
                      value={activeEnvId || ''}
                      onChange={(e) => setActiveEnvId(e.target.value)}
                    >
                      <option value="">None</option>
                      {environments.map((e) => (
                        <option key={e.id} value={e.id}>{e.name}</option>
                      ))}
                    </select>
                    <Icon.Chevron className="ch-dropdown-caret" />
                  </label>
                </div>
              )}

              {creatingEnv && (
                <InlineInput
                  placeholder="Environment name"
                  onSubmit={createEnvInline}
                  onCancel={() => setCreatingEnv(false)}
                  autoFocus
                />
              )}

              {environments.length === 0 && !creatingEnv && (
                <div className="pg-side-empty">
                  <Icon.Bolt />
                  <div className="pg-side-empty-title">No environments yet</div>
                  <div className="pg-side-empty-sub">Create one to store {'{{variables}}'} used in requests.</div>
                </div>
              )}

              {activeEnv ? (
                <>
                  <div className="pg-env-head">
                    <div className="pg-env-name">{activeEnv.name}</div>
                    <ConfirmButton
                      title="Delete environment"
                      onConfirm={() => deleteEnv(activeEnv.id)}
                    />
                  </div>
                  <EnvVariablesEditor
                    key={activeEnv.id}
                    env={activeEnv}
                    onSave={async (ops) => {
                      for (const op of ops.creates) {
                        await addVariable(op.key, op.value);
                      }
                      for (const op of ops.updates) {
                        await updateVariable(op.id, { key: op.key, value: op.value });
                      }
                      for (const id of ops.deletes) {
                        await deleteVariable(id);
                      }
                      setToast({ tone: 'success', msg: 'Saved' });
                    }}
                  />
                </>
              ) : (
                !creatingEnv && environments.length > 0 && (
                  <div className="pg-hint">Pick an environment above.</div>
                )
              )}
            </div>
          )}
        </aside>

        <div className="pg-main">
          <div className="pg-header">
            <div className="pg-header-titles">
              <input
                className="pg-name-input"
                value={req.name}
                onChange={(e) => setField('name', e.target.value)}
                placeholder="Untitled request"
              />
              {activeEndpoint?.folderId && (
                <div className="pg-breadcrumb">
                  {folders.find((f) => f.id === activeEndpoint.folderId)?.name || 'Folder'} · saved
                </div>
              )}
            </div>
            <div className="pg-header-actions">
              {req.endpointId ? (
                <button
                  className="btn secondary"
                  onClick={() => {
                    if (!req.description.trim()) {
                      setDescOpen(true);
                      setToast({ tone: 'danger', msg: 'Add a tool description before saving.' });
                      return;
                    }
                    updateCurrentEndpoint();
                  }}
                  title="Save changes"
                >
                  <Icon.Check /> Save
                </button>
              ) : (
                <div className="pg-save-menu">
                  <button
                    className="btn secondary"
                    onClick={() => {
                      if (!req.description.trim()) {
                        setDescOpen(true);
                        setToast({ tone: 'danger', msg: 'Add a tool description before saving.' });
                        return;
                      }
                      setSavePickerOpen((v) => !v);
                    }}
                    title="Save to folder"
                  >
                    <Icon.Check /> Save…
                  </button>
                  {savePickerOpen && (
                    <SavePicker
                      folders={folders}
                      onPick={(fid) => { setSavePickerOpen(false); saveCurrentAsEndpoint(fid); }}
                      onClose={() => setSavePickerOpen(false)}
                    />
                  )}
                </div>
              )}
              {sending ? (
                <button className="btn secondary" onClick={cancel}>Cancel</button>
              ) : (
                <button className="btn" onClick={send} disabled={!canSend}>
                  <Icon.Play /> Send
                </button>
              )}
            </div>
          </div>

          <div className="pg-url-row">
            <select
              className="pg-method"
              value={req.method}
              onChange={(e) => setField('method', e.target.value)}
            >
              {METHODS.map((m) => <option key={m}>{m}</option>)}
            </select>
            <input
              className="pg-url"
              value={req.url}
              onChange={(e) => setField('url', e.target.value)}
              placeholder="https://api.example.com/v1/resource  (use {{var}} from env)"
              onKeyDown={(e) => { if (e.key === 'Enter' && canSend) send(); }}
            />
          </div>

          {unresolvedVars.length > 0 && (
            <div className="pg-warning">
              <Icon.AlertTriangle />
              <span>
                Unresolved variables: {unresolvedVars.map((v) => `{{${v}}}`).join(', ')}
                {activeEnv
                  ? ` — add them to "${activeEnv.name}".`
                  : ' — pick an environment on the left.'}
              </span>
            </div>
          )}

          <div className="pg-toolbar">
            <button
              type="button"
              className={'pg-toolbar-chip' + (curlOpen ? ' active' : '')}
              onClick={() => { setCurlOpen((v) => !v); setCurlError(''); }}
              title="Paste a curl command and we'll fill in the request"
            >
              <Icon.Code /> Import curl
            </button>
            <button
              type="button"
              className={'pg-toolbar-chip' + (descOpen || req.description ? ' active' : '')}
              onClick={() => setDescOpen((v) => !v)}
            >
              {req.description.trim() ? <Icon.Check /> : <Icon.Info />}
              Tool description
              {!req.description.trim() && (
                <span className="pg-toolbar-chip-hint">optional · needed to save as tool</span>
              )}
            </button>
            {!req.endpointId && !req.url && !req.body && (
              <button
                type="button"
                className="pg-toolbar-chip subtle"
                onClick={() => setReq((r) => ({ ...r, ...GEMINI_EXAMPLE }))}
              >
                <Icon.Bolt /> Try a sample request
              </button>
            )}
          </div>

          {curlOpen && (
            <div className="pg-curl-import">
              <textarea
                value={curlText}
                onChange={(e) => { setCurlText(e.target.value); setCurlError(''); }}
                placeholder={'curl -X POST https://api.example.com/v1/foo \\\n  -H "Authorization: Bearer XYZ" \\\n  -d \'{"hello":"world"}\''}
                rows={5}
                spellCheck={false}
              />
              {curlError && <div className="pg-curl-error"><Icon.AlertTriangle /> {curlError}</div>}
              <div className="pg-curl-actions">
                <span className="pg-hint">We&rsquo;ll fill the URL, headers and body for you.</span>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    type="button"
                    className="btn secondary"
                    onClick={() => { setCurlOpen(false); setCurlText(''); setCurlError(''); }}
                  >Cancel</button>
                  <button
                    type="button"
                    className="btn"
                    disabled={!curlText.trim()}
                    onClick={() => {
                      const parsed = parseCurl(curlText);
                      if (!parsed) {
                        setCurlError('Could not parse — make sure it starts with `curl`.');
                        return;
                      }
                      setReq((r) => ({
                        ...r,
                        method: parsed.method || r.method,
                        url: parsed.url || r.url,
                        headers: parsed.headers.length ? parsed.headers : r.headers,
                        body: parsed.body || r.body,
                      }));
                      setCurlOpen(false);
                      setCurlText('');
                      setToast({ tone: 'success', msg: 'Imported from curl' });
                    }}
                  >Import</button>
                </div>
              </div>
            </div>
          )}

          {(descOpen || req.description) && (
            <div className="pg-desc-panel">
              <div className="pg-desc-panel-head">
                <span className="pg-desc-panel-title">Tool description</span>
                <span className="pg-desc-panel-sub">
                  Shown to LLMs so they know when to call this tool. Required only when saving.
                </span>
              </div>
              <textarea
                className="pg-desc-input"
                value={req.description}
                onChange={(e) => setField('description', e.target.value)}
                placeholder="What does this API do? When should the agent call it? What arguments matter?"
                rows={3}
                spellCheck={false}
              />
            </div>
          )}

          <div className="ch-segment pg-sub-seg">
            <button
              className={'ch-segment-btn' + (tab === 'body' ? ' active' : '')}
              onClick={() => setTab('body')}
            >Body</button>
            <button
              className={'ch-segment-btn' + (tab === 'headers' ? ' active' : '')}
              onClick={() => setTab('headers')}
            >
              Headers
              <span className="ch-segment-count">
                {req.headers.filter((h) => h.key.trim()).length}
              </span>
            </button>
            <button
              className={'ch-segment-btn' + (tab === 'schema' ? ' active' : '')}
              onClick={() => setTab('schema')}
            >
              Schema
              {activeEndpoint?.outputSchema ? <span className="ch-segment-count">✓</span> : null}
            </button>
          </div>

          <div className="pg-tab-panel">
            {tab === 'body' && (
              <textarea
                className="pg-editor"
                value={req.body}
                onChange={(e) => setField('body', e.target.value)}
                placeholder={
                  ['GET', 'HEAD'].includes(req.method)
                    ? 'Body is ignored for GET/HEAD requests.'
                    : '{\n  "key": "value"\n}'
                }
                spellCheck={false}
              />
            )}
            {tab === 'headers' && (
              <div className="pg-headers">
                {req.headers.map((h, i) => (
                  <div className="pg-header-row" key={i}>
                    <input
                      placeholder="Header"
                      value={h.key}
                      onChange={(e) => setHeader(i, { key: e.target.value })}
                    />
                    <input
                      placeholder="Value (use {{var}})"
                      value={h.value}
                      onChange={(e) => setHeader(i, { value: e.target.value })}
                    />
                    <button
                      className="pg-icon-btn danger"
                      onClick={() => removeHeader(i)}
                      title="Remove header"
                    >
                      <Icon.Close />
                    </button>
                  </div>
                ))}
                <button className="btn secondary pg-add-btn" onClick={addHeader}>
                  <Icon.Plus /> Add header
                </button>
              </div>
            )}
            {tab === 'schema' && (
              <SchemaTabSummary
                endpoint={activeEndpoint}
                lastResult={result}
                onOpenEditor={() => setSchemaDialogOpen(true)}
              />
            )}
          </div>

          <div className="pg-response">
            <div className="pg-response-head">
              <div className="pg-response-title">Response</div>
              {result && (
                <div className="pg-response-meta">
                  {typeof result.status === 'number' && (
                    <span className={'ch-pill ' + statusPill(result.status)}>
                      <span className="ch-pill-dot" />
                      {result.status}
                    </span>
                  )}
                  {typeof result.durationMs === 'number' && (
                    <span className="ch-pill ch-pill-muted">
                      <span className="ch-pill-dot" />
                      {result.durationMs} ms
                    </span>
                  )}
                </div>
              )}
            </div>

            {!result ? (
              <div className="pg-response-empty">
                <Icon.Play />
                <div>Send a request to see the response here.</div>
              </div>
            ) : result.error ? (
              <pre className="pg-response-body error">{result.error}</pre>
            ) : (
              <>
                <div className="ch-segment pg-sub-seg">
                  <button
                    className={'ch-segment-btn' + (responseTab === 'body' ? ' active' : '')}
                    onClick={() => setResponseTab('body')}
                  >Body</button>
                  <button
                    className={'ch-segment-btn' + (responseTab === 'headers' ? ' active' : '')}
                    onClick={() => setResponseTab('headers')}
                  >
                    Headers
                    <span className="ch-segment-count">
                      {Object.keys(result.headers || {}).length}
                    </span>
                  </button>
                </div>
                {responseTab === 'body' ? (
                  <>
                    {activeEndpoint?.outputSchema && (
                      <div className="pg-response-filter">
                        <span className="pg-response-filter-label">View</span>
                        <div className="pg-response-filter-toggle">
                          <button
                            className={'pg-seg-btn' + (responseFilter === 'filtered' ? ' active' : '')}
                            onClick={() => setResponseFilter('filtered')}
                            title="Show only the keys this endpoint exposes to the LLM"
                          >Filtered</button>
                          <button
                            className={'pg-seg-btn' + (responseFilter === 'raw' ? ' active' : '')}
                            onClick={() => setResponseFilter('raw')}
                            title="Show the full upstream payload"
                          >Raw</button>
                        </div>
                      </div>
                    )}
                    <pre className="pg-response-body">{prettyBody(
                      (responseFilter === 'filtered' && activeEndpoint?.outputSchema)
                        ? applyFilter(result.body, normalizeSchema(activeEndpoint.outputSchema))
                        : result.body
                    )}</pre>
                  </>
                ) : (
                  <div className="pg-response-headers">
                    {Object.entries(result.headers || {}).length === 0 ? (
                      <div className="pg-hint">No headers</div>
                    ) : (
                      Object.entries(result.headers).map(([k, v]) => (
                        <div key={k} className="pg-header-line">
                          <span className="pg-header-key">{k}</span>
                          <span className="pg-header-val">{String(v)}</span>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="pg-history">
            <div className="pg-history-head">
              <div className="pg-history-title">Recent requests</div>
              {history.length > 0 && (
                <button className="btn secondary pg-clear-btn" onClick={clearHistory}>
                  Clear
                </button>
              )}
            </div>
            {history.length === 0 ? (
              <div className="pg-hint">Your recent requests will appear here.</div>
            ) : (
              <div className="pg-history-list">
                {history.map((h) => (
                  <button key={h.id} className="pg-history-item" onClick={() => replay(h)}>
                    <span className={'ch-method ch-method-' + (h.method || '').toLowerCase()}>
                      {h.method}
                    </span>
                    <span className="pg-history-url">{h.url}</span>
                    <span className={'ch-pill ' + statusPill(h.status)}>
                      <span className="ch-pill-dot" />
                      {h.status ?? '—'}
                    </span>
                    <span className="pg-history-time">{h.durationMs} ms</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {toast && (
        <div className={`pg-toast pg-toast-${toast.tone || 'info'}`}>{toast.msg}</div>
      )}

      {schemaDialogOpen && activeEndpoint && (
        <SchemaDialog
          endpoint={activeEndpoint}
          lastResult={result}
          onClose={() => setSchemaDialogOpen(false)}
          onSaved={(updated) => {
            setEndpoints((prev) => prev.map((e) => (e.id === updated.id ? updated : e)));
            setToast({ tone: 'success', msg: 'Schema saved' });
            setSchemaDialogOpen(false);
          }}
        />
      )}
    </>
  );
}

// ---------- small components ----------

function InlineInput({ placeholder = '', initial = '', onSubmit, onCancel, autoFocus }) {
  const [value, setValue] = useState(initial);
  const ref = useRef(null);
  useEffect(() => { if (autoFocus && ref.current) ref.current.focus(); }, [autoFocus]);
  return (
    <input
      ref={ref}
      className="pg-inline-input"
      value={value}
      placeholder={placeholder}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onSubmit(value);
        else if (e.key === 'Escape') onCancel?.();
      }}
      onBlur={() => { if (value.trim()) onSubmit(value); else onCancel?.(); }}
    />
  );
}

function ConfirmButton({ title, onConfirm }) {
  const [arm, setArm] = useState(false);
  useEffect(() => {
    if (!arm) return;
    const t = setTimeout(() => setArm(false), 3000);
    return () => clearTimeout(t);
  }, [arm]);
  return (
    <span
      role="button"
      className={'pg-icon-btn danger' + (arm ? ' armed' : '')}
      title={arm ? 'Click again to confirm' : title}
      onClick={(e) => {
        e.stopPropagation();
        if (arm) { setArm(false); onConfirm(); }
        else setArm(true);
      }}
    >
      <Icon.Trash />
    </span>
  );
}

function SavePicker({ folders, onPick, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [onClose]);
  return (
    <div className="pg-save-pop" ref={ref}>
      <div className="pg-save-head">Save to folder</div>
      {folders.length === 0 && (
        <div className="pg-hint" style={{ padding: '6px 10px' }}>
          No folders yet. Create one first.
        </div>
      )}
      {folders.map((f) => (
        <button key={f.id} className="pg-save-item" onClick={() => onPick(f.id)}>
          {f.name}
        </button>
      ))}
      <div className="pg-save-sep" />
      <button className="pg-save-item" onClick={() => onPick(null)}>
        Save unfiled
      </button>
    </div>
  );
}

function EnvVariablesEditor({ env, onSave }) {
  // Draft-editor pattern: local `rows` owns editing state, Save diffs against
  // the server list (env.variables) and batches create / patch / delete ops.
  const seed = () => {
    const base = (env.variables || []).map((v) => ({
      _localId: v.id,
      id: v.id,
      key: v.key,
      value: v.value ?? '',
      _origKey: v.key,
      _origValue: v.value ?? '',
      _new: false,
      _removed: false,
    }));
    if (base.length === 0) base.push(newBlankRow());
    return base;
  };

  const [rows, setRows] = useState(seed);
  const [saving, setSaving] = useState(false);
  const [revealed, setRevealed] = useState(() => new Set()); // localIds with value visible
  const [revealAll, setRevealAll] = useState(false);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (!saving) setRows(seed()); }, [env.id, env.variables, saving]);

  const toggleReveal = (localId) => {
    setRevealed((prev) => {
      const next = new Set(prev);
      if (next.has(localId)) next.delete(localId); else next.add(localId);
      return next;
    });
  };

  const visibleRows = rows.filter((r) => !r._removed);
  const hasDirty = rows.some((r) =>
    r._removed
    || (r._new && r.key.trim())
    || (!r._new && (r.key !== r._origKey || r.value !== r._origValue))
  );

  const setCell = (localId, patch) =>
    setRows((prev) => prev.map((r) => (r._localId === localId ? { ...r, ...patch } : r)));

  const addAfter = (localId) => {
    setRows((prev) => {
      const idx = prev.findIndex((r) => r._localId === localId);
      const next = [...prev];
      next.splice(idx + 1, 0, newBlankRow());
      return next;
    });
  };

  const remove = (localId) => {
    setRows((prev) => {
      const r = prev.find((x) => x._localId === localId);
      if (!r) return prev;
      if (r._new) return prev.filter((x) => x._localId !== localId);
      return prev.map((x) => (x._localId === localId ? { ...x, _removed: true } : x));
    });
  };

  const cancel = () => setRows(seed());

  const save = async () => {
    const ops = { creates: [], updates: [], deletes: [] };
    for (const r of rows) {
      if (r._removed && !r._new && r.id) {
        ops.deletes.push(r.id);
      } else if (r._new && r.key.trim()) {
        ops.creates.push({ key: r.key.trim(), value: r.value });
      } else if (!r._new && r.id && (r.key !== r._origKey || r.value !== r._origValue)) {
        if (r.key.trim()) ops.updates.push({ id: r.id, key: r.key.trim(), value: r.value });
      }
    }
    if (!ops.creates.length && !ops.updates.length && !ops.deletes.length) return;
    setSaving(true);
    try {
      await onSave(ops);
    } finally {
      setSaving(false);
    }
  };

  const appendBlank = () => {
    setRows((prev) => [...prev, newBlankRow()]);
    // Focus the newly-added key input on next tick.
    setTimeout(() => {
      const inputs = document.querySelectorAll('.pg-env-table .pg-env-key-input');
      const last = inputs[inputs.length - 1];
      if (last) last.focus();
    }, 0);
  };

  return (
    <div className="pg-env-editor">
      <div className="pg-env-table">
        <div className="pg-env-table-head">
          <div>Key</div>
          <div className="pg-env-value-head">
            <span>Value</span>
            <button
              type="button"
              className="pg-env-reveal-all"
              onClick={() => setRevealAll((v) => !v)}
              title={revealAll ? 'Hide all values' : 'Show all values'}
            >
              {revealAll ? <EyeOffIcon /> : <EyeIcon />}
              {revealAll ? 'Hide all' : 'Show all'}
            </button>
          </div>
          <div aria-hidden="true" />
        </div>
        <div className="pg-env-table-body">
          {visibleRows.map((r) => {
            const isVisible = revealAll || revealed.has(r._localId);
            return (
            <div className="pg-env-table-row" key={r._localId}>
              <input
                className="pg-env-key-input"
                value={r.key}
                onChange={(e) => setCell(r._localId, { key: e.target.value })}
                placeholder="key"
                spellCheck={false}
              />
              <div className="pg-env-value-cell">
                <input
                  className="pg-env-value-input"
                  type={isVisible ? 'text' : 'password'}
                  value={r.value}
                  onChange={(e) => setCell(r._localId, { value: e.target.value })}
                  placeholder="value"
                  spellCheck={false}
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="pg-env-eye"
                  onClick={() => toggleReveal(r._localId)}
                  title={isVisible ? 'Hide value' : 'Show value'}
                  aria-label={isVisible ? 'Hide value' : 'Show value'}
                  tabIndex={-1}
                >
                  {isVisible ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
              <button
                className="pg-env-remove"
                onClick={() => remove(r._localId)}
                title="Remove this variable"
                type="button"
                aria-label="Remove"
                disabled={visibleRows.length === 1 && !r.key && !r.value && r._new}
              >
                ×
              </button>
            </div>
            );
          })}
        </div>
        <button
          className="pg-env-add-row"
          onClick={appendBlank}
          type="button"
        >
          <Icon.Plus /> Add variable
        </button>
      </div>
      <div className="pg-env-footer">
        <button className="btn" onClick={save} disabled={saving || !hasDirty}>
          {saving ? 'Saving…' : 'Save changes'}
        </button>
        <button
          className="btn secondary"
          onClick={cancel}
          disabled={saving || !hasDirty}
        >
          Reset
        </button>
      </div>
    </div>
  );
}

function newBlankRow() {
  return {
    _localId: 'tmp-' + (globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)),
    id: null,
    key: '',
    value: '',
    _origKey: '',
    _origValue: '',
    _new: true,
    _removed: false,
  };
}

function SchemaTabSummary({ endpoint, lastResult, onOpenEditor }) {
  if (!endpoint) {
    return <div className="pg-hint pg-schema-empty">Save this request to a folder first, then pick which keys the LLM can see.</div>;
  }

  const hasBody = lastResult && lastResult.body !== undefined && lastResult.body !== null;
  const hasSchema = !!endpoint.outputSchema;
  const counts = useMemo(() => hasSchema ? countInclude(normalizeSchema(endpoint.outputSchema)) : { total: 0, included: 0 }, [endpoint.outputSchema, hasSchema]);

  return (
    <div className="pg-schema-tab">
      <div className="pg-schema-tab-head">
        <div>
          <div className="pg-schema-tab-title">Output schema</div>
          <div className="pg-schema-tab-sub">
            Choose which keys from the response are visible to the LLM when this endpoint is used as a tool.
          </div>
        </div>
        {hasSchema && counts.total > 0 && (
          <span className="ch-pill ch-pill-info">
            <span className="ch-pill-dot" />
            {counts.included} of {counts.total} exposed
          </span>
        )}
      </div>

      {!hasBody && !hasSchema && (
        <div className="pg-hint pg-schema-empty">
          Send this request once so we have a real payload to pick keys from, then click "Edit schema" to tick what the LLM can see.
        </div>
      )}
      {(hasBody || hasSchema) && (
        <div className="pg-schema-tab-actions">
          <button className="btn" onClick={onOpenEditor}>
            <Icon.Pencil /> Edit schema
          </button>
          {!hasBody && hasSchema && (
            <span className="pg-hint">Last saved snapshot will be used — send the request again to pick from the live payload.</span>
          )}
        </div>
      )}
    </div>
  );
}

function SchemaDialog({ endpoint, lastResult, onClose, onSaved }) {
  // Build the tree from the live response body when available so the user
  // sees every nested key from the actual payload — if no body is on hand,
  // fall back to whatever schema was persisted.
  const livePayload = lastResult && lastResult.body !== undefined ? lastResult.body : null;
  const savedSchema = useMemo(
    () => endpoint.outputSchema ? normalizeSchema(endpoint.outputSchema) : null,
    [endpoint.outputSchema],
  );
  const initialDraft = useMemo(
    () => {
      if (livePayload !== null) {
        const fromBody = schemaFromValue(livePayload);
        return savedSchema ? mergeIncludeFlags(fromBody, savedSchema) : fromBody;
      }
      return savedSchema;
    },
    [livePayload, savedSchema],
  );

  const [draft, setDraft] = useState(initialDraft);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState('');
  const [searchInputRef] = useState(() => ({ current: null }));
  const [previewMode, setPreviewMode] = useState('filtered'); // 'filtered' | 'raw'

  // Close on Escape, Ctrl/Cmd+S to save, Ctrl/Cmd+F to focus search
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
      else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, searchInputRef]);

  const stats = useMemo(() => draft ? countInclude(draft) : { total: 0, included: 0 }, [draft]);
  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initialDraft),
    [draft, initialDraft],
  );
  const filtered = useMemo(() => {
    if (!draft) return null;
    const source = livePayload !== null ? livePayload : synthesizeFromSchema(draft);
    return applyFilter(source, draft);
  }, [draft, livePayload]);

  const toggleAtPath = (path, value) => setDraft((prev) => setIncludeAt(prev, path, value));
  const toggleSubtree = (path, value) => setDraft((prev) => applyIncludeAt(prev, path, value));
  const toggleAll = (value) => setDraft((prev) => applyInclude(prev, value));
  const resetToSaved = () => setDraft(initialDraft);

  async function save() {
    if (!draft) return;
    setSaving(true);
    try {
      const updated = await apiClient.patch(
        `/api-playground/endpoints/${encodeURIComponent(endpoint.id)}`,
        { outputSchema: draft },
      );
      onSaved?.(updated);
    } finally {
      setSaving(false);
    }
  }

  const term = search.trim().toLowerCase();
  const previewValue = previewMode === 'filtered' ? filtered : (livePayload !== null ? livePayload : synthesizeFromSchema(draft));

  return (
    <div
      className="pg-dialog-overlay"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
    >
      <div className="pg-dialog pg-schema-dialog">
        <div className="pg-dialog-head">
          <div>
            <div className="pg-dialog-title">Output schema — {endpoint.name}</div>
            <div className="pg-dialog-sub">
              Tick every key the LLM may read. Unticked keys stay server-side when this endpoint runs as a tool.
            </div>
          </div>
          <div className="pg-dialog-head-meta">
            <span className="pg-schema-counts-pill">
              <span className="pg-schema-counts-num">{stats.included}</span>
              <span className="pg-schema-counts-of">/ {stats.total}</span>
              exposed
            </span>
            <button className="pg-icon-btn" onClick={onClose} title="Close (Esc)" aria-label="Close">
              <Icon.Close />
            </button>
          </div>
        </div>

        <div className="pg-dialog-toolbar">
          <div className="pg-schema-search">
            <Icon.Search />
            <input
              ref={(el) => { searchInputRef.current = el; }}
              className="pg-schema-search-input"
              placeholder="Search keys… (Ctrl+F)"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              spellCheck={false}
            />
            {search && (
              <button
                className="pg-icon-btn pg-schema-search-clear"
                onClick={() => setSearch('')}
                title="Clear search"
                aria-label="Clear search"
              >
                <Icon.Close />
              </button>
            )}
          </div>
          <div className="pg-dialog-toolbar-actions">
            <button className="btn secondary pg-schema-tinybtn" onClick={() => toggleAll(true)}>
              <Icon.Check /> Select all
            </button>
            <button className="btn secondary pg-schema-tinybtn" onClick={() => toggleAll(false)}>
              Deselect all
            </button>
            <button
              className="btn secondary pg-schema-tinybtn"
              onClick={resetToSaved}
              disabled={!dirty}
              title="Revert to last saved selection"
            >
              Reset
            </button>
          </div>
        </div>

        {!draft ? (
          <div className="pg-hint pg-schema-empty">
            Nothing to pick from yet — send this request once so we can show the keys it returns.
          </div>
        ) : (
          <div className="pg-schema-split">
            <div className="pg-schema-tree-wrap">
              <div className="pg-schema-section-label">Keys in the response</div>
              <div className="pg-schema-tree-scroll">
                <SchemaCheckTree
                  node={draft}
                  path={[]}
                  keyName="$"
                  depth={0}
                  onToggle={toggleAtPath}
                  onToggleSubtree={toggleSubtree}
                  searchTerm={term}
                />
                {term && !nodeMatchesDeep(draft, '$', term) && (
                  <div className="pg-hint" style={{ padding: 16, textAlign: 'center' }}>
                    No keys match "{search}".
                  </div>
                )}
              </div>
            </div>
            <div className="pg-schema-preview-wrap">
              <div className="pg-schema-preview-head">
                <div className="pg-schema-section-label" style={{ padding: 0 }}>Preview</div>
                <div className="pg-schema-preview-toggle">
                  <button
                    className={'pg-seg-btn' + (previewMode === 'filtered' ? ' active' : '')}
                    onClick={() => setPreviewMode('filtered')}
                  >Filtered (LLM)</button>
                  <button
                    className={'pg-seg-btn' + (previewMode === 'raw' ? ' active' : '')}
                    onClick={() => setPreviewMode('raw')}
                    disabled={livePayload === null}
                    title={livePayload === null ? 'No live payload — send the request first' : 'Show original response'}
                  >Raw response</button>
                </div>
              </div>
              <pre className="pg-schema-preview">{prettyBody(previewValue)}</pre>
            </div>
          </div>
        )}

        <div className="pg-dialog-footer">
          <div className="pg-dialog-footer-meta">
            {dirty
              ? <span className="pg-dirty-dot">● Unsaved changes</span>
              : <span style={{ color: 'var(--muted)' }}>No changes</span>}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn secondary" onClick={onClose} disabled={saving}>Cancel</button>
            <button
              className="btn"
              onClick={save}
              disabled={saving || !draft || !dirty}
            >
              {saving ? 'Saving…' : 'Save schema'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Legacy inline editor — left around for type completeness but unused now.
function SchemaEditor({ endpoint, lastResult, onSaved }) {
  // Draft editor: start from the persisted schema, let the user tick/untick
  // `include` on any node, preview filtered output, then Save patches the
  // endpoint. Reset reverts to whatever the server currently has.
  const normalize = (s) => normalizeSchema(s);
  const [draft, setDraft] = useState(() => normalize(endpoint.outputSchema));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(normalize(endpoint.outputSchema));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint.id, endpoint.outputSchema]);

  const original = useMemo(() => JSON.stringify(normalize(endpoint.outputSchema)), [endpoint.outputSchema]);
  const dirty = useMemo(() => JSON.stringify(draft) !== original, [draft, original]);

  const stats = useMemo(() => countInclude(draft), [draft]);

  const toggleAtPath = (path, value) => {
    setDraft((prev) => setIncludeAt(prev, path, value));
  };
  const toggleAll = (value) => {
    setDraft((prev) => applyInclude(prev, value));
  };

  // Prefer the live response body if it matches this endpoint; otherwise
  // fall back to a sample value we synthesize from the schema itself so the
  // operator still sees which keys survive the filter.
  const sample = lastResult?.body ?? synthesizeFromSchema(endpoint.outputSchema);
  const filtered = useMemo(() => applyFilter(sample, draft), [sample, draft]);

  async function save() {
    setSaving(true);
    try {
      const updated = await apiClient.patch(
        `/api-playground/endpoints/${encodeURIComponent(endpoint.id)}`,
        { outputSchema: draft },
      );
      onSaved?.(updated);
    } finally {
      setSaving(false);
    }
  }
  function reset() {
    setDraft(normalize(endpoint.outputSchema));
  }

  return (
    <div className="pg-schema-editor">
      <div className="pg-schema-toolbar">
        <div className="pg-schema-title">Output schema</div>
        <div className="pg-schema-toolbar-actions">
          <span className="pg-schema-counts">
            {stats.included} of {stats.total} fields exposed
          </span>
          <button
            className="btn secondary pg-schema-tinybtn"
            onClick={() => toggleAll(true)}
            type="button"
          >
            Select all
          </button>
          <button
            className="btn secondary pg-schema-tinybtn"
            onClick={() => toggleAll(false)}
            type="button"
          >
            Clear
          </button>
          {endpoint.lastStatus != null && (
            <span className={'ch-pill ' + statusPill(endpoint.lastStatus)}>
              <span className="ch-pill-dot" />
              {endpoint.lastStatus}
            </span>
          )}
        </div>
      </div>

      <div className="pg-schema-hint">
        Tick fields the LLM should see when this request is used as a tool.
        Unticked fields stay on the server.
      </div>

      <div className="pg-schema-split">
        <div className="pg-schema-tree-wrap">
          <div className="pg-schema-section-label">Fields</div>
          <SchemaCheckTree node={draft} path={[]} keyName="$" depth={0} onToggle={toggleAtPath} />
        </div>
        <div className="pg-schema-preview-wrap">
          <div className="pg-schema-section-label">Filtered output (what the LLM sees)</div>
          <pre className="pg-schema-preview">{prettyBody(filtered)}</pre>
        </div>
      </div>

      <div className="pg-schema-footer">
        <button
          className="btn"
          onClick={save}
          disabled={saving || !dirty}
        >
          {saving ? 'Saving…' : 'Save schema'}
        </button>
        <button
          className="btn secondary"
          onClick={reset}
          disabled={saving || !dirty}
        >
          Reset
        </button>
      </div>
    </div>
  );
}

function SchemaCheckTree({
  node, path, keyName, depth, onToggle, onToggleSubtree,
  parentIncluded = true, searchTerm = '',
}) {
  if (!node || typeof node !== 'object') return null;
  if (searchTerm && !nodeMatchesDeep(node, keyName, searchTerm)) return null;

  const t = node.type || 'any';
  const included = !!node.include;
  const effective = parentIncluded && included;
  const childCount = countChildren(node);
  const isBranch = t === 'object' || t === 'array';
  const matchesHere = !searchTerm || String(keyName).toLowerCase().includes(searchTerm);

  const row = (
    <div className={'pg-schema-line' + (effective ? '' : ' muted') + (matchesHere && searchTerm ? ' hit' : '')}>
      <span className="pg-schema-indent" style={{ width: depth * 18 }} aria-hidden="true">
        {Array.from({ length: depth }).map((_, i) => (
          <span key={i} className="pg-schema-guide" />
        ))}
      </span>
      <input
        type="checkbox"
        checked={included}
        onChange={(e) => {
          if (isBranch && e.nativeEvent.shiftKey) {
            onToggleSubtree(path, e.target.checked);
          } else {
            onToggle(path, e.target.checked);
          }
        }}
        disabled={!parentIncluded}
        title={isBranch ? 'Click: toggle this node only · Shift+click: toggle subtree' : ''}
      />
      <span className="pg-schema-key">{highlightMatch(keyName, searchTerm)}</span>
      <span className="pg-schema-type">{t}{isBranch && childCount > 0 ? ` · ${childCount}` : ''}</span>
      {isBranch && (
        <button
          type="button"
          className="pg-schema-bulk"
          onClick={(e) => { e.preventDefault(); onToggleSubtree(path, !included); }}
          title={included ? 'Untick this and everything inside' : 'Tick this and everything inside'}
        >
          {included ? 'all off' : 'all on'}
        </button>
      )}
    </div>
  );

  if (t === 'object') {
    const props = node.properties || {};
    const keys = Object.keys(props);
    return (
      <div className="pg-schema-node">
        {row}
        {keys.map((k) => (
          <SchemaCheckTree
            key={k}
            node={props[k]}
            path={[...path, 'properties', k]}
            keyName={k}
            depth={depth + 1}
            onToggle={onToggle}
            onToggleSubtree={onToggleSubtree}
            parentIncluded={effective}
            searchTerm={searchTerm}
          />
        ))}
      </div>
    );
  }
  if (t === 'array') {
    return (
      <div className="pg-schema-node">
        {row}
        {node.items && (
          <SchemaCheckTree
            node={node.items}
            path={[...path, 'items']}
            keyName="[0]"
            depth={depth + 1}
            onToggle={onToggle}
            onToggleSubtree={onToggleSubtree}
            parentIncluded={effective}
            searchTerm={searchTerm}
          />
        )}
      </div>
    );
  }
  return row;
}

function highlightMatch(text, term) {
  if (!term) return String(text);
  const s = String(text);
  const idx = s.toLowerCase().indexOf(term);
  if (idx < 0) return s;
  return (
    <>
      {s.slice(0, idx)}
      <mark className="pg-schema-hit">{s.slice(idx, idx + term.length)}</mark>
      {s.slice(idx + term.length)}
    </>
  );
}

function countChildren(node) {
  if (!node || typeof node !== 'object') return 0;
  if (node.type === 'object') return Object.keys(node.properties || {}).length;
  if (node.type === 'array' && node.items) return 1;
  return 0;
}

function nodeMatchesDeep(node, keyName, term) {
  if (!term) return true;
  if (String(keyName).toLowerCase().includes(term)) return true;
  if (!node || typeof node !== 'object') return false;
  if (node.type === 'object' && node.properties) {
    for (const [k, child] of Object.entries(node.properties)) {
      if (nodeMatchesDeep(child, k, term)) return true;
    }
  }
  if (node.type === 'array' && node.items) {
    return nodeMatchesDeep(node.items, '[0]', term);
  }
  return false;
}

function ChevronDown(props) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" {...props}>
      <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function ChevronRight(props) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" {...props}>
      <path d="m9 6 6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function EyeIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" {...props}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}
function EyeOffIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" {...props}>
      <path d="M3 3l18 18" strokeLinecap="round" />
      <path d="M10.6 6.1A10 10 0 0 1 12 6c6.5 0 10 6 10 6a17.6 17.6 0 0 1-3.1 4M6.5 7.6A17.7 17.7 0 0 0 2 12s3.5 6 10 6c1.4 0 2.7-.3 3.8-.7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M9.5 9.5a3 3 0 0 0 4.2 4.2" strokeLinecap="round" />
    </svg>
  );
}
function InfoIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8v.01" strokeLinecap="round" />
    </svg>
  );
}
// ---------- First-time onboarding: a curl-shaped example the user can load ----------

const GEMINI_EXAMPLE = {
  name: 'Gemini · streamGenerateContent',
  description:
    'Streams a generation from Google\'s Gemini model. The agent should call this when it needs to ask Gemini a follow-up question with web search grounding enabled. Body is the standard Generative Language API payload.',
  method: 'POST',
  url:
    'https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:streamGenerateContent?key={{GEMINI_API_KEY}}',
  headers: [
    { key: 'Content-Type', value: 'application/json' },
  ],
  body: JSON.stringify(
    {
      contents: [
        { role: 'user', parts: [{ text: 'INSERT_INPUT_HERE' }] },
      ],
      generationConfig: { thinkingConfig: { thinkingLevel: 'HIGH' } },
      tools: [{ googleSearch: {} }],
    },
    null,
    2,
  ),
};

// Best-effort curl parser. Handles common shapes: -X METHOD, multiple -H "K: V",
// -d / --data / --data-raw with single or double quotes and \-line-continuations.
// Doesn't try to be a full shell parser — just enough to seed the playground.
function parseCurl(input) {
  if (!input) return null;
  const trimmed = String(input).trim();
  if (!/^curl\b/i.test(trimmed)) return null;

  const tokens = tokenizeCurl(trimmed.replace(/\\\s*\n/g, ' '));
  if (tokens.length === 0) return null;

  let method = '';
  let url = '';
  const headers = [];
  let body = '';

  for (let i = 1; i < tokens.length; i += 1) {
    const tok = tokens[i];
    if (tok === '-X' || tok === '--request') {
      method = (tokens[++i] || '').toUpperCase();
    } else if (tok === '-H' || tok === '--header') {
      const raw = tokens[++i] || '';
      const idx = raw.indexOf(':');
      if (idx > 0) headers.push({ key: raw.slice(0, idx).trim(), value: raw.slice(idx + 1).trim() });
    } else if (tok === '-d' || tok === '--data' || tok === '--data-raw' || tok === '--data-binary') {
      body = tokens[++i] || '';
      if (!method) method = 'POST';
    } else if (tok === '-u' || tok === '--user') {
      const creds = tokens[++i] || '';
      headers.push({ key: 'Authorization', value: 'Basic ' + btoa(creds) });
    } else if (tok === '--url') {
      url = tokens[++i] || '';
    } else if (!tok.startsWith('-') && !url) {
      url = tok;
    }
  }
  if (!url) return null;
  if (!method) method = 'GET';
  if (headers.length === 0) headers.push({ key: '', value: '' });
  return { method, url, headers, body };
}

function tokenizeCurl(s) {
  const out = [];
  let buf = '';
  let quote = null;
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (quote) {
      if (c === '\\' && quote === '"' && s[i + 1] != null) {
        buf += s[i + 1];
        i += 1;
      } else if (c === quote) {
        quote = null;
      } else {
        buf += c;
      }
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (/\s/.test(c)) {
      if (buf) { out.push(buf); buf = ''; }
    } else {
      buf += c;
    }
  }
  if (buf) out.push(buf);
  return out;
}

function EnvHelpButton() {
  // The Environments sidebar has `overflow-y: auto` so an absolutely-positioned
  // popover gets clipped at its edges. Rendering the popover with
  // `position: fixed` (anchored to the button's bounding rect) lets it float
  // freely above the layout regardless of the scrolling parent.
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const btnRef = useRef(null);
  const popRef = useRef(null);

  const place = () => {
    if (!btnRef.current) return;
    const r = btnRef.current.getBoundingClientRect();
    const POP_W = 320;
    setPos({
      top: Math.round(r.bottom + 8),
      left: Math.round(Math.max(8, Math.min(window.innerWidth - POP_W - 8, r.right - POP_W))),
      width: POP_W,
    });
  };

  useEffect(() => {
    if (!open) return;
    place();
    const onDown = (e) => {
      if (
        (popRef.current && popRef.current.contains(e.target))
        || (btnRef.current && btnRef.current.contains(e.target))
      ) return;
      setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    const onResize = () => place();
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onResize, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('scroll', onResize, true);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        className="pg-icon-btn"
        title="How to use environment variables"
        onClick={() => setOpen((v) => !v)}
      >
        <InfoIcon />
      </button>
      {open && pos && (
        <div
          ref={popRef}
          className="pg-help-pop"
          role="tooltip"
          style={{ top: pos.top, left: pos.left, width: pos.width }}
        >
          <div className="pg-help-title">Using environment variables</div>
          <div className="pg-help-body">
            <p>
              Reference any variable from the active environment with the
              <code>{'{{name}}'}</code> token. The Playground substitutes
              tokens at send time — they work in:
            </p>
            <ul>
              <li>The <strong>URL</strong> (path or query)</li>
              <li>Any <strong>header</strong> value</li>
              <li>The request <strong>body</strong></li>
            </ul>
            <div className="pg-help-section">Examples</div>
            <pre className="pg-help-code">{`URL    https://{{host}}/v1/users/{{userId}}
Header Authorization: Bearer {{api_key}}
Body   { "tenant": "{{tenant}}" }`}</pre>
            <div className="pg-help-tip">
              <Icon.AlertTriangle />
              <span>
                Switch the <em>active env</em> in the dropdown above to use
                a different set of values without editing the request.
              </span>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
function RefreshIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" {...props}>
      <path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function CollapseIcon(props) {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none" {...props}>
      <path d="M5 12h14" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// ---------- helpers ----------

function statusPill(status) {
  if (typeof status !== 'number') return 'ch-pill-muted';
  if (status >= 200 && status < 300) return 'ch-pill-success';
  if (status >= 300 && status < 400) return 'ch-pill-info';
  if (status >= 400 && status < 500) return 'ch-pill-warning';
  return 'ch-pill-danger';
}

function prettyBody(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function suggestName(req) {
  try {
    const u = new URL(req.url);
    return `${req.method} ${u.pathname}`;
  } catch {
    return `${req.method} request`;
  }
}

// ---------- Schema helpers ----------

// Build a schema tree straight from an actual value (the response body).
// Unlike the backend's sample-based inference, this walks every key of every
// object it finds so the operator sees the full nested key list — not just
// the shape of the first array element.
function schemaFromValue(value, depth = 0) {
  if (depth > 16) return { type: 'any', include: true };
  if (value === null) return { type: 'null', include: true };
  if (typeof value === 'boolean') return { type: 'boolean', include: true };
  if (typeof value === 'number') return { type: Number.isInteger(value) ? 'integer' : 'number', include: true };
  if (typeof value === 'string') return { type: 'string', include: true };
  if (Array.isArray(value)) {
    // Merge every element so we see the union of keys any row might carry.
    let items = { type: 'any', include: true };
    for (const el of value) {
      const s = schemaFromValue(el, depth + 1);
      items = mergeSchemas(items, s);
    }
    return { type: 'array', items, include: true };
  }
  if (typeof value === 'object') {
    const properties = {};
    for (const [k, v] of Object.entries(value)) {
      properties[k] = schemaFromValue(v, depth + 1);
    }
    return { type: 'object', properties, include: true };
  }
  return { type: 'any', include: true };
}

// Combine two schemas (used when walking array elements) so we capture the
// union of keys. Keeps `include: true` by default on new nodes.
function mergeSchemas(a, b) {
  if (!a || a.type === 'any') return b;
  if (!b || b.type === 'any') return a;
  if (a.type !== b.type) return { type: 'any', include: true };
  if (a.type === 'object') {
    const properties = { ...(a.properties || {}) };
    for (const [k, v] of Object.entries(b.properties || {})) {
      properties[k] = properties[k] ? mergeSchemas(properties[k], v) : v;
    }
    return { type: 'object', properties, include: a.include !== false || b.include !== false };
  }
  if (a.type === 'array') {
    return {
      type: 'array',
      items: mergeSchemas(a.items || { type: 'any', include: true }, b.items || { type: 'any', include: true }),
      include: true,
    };
  }
  return a;
}

// Copy `include` flags from `saved` onto matching nodes in `fresh`. Any new
// keys in `fresh` stay at `include: true` (the default for freshly-discovered
// fields — operators can untick them after the fact).
function mergeIncludeFlags(fresh, saved) {
  if (!fresh || !saved) return fresh;
  const out = { ...fresh };
  if (typeof saved.include === 'boolean') out.include = saved.include;
  if (fresh.type === 'object' && saved.type === 'object') {
    const freshProps = fresh.properties || {};
    const savedProps = saved.properties || {};
    out.properties = {};
    for (const [k, v] of Object.entries(freshProps)) {
      out.properties[k] = savedProps[k] ? mergeIncludeFlags(v, savedProps[k]) : v;
    }
  } else if (fresh.type === 'array' && saved.type === 'array') {
    out.items = saved.items ? mergeIncludeFlags(fresh.items, saved.items) : fresh.items;
  }
  return out;
}

function normalizeSchema(node) {
  if (!node || typeof node !== 'object') return node;
  const out = { ...node };
  if (out.include === undefined) out.include = true;
  if (out.type === 'object' && out.properties && typeof out.properties === 'object') {
    out.properties = Object.fromEntries(
      Object.entries(out.properties).map(([k, v]) => [k, normalizeSchema(v)]),
    );
  } else if (out.type === 'array' && out.items) {
    out.items = normalizeSchema(out.items);
  }
  return out;
}

function countInclude(node) {
  let total = 0;
  let included = 0;
  const walk = (n, parentOn) => {
    if (!n || typeof n !== 'object') return;
    total += 1;
    const on = !!n.include && parentOn;
    if (on) included += 1;
    if (n.type === 'object' && n.properties) {
      for (const v of Object.values(n.properties)) walk(v, on);
    } else if (n.type === 'array' && n.items) {
      walk(n.items, on);
    }
  };
  walk(node, true);
  return { total, included };
}

// Walk a list path like ['properties', 'data', 'items', 'properties', 'id']
// and set `include` on the terminal node, returning a fresh tree.
function setIncludeAt(root, path, value) {
  if (path.length === 0) {
    return { ...root, include: value };
  }
  const [head, ...rest] = path;
  if (head === 'properties') {
    const [key, ...rest2] = rest;
    return {
      ...root,
      properties: {
        ...root.properties,
        [key]: setIncludeAt(root.properties?.[key] || {}, rest2, value),
      },
    };
  }
  if (head === 'items') {
    return {
      ...root,
      items: setIncludeAt(root.items || {}, rest, value),
    };
  }
  return root;
}

// Like `setIncludeAt`, but also cascades the value into every descendant of
// the targeted node — used for Shift+click and the per-row "all on/off"
// shortcut so operators can flip a whole subtree in one click.
function applyIncludeAt(root, path, value) {
  if (path.length === 0) return applyInclude(root, value);
  const [head, ...rest] = path;
  if (head === 'properties') {
    const [key, ...rest2] = rest;
    return {
      ...root,
      properties: {
        ...root.properties,
        [key]: applyIncludeAt(root.properties?.[key] || {}, rest2, value),
      },
    };
  }
  if (head === 'items') {
    return { ...root, items: applyIncludeAt(root.items || {}, rest, value) };
  }
  return root;
}

function applyInclude(node, value) {
  if (!node || typeof node !== 'object') return node;
  const out = { ...node, include: value };
  if (out.type === 'object' && out.properties) {
    out.properties = Object.fromEntries(
      Object.entries(out.properties).map(([k, v]) => [k, applyInclude(v, value)]),
    );
  } else if (out.type === 'array' && out.items) {
    out.items = applyInclude(out.items, value);
  }
  return out;
}

// Given a live response body and a schema with `include` flags, keep only
// the values whose corresponding schema node is on (and all ancestors are on).
function applyFilter(data, schema) {
  if (!schema || typeof schema !== 'object') return data;
  if (!schema.include) return undefined;
  const t = schema.type;
  if (t === 'object' && schema.properties && data && typeof data === 'object' && !Array.isArray(data)) {
    const out = {};
    for (const [k, childSchema] of Object.entries(schema.properties)) {
      if (!childSchema?.include) continue;
      if (!(k in data)) continue;
      const filtered = applyFilter(data[k], childSchema);
      if (filtered !== undefined) out[k] = filtered;
    }
    return out;
  }
  if (t === 'array' && Array.isArray(data)) {
    if (!schema.items) return data;
    return data.map((item) => applyFilter(item, schema.items)).filter((x) => x !== undefined);
  }
  return data;
}

// When the user hasn't sent the request yet but wants to preview, build a
// placeholder sample from the schema itself so the preview isn't empty.
function synthesizeFromSchema(schema) {
  if (!schema || typeof schema !== 'object') return null;
  const t = schema.type;
  if (t === 'object') {
    const out = {};
    for (const [k, child] of Object.entries(schema.properties || {})) {
      out[k] = synthesizeFromSchema(child);
    }
    return out;
  }
  if (t === 'array') return schema.items ? [synthesizeFromSchema(schema.items)] : [];
  if (t === 'string') return '<string>';
  if (t === 'integer') return 0;
  if (t === 'number') return 0;
  if (t === 'boolean') return false;
  if (t === 'null') return null;
  return null;
}
