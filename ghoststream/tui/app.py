"""Textual application for the GhostStream management dashboard."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import httpx
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Collapsible, DataTable, Footer, Header, Label, RichLog, Static

from ..app.entrypoints import create_runtime
from ..client import GhostStreamClient
from ..config import get_config
from ..contracts.security import API_KEY_HEADER
from ..server.controllers.websocket import INTERNAL_DASHBOARD_HEADER


class TUILogHandler(logging.Handler):
    """Bridge to send engine logs directly to the TUI RichLog."""

    def __init__(self, log_widget):
        super().__init__()
        self.log_widget = log_widget

    def emit(self, record):
        try:
            msg = self.format(record)
            color = "white"
            if record.levelno >= logging.ERROR:
                color = "bold red"
            elif record.levelno >= logging.WARNING:
                color = "bold yellow"
            elif record.levelno >= logging.INFO:
                color = "cyan"
            self.log_widget.app.call_from_thread(
                self.log_widget.write, Text(msg, style=color)
            )
        except Exception:
            pass


class GhostStreamTUI(App):
    """GhostStream Management Dashboard."""

    CSS = """
    Screen {
        background: #020617;
        color: #94a3b8;
    }

    Header {
        background: #1e293b;
        color: #38bdf8;
        text-style: bold;
        border-bottom: solid #0ea5e9;
    }

    Footer {
        background: #1e293b;
        color: #64748b;
    }

    #main-container {
        layout: horizontal;
        height: 100%;
        width: 100%;
    }

    #sidebar {
        width: 30;
        height: 100%;
        border-right: panel #1e293b;
        background: #0f172a;
    }

    #sidebar-scroll {
        height: 1fr;
        padding: 0 1;
    }

    .sidebar-label {
        color: #64748b;
        text-style: bold;
        padding: 1 0 0 0;
    }

    .status-panel {
        padding: 0 1;
        margin-bottom: 0;
        height: auto;
        border-left: solid #38bdf8;
    }

    .status-online { border-left: solid #10b981; }
    .status-offline { border-left: solid #ef4444; }
    .status-warning { border-left: solid #f59e0b; }

    #btn-shutdown {
        width: 100%;
        margin-top: 2;
        background: #450a0a;
        color: #fecaca;
        border: none;
    }
    #btn-shutdown:hover { background: #7f1d1d; }

    #dashboard-content {
        width: 1fr;
        height: 100%;
        padding: 1;
    }

    #metrics-row {
        layout: horizontal;
        height: 5;
        margin-bottom: 1;
    }

    .metric-cell {
        width: 1fr;
        height: 100%;
        content-align: center middle;
        background: #0f172a;
        border: solid #1e293b;
        margin: 0 0 0 1;
        text-align: center;
    }
    .metric-cell:first-of-type {
        margin-left: 0;
    }

    #streams-label {
        color: #38bdf8;
        text-style: bold;
        padding: 0;
    }

    #streams-summary {
        color: #64748b;
        padding: 0 0 1 0;
    }

    #panel-stack {
        height: 1fr;
    }

    #streams-collapsible, #logs-collapsible {
        height: 1fr;
        border: solid #1e293b;
        background: #020617;
    }

    #streams-collapsible.-collapsed, #logs-collapsible.-collapsed {
        height: auto;
    }

    #streams-body {
        height: 1fr;
        padding: 1;
    }

    DataTable {
        height: 1fr;
        min-height: 6;
        border: solid #1e293b;
        background: #020617;
        margin-bottom: 0;
    }

    DataTable > .datatable--header {
        background: #0f172a;
        color: #38bdf8;
        text-style: bold;
    }

    #logs-body {
        height: 1fr;
        padding: 1;
    }

    CollapsibleTitle {
        color: #38bdf8;
        text-style: bold;
        background: #0f172a;
        padding: 0 1;
    }
    CollapsibleTitle:hover {
        background: #1e293b;
    }

    RichLog {
        height: 1fr;
        min-height: 4;
        background: #020617;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        Binding("l", "toggle_logs", "Toggle Logs", priority=True),
        Binding("s", "toggle_streams", "Toggle Streams", priority=True),
    ]

    def __init__(self, host="127.0.0.1", port=8765, config_path=None, bind_host=None):
        super().__init__()
        self.host = host
        self.port = port
        self.config_path = config_path
        self.bind_host = bind_host or host
        self.client = GhostStreamClient(manual_server=f"{self.host}:{self.port}")
        self.child_process: Optional[subprocess.Popen] = None
        self._polling_active = True
        self._shutdown_started = False
        self._engine_runtime = None
        self._engine_thread: Optional[threading.Thread] = None
        self._log_reader_thread: Optional[threading.Thread] = None
        self._metric_thread: Optional[threading.Thread] = None
        self._tui_log_handler: Optional[TUILogHandler] = None
        self._stream_row_keys: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-container"):
            with Vertical(id="sidebar"):
                with VerticalScroll(id="sidebar-scroll"):
                    yield Label("ENGINE", classes="sidebar-label")
                    yield Static("[yellow]BOOTING...[/]", id="lbl-status", classes="status-panel")

                    yield Label("CLIENTS", classes="sidebar-label")
                    yield Static("[dim]No clients[/]", id="lbl-clients", classes="status-panel")

                    yield Label("HARDWARE", classes="sidebar-label")
                    yield Static("[dim]Detecting...[/]", id="lbl-caps", classes="status-panel")

                    yield Button("Shut Down", id="btn-shutdown", variant="error")

            with Vertical(id="dashboard-content"):
                with Horizontal(id="metrics-row"):
                    yield Static("[dim]UPTIME[/]\n[bold cyan]—[/]", id="m-uptime", classes="metric-cell")
                    yield Static("[dim]ACTIVE[/]\n[bold cyan]—[/]", id="m-active", classes="metric-cell")
                    yield Static("[dim]JOBS[/]\n[bold cyan]—[/]", id="m-jobs", classes="metric-cell")
                    yield Static("[dim]DATA[/]\n[bold cyan]—[/]", id="m-data", classes="metric-cell")

                with Vertical(id="panel-stack"):
                    with Collapsible(title="Active Streams", collapsed=False, id="streams-collapsible"):
                        with Vertical(id="streams-body"):
                            yield Static("[dim]Waiting for stream activity...[/]", id="streams-summary")
                            yield DataTable(id="streams-table")
                    with Collapsible(title="Engine Logs", collapsed=True, id="logs-collapsible"):
                        with Vertical(id="logs-body"):
                            yield RichLog(id="server-logs", highlight=True, wrap=True, auto_scroll=True)

        yield Footer()

    def action_toggle_logs(self) -> None:
        logs = self.query_one("#logs-collapsible", Collapsible)
        streams = self.query_one("#streams-collapsible", Collapsible)
        show_logs = logs.collapsed
        logs.collapsed = not show_logs
        streams.collapsed = show_logs

    def action_toggle_streams(self) -> None:
        logs = self.query_one("#logs-collapsible", Collapsible)
        streams = self.query_one("#streams-collapsible", Collapsible)
        show_streams = streams.collapsed
        streams.collapsed = not show_streams
        logs.collapsed = show_streams

    def on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
        if event.collapsible.id == "logs-collapsible":
            self.query_one("#streams-collapsible", Collapsible).collapsed = True
        elif event.collapsible.id == "streams-collapsible":
            self.query_one("#logs-collapsible", Collapsible).collapsed = True

    def on_collapsible_collapsed(self, event: Collapsible.Collapsed) -> None:
        if event.collapsible.id == "logs-collapsible":
            streams = self.query_one("#streams-collapsible", Collapsible)
            if streams.collapsed:
                streams.collapsed = False
        elif event.collapsible.id == "streams-collapsible":
            logs = self.query_one("#logs-collapsible", Collapsible)
            if logs.collapsed:
                logs.collapsed = False

    def on_ready(self) -> None:
        self.title = "GhostStream"
        table = self.query_one("#streams-table", DataTable)
        table.add_columns(
            ("Source", "source"),
            ("Status", "status"),
            ("Viewers", "viewers"),
            ("Progress", "progress"),
        )
        table.cursor_type = "row"
        table.zebra_stripes = True
        self.set_timer(0.2, self._startup_engine)

    def _format_stream_source(self, source: str) -> str:
        parsed = urlparse(source)
        if parsed.scheme and parsed.netloc:
            path = parsed.path.rstrip("/")
            leaf = path.split("/")[-1] if path else parsed.netloc
            if leaf:
                label = f"{parsed.netloc} / {leaf}"
            else:
                label = parsed.netloc
        else:
            label = source.rstrip("/").split("/")[-1] or source

        return label if len(label) <= 48 else f"{label[:45]}..."

    def _stream_rows(self, streams: dict) -> list[tuple[str, tuple[str, str, str, str]]]:
        rows = []
        for source, info in streams.items():
            row_key = info.get("job_id") or source
            progress = info.get("progress", 0)
            progress_str = f"{progress:.0f}%" if progress else "\u2014"
            rows.append(
                (
                    row_key,
                    (
                        self._format_stream_source(source),
                        str(info.get("status", "?")).upper(),
                        str(info.get("viewers", 0)),
                        progress_str,
                    ),
                )
            )

        rows.sort(key=lambda item: item[1][0].lower())
        return rows

    def _update_streams_table(self, streams: dict) -> None:
        table = self.query_one("#streams-table", DataTable)
        summary = self.query_one("#streams-summary", Static)

        rows = self._stream_rows(streams)
        desired_keys = {row_key for row_key, _ in rows}

        for stale_key in sorted(self._stream_row_keys - desired_keys):
            table.remove_row(stale_key)

        if not rows:
            if "__empty__" not in self._stream_row_keys:
                table.add_row("[dim]No active streams[/]", "", "", "", key="__empty__")
            self._stream_row_keys = {"__empty__"}
            summary.update("[dim]No active streams. New shared streams will appear here.[/]")
            return

        if "__empty__" in self._stream_row_keys:
            table.remove_row("__empty__")

        total_viewers = sum(int(info.get("viewers", 0)) for info in streams.values())
        viewer_label = "viewer" if total_viewers == 1 else "viewers"
        stream_label = "stream" if len(rows) == 1 else "streams"
        summary.update(
            f"[cyan]{len(rows)} active {stream_label}[/] [dim]\u00b7 {total_viewers} {viewer_label}[/]"
        )

        for row_key, values in rows:
            if row_key in self._stream_row_keys:
                table.update_cell(row_key, "source", values[0], update_width=True)
                table.update_cell(row_key, "status", values[1], update_width=True)
                table.update_cell(row_key, "viewers", values[2], update_width=True)
                table.update_cell(row_key, "progress", values[3], update_width=True)
            else:
                table.add_row(*values, key=row_key)

        self._stream_row_keys = desired_keys

    def _startup_engine(self) -> None:
        log_tab = self.query_one("#server-logs", RichLog)
        log_tab.write("[TUI] VERSION 1.0.2 READY")
        log_tab.write("[TUI] Spawning Engine...")

        config = get_config()

        if getattr(sys, "frozen", False):
            log_tab.write("[TUI] Running Unified Process Mode (Engine in Thread)")
            engine_logger = logging.getLogger("ghoststream")
            self._tui_log_handler = TUILogHandler(log_tab)
            engine_logger.addHandler(self._tui_log_handler)

            def _internal_engine_runner():
                try:
                    self._engine_runtime = create_runtime(config)
                    self._engine_runtime.start()
                    import gevent

                    while self._polling_active:
                        gevent.sleep(1.0)
                except Exception as exc:
                    self.call_from_thread(log_tab.write, f"[TUI] ENGINE ERROR: {exc}")
                finally:
                    if self._engine_runtime is not None:
                        try:
                            self._engine_runtime.stop()
                        except Exception:
                            pass
                        self._engine_runtime = None

            self._engine_thread = threading.Thread(
                target=_internal_engine_runner,
                daemon=True,
                name="EngineThread",
            )
            self._engine_thread.start()
            log_tab.write("[TUI] Internal Engine Started")
        else:
            log_tab.write("[TUI] Running Development Mode (Engine in Subprocess)")
            cmd = [sys.executable, "-u", "-m", "ghoststream", "--server-only"]
            cmd.extend(["--log-format", "text", "--log-level", "INFO"])
            if self.config_path:
                cmd.extend(["-c", self.config_path])
            if self.bind_host:
                cmd.extend(["--host", self.bind_host])
            if self.port:
                cmd.extend(["--port", str(self.port)])

            env = os.environ.copy()
            env["GHOSTSTREAM_ENV"] = "production"
            env["PYTHONUNBUFFERED"] = "1"

            try:
                self.child_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                log_tab.write(f"[TUI] External Engine Spawned (PID {self.child_process.pid})")
                self._log_reader_thread = threading.Thread(
                    target=self._log_reader_loop,
                    args=(log_tab,),
                    daemon=True,
                    name="LogReader",
                )
                self._log_reader_thread.start()
            except Exception as exc:
                log_tab.write(f"[TUI] ERROR: Failed to spawn engine: {exc}")
                return

        def _poll_thread():
            while self._polling_active:
                try:
                    self._update_metrics()
                except Exception:
                    pass
                time.sleep(2.0)

        self._metric_thread = threading.Thread(
            target=_poll_thread,
            daemon=True,
            name="MetricPoller",
        )
        self._metric_thread.start()

    def _log_reader_loop(self, log_tab):
        if not self.child_process or not self.child_process.stdout:
            return

        while self._polling_active:
            line = self.child_process.stdout.readline()
            if not line:
                break

            msg = line.strip()
            if not msg:
                continue

            color = "white"
            if "INFO" in msg or "\u2713" in msg:
                color = "cyan"
            elif "WARNING" in msg:
                color = "yellow"
            elif "ERROR" in msg or "CRITICAL" in msg or "\u2717" in msg:
                color = "red"

            self.call_from_thread(log_tab.write, Text(msg, style=color))

    def _dashboard_headers(self):
        headers = {INTERNAL_DASHBOARD_HEADER: "tui-dashboard"}

        api_key = getattr(self.client, "api_key", None)
        if api_key:
            headers[API_KEY_HEADER] = api_key

        return headers

    def _update_metrics(self) -> None:
        base = f"http://{self.host}:{self.port}"
        headers = self._dashboard_headers()

        try:
            response = httpx.get(f"{base}/api/health", headers=headers, timeout=5.0)
            is_healthy = response.status_code == 200
        except Exception:
            is_healthy = False

        status_lbl = self.query_one("#lbl-status", Static)
        clients_lbl = self.query_one("#lbl-clients", Static)
        caps_lbl = self.query_one("#lbl-caps", Static)

        m_uptime = self.query_one("#m-uptime", Static)
        m_active = self.query_one("#m-active", Static)
        m_jobs = self.query_one("#m-jobs", Static)
        m_data = self.query_one("#m-data", Static)

        if not is_healthy:
            def _off():
                status_lbl.update(f"[red]OFFLINE[/]\n[dim]{self.host}:{self.port}[/]")
                status_lbl.set_class(True, "status-offline")
                clients_lbl.update("[dim]No clients[/]")
                clients_lbl.set_class(True, "status-offline")
                caps_lbl.update("[dim]Waiting...[/]")
                caps_lbl.set_class(True, "status-offline")
                m_uptime.update("[dim]UPTIME[/]\n[dim]—[/]")
                m_active.update("[dim]ACTIVE[/]\n[dim]—[/]")
                m_jobs.update("[dim]JOBS[/]\n[dim]—[/]")
                m_data.update("[dim]DATA[/]\n[dim]—[/]")
                self._update_streams_table({})

            self.call_from_thread(_off)
            return

        try:
            caps_resp = httpx.get(f"{base}/api/capabilities", headers=headers, timeout=5.0)
            caps_data = caps_resp.json() if caps_resp.status_code == 200 else None
        except Exception:
            caps_data = None

        try:
            clients_resp = httpx.get(f"{base}/api/clients", headers=headers, timeout=5.0)
            clients_data = clients_resp.json() if clients_resp.status_code == 200 else []
        except Exception:
            clients_data = []

        def _on():
            pid = self.child_process.pid if self.child_process else "?"
            status_lbl.update(f"[green]ONLINE[/]\n[dim]{self.host}:{self.port} · PID {pid}[/]")
            status_lbl.set_class(True, "status-online")
            if clients_data:
                names = sorted({c.get("client", "Unknown") for c in clients_data})
                count = len(clients_data)
                label = " · ".join(names)
                clients_lbl.update(f"[green]{label}[/]\n[dim]{count} connected[/]")
                clients_lbl.set_class(True, "status-online")
            else:
                clients_lbl.update("[dim]No clients[/]")
                clients_lbl.set_class(False, "status-online")
            if caps_data:
                hw_parts = []
                for hw in caps_data.get("hw_accels", []):
                    if hw.get("available"):
                        hw_parts.append(hw.get("type", "???").upper())
                if hw_parts:
                    caps_lbl.update(f"[cyan]{' · '.join(hw_parts)}[/]")
                    caps_lbl.set_class(True, "status-online")
                else:
                    caps_lbl.update("[yellow]Software Only[/]")
                    caps_lbl.set_class(True, "status-warning")

        self.call_from_thread(_on)

        try:
            resp = httpx.get(f"{base}/api/stats", headers=headers, timeout=5.0)
            if resp.status_code == 200:
                stats = resp.json()

                def _stats():
                    uptime = stats.get("uptime_seconds", 0)
                    if uptime >= 3600:
                        ut = f"{uptime / 3600:.1f}h"
                    elif uptime >= 60:
                        ut = f"{uptime / 60:.0f}m"
                    else:
                        ut = f"{uptime:.0f}s"
                    m_uptime.update(f"[dim]UPTIME[/]\n[bold cyan]{ut}[/]")

                    active = stats.get("active_jobs", 0)
                    active_color = "green" if active > 0 else "cyan"
                    m_active.update(f"[dim]ACTIVE[/]\n[bold {active_color}]{active}[/]")

                    total = stats.get("total_jobs_processed", 0)
                    successful = stats.get("successful_jobs", 0)
                    failed = stats.get("failed_jobs", 0)
                    m_jobs.update(
                        f"[dim]JOBS[/]\n[bold cyan]{total}[/] "
                        f"[dim]([green]{successful}[/green]/[red]{failed}[/red])[/]"
                    )

                    megabytes = stats.get("total_bytes_processed", 0) / (1024 * 1024)
                    data_str = f"{megabytes / 1024:.1f} GB" if megabytes >= 1024 else f"{megabytes:.0f} MB"
                    m_data.update(f"[dim]DATA[/]\n[bold cyan]{data_str}[/]")

                self.call_from_thread(_stats)
        except Exception:
            pass

        try:
            streams_resp = httpx.get(f"{base}/api/streams/shared", headers=headers, timeout=5.0)
            if streams_resp.status_code == 200:
                streams_data = streams_resp.json()
                streams = streams_data.get("streams", {})

                def _streams():
                    self._update_streams_table(streams)

                self.call_from_thread(_streams)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-shutdown":
            self.exit()

    def on_unmount(self) -> None:
        if self._shutdown_started:
            return

        self._shutdown_started = True
        self._polling_active = False

        if self._engine_runtime is not None:
            try:
                self._engine_runtime.stop()
            except Exception:
                pass
            self._engine_runtime = None

        if self.child_process:
            try:
                self.child_process.terminate()
                self.child_process.wait(timeout=5)
            except Exception:
                try:
                    self.child_process.kill()
                except Exception:
                    pass

        for thread in (self._log_reader_thread, self._metric_thread, self._engine_thread):
            if thread and thread.is_alive():
                thread.join(timeout=2.0)

        if self._tui_log_handler is not None:
            logging.getLogger("ghoststream").removeHandler(self._tui_log_handler)
            self._tui_log_handler = None


def run_tui_app(host="127.0.0.1", port=8765, config_path=None, bind_host=None):
    app = GhostStreamTUI(host=host, port=port, config_path=config_path, bind_host=bind_host)
    try:
        app.run()
    finally:
        app.on_unmount()
        os._exit(0)
