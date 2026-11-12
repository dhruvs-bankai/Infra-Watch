export type User = { id: string; username: string; is_admin: boolean }
export type Server = { id: string; name: string; host: string; exporter_port: number; environment: string; description: string; tags: string[]; created_at: string; updated_at: string }
export type ServerList = { items: Server[]; total: number }
export type MetricPoint = { timestamp: string; value: number }
export type MetricName = 'cpu' | 'cpu_idle' | 'cpu_system' | 'cpu_user' | 'memory' | 'memory_total' | 'memory_used' | 'memory_available' | 'disk' | 'disk_total' | 'disk_used' | 'disk_free' | 'disk_read' | 'disk_write' | 'network_in' | 'network_out' | 'network_packets_in' | 'network_packets_out' | 'network_errors' | 'load' | 'load1' | 'load5' | 'load15' | 'uptime'
export type Series = { server_id: string; server_name: string; metric: string; unit: string; points: MetricPoint[] }
export type Alert = { fingerprint: string; name: string; severity: string; status: string; instance: string; summary: string; description: string; started_at: string; ends_at: string | null }
export type Overview = { total_servers: number; online_servers: number; offline_servers: number; unknown_servers: number; exporter_errors: number; avg_cpu_percent: number | null; avg_memory_percent: number | null; total_disk_bytes: number | null; network_in_bytes_per_second: number | null; network_out_bytes_per_second: number | null; servers: Array<{ id: string; name: string; environment: string; status: string; cpu_percent: number | null; memory_percent: number | null }>; as_of: string }

const TOKEN_KEY = 'infrawatch.session'
export const getToken = () => sessionStorage.getItem(TOKEN_KEY)
export const setToken = (token: string | null) => token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY)

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (options.body) headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try { response = await fetch(`/api/v1${path}`, { ...options, headers }) }
  catch { throw new Error('Cannot reach InfraWatch API. Check that the service is running and try again.') }
  if (!response.ok) {
    if (response.status === 401 && token) setToken(null)
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const formatNumber = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value) ? 'Unavailable' : value.toLocaleString(undefined, { maximumFractionDigits: digits })
export function formatBytes(value: number | null | undefined, suffix = 'B') {
  if (value == null || !Number.isFinite(value)) return 'Unavailable'
  if (value === 0) return `0 ${suffix}`
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']; const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}
