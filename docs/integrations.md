# Monitoring integrations — plain-language guide

InfraWatch began with one signal: Linux server metrics from Node Exporter. This guide covers the extra
monitoring you can turn on: Docker containers, websites and APIs, individual processes, Windows servers,
and centralized logs.

You do **not** need to be a monitoring expert. Each section follows the same three steps:

1. **Install** the small helper program (called an *exporter*) on the machine you want to watch.
2. **Open a port** so only your InfraWatch machine can read it.
3. **Add it in InfraWatch** so Prometheus starts collecting it automatically.

> **InfraWatch never installs anything for you.** It does not use SSH, passwords, private keys, or remote
> commands. You stay in control of every machine. All download links below are the official project pages.

---

## 1. What each integration does

| Integration | What you can see | Default port | Where it runs |
| --- | --- | --- | --- |
| **Node Exporter** | CPU, memory, disk, network, load, uptime | 9100 | Every Linux server (already supported) |
| **cAdvisor** | CPU, memory, disk I/O, filesystem and network use **per container**, plus limits | 8080 | Any Docker host |
| **Blackbox Exporter** | Website/API up or down, response time, TCP ports, DNS, ping, TLS expiry | 9115 | Bundled with InfraWatch |
| **process-exporter** | CPU and memory for named process groups, process count, restarts | 9256 | Linux servers |
| **windows_exporter** | CPU, memory, disks, network, services, uptime | 9182 | Windows Server / Windows 10–11 |
| **Loki + Grafana Alloy** | Centralized logs with server, service and severity filters | 3100 (internal) | Bundled with InfraWatch |

The first four exporters expose a `/metrics` page. Prometheus reads that page on a schedule. Grafana and
the InfraWatch UI read from Prometheus.

---

## 2. Before you start

### Allow the ports in InfraWatch

InfraWatch refuses exporter ports it has not been told about. The default list already includes
Node Exporter, cAdvisor, windows_exporter and process-exporter:

```env
MONITORING_ALLOWED_PORTS=9100,8080,9182,9256
```

If you use a different port, add it to `.env` and recreate the backend:

```sh
docker compose up -d backend
```

### Allow only your monitoring network

On the monitored machine's firewall, allow the exporter port **only from the InfraWatch host**. Never
expose these ports to the public Internet.

```sh
# Ubuntu/Debian example: replace 10.0.0.5 with your InfraWatch IP
sudo ufw allow from 10.0.0.5 to any port 9100 proto tcp
```

### Check the address policy

InfraWatch only talks to private networks you approved:

```env
MONITORING_ALLOWED_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7
```

Narrow this to your own subnets when you deploy for real.

---

## 3. Docker containers with cAdvisor

**Use it when:** you want to know which container is using the CPU, which one is about to hit its memory
limit, or which one keeps restarting.

### Step 1 — Start cAdvisor on the Docker host

```sh
docker run -d \
  --name cadvisor \
  --restart unless-stopped \
  --privileged \
  --volume=/:/rootfs:ro \
  --volume=/var/run:/var/run:ro \
  --volume=/sys:/sys:ro \
  --volume=/var/lib/docker/:/var/lib/docker:ro \
  --volume=/dev/disk/:/dev/disk:ro \
  --publish=8080:8080 \
  gcr.io/cadvisor/cadvisor:v0.52.1
```

Download page: <https://github.com/google/cadvisor/releases/latest>

**Why `--privileged`?** cAdvisor reads host-level container statistics. It needs broad host access. This
is normal for cAdvisor, but it means you should run it only on hosts you control and keep port 8080
private.

### Step 2 — Verify it is alive

```sh
curl --fail http://127.0.0.1:8080/metrics | grep container_cpu_usage_seconds_total | head
```

You should see lines starting with `container_cpu_usage_seconds_total`.

### Step 3 — Add it in InfraWatch

1. Make sure the Docker host is registered in **Server inventory**.
2. Open **Monitoring integrations**.
3. On **Docker container metrics**, choose **Attach to server**.
4. Pick the host, keep port `8080` and path `/metrics`.
5. Choose **Attach and verify**.

InfraWatch checks that the endpoint returns container metrics before saving. If you see an error, the
message tells you whether the port is closed, the host is not allowed, or the metrics are missing.

### What you get

- **Monitoring integrations → Container inventory** shows the busiest containers with CPU, memory, limit,
  network and restart count.
- The **InfraWatch Docker Container Monitoring** dashboard has CPU, memory, limit, filesystem, network,
  restart and availability panels.
- Alerts fire for cAdvisor outages, container CPU above 85%, memory above 90% of its limit, filesystem
  above 90%, and frequent restarts.

---

## 4. Websites, APIs, TCP ports, DNS and ping with Blackbox Exporter

**Use it when:** you want to know that a website, API, database port, DNS name or host is reachable from
your monitoring network — before your users tell you.

Blackbox Exporter is **already running** inside InfraWatch. You do not install anything.

### Step 1 — Add an endpoint

1. Open **Endpoints** in the sidebar.
2. Choose **Monitor endpoint**.
3. Pick a check type:

| Check | Example target | What it proves |
| --- | --- | --- |
| HTTPS URL | `https://api.example.com/health` | The page answers with a 2xx response and a valid certificate |
| HTTP URL | `http://intranet.example.local/` | An internal web page answers |
| TCP port | host `db.example.com`, port `5432` | Something is accepting connections on that port |
| DNS | `example.com` | The name resolves with a NOERROR answer |
| ICMP | `10.20.4.1` | The host answers a ping |

4. Optionally link the endpoint to a registered server, add a description and choose an interval.
5. Choose **Start monitoring**.

### Step 2 — Wait for the first probe

Probes run every 30 seconds. A brand-new endpoint shows **No data** until the first result arrives, then
turns **Healthy** or **Failing**. This is deliberate: InfraWatch never guesses that a new target is up.

### What you get

- Live status, latency and certificate expiry on the **Endpoints** page.
- The **Network & Endpoint Monitoring** dashboard for success rate, latency, HTTP status codes and DNS
  timing.
- The **SSL Certificate Monitoring** dashboard with days remaining, plus warnings at 21 days and a
  critical alert at 7 days.
- Alerts for failed probes, responses slower than 2 seconds, HTTP 5xx responses and expiring certificates.

### Notes and limits

- HTTPS probes verify certificates. A self-signed certificate will fail the probe — that is intentional.
- URL checks accept `http://` and `https://` only. Credentials in URLs are rejected.
- Public websites require the InfraWatch containers to have outbound Internet access. If your Docker host
  has no Internet access, monitor internal targets instead.
- Loopback (`127.0.0.1`), link-local and cloud metadata addresses are always rejected. Private targets
  must be inside `MONITORING_ALLOWED_CIDRS`.

---

## 5. Processes with process-exporter

**Use it when:** you care about one service, not the whole machine — for example nginx, PostgreSQL or a
Java application.

### Step 1 — Download and install

Download the official release for your architecture from
<https://github.com/ncabatoff/process-exporter/releases/latest>, then:

```sh
tar xzf process-exporter-*.linux-amd64.tar.gz
sudo install -m 0755 process-exporter-*.linux-amd64/process-exporter /usr/local/bin/process-exporter
sudo useradd --system --no-create-home --shell /usr/sbin/nologin process_exporter
sudo mkdir -p /etc/process-exporter
```

### Step 2 — Describe the processes you care about

Create `/etc/process-exporter/config.yml`:

```yaml
process_names:
  - name: nginx
    comm:
      - nginx
  - name: postgres
    comm:
      - postgres
  - name: node-app
    cmdline:
      - '/usr/bin/node .*server.js'
```

`comm` matches the short process name. `cmdline` matches the full command line with a regular
expression. Start with two or three groups; each group becomes a chart series.

### Step 3 — Run it as a service

Create `/etc/systemd/system/process-exporter.service`:

```ini
[Unit]
Description=Prometheus process-exporter
After=network-online.target

[Service]
User=process_exporter
ExecStart=/usr/local/bin/process-exporter --config.path=/etc/process-exporter/config.yml --web.listen-address=:9256
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now process-exporter
curl --fail http://127.0.0.1:9256/metrics | grep namedprocess_namegroup | head
```

### Step 4 — Attach it in InfraWatch

**Monitoring integrations → Process monitoring → Attach to server**, port `9256`, path `/metrics`.

The **Process Monitoring** dashboard shows CPU and memory per group, thread counts, process counts and a
restart/disappearance table. Alerts cover exporter outages, CPU above 80%, a group with zero processes,
and detected restarts.

---

## 6. Windows servers with windows_exporter

**Use it when:** you need the same visibility on Windows Server that you already have on Linux.

### Step 1 — Download the installer

Download the latest `.msi` from
<https://github.com/prometheus-community/windows_exporter/releases/latest>
(for example `windows_exporter-<version>-amd64.msi`).

### Step 2 — Install with the collectors you need

Open **Command Prompt as Administrator** in the download folder:

```bat
msiexec /i windows_exporter-<version>-amd64.msi /qn ENABLED_COLLECTORS=cpu,memory,logical_disk,physical_disk,net,os,service,system,process LISTEN_PORT=9182
```

The installer creates a Windows service named `windows_exporter` that listens on TCP/9182. The
`process` collector adds per-process CPU and memory series; omit it if you do not want that overhead.
The bundled Windows dashboard targets windows_exporter **0.32 or newer** metric names
(`windows_cpu_time_seconds_total`, `windows_memory_physical_total_bytes`).

### Step 3 — Allow only InfraWatch through the firewall

In **PowerShell as Administrator** (replace the address with your InfraWatch host):

```powershell
New-NetFirewallRule -DisplayName "InfraWatch windows_exporter" `
  -Direction Inbound -Protocol TCP -LocalPort 9182 `
  -RemoteAddress 10.0.0.5 -Action Allow
```

### Step 4 — Verify and attach

```powershell
Invoke-WebRequest -UseBasicParsing http://localhost:9182/metrics | Select-Object -ExpandProperty Content | Select-String "windows_cpu_time_seconds_total", "windows_memory_physical_total_bytes"
Get-Service windows_exporter
```

Then in InfraWatch: **Server inventory → Register server**, choose **Windows**, and use the host's IP or DNS
name with port `9182`. The Windows integration is attached automatically. If the host is already
registered, use **Monitoring integrations → Windows server metrics → Attach to server**.

### Step 5 — Prove the dashboard matches the host (optional)

The **Windows Server Details** dashboard reads live metrics, so you can check its numbers against Windows
itself. On the InfraWatch machine:

```powershell
powershell -NoProfile -File scripts/collect_windows_truth.ps1 > host_truth.json
python scripts/verify_windows_dashboard.py --instance Windows-PC --truth host_truth.json
```

Use the server name as `--instance` (the value shown in the dashboard's server selector). The script runs
every panel query through the Grafana Prometheus datasource, then compares total memory, `C:` free space,
uptime, process count and running-service count with the values Windows reported. It exits non-zero if any
panel is empty or any value is outside tolerance.

When you register the host in **Server inventory**, set **Operating system** to **Windows**. InfraWatch then
validates `windows_exporter` on port 9182 (Node Exporter does not exist for Windows) and automatically
attaches the Windows integration, so the host is scraped by the Windows job instead of the Linux job.

The **Windows Server Details** dashboard shows CPU per core, memory, logical disk usage, network, service
state, process count and uptime. Alerts cover exporter outages, CPU above 85%, memory above 90%, volumes
above 90% and services that are not running.

---

## 7. Centralized logs with Loki and Grafana Alloy

**Use it when:** you are tired of opening five terminals to read logs, or you need to find the error that
happened at 02:14 across several services.

Both **Loki** (storage and query) and **Grafana Alloy** (collector) start with the normal Compose stack.
Nothing else to install for the InfraWatch host itself.

### What is collected on the InfraWatch host

- All Docker container logs, including the bundled Nginx access and error logs and the backend/frontend.
- `/var/log/syslog`, `/var/log/auth.log`, `/var/log/messages` when present.
- `/var/log/nginx/*.log` and `/var/log/infrawatch/*.log` when present.

Every stream gets a `host` label from `INFRAWATCH_HOST_LABEL` plus `service`, `container`, `source` and an
extracted `level` when a severity word appears in the message.

### Query logs in Grafana

1. Open **http://localhost:8080/grafana/**.
2. Choose **Explore**, then the **Loki** data source.
3. Use a query such as:

```logql
{host="infrawatch-host", service="nginx"} |= "GET"
{job="docker"} |= "error" != "healthcheck"
{level=~"error|fatal|crit"}
```

Or open the provisioned **InfraWatch Centralized Logs** dashboard, which has server, service and severity
filters plus log-volume panels.

### Add another Linux host

Install Grafana Alloy from <https://grafana.com/docs/alloy/latest/set-up/install/> and use a configuration
like this (`/etc/alloy/config.alloy`), replacing the address with your InfraWatch host:

```alloy
local.file_match "system" {
  path_targets = [
    {__path__ = "/var/log/*.log", job = "system", host = "web-01", source = "file"},
  ]
}

loki.source.file "system" {
  targets    = local.file_match.system.targets
  forward_to = [loki.write.infrawatch.receiver]
}

loki.write "infrawatch" {
  endpoint { url = "http://10.0.0.5:3100/loki/api/v1/push" }
}
```

Port 3100 is **not** published by the default Compose file. To receive logs from other machines, publish
it on a private interface only (`127.0.0.1` is the default for admin ports; use the host's LAN address and
a firewall rule that allows only your servers). Never expose Loki to the public Internet — it has no
built-in authentication in this configuration.

### Retention

Loki keeps **7 days** by default (`monitoring/loki/loki-config.yml`) and stores data on the `loki-data`
volume. Change `retention_period` if your disk budget allows more.

---

## 8. Managing integrations

Open **Monitoring integrations** to see:

- Every supported integration and what it needs.
- How many are configured and how many are reporting to Prometheus right now.
- A **check** button that re-tests the exporter and records the result.
- An enable/disable switch that adds or removes the target from Prometheus discovery.
- The official download link and a copyable installation guide.

**Status meanings**

| Status | Meaning |
| --- | --- |
| **Healthy** | Prometheus scraped the target successfully on the last attempt. |
| **Failing** | The target could not be scraped. Check the exporter and firewall. |
| **No data** | The target is new, disabled, or Prometheus has not scraped it yet. |
| **Reachable / Unreachable** | Result of the last explicit connectivity check from the API. |

Removing an integration or a server removes its targets on the next discovery refresh (up to 30 seconds).
Prometheus may keep showing the last value for a few minutes as a stale series — that is normal and not a
new sample.

---

## 9. Dashboards included

All dashboards are provisioned automatically in the **InfraWatch** Grafana folder:

| Dashboard | Use it for |
| --- | --- |
| InfraWatch Infrastructure Overview | Fleet-wide CPU, memory, disk, network and availability |
| InfraWatch Linux Server Details | One Linux server, with a server picker |
| InfraWatch Windows Server Details | Windows CPU, memory, disks, services and uptime |
| InfraWatch Docker Container Monitoring | Per-container CPU, memory, limits, filesystem, network and restarts |
| InfraWatch Network & Endpoint Monitoring | Probe success, latency, HTTP status, DNS timing |
| InfraWatch Process Monitoring | Process-group CPU, memory, threads and restarts |
| InfraWatch Centralized Logs | Log volume and searchable streams by host/service/severity |
| InfraWatch SSL Certificate Monitoring | Days until expiry and certificate inventory |
| InfraWatch Integration Health | One page showing every exporter and probe |

Each dashboard has a server or endpoint variable at the top, so you can filter without editing queries.

---

## 10. Alert rules added

| Alert | Fires when |
| --- | --- |
| CAdvisorUnavailable | cAdvisor is down for 5 minutes |
| ContainerCPUUsageHigh | A container stays above 85% CPU for 10 minutes |
| ContainerMemoryNearLimit | Working set is above 90% of the container limit |
| ContainerFilesystemUsageHigh | Writable layer is above 90% of its limit |
| ContainerRestartingFrequently | More than 3 restarts in an hour |
| EndpointProbeFailed | Any HTTP/TCP/DNS/ICMP probe fails for 3 minutes |
| EndpointResponseSlow | Probe latency above 2 seconds for 10 minutes |
| EndpointHTTPError | A monitored URL returns 5xx for 5 minutes |
| DNSCheckFailing | A DNS probe has no NOERROR answer for 5 minutes |
| TLSCertificateExpiringSoon | Fewer than 21 days remain |
| TLSCertificateExpiryCritical | Fewer than 7 days remain |
| ProcessExporterUnavailable | process-exporter is down for 5 minutes |
| ProcessCPUUsageHigh | A process group stays above 80% CPU for 15 minutes |
| ProcessGroupMissing | A configured process group has zero processes |
| ProcessGroupRestarted | A process group's oldest start time changed |
| WindowsExporterUnavailable | windows_exporter is down for 5 minutes |
| WindowsCPUUsageHigh | Windows CPU stays above 85% for 10 minutes |
| WindowsMemoryUsageHigh | Windows memory stays above 90% for 10 minutes |
| WindowsDiskSpaceLow | A Windows volume stays above 90% used for 10 minutes |
| WindowsAutomaticServiceNotRunning | An automatic-start Windows service has been stopped for 15 minutes (manual/disabled services are ignored) |

Thresholds live in `monitoring/prometheus/rules/integration-alerts.yml`. Edit them there and restart
Prometheus; the files are validated by `promtool` in CI.

---

## 11. Troubleshooting

**“Port … is not allowed by monitoring policy.”**
Add the port to `MONITORING_ALLOWED_PORTS` in `.env`, then `docker compose up -d backend`.

**“The target resolves outside the approved monitoring networks.”**
The hostname resolved to an address outside `MONITORING_ALLOWED_CIDRS`, or to loopback/link-local/metadata.
Use the host's private address and add that subnet to the allowlist.

**“Could not validate … endpoint did not expose … metrics.”**
The port is open but the wrong program answered, or the exporter is still starting. Open
`http://HOST:PORT/metrics` from the InfraWatch machine and confirm the expected metric names appear.

**Container page is empty.**
cAdvisor must be attached to the server whose name matches the Docker host, and Prometheus needs one
scrape (up to 30 seconds) plus a second sample for CPU rates (about 45 seconds).

**A monitor is stuck on “No data”.**
Check **Endpoints** and Grafana → Explore with `probe_success{instance="Your endpoint name"}`. If the
series exists but is 0, the target is failing. If no series exists, Prometheus has not scraped the probe
job yet — check the target under Prometheus → Status → Targets (available only inside the Compose network).

**DNS checks fail but HTTP and ping work.**
Some container DNS resolvers do not answer the DNS prober's direct queries. Try a fully qualified name,
or run the DNS check from a host that uses your normal DNS server. This is a resolver issue, not an
InfraWatch policy failure.

**Logs do not appear in Grafana.**
Alloy needs the Docker socket. Check `docker compose logs alloy` and confirm the `loki` and `alloy`
services are healthy. Loki label queries are case-sensitive; use `host="infrawatch-host"` exactly.

**Where are secrets?**
Only in the ignored `.env` file. InfraWatch never stores exporter credentials, and integrations accept
only endpoint metadata (host, port, path).
