# InfraWatch: a beginner's setup guide

This guide gets InfraWatch running on one computer. It does not install anything on the servers you want to monitor. You install the small, official Node Exporter program on each Linux server yourself, then add that server in InfraWatch.

## What you need

- A computer that can stay on while you monitor your servers.
- Docker Engine and Docker Compose v2.20 or newer. On Windows or macOS, install Docker Desktop and wait until it says Docker is running. On Linux, install Docker Engine and the Compose plugin.
- About 4 GB of free memory for a small setup, plus disk space for stored metrics.
- Network access from the computer running Docker to each server on the Node Exporter port (usually TCP 9100).

## 1. Download and unpack InfraWatch

On GitHub, open the InfraWatch repository, choose **Code → Download ZIP**, and extract the ZIP file. Open a terminal in the extracted `infrawatch` folder. On Windows, open PowerShell in that folder. Do not run commands from inside the ZIP preview.

## 2. Create private passwords

InfraWatch creates different random passwords for PostgreSQL, Grafana, login tokens, and the one-time administrator setup. The helper makes a private `.env` file for you. It does not send these passwords anywhere.

Linux/macOS:

```sh
sh ./scripts/prepare-env.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare-env.ps1
```

Keep `.env` on this computer. Never upload or email it. It is ignored by Git.

## 3. Start the whole application

From the same `infrawatch` folder, run:

```sh
docker compose up -d --build
```

The first start downloads and builds the web app, API, database, Prometheus, Grafana, and Alertmanager. It can take a few minutes. Check that services started:

```sh
docker compose ps
```

Wait until services show `healthy` (the one-time `discovery-init` service may show `Exited (0)`, which is expected). Then open **http://localhost:8080**.

To stop InfraWatch later, run `docker compose down`. Your data stays in Docker volumes. Do not add `--volumes` unless you deliberately want to delete the saved database and metrics.

## 4. Create your administrator account

The first time you open the page, InfraWatch shows **Set up InfraWatch**. Find the one-time code on the computer running InfraWatch:

1. Open the `.env` file with a text editor.
2. Find `INITIAL_ADMIN_SETUP_TOKEN=...` and copy only the part after `=`.
3. Paste it into the setup page, choose an administrator username and a strong password, then choose **Create administrator**.
4. Save the username and password in your password manager. This setup code works only once.

There is no default account or shared default password.

## 5. Install Node Exporter on each Linux server

InfraWatch does not install software on your servers. For each Linux computer you want to monitor:

1. Open the **Node Exporter guide** inside InfraWatch. It has copy buttons for the Linux installation commands and systemd service.
2. Download Node Exporter only from the [official Prometheus downloads page](https://prometheus.io/download/).
3. Install and start its systemd service on that Linux server.
4. Allow inbound TCP port **9100** from the computer running InfraWatch. Do not open this port to the public Internet.
5. From the InfraWatch computer, check that `http://SERVER-IP:9100/metrics` is reachable. The page should contain text such as `node_cpu_seconds_total`.

The monitored computer must be on a private network that the Docker host can reach. If the server is remote, connect the networks securely (for example, through a VPN) and restrict its firewall to the monitoring host.

## 6. Add your server

In InfraWatch, open **Server inventory → Register server**. Enter a friendly name, the IP address or hostname reachable from Docker, exporter port (usually `9100`), environment, and optional description/tags. Choose **Register server**.

InfraWatch checks the exporter before saving. If the check fails, the screen explains what to verify. After a successful save, Prometheus finds the new target automatically; no restart or configuration edit is needed. Metrics can take about a minute to appear.

The default security policy permits private networks and port 9100. If your exporter uses a different port or network, ask the administrator to update `MONITORING_ALLOWED_PORTS` or `MONITORING_ALLOWED_CIDRS` in `.env`, then run `docker compose up -d` again. Do not allow public or untrusted networks.

## 7. View metrics, Grafana, and alerts

- The InfraWatch **Overview**, **Server details**, and **Metrics explorer** pages show real Prometheus samples. Missing samples say **Unavailable** or **No data**; they are not made up.
- Open Grafana from InfraWatch or visit **http://localhost:8080/grafana/**. Its Prometheus data source and starter dashboard are set up automatically. The Grafana username is in `.env` as `GRAFANA_ADMIN_USER`; its password is `GRAFANA_ADMIN_PASSWORD`.
- Open **Alert activity** in InfraWatch to view active and saved alert events. Prometheus checks exporter availability, CPU above 85%, memory above 90%, and disk usage above 90%.
- A fresh install keeps notifications local, so alerts appear in InfraWatch/Alertmanager but are **not emailed or sent to chat**. To send external notifications, an administrator must add their own webhook/email receiver to `monitoring/alertmanager/alertmanager.yml` and restart Alertmanager with `docker compose restart alertmanager`. Never commit notification passwords or tokens.

## Useful commands

Run from the extracted repository folder:

```sh
docker compose ps                 # See whether services are healthy
docker compose logs -f            # Follow logs (Ctrl+C stops viewing logs)
docker compose logs backend       # Show API/setup problems
docker compose restart            # Restart all services
docker compose down               # Stop services; keep saved data
docker compose up -d              # Start again
```

## If something does not work

- **Docker command not found:** install Docker Desktop or Docker Engine with Compose v2, then open a new terminal.
- **Port 8080 is already in use:** stop the other program using port 8080, then run `docker compose up -d` again.
- **A service is unhealthy:** run `docker compose ps` and `docker compose logs SERVICE_NAME`. Give the first startup a few minutes.
- **Cannot register a server:** check Node Exporter is running, TCP 9100 is allowed by that server's firewall, and you entered an address reachable from Docker. Do not enter `localhost` unless the exporter is actually in InfraWatch's container network.
- **Dashboard has no data:** register a reachable exporter and wait for the first Prometheus scrape. Check **Server inventory** for its status.
- **Want to start fresh:** deleting Compose volumes permanently deletes saved users, servers, alert history, and metrics. Make a backup first.

For network design, secrets, and production limitations, read the [security guide](security.md). For services and environment variables, read [configuration](configuration.md).
