"""Tests for the lightweight Streamlit wake and idle supervisor."""

from __future__ import annotations

import json
import subprocess
import threading
from urllib.request import urlopen
from unittest.mock import Mock

import pytest

from scripts import streamlit_wake_server as wake


def test_streamlit_health_probe_accepts_ok_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b"ok\n"
    monkeypatch.setattr(wake, "urlopen", Mock(return_value=response))

    assert wake.streamlit_is_healthy(timeout=0.1) is True


def test_streamlit_health_probe_returns_false_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        wake,
        "urlopen",
        Mock(side_effect=wake.URLError("connection refused")),
    )

    assert wake.streamlit_is_healthy(timeout=0.1) is False


def test_idle_tracker_stops_after_timeout_without_clients() -> None:
    tracker = wake.IdleTracker(timeout_seconds=1800)

    assert tracker.observe(streamlit_running=True, has_clients=False, now=100.0) is False
    assert tracker.observe(streamlit_running=True, has_clients=False, now=1899.9) is False
    assert tracker.observe(streamlit_running=True, has_clients=False, now=1900.0) is True


def test_idle_tracker_active_client_resets_timeout() -> None:
    tracker = wake.IdleTracker(timeout_seconds=1800)

    tracker.observe(streamlit_running=True, has_clients=False, now=100.0)
    assert tracker.observe(streamlit_running=True, has_clients=True, now=1000.0) is False
    assert tracker.observe(streamlit_running=True, has_clients=False, now=2000.0) is False
    assert tracker.observe(streamlit_running=True, has_clients=False, now=3799.9) is False
    assert tracker.observe(streamlit_running=True, has_clients=False, now=3800.0) is True


def test_idle_tracker_resets_when_streamlit_is_stopped() -> None:
    tracker = wake.IdleTracker(timeout_seconds=10)

    tracker.observe(streamlit_running=True, has_clients=False, now=1.0)
    assert tracker.observe(streamlit_running=False, has_clients=False, now=20.0) is False
    assert tracker.observe(streamlit_running=True, has_clients=False, now=21.0) is False


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [(0, "123\n456\n", True), (1, "", False)],
)
def test_has_active_clients_handles_lsof_results(
    returncode: int, stdout: str, expected: bool
) -> None:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=["lsof"], returncode=returncode, stdout=stdout, stderr=""
        )
    )

    assert wake.has_active_clients(port=8502, runner=runner) is expected
    runner.assert_called_once()


def test_has_active_clients_surfaces_lsof_failure() -> None:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=["lsof"], returncode=2, stdout="", stderr="permission denied"
        )
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        wake.has_active_clients(port=8502, runner=runner)


def test_has_active_clients_treats_returncode_one_with_stderr_as_failure() -> None:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=["lsof"], returncode=1, stdout="", stderr="permission denied"
        )
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        wake.has_active_clients(port=8502, runner=runner)


def test_has_active_clients_converts_timeout_to_runtime_error() -> None:
    runner = Mock(side_effect=subprocess.TimeoutExpired(cmd=["lsof"], timeout=5))

    with pytest.raises(RuntimeError, match="timed out"):
        wake.has_active_clients(port=8502, runner=runner)


def test_run_control_surfaces_command_failure() -> None:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=["bash"], returncode=1, stdout="", stderr="launchctl failed"
        )
    )

    with pytest.raises(RuntimeError, match="launchctl failed"):
        wake.run_control("web-start", runner=runner)


def test_run_control_accepts_successful_web_stop() -> None:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=["bash"], returncode=0, stdout="stopped", stderr=""
        )
    )

    wake.run_control("web-stop", runner=runner)

    args = runner.call_args.args[0]
    assert args[-1] == "web-stop"


def test_run_control_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="Unsupported control action"):
        wake.run_control("restart")


def test_idle_supervisor_stops_streamlit_once_timeout_is_reached() -> None:
    clock = Mock(side_effect=[100.0, 110.0])
    stop = Mock()
    supervisor = wake.IdleSupervisor(
        timeout_seconds=10,
        health_probe=Mock(return_value=True),
        client_probe=Mock(return_value=False),
        stop_streamlit=stop,
        clock=clock,
    )

    supervisor.check_once()
    supervisor.check_once()

    stop.assert_called_once_with()


def test_idle_supervisor_does_not_stop_when_client_probe_fails() -> None:
    stop = Mock()
    supervisor = wake.IdleSupervisor(
        timeout_seconds=10,
        health_probe=Mock(return_value=True),
        client_probe=Mock(side_effect=RuntimeError("lsof failed")),
        stop_streamlit=stop,
        clock=Mock(return_value=100.0),
    )

    supervisor.check_once()

    stop.assert_not_called()


def test_idle_supervisor_probe_failure_resets_idle_period() -> None:
    stop = Mock()
    supervisor = wake.IdleSupervisor(
        timeout_seconds=10,
        health_probe=Mock(return_value=True),
        client_probe=Mock(side_effect=[False, RuntimeError("lsof failed"), False, False]),
        stop_streamlit=stop,
        clock=Mock(side_effect=[0.0, 20.0, 29.9]),
    )

    supervisor.check_once()
    supervisor.check_once()
    supervisor.check_once()
    supervisor.check_once()

    stop.assert_not_called()


def test_wake_activity_resets_supervisor_idle_period() -> None:
    stop = Mock()
    supervisor = wake.IdleSupervisor(
        timeout_seconds=10,
        health_probe=Mock(return_value=True),
        client_probe=Mock(return_value=False),
        stop_streamlit=stop,
        clock=Mock(side_effect=[0.0, 20.0]),
    )

    supervisor.check_once()
    supervisor.note_wake_activity()
    supervisor.check_once()

    stop.assert_not_called()


def test_root_request_starts_streamlit_and_returns_wake_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = Mock()
    monkeypatch.setattr(wake, "streamlit_is_healthy", Mock(return_value=False))
    monkeypatch.setattr(wake, "run_control", start)
    server = wake.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=2) as response:
            body = response.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    start.assert_called_once_with("web-start")
    assert 'target.port = "8502"' in body
    assert "Streamlit 啟動中" in body


def test_root_request_records_wake_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wake_activity = Mock()
    monkeypatch.setattr(wake, "streamlit_is_healthy", Mock(return_value=True))
    server = wake.create_server("127.0.0.1", 0, wake_activity=wake_activity)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=2):
            pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    wake_activity.assert_called_once_with()


def test_status_request_reports_streamlit_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wake, "streamlit_is_healthy", Mock(return_value=True))
    server = wake.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/status", timeout=2
        ) as response:
            payload = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert payload == {"ready": True}


def test_stale_streamlit_asset_request_does_not_wake_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = Mock()
    monkeypatch.setattr(wake, "streamlit_is_healthy", Mock(return_value=False))
    monkeypatch.setattr(wake, "run_control", start)
    server = wake.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with pytest.raises(wake.URLError) as exc_info:
            urlopen(
                f"http://127.0.0.1:{server.server_port}/_stcore/stream",
                timeout=2,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert exc_info.value.code == 404
    start.assert_not_called()


def test_root_request_returns_503_when_streamlit_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wake, "streamlit_is_healthy", Mock(return_value=False))
    monkeypatch.setattr(
        wake,
        "run_control",
        Mock(side_effect=RuntimeError("launchctl failed")),
    )
    server = wake.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        with pytest.raises(wake.URLError) as exc_info:
            urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert exc_info.value.code == 503


def test_main_starts_monitor_and_closes_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = Mock()
    supervisor = Mock()
    monitor = Mock()
    monkeypatch.setattr(wake, "create_server", Mock(return_value=server))
    monkeypatch.setattr(wake, "IdleSupervisor", Mock(return_value=supervisor))
    monkeypatch.setattr(wake.threading, "Thread", Mock(return_value=monitor))

    wake.main()

    monitor.start.assert_called_once_with()
    server.serve_forever.assert_called_once_with()
    server.server_close.assert_called_once_with()
