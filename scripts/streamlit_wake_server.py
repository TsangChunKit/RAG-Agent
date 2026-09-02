"""Keep a small wake page on port 8501 and stop idle Streamlit on port 8502."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Protocol, cast
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROL_SCRIPT = PROJECT_ROOT / "scripts" / "counseling_agent_ctl.sh"
LSOF_PATH = "/usr/sbin/lsof"

WAKE_HOST = os.getenv("RAG_WAKE_HOST", "0.0.0.0")
WAKE_PORT = int(os.getenv("RAG_WAKE_PORT", "8501"))
STREAMLIT_HOST = os.getenv("RAG_STREAMLIT_HOST", "127.0.0.1")
STREAMLIT_PORT = int(os.getenv("RAG_STREAMLIT_PORT", "8502"))
IDLE_TIMEOUT_SECONDS = int(os.getenv("RAG_STREAMLIT_IDLE_SECONDS", "1800"))
CHECK_INTERVAL_SECONDS = int(os.getenv("RAG_STREAMLIT_CHECK_SECONDS", "30"))


class Runner(Protocol):
    def __call__(self, args: list[str], **kwargs) -> subprocess.CompletedProcess[str]: ...


def streamlit_is_healthy(
    host: str = STREAMLIT_HOST,
    port: int = STREAMLIT_PORT,
    timeout: float = 1.0,
) -> bool:
    """Return whether Streamlit's health endpoint responds with ``ok``."""
    try:
        with urlopen(
            f"http://{host}:{port}/_stcore/health", timeout=timeout
        ) as response:
            return response.read().strip() == b"ok"
    except (URLError, TimeoutError, OSError):
        return False


def run_control(
    action: str,
    *,
    runner: Runner = subprocess.run,
) -> None:
    """Run the shared web-only service control command and surface failures."""
    if action not in {"web-start", "web-stop"}:
        raise ValueError(f"Unsupported control action: {action}")

    try:
        result = runner(
            ["bash", str(CONTROL_SCRIPT), action],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{action} timed out after {exc.timeout} seconds") from exc
    except OSError as exc:
        raise RuntimeError(f"{action} could not run: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"{action} failed: {detail}")


def has_active_clients(
    port: int = STREAMLIT_PORT,
    *,
    runner: Runner = subprocess.run,
) -> bool:
    """Return whether Streamlit has any established TCP client connections."""
    try:
        result = runner(
            [LSOF_PATH, "-nP", f"-iTCP:{port}", "-sTCP:ESTABLISHED", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Streamlit client inspection timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Unable to run Streamlit client inspection: {exc}") from exc
    if result.returncode == 0:
        return bool(result.stdout.strip())
    if (
        result.returncode == 1
        and not result.stdout.strip()
        and not result.stderr.strip()
    ):
        return False

    detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
    raise RuntimeError(f"Unable to inspect Streamlit clients: {detail}")


@dataclass
class IdleTracker:
    """Track how long a running Streamlit server has had no connected clients."""

    timeout_seconds: float
    idle_since: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

    def reset(self) -> None:
        """Discard the current idle observation period."""
        self.idle_since = None

    def observe(
        self,
        *,
        streamlit_running: bool,
        has_clients: bool,
        now: float,
    ) -> bool:
        """Return ``True`` once the continuous no-client period reaches timeout."""
        if not streamlit_running or has_clients:
            self.reset()
            return False
        if self.idle_since is None:
            self.idle_since = now
            return False
        return now - self.idle_since >= self.timeout_seconds


class IdleSupervisor:
    """Poll Streamlit connectivity and stop it after a continuous idle period."""

    def __init__(
        self,
        *,
        timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        health_probe: Callable[[], bool] = streamlit_is_healthy,
        client_probe: Callable[[], bool] = has_active_clients,
        stop_streamlit: Callable[[], None] = lambda: run_control("web-stop"),
        clock: Callable[[], float] = time.monotonic,
        lifecycle_lock: threading.RLock | None = None,
    ) -> None:
        self._tracker = IdleTracker(timeout_seconds)
        self._health_probe = health_probe
        self._client_probe = client_probe
        self._stop_streamlit = stop_streamlit
        self._clock = clock
        self._lifecycle_lock = lifecycle_lock or threading.RLock()

    def note_wake_activity(self) -> None:
        """Reset idle time while a browser is actively requesting a wake."""
        with self._lifecycle_lock:
            self._tracker.reset()

    def check_once(self) -> None:
        """Perform one fail-safe health/client observation."""
        with self._lifecycle_lock:
            running = self._health_probe()
            if not running:
                self._tracker.reset()
                return

            try:
                clients = self._client_probe()
            except RuntimeError as exc:
                self._tracker.reset()
                LOGGER.error(
                    "Client probe failed; idle observation reset and Streamlit kept running: %s",
                    exc,
                )
                return

            if self._tracker.observe(
                streamlit_running=True,
                has_clients=clients,
                now=self._clock(),
            ):
                LOGGER.info(
                    "Streamlit has been idle for %.0f seconds; stopping it",
                    self._tracker.timeout_seconds,
                )
                self._stop_streamlit()

    def run_forever(
        self,
        stop_event: threading.Event,
        interval_seconds: float = CHECK_INTERVAL_SECONDS,
    ) -> None:
        """Run checks until ``stop_event`` is set, keeping transient errors visible."""
        while not stop_event.wait(interval_seconds):
            try:
                self.check_once()
            except RuntimeError as exc:
                LOGGER.error("Idle supervisor check failed: %s", exc)


_WAKE_PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RAG-Agent 啟動中</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 38rem; margin: 18vh auto; padding: 0 1.5rem; }
    .spinner { width: 1.4rem; height: 1.4rem; border: .2rem solid #ddd; border-top-color: #ff4b4b;
      border-radius: 50%; animation: spin .8s linear infinite; display: inline-block; vertical-align: middle; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <h1><span class="spinner"></span> Streamlit 啟動中</h1>
  <p>RAG-Agent 正在恢復服務，完成後會自動進入。</p>
  <p id="error" hidden>啟動時間比預期長，請重新整理；若仍失敗，請檢查 wake gateway 日誌。</p>
  <script>
    const target = new URL(window.location.href);
    target.port = "8502";
    target.pathname = "/";
    target.search = "";
    target.hash = "";
    let attempts = 0;
    async function waitUntilReady() {
      try {
        const response = await fetch("/status", {cache: "no-store"});
        const status = await response.json();
        if (status.ready) {
          window.location.replace(target.toString());
          return;
        }
      } catch (_) {}
      attempts += 1;
      if (attempts >= 60) document.getElementById("error").hidden = false;
      setTimeout(waitUntilReady, 1000);
    }
    waitUntilReady();
  </script>
</body>
</html>
"""


class WakeRequestHandler(BaseHTTPRequestHandler):
    """Serve health/status endpoints and trigger Streamlit on page requests."""

    server_version = "RAGWakeGateway/1.0"

    def _write(
        self,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/health", "/_stcore/health"}:
            self._write(200, b"ok", "text/plain; charset=utf-8")
            return
        if path == "/status":
            body = json.dumps({"ready": streamlit_is_healthy()}).encode()
            self._write(200, body, "application/json")
            return
        if path != "/":
            self._write(404, b"not found", "text/plain; charset=utf-8")
            return

        server = cast(WakeHTTPServer, self.server)
        with server.lifecycle_lock:
            server.wake_activity()
            if not streamlit_is_healthy():
                try:
                    run_control("web-start")
                except RuntimeError as exc:
                    LOGGER.error("Unable to wake Streamlit: %s", exc)
                    self._write(
                        503,
                        "Streamlit 啟動失敗，請檢查 wake gateway 日誌。".encode(),
                        "text/plain; charset=utf-8",
                    )
                    return

        self._write(200, _WAKE_PAGE.encode(), "text/html; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.client_address[0], format % args)


class WakeHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying shared wake/idle lifecycle coordination."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        wake_activity: Callable[[], None],
        lifecycle_lock: threading.RLock,
    ) -> None:
        self.wake_activity = wake_activity
        self.lifecycle_lock = lifecycle_lock
        super().__init__(address, WakeRequestHandler)


def create_server(
    host: str = WAKE_HOST,
    port: int = WAKE_PORT,
    *,
    wake_activity: Callable[[], None] = lambda: None,
    lifecycle_lock: threading.RLock | None = None,
) -> WakeHTTPServer:
    """Create the wake HTTP server without starting its serving loop."""
    return WakeHTTPServer(
        (host, port),
        wake_activity=wake_activity,
        lifecycle_lock=lifecycle_lock or threading.RLock(),
    )


def main() -> None:
    """Run the wake HTTP server and idle supervisor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = threading.Event()
    lifecycle_lock = threading.RLock()
    supervisor = IdleSupervisor(lifecycle_lock=lifecycle_lock)
    monitor = threading.Thread(
        target=supervisor.run_forever,
        args=(stop_event,),
        name="streamlit-idle-supervisor",
        daemon=True,
    )
    server = create_server(
        wake_activity=supervisor.note_wake_activity,
        lifecycle_lock=lifecycle_lock,
    )
    monitor.start()
    LOGGER.info(
        "Wake gateway listening on %s:%d; Streamlit=%s:%d; idle timeout=%ds",
        WAKE_HOST,
        WAKE_PORT,
        STREAMLIT_HOST,
        STREAMLIT_PORT,
        IDLE_TIMEOUT_SECONDS,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Wake gateway interrupted")
    finally:
        stop_event.set()
        server.server_close()
        monitor.join(timeout=CHECK_INTERVAL_SECONDS + 1)


if __name__ == "__main__":
    main()
