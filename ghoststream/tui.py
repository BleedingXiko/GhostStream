import os
import sys
import subprocess
import threading
import time
import logging
from typing import Optional, Dict

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.binding import Binding
from textual.widgets import Header, Footer, Static, Label, DataTable, RichLog, Button, Rule, Collapsible
import httpx
from rich.text import Text

from .client import GhostStreamClient, GhostStreamServer
from .config import get_config


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
        except:
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

    /* ── Layout ── */
    #main-container {
        layout: horizontal;
        height: 100%;
        width: 100%;
    }

    /* ── Sidebar ── */
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

    /* ── Right Panel ── */
    #dashboard-content {
        width: 1fr;
        height: 100%;
        padding: 1;
    }

    /* ── Metrics row (original style) ── */
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

    /* ── Active Streams table ── */
    #streams-label {
        color: #38bdf8;
        text-style: bold;
        padding: 0;
    }

    DataTable {
        height: auto;
        max-height: 12;
        min-height: 3;
        border: solid #1e293b;
        background: #020617;
        margin-bottom: 1;
    }

    DataTable > .datatable--header {
        background: #0f172a;
        color: #38bdf8;
        text-style: bold;
    }

    #no-streams {
        color: #475569;
        text-align: center;
        padding: 1;
    }

    /* ── Collapsible logs ── */
    #logs-collapsible {
        height: 1fr;
        background: #020617;
        border: solid #1e293b;
    }

    #logs-collapsible.-collapsed {
        height: auto;
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
    ]

    def __init__(self, host="127.0.0.1", port=8765, config_path=None):
        super().__init__()
        self.host = host
        self.port = port
        self.config_path = config_path
        self.client = GhostStreamClient(manual_server=f"{self.host}:{self.port}")
        self.child_process: Optional[subprocess.Popen] = None
        self._polling_active = True
        self._shutdown_started = False
        self._engine_runtime = None
        self._engine_thread: Optional[threading.Thread] = None
        self._log_reader_thread: Optional[threading.Thread] = None
        self._metric_thread: Optional[threading.Thread] = None
        self._tui_log_handler: Optional[TUILogHandler] = None

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

                yield Label("Active Streams", id="streams-label")
                yield DataTable(id="streams-table")

                with Collapsible(title="Engine Logs", collapsed=False, id="logs-collapsible"):
                    yield RichLog(id="server-logs", highlight=True, wrap=True, auto_scroll=True)

        yield Footer()

    def action_toggle_logs(self) -> None:
        c = self.query_one("#logs-collapsible", Collapsible)
        c.collapsed = not c.collapsed

    def on_ready(self) -> None:
        self.title = "GhostStream"
        table = self.query_one("#streams-table", DataTable)
        table.add_columns("Source", "Status", "Viewers", "Progress")
        table.cursor_type = "row"
        table.zebra_stripes = True
        self.set_timer(0.2, self._startup_engine)

    def _startup_engine(self) -> None:
        log_tab = self.query_one("#server-logs", RichLog)
        log_tab.write("[TUI] VERSION 1.0.2 READY")
        log_tab.write("[TUI] Spawning Engine...")

        config = get_config()

        if getattr(sys, 'frozen', False):
            log_tab.write("[TUI] Running Unified Process Mode (Engine in Thread)")
            engine_logger = logging.getLogger("ghoststream")
            self._tui_log_handler = TUILogHandler(log_tab)
            engine_logger.addHandler(self._tui_log_handler)

            def _internal_engine_runner():
                try:
                    from .runtime import create_runtime
                    self._engine_runtime = create_runtime()
                    self._engine_runtime.start()
                    import gevent
                    while self._polling_active:
                        gevent.sleep(1.0)
                except Exception as e:
                    self.call_from_thread(log_tab.write, f"[TUI] ENGINE ERROR: {e}")
                finally:
                    if self._engine_runtime is not None:
                        try:
                            self._engine_runtime.stop()
                        except Exception:
                            pass
                        self._engine_runtime = None

            self._engine_thread = threading.Thread(target=_internal_engine_runner, daemon=True, name="EngineThread")
            self._engine_thread.start()
            log_tab.write("[TUI] Internal Engine Started")
        else:
            log_tab.write("[TUI] Running Development Mode (Engine in Subprocess)")
            cmd = [sys.executable, "-u", "-m", "ghoststream", "--server-only"]
            cmd.extend(["--log-format", "text", "--log-level", "INFO"])
            if self.config_path:
                cmd.extend(["-c", self.config_path])
            if self.host:
                cmd.extend(["--host", self.host])
            if self.port:
                cmd.extend(["--port", str(self.port)])

            env = os.environ.copy()
            env["GHOSTSTREAM_ENV"] = "production"
            env["PYTHONUNBUFFERED"] = "1"

            try:
                self.child_process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, env=env,
                )
                log_tab.write(f"[TUI] External Engine Spawned (PID {self.child_process.pid})")
                self._log_reader_thread = threading.Thread(
                    target=self._log_reader_loop,
                    args=(log_tab,),
                    daemon=True,
                    name="LogReader",
                )
                self._log_reader_thread.start()
            except Exception as e:
                log_tab.write(f"[TUI] ERROR: Failed to spawn engine: {e}")
                return

        def _poll_thread():
            while self._polling_active:
                try:
                    self._update_metrics()
                except Exception:
                    pass
                time.sleep(2.0)

        self._metric_thread = threading.Thread(target=_poll_thread, daemon=True, name="MetricPoller")
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

    def _update_metrics(self) -> None:
        base = f"http://{self.host}:{self.port}"

        # Health check with raw httpx (no gevent dependency)
        try:
            r = httpx.get(f"{base}/api/health", timeout=5.0)
            is_healthy = r.status_code == 200
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
            self.call_from_thread(_off)
            return

        # Sidebar status
        try:
            caps_resp = httpx.get(f"{base}/api/capabilities", timeout=5.0)
            caps_data = caps_resp.json() if caps_resp.status_code == 200 else None
        except Exception:
            caps_data = None

        try:
            clients_resp = httpx.get(f"{base}/api/clients", timeout=5.0)
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

        # Stats — raw httpx, no broken method calls
        try:
            resp = httpx.get(f"{base}/api/stats", timeout=5.0)
            if resp.status_code == 200:
                stats = resp.json()

                def _stats():
                    uptime = stats.get('uptime_seconds', 0)
                    if uptime >= 3600:
                        ut = f"{uptime/3600:.1f}h"
                    elif uptime >= 60:
                        ut = f"{uptime/60:.0f}m"
                    else:
                        ut = f"{uptime:.0f}s"
                    m_uptime.update(f"[dim]UPTIME[/]\n[bold cyan]{ut}[/]")

                    active = stats.get('active_jobs', 0)
                    ac = "green" if active > 0 else "cyan"
                    m_active.update(f"[dim]ACTIVE[/]\n[bold {ac}]{active}[/]")

                    total = stats.get('total_jobs_processed', 0)
                    s = stats.get('successful_jobs', 0)
                    f = stats.get('failed_jobs', 0)
                    m_jobs.update(f"[dim]JOBS[/]\n[bold cyan]{total}[/] [dim]([green]{s}[/green]/[red]{f}[/red])[/]")

                    mb = stats.get('total_bytes_processed', 0) / (1024 * 1024)
                    data_str = f"{mb/1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"
                    m_data.update(f"[dim]DATA[/]\n[bold cyan]{data_str}[/]")

                self.call_from_thread(_stats)
        except Exception:
            pass

        # Active streams table
        try:
            streams_resp = httpx.get(f"{base}/api/streams/shared", timeout=5.0)
            if streams_resp.status_code == 200:
                streams_data = streams_resp.json()
                streams = streams_data.get("streams", {})

                def _streams():
                    table = self.query_one("#streams-table", DataTable)
                    table.clear()
                    if streams:
                        for source, info in streams.items():
                            prog = info.get("progress", 0)
                            prog_str = f"{prog:.0f}%" if prog else "—"
                            table.add_row(
                                source[:40],
                                info.get("status", "?"),
                                str(info.get("viewers", 0)),
                                prog_str,
                            )
                    else:
                        table.add_row("[dim]No active streams[/]", "", "", "")
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


def run_tui_app(host="127.0.0.1", port=8765, config_path=None):
    app = GhostStreamTUI(host=host, port=port, config_path=config_path)
    try:
        app.run()
    finally:
        app.on_unmount()
        os._exit(0)


if __name__ == "__main__":
    run_tui_app()
