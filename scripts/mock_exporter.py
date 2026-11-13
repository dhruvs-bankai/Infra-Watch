"""Test-only synthetic exporter for the live integration verification.

Serves Node Exporter and cAdvisor metric shapes on ports 9100 and 8080 so the
API, discovery publisher and Prometheus scrape jobs can be exercised without
requiring a second physical machine. This file is never used by the application.
"""

from __future__ import annotations

import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

START = time.time()


def node_metrics() -> str:
    return f"""# HELP node_cpu_seconds_total Seconds the CPUs spent in each mode.
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{{cpu="0",mode="idle"}} {1000 + time.time() - START:.3f}
node_cpu_seconds_total{{cpu="0",mode="user"}} {200 + (time.time() - START) * 0.2:.3f}
node_cpu_seconds_total{{cpu="0",mode="system"}} {100 + (time.time() - START) * 0.1:.3f}
# HELP node_memory_MemTotal_bytes Memory information field MemTotal_bytes.
# TYPE node_memory_MemTotal_bytes gauge
node_memory_MemTotal_bytes 8589934592
# HELP node_memory_MemAvailable_bytes Memory information field MemAvailable_bytes.
# TYPE node_memory_MemAvailable_bytes gauge
node_memory_MemAvailable_bytes 6442450944
# HELP node_filesystem_size_bytes Filesystem size in bytes.
# TYPE node_filesystem_size_bytes gauge
node_filesystem_size_bytes{{device="/dev/sda1",mountpoint="/",fstype="ext4"}} 107374182400
# HELP node_filesystem_avail_bytes Filesystem space available to non-root users in bytes.
# TYPE node_filesystem_avail_bytes gauge
node_filesystem_avail_bytes{{device="/dev/sda1",mountpoint="/",fstype="ext4"}} 64424509440
node_boot_time_seconds {time.time() - 86400:.0f}
node_load1 0.42
node_load5 0.38
node_load15 0.31
node_network_receive_bytes_total{{device="eth0"}} {5_000_000 + (time.time() - START) * 1024:.0f}
node_network_transmit_bytes_total{{device="eth0"}} {3_000_000 + (time.time() - START) * 512:.0f}
node_network_receive_packets_total{{device="eth0"}} {40_000 + (time.time() - START) * 5:.0f}
node_network_transmit_packets_total{{device="eth0"}} {30_000 + (time.time() - START) * 4:.0f}
node_network_receive_errs_total{{device="eth0"}} 0
node_network_transmit_errs_total{{device="eth0"}} 0
node_disk_read_bytes_total{{device="sda"}} {1_000_000 + (time.time() - START) * 256:.0f}
node_disk_written_bytes_total{{device="sda"}} {2_000_000 + (time.time() - START) * 128:.0f}
"""


def cadvisor_metrics() -> str:
    offset = time.time() - START
    return f"""# HELP container_cpu_usage_seconds_total Cumulative cpu time consumed in seconds.
# TYPE container_cpu_usage_seconds_total counter
container_cpu_usage_seconds_total{{name="web",image="nginx:1.27",id="aaa"}} {100 + offset * 0.35:.3f}
container_cpu_usage_seconds_total{{name="worker",image="example/worker:1.2",id="bbb"}} {80 + offset * 0.05:.3f}
# HELP container_memory_working_set_bytes Current working set in bytes.
# TYPE container_memory_working_set_bytes gauge
container_memory_working_set_bytes{{name="web",image="nginx:1.27",id="aaa"}} 52428800
container_memory_working_set_bytes{{name="worker",image="example/worker:1.2",id="bbb"}} 134217728
# HELP container_spec_memory_limit_bytes Memory limit for the container.
# TYPE container_spec_memory_limit_bytes gauge
container_spec_memory_limit_bytes{{name="web",image="nginx:1.27",id="aaa"}} 268435456
container_spec_memory_limit_bytes{{name="worker",image="example/worker:1.2",id="bbb"}} 0
# HELP container_fs_usage_bytes Number of bytes that are consumed by the container on this filesystem.
# TYPE container_fs_usage_bytes gauge
container_fs_usage_bytes{{name="web",image="nginx:1.27",id="aaa"}} 10485760
# HELP container_network_receive_bytes_total Cumulative count of bytes received.
# TYPE container_network_receive_bytes_total counter
container_network_receive_bytes_total{{name="web",id="aaa"}} {1_000_000 + offset * 2048:.0f}
container_network_transmit_bytes_total{{name="web",id="aaa"}} {500_000 + offset * 1024:.0f}
# HELP container_start_time_seconds Start time of the container since unix epoch in seconds.
# TYPE container_start_time_seconds gauge
container_start_time_seconds{{name="web",id="aaa"}} {time.time() - 3600:.0f}
container_start_time_seconds{{name="worker",id="bbb"}} {time.time() - 7200:.0f}
# HELP container_last_seen Last time a container was seen by the exporter.
# TYPE container_last_seen gauge
container_last_seen{{name="web",image="nginx:1.27",id="aaa"}} {time.time():.0f}
container_last_seen{{name="worker",image="example/worker:1.2",id="bbb"}} {time.time():.0f}
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/metrics":
            self.send_response(200 if self.path in {"/", "/healthz"} else 404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        payload = (cadvisor_metrics() if self.server.server_port == 8080 else node_metrics()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        return


def serve(port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=serve, args=(9100,), daemon=True).start()
    serve(8080)
