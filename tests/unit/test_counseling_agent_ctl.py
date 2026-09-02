"""Tests for the counseling agent service control script."""

import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "counseling_agent_ctl.sh"
TAILSCALE_PLIST = ROOT / "scripts" / "launchd" / "com.andytsang.aitherapist.tailscale.plist"
STREAMLIT_PLIST = ROOT / "scripts" / "launchd" / "com.andytsang.aitherapist.streamlit.plist"
WAKE_GATEWAY_PLIST = ROOT / "scripts" / "launchd" / "com.andytsang.aitherapist.wakegateway.plist"
APP_SERVICES = {
    "com.andytsang.aitherapist.streamlit",
    "com.andytsang.aitherapist.wakegateway",
    "com.andytsang.aitherapist.chatmemorywatcher",
    "com.andytsang.aitherapist.rawingestwatcher",
}


def _run_script(tmp_path: Path, command: str) -> tuple[subprocess.CompletedProcess[str], str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl_log = tmp_path / "launchctl.log"

    launchctl = bin_dir / "launchctl"
    launchctl.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$LAUNCHCTL_LOG"\n'
        'if [[ "$1" == "list" ]]; then printf "123\\t0\\tcom.andytsang.aitherapist.streamlit\\n"; fi\n'
    )
    launchctl.chmod(0o755)

    curl = bin_dir / "curl"
    curl.write_text('#!/bin/bash\nprintf "ok\\n"\n')
    curl.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["LAUNCHCTL_LOG"] = str(launchctl_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(SCRIPT), command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    log = launchctl_log.read_text() if launchctl_log.exists() else ""
    return result, log


def test_start_only_manages_application_services(tmp_path: Path) -> None:
    result, log = _run_script(tmp_path, "start")

    assert result.returncode == 0
    assert all(service in log for service in APP_SERVICES)
    assert "tailscale" not in log.lower()


def test_stop_only_manages_application_services(tmp_path: Path) -> None:
    result, log = _run_script(tmp_path, "stop")

    assert result.returncode == 0
    assert all(service in log for service in APP_SERVICES)
    assert "tailscale" not in log.lower()


def test_web_start_only_manages_streamlit(tmp_path: Path) -> None:
    result, log = _run_script(tmp_path, "web-start")

    assert result.returncode == 0
    assert "com.andytsang.aitherapist.streamlit" in log
    assert "wakegateway" not in log
    assert "watcher" not in log


def test_web_stop_only_manages_streamlit(tmp_path: Path) -> None:
    result, log = _run_script(tmp_path, "web-stop")

    assert result.returncode == 0
    assert "com.andytsang.aitherapist.streamlit" in log
    assert "wakegateway" not in log
    assert "watcher" not in log


def test_status_does_not_report_tailscale(tmp_path: Path) -> None:
    result, log = _run_script(tmp_path, "status")

    assert result.returncode == 0
    assert "Wake gateway" in result.stdout
    assert "Streamlit" in result.stdout
    assert "Tailscale" not in result.stdout
    assert "tailscale" not in log.lower()


def test_tailscale_has_no_automatic_launchd_job() -> None:
    assert not TAILSCALE_PLIST.exists()


def test_streamlit_launchd_job_is_on_demand_on_port_8502() -> None:
    with STREAMLIT_PLIST.open("rb") as handle:
        config = plistlib.load(handle)

    assert config["RunAtLoad"] is False
    assert config["KeepAlive"] is False
    port_index = config["ProgramArguments"].index("--server.port")
    assert config["ProgramArguments"][port_index + 1] == "8502"


def test_wake_gateway_launchd_job_owns_port_8501_and_uses_30_minute_idle() -> None:
    with WAKE_GATEWAY_PLIST.open("rb") as handle:
        config = plistlib.load(handle)

    assert config["RunAtLoad"] is True
    assert config["KeepAlive"] is True
    assert config["EnvironmentVariables"]["RAG_WAKE_PORT"] == "8501"
    assert config["EnvironmentVariables"]["RAG_STREAMLIT_IDLE_SECONDS"] == "1800"