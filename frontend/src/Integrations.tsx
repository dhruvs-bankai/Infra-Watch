import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, Boxes, Check, ChevronDown, CircleHelp, Container as ContainerIcon,
  Cpu, ExternalLink, Globe, LoaderCircle, MemoryStick, Network, Plus, RefreshCw, ShieldCheck,
  Trash2, X,
} from 'lucide-react'
import { api, formatBytes, formatNumber, Server } from './api'

type CatalogItem = {
  kind: string; title: string; category: string; scope: string; description: string
  default_port: number | null; metrics_path: string; job: string; install_url: string
  requires: string; dashboard: string; metrics: string[]; operating_systems: string[]
  install_steps: string[]; configured_count: number; reachable_count: number
}
type Instance = {
  id: string; server_id: string; server_name: string; kind: string; title: string
  port: number; metrics_path: string; enabled: boolean; status: string
  status_message: string; scrape_status: string; last_checked_at: string | null
}
type CatalogResponse = { items: CatalogItem[]; instances: Instance[]; endpoint_count: number }
type EndpointMonitor = {
  id: string; name: string; kind: string; target: string; port: number | null; path: string
  expected_status: number; interval_seconds: number; timeout_seconds: number
  environment: string; description: string; server_id: string | null; server_name: string
  enabled: boolean; probe_status: string; latency_seconds: number | null
  ssl_expires_at: string | null
}
type EndpointResponse = { items: EndpointMonitor[]; total: number; healthy: number; failing: number }
type ContainerRow = {
  server_id: string; server_name: string; name: string; image?: string
  cpu?: number; memory?: number; limit?: number; filesystem?: number
  network_in?: number; network_out?: number; restarts?: number
}
type ContainerResponse = { total_containers: number; items: ContainerRow[]; as_of: string }

function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  if (!message) return null
  return <div className="error-banner" role="alert"><AlertTriangle size={17} /><div><strong>Unable to load integration data</strong><span>{message}</span></div>{onRetry && <button className="text-button" onClick={onRetry}>Retry</button>}</div>
}

function ProbePill({ status }: { status: string }) {
  const label = status === 'healthy' || status === 'up' || status === 'reachable' ? 'Healthy' : status === 'failing' || status === 'down' || status === 'unreachable' ? 'Failing' : status === 'disabled' ? 'Disabled' : 'No data'
  const tone = status === 'healthy' || status === 'up' || status === 'reachable' ? 'online' : status === 'failing' || status === 'down' || status === 'unreachable' ? 'offline' : 'unknown'
  return <span className={`status-pill ${tone}`}><i />{label}</span>
}

function IntegrationCatalogCard({ item, onAttach, onNotice }: { item: CatalogItem; onAttach: () => void; onNotice: (msg: string) => void }) {
  const [open, setOpen] = useState(false)
  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); onNotice('Copied to clipboard.') } catch { onNotice('Clipboard unavailable. Select the text to copy.') }
  }
  return <article className="panel integration-card">
    <div className="integration-card-top">
      <span className={`integration-glyph ${item.scope}`}>{item.category === 'Containers' ? <ContainerIcon size={17} /> : item.category === 'Logs' ? <Activity size={17} /> : item.category === 'Availability' ? <Globe size={17} /> : item.category === 'Windows' ? <Boxes size={17} /> : <Cpu size={17} />}</span>
      <div><h3>{item.title}</h3><span className="integration-category">{item.category} · {item.scope === 'server' ? 'Per server' : 'Platform'}</span></div>
      {item.scope === 'platform' ? <span className="chip included">Included</span> : <span className={`chip ${item.configured_count ? 'configured' : ''}`}>{item.configured_count} configured</span>}
    </div>
    <p>{item.description}</p>
    <div className="integration-meta"><span><b>{item.default_port ? `TCP ${item.default_port}` : 'Bundled'}</b> default port</span><span><b>{item.reachable_count}</b> reachable now</span><span><b>{item.operating_systems.join(', ')}</b> supported</span></div>
    <p className="integration-requires"><ShieldCheck size={13} />{item.requires}</p>
    <ul className="integration-metrics">{item.metrics.slice(0, 5).map(metric => <li key={metric}>{metric}</li>)}</ul>
    <div className="integration-actions">
      {item.scope === 'server' && <button className="button button-primary button-small" onClick={onAttach}><Plus size={14} /> Attach to server</button>}
      <button className="button button-secondary button-small" onClick={() => setOpen(value => !value)}>{open ? 'Hide guide' : 'Install guide'} <ChevronDown size={14} /></button>
      <a className="button button-secondary button-small" href={item.install_url} target="_blank" rel="noreferrer">Official downloads <ExternalLink size={13} /></a>
    </div>
    {open && <div className="integration-guide"><div className="guide-mini-head"><strong>Manual installation</strong><span>Run these steps on the monitored host. InfraWatch never connects over SSH or installs agents.</span></div>{item.install_steps.map((step, index) => <div className="guide-mini-step" key={step}><span>{String(index + 1).padStart(2, '0')}</span><p>{step}</p><button className="icon-button" aria-label="Copy step" onClick={() => void copy(step)}><Check size={12} /></button></div>)}</div>}
  </article>
}

function AttachDialog({ item, servers, onClose, onSaved }: { item: CatalogItem; servers: Server[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [serverId, setServerId] = useState(servers[0]?.id ?? '')
  const [port, setPort] = useState(String(item.default_port ?? 9100))
  const [metricsPath, setMetricsPath] = useState(item.metrics_path)
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      await api(`/servers/${serverId}/integrations`, { method: 'POST', body: JSON.stringify({ kind: item.kind, port: Number(port), metrics_path: metricsPath, enabled: true }) })
      await onSaved()
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to attach integration') } finally { setBusy(false) }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <form className="server-dialog" role="dialog" aria-modal="true" aria-labelledby="attach-title" onSubmit={submit}>
      <div className="dialog-heading"><div><div className="eyebrow">{item.category.toUpperCase()}</div><h2 id="attach-title">Attach {item.title}</h2><p>InfraWatch checks reachability and expected metrics before enabling collection.</p></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={19} /></button></div>
      {error && <div className="form-error" role="alert"><AlertTriangle size={16} />{error}</div>}
      <label>Server<select aria-label="Server" value={serverId} onChange={e => setServerId(e.target.value)} required>{servers.map(server => <option key={server.id} value={server.id}>{server.name} · {server.host}</option>)}</select></label>
      <div className="form-row"><label>Exporter port<input required type="number" min="1" max="65535" value={port} onChange={e => setPort(e.target.value)} /></label><label>Metrics path<input required value={metricsPath} onChange={e => setMetricsPath(e.target.value)} /></label></div>
      <div className="modal-policy"><CircleHelp size={16} /><span>If the check fails, install the exporter using the official guide first, verify its port is reachable only from the InfraWatch monitoring network, then retry.</span></div>
      <div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" disabled={busy || !serverId}>{busy ? <><LoaderCircle className="spin" size={15} /> Checking endpoint</> : 'Attach and verify'}</button></div>
    </form>
  </div>
}

export function IntegrationsPage({ onNotice }: { onNotice: (msg: string) => void }) {
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null)
  const [servers, setServers] = useState<Server[]>([])
  const [containers, setContainers] = useState<ContainerResponse | null>(null)
  const [containerError, setContainerError] = useState('')
  const [error, setError] = useState(''); const [loading, setLoading] = useState(true)
  const [attaching, setAttaching] = useState<CatalogItem | null>(null)
  const [busyId, setBusyId] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [catalogData, serverData] = await Promise.all([api<CatalogResponse>('/integrations'), api<{ items: Server[] }>('/servers?limit=200')])
      setCatalog(catalogData); setServers(serverData.items)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load integrations') }
    finally { setLoading(false) }
    try { setContainers(await api<ContainerResponse>('/metrics/containers')); setContainerError('') }
    catch (e) { setContainerError(e instanceof Error ? e.message : 'Container metrics are unavailable') }
  }, [])
  useEffect(() => { void load() }, [load])

  async function checkInstance(instance: Instance) {
    setBusyId(instance.id)
    try { const updated = await api<Instance>(`/integrations/${instance.id}/check`, { method: 'POST' }); onNotice(`${updated.title} on ${instance.server_name}: ${updated.status}.`); await load() }
    catch (e) { onNotice(e instanceof Error ? e.message : 'Reachability check failed') } finally { setBusyId('') }
  }
  async function toggleInstance(instance: Instance) {
    setBusyId(instance.id)
    try { await api(`/integrations/${instance.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !instance.enabled }) }); onNotice(instance.enabled ? 'Integration disabled and removed from scrape targets.' : 'Integration enabled and added to scrape targets.'); await load() }
    catch (e) { onNotice(e instanceof Error ? e.message : 'Unable to update integration') } finally { setBusyId('') }
  }
  async function removeInstance(instance: Instance) {
    if (!window.confirm(`Remove ${instance.title} from ${instance.server_name}?`)) return
    setBusyId(instance.id)
    try { await api(`/integrations/${instance.id}`, { method: 'DELETE' }); onNotice('Integration removed and discovery updated.'); await load() }
    catch (e) { onNotice(e instanceof Error ? e.message : 'Unable to remove integration') } finally { setBusyId('') }
  }

  const serverItems = catalog?.items.filter(item => item.scope === 'server') ?? []
  const platformItems = catalog?.items.filter(item => item.scope === 'platform') ?? []
  const instances = catalog?.instances ?? []
  const reachable = instances.filter(instance => instance.scrape_status === 'up').length
  const containerRows = containers?.items ?? []

  return <>
    <div className="page-heading">
      <div><div className="eyebrow">OBSERVABILITY INTEGRATIONS</div><h1>Extend what you can see</h1><p>Containers, external endpoints, processes, Windows hosts and centralized logs — all through exporters you control.</p></div>
      <div className="heading-actions"><button className="button button-secondary" onClick={() => void load()}><RefreshCw size={15} className={loading ? 'spin' : ''} /> Refresh</button><a className="button button-secondary" href="/grafana/" target="_blank" rel="noreferrer">Open Grafana <ExternalLink size={14} /></a></div>
    </div>
    <ErrorBanner message={error} onRetry={() => void load()} />
    <div className="stat-grid">
      <div className="stat-card"><div className="stat-icon blue"><Boxes /></div><span className="stat-label">Configured integrations</span><strong className="stat-value">{formatNumber(instances.length, 0)}</strong><span className="stat-detail">{reachable} reporting to Prometheus</span></div>
      <div className="stat-card"><div className="stat-icon green"><Activity /></div><span className="stat-label">Exporter targets</span><strong className="stat-value">{formatNumber(instances.filter(i => i.enabled).length + servers.length, 0)}</strong><span className="stat-detail">Node, container, process and Windows</span></div>
      <div className="stat-card"><div className="stat-icon violet"><Globe /></div><span className="stat-label">Endpoint probes</span><strong className="stat-value">{formatNumber(catalog?.endpoint_count ?? 0, 0)}</strong><span className="stat-detail">HTTP, TCP, DNS and ICMP</span></div>
      <div className="stat-card"><div className="stat-icon amber"><ContainerIcon /></div><span className="stat-label">Containers observed</span><strong className="stat-value">{containers ? formatNumber(containers.total_containers, 0) : '—'}</strong><span className="stat-detail">{containerError ? 'cAdvisor not reporting' : 'Live from cAdvisor'}</span></div>
    </div>

    <section className="panel integration-section">
      <div className="panel-heading"><div><h2>Server integrations</h2><p>Install the exporter on your host, then attach its validated endpoint to a registered server.</p></div><span className="chip included">Self-managed install</span></div>
      {loading && !catalog ? <div className="loading-panel"><LoaderCircle className="spin" size={18} /> Loading integration catalog…</div> : <div className="integration-grid">{serverItems.map(item => <IntegrationCatalogCard key={item.kind} item={item} onNotice={onNotice} onAttach={() => setAttaching(item)} />)}</div>}
      {servers.length === 0 && !loading && <div className="data-note"><AlertTriangle size={14} /> Register a server first; integrations attach to an existing inventory entry.</div>}
    </section>

    <section className="panel">
      <div className="panel-heading"><div><h2>Configured exporters</h2><p>Live scrape state comes from Prometheus; the last check records endpoint reachability.</p></div></div>
      {instances.length === 0 ? <div className="empty-panel"><span><Boxes size={18} /></span><strong>No integrations configured</strong><p>Attach cAdvisor, process-exporter or windows_exporter to start collecting additional signals.</p></div> : <div className="table-wrap"><table><thead><tr><th>INTEGRATION</th><th>SERVER</th><th>ENDPOINT</th><th>SCRAPE</th><th>LAST CHECK</th><th>ENABLED</th><th /></tr></thead><tbody>{instances.map(instance => <tr key={instance.id}><td><strong>{instance.title}</strong><small className="row-sub">{instance.kind}</small></td><td>{instance.server_name}</td><td><code>{instance.port}{instance.metrics_path}</code></td><td><ProbePill status={instance.scrape_status} /></td><td><ProbePill status={instance.status} />{instance.last_checked_at && <small className="row-sub">{new Date(instance.last_checked_at).toLocaleString()}</small>}</td><td><button className={`toggle ${instance.enabled ? 'on' : ''}`} aria-label={instance.enabled ? 'Disable integration' : 'Enable integration'} aria-pressed={instance.enabled} onClick={() => void toggleInstance(instance)} disabled={busyId === instance.id}><span /></button></td><td className="row-actions"><button className="icon-button" aria-label={`Check ${instance.title}`} title="Check reachability" onClick={() => void checkInstance(instance)} disabled={busyId === instance.id}>{busyId === instance.id ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}</button><button className="icon-button" aria-label={`Remove ${instance.title}`} title="Remove integration" onClick={() => void removeInstance(instance)}><Trash2 size={14} /></button></td></tr>)}</tbody></table></div>}
    </section>

    <section className="panel">
      <div className="panel-heading"><div><h2>Container inventory</h2><p>Live container resource use from cAdvisor. Empty results mean cAdvisor is not yet scraping.</p></div><a className="text-button" href="/grafana/d/docker-containers" target="_blank" rel="noreferrer">Container dashboard <span>→</span></a></div>
      {containerError ? <div className="inline-error" role="alert">{containerError}</div> : !containers ? <div className="loading-panel"><LoaderCircle className="spin" size={18} /> Loading container metrics…</div> : containerRows.length === 0 ? <div className="empty-panel"><span><ContainerIcon size={18} /></span><strong>No container metrics</strong><p>Attach cAdvisor to a Docker host. The bundled cAdvisor service monitors the InfraWatch host itself once registered.</p></div> : <div className="table-wrap"><table><thead><tr><th>CONTAINER</th><th>HOST</th><th>CPU</th><th>MEMORY</th><th>LIMIT</th><th>NETWORK IN / OUT</th><th>RESTARTS 1H</th></tr></thead><tbody>{containerRows.slice(0, 25).map(row => <tr key={`${row.server_id}-${row.name}`}><td><span className="server-name"><span className="server-glyph"><ContainerIcon size={14} /></span><span><strong>{row.name}</strong><small>{row.image || 'image unavailable'}</small></span></span></td><td>{row.server_name}</td><td>{row.cpu == null ? <span className="unavailable">—</span> : `${formatNumber(row.cpu)}%`}</td><td>{row.memory == null ? <span className="unavailable">—</span> : formatBytes(row.memory)}</td><td>{row.limit ? formatBytes(row.limit) : <span className="unavailable">No limit</span>}</td><td>{row.network_in == null && row.network_out == null ? <span className="unavailable">—</span> : `${formatBytes(row.network_in ?? 0)}/s · ${formatBytes(row.network_out ?? 0)}/s`}</td><td>{row.restarts ?? <span className="unavailable">—</span>}</td></tr>)}</tbody></table></div>}
    </section>

    <section className="panel integration-section">
      <div className="panel-heading"><div><h2>Platform services</h2><p>Bundled with InfraWatch. No separate installation is required for these components.</p></div></div>
      <div className="integration-grid">{platformItems.map(item => <IntegrationCatalogCard key={item.kind} item={item} onNotice={onNotice} onAttach={() => undefined} />)}</div>
    </section>

    <div className="data-note"><ShieldCheck size={14} /> Integrations are read-only metric endpoints. InfraWatch never stores credentials, connects over SSH, or executes commands on monitored hosts.</div>
    {attaching && <AttachDialog item={attaching} servers={servers} onClose={() => setAttaching(null)} onSaved={async () => { setAttaching(null); onNotice('Integration verified and added to Prometheus discovery.'); await load() }} />}
  </>
}

function EndpointDialog({ servers, onClose, onSaved }: { servers: Server[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [form, setForm] = useState({ name: '', kind: 'https', target: 'https://', port: '443', interval_seconds: '30', environment: 'production', description: '', server_id: '' })
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  function update(key: keyof typeof form, value: string) { setForm(current => ({ ...current, [key]: value })) }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('')
    const payload: Record<string, unknown> = {
      name: form.name, kind: form.kind, target: form.target.trim(), interval_seconds: Number(form.interval_seconds),
      environment: form.environment, description: form.description, enabled: true,
      ...(form.kind === 'tcp' ? { port: Number(form.port) } : {}),
      ...(form.server_id ? { server_id: form.server_id } : {}),
    }
    try { await api('/endpoints', { method: 'POST', body: JSON.stringify(payload) }); await onSaved() }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to add endpoint') } finally { setBusy(false) }
  }
  const targetHint = form.kind === 'tcp' ? '10.20.4.10 or api.example.com' : form.kind === 'dns' ? 'example.com' : form.kind === 'icmp' ? '10.20.4.10' : 'https://api.example.com/health'
  return <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <form className="server-dialog" role="dialog" aria-modal="true" aria-labelledby="endpoint-title" onSubmit={submit}>
      <div className="dialog-heading"><div><div className="eyebrow">EXTERNAL AVAILABILITY</div><h2 id="endpoint-title">Monitor an endpoint</h2><p>Blackbox Exporter probes from inside the monitoring network.</p></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={19} /></button></div>
      {error && <div className="form-error" role="alert"><AlertTriangle size={16} />{error}</div>}
      <label>Friendly name<input required minLength={2} maxLength={100} value={form.name} onChange={e => update('name', e.target.value)} placeholder="Public API health check" /></label>
      <div className="form-row"><label>Check type<select aria-label="Check type" value={form.kind} onChange={e => update('kind', e.target.value)}><option value="https">HTTPS URL</option><option value="http">HTTP URL</option><option value="tcp">TCP port</option><option value="dns">DNS resolution</option><option value="icmp">ICMP ping</option></select></label>{form.kind === 'tcp' && <label>Port<input required type="number" min="1" max="65535" value={form.port} onChange={e => update('port', e.target.value)} /></label>}</div>
      <label>{form.kind === 'dns' ? 'Domain name' : form.kind === 'icmp' ? 'Host or IP address' : form.kind === 'tcp' ? 'Host' : 'URL'}<input required value={form.target} onChange={e => update('target', e.target.value)} placeholder={targetHint} /></label>
      <div className="form-row"><label>Environment<select aria-label="Endpoint environment" value={form.environment} onChange={e => update('environment', e.target.value)}><option value="production">Production</option><option value="staging">Staging</option><option value="development">Development</option></select></label><label>Check interval<select aria-label="Check interval" value={form.interval_seconds} onChange={e => update('interval_seconds', e.target.value)}><option value="15">15 seconds</option><option value="30">30 seconds</option><option value="60">1 minute</option><option value="120">2 minutes</option></select></label></div>
      <label>Related server <span className="optional">OPTIONAL</span><select aria-label="Related server" value={form.server_id} onChange={e => update('server_id', e.target.value)}><option value="">No server association</option>{servers.map(server => <option key={server.id} value={server.id}>{server.name}</option>)}</select></label>
      <label>Description <span className="optional">OPTIONAL</span><textarea rows={2} maxLength={1000} value={form.description} onChange={e => update('description', e.target.value)} placeholder="What does a failure mean for users?" /></label>
      <div className="modal-policy"><ShieldCheck size={16} /><span>Loopback, link-local and cloud metadata addresses are rejected. Private targets must match MONITORING_ALLOWED_CIDRS. Credentials are never accepted in URLs.</span></div>
      <div className="dialog-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" disabled={busy}>{busy ? <><LoaderCircle className="spin" size={15} /> Validating target</> : 'Start monitoring'}</button></div>
    </form>
  </div>
}

export function EndpointsPage({ onNotice }: { onNotice: (msg: string) => void }) {
  const [data, setData] = useState<EndpointResponse | null>(null)
  const [servers, setServers] = useState<Server[]>([])
  const [error, setError] = useState(''); const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all'); const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false); const [busyId, setBusyId] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [endpoints, inventory] = await Promise.all([api<EndpointResponse>('/endpoints'), api<{ items: Server[] }>('/servers?limit=200')])
      setData(endpoints); setServers(inventory.items)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load endpoints') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const rows = useMemo(() => (data?.items ?? []).filter(item => (filter === 'all' || item.kind === filter) && `${item.name} ${item.target}`.toLowerCase().includes(search.toLowerCase())), [data, filter, search])

  async function check(item: EndpointMonitor) {
    setBusyId(item.id)
    try { const result = await api<EndpointMonitor>(`/endpoints/${item.id}/check`, { method: 'POST' }); onNotice(`${result.name}: ${result.probe_status}.`); await load() }
    catch (e) { onNotice(e instanceof Error ? e.message : 'Unable to read probe state') } finally { setBusyId('') }
  }
  async function remove(item: EndpointMonitor) {
    if (!window.confirm(`Stop monitoring ${item.name}?`)) return
    setBusyId(item.id)
    try { await api(`/endpoints/${item.id}`, { method: 'DELETE' }); onNotice('Endpoint removed and discovery updated.'); await load() }
    catch (e) { onNotice(e instanceof Error ? e.message : 'Unable to remove endpoint') } finally { setBusyId('') }
  }

  return <>
    <div className="page-heading">
      <div><div className="eyebrow">EXTERNAL AVAILABILITY</div><h1>Endpoints that matter</h1><p>Probe websites, APIs, TCP ports, DNS names and hosts with Blackbox Exporter.</p></div>
      <div className="heading-actions"><a className="button button-secondary" href="/grafana/d/network-endpoints" target="_blank" rel="noreferrer">Endpoint dashboard <ExternalLink size={14} /></a><button className="button button-primary" onClick={() => setAdding(true)}><Plus size={16} /> Monitor endpoint</button></div>
    </div>
    <ErrorBanner message={error} onRetry={() => void load()} />
    <div className="inventory-summary"><span><b>{data?.total ?? 0}</b> monitored</span><span><i className="legend-online" />{data?.healthy ?? 0} healthy</span><span><i className="legend-offline" />{data?.failing ?? 0} failing</span><span><i className="legend-unknown" />{Math.max(0, (data?.total ?? 0) - (data?.healthy ?? 0) - (data?.failing ?? 0))} awaiting samples</span></div>
    <section className="panel inventory-panel">
      <div className="inventory-toolbar">
        <label className="search-field"><Globe size={16} /><input placeholder="Search endpoint name or target…" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <select aria-label="Filter by check type" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All check types</option><option value="https">HTTPS</option><option value="http">HTTP</option><option value="tcp">TCP</option><option value="dns">DNS</option><option value="icmp">ICMP</option></select>
      </div>
      {loading && !data ? <div className="loading-panel"><LoaderCircle className="spin" size={18} /> Loading endpoint probes…</div> : rows.length === 0 ? <div className="empty-panel"><span><Globe size={18} /></span><strong>{data?.total ? 'No matching endpoints' : 'No endpoints monitored yet'}</strong><p>{data?.total ? 'Adjust the search or check-type filter.' : 'Add a website, API, TCP port, DNS name or host to get availability, latency and TLS expiry signals.'}</p></div> : <div className="table-wrap"><table><thead><tr><th>ENDPOINT</th><th>TYPE</th><th>TARGET</th><th>ENVIRONMENT</th><th>STATUS</th><th>LATENCY</th><th>TLS EXPIRY</th><th /></tr></thead><tbody>{rows.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small className="row-sub">{item.server_name || item.description || `Every ${item.interval_seconds}s`}</small></td><td><span className={`chip ${item.kind}`}>{item.kind.toUpperCase()}</span></td><td><code className="target-code">{item.target}</code></td><td><span className={`env-badge ${item.environment}`}>{item.environment}</span></td><td><ProbePill status={item.probe_status} /></td><td>{item.latency_seconds == null ? <span className="unavailable">—</span> : `${formatNumber(item.latency_seconds * 1000, 0)} ms`}</td><td>{item.ssl_expires_at ? <span className={new Date(item.ssl_expires_at).getTime() - Date.now() < 21 * 86400000 ? 'text-warning' : ''}>{new Date(item.ssl_expires_at).toLocaleDateString()}</span> : <span className="unavailable">—</span>}</td><td className="row-actions"><button className="icon-button" aria-label={`Check ${item.name}`} title="Read latest probe result" onClick={() => void check(item)} disabled={busyId === item.id}>{busyId === item.id ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}</button><button className="icon-button" aria-label={`Remove ${item.name}`} title="Remove endpoint" onClick={() => void remove(item)}><Trash2 size={14} /></button></td></tr>)}</tbody></table></div>}
      <div className="data-note integration-note"><Network size={14} /> Probe results are Prometheus samples, not stored test fixtures. New endpoints show “No data” until the first scrape completes.</div>
    </section>
    {adding && <EndpointDialog servers={servers} onClose={() => setAdding(false)} onSaved={async () => { setAdding(false); onNotice('Endpoint added and Prometheus discovery updated.'); await load() }} />}
  </>
}

export function ServerIntegrationsPanel({ server }: { server: Server }) {
  const [items, setItems] = useState<Instance[]>([])
  const [error, setError] = useState(''); const [loading, setLoading] = useState(true)
  useEffect(() => {
    setLoading(true); setError('')
    api<Instance[]>(`/servers/${server.id}/integrations`).then(setItems).catch(e => setError(e instanceof Error ? e.message : 'Unable to load integrations')).finally(() => setLoading(false))
  }, [server.id])
  return <section className="panel">
    <div className="panel-heading"><div><h2>Monitoring integrations</h2><p>Additional exporters attached to this server</p></div><a className="text-button" href="/grafana/d/infrawatch-integrations" target="_blank" rel="noreferrer">Integration dashboard <span>→</span></a></div>
    {loading ? <div className="loading-panel"><LoaderCircle className="spin" size={16} /> Loading integrations…</div> : error ? <div className="inline-error" role="alert">{error}</div> : items.length === 0 ? <div className="empty-panel compact-empty"><span><MemoryStick size={18} /></span><strong>No additional exporters</strong><p>Use the Integrations page to attach cAdvisor, process-exporter or windows_exporter to this server.</p></div> : <div className="detail-integrations">{items.map(item => <div className="detail-integration" key={item.id}><div><strong>{item.title}</strong><span>Port {item.port}{item.metrics_path}</span></div><ProbePill status={item.scrape_status} /><ProbePill status={item.enabled ? item.status : 'disabled'} /></div>)}</div>}
  </section>
}
