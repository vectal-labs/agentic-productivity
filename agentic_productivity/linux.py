from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


SERVICE = "agentic-productivity.service"
TIMER = "agentic-productivity.timer"


def linux_paths(home: Path | None = None) -> dict[str, Path]:
    home = home or Path.home()
    state = Path(
        os.environ.get(
            "CORRAL_PRODUCTIVITY_STATE_DIR",
            home / ".local/share/corral/agentic-productivity",
        )
    )
    return {
        "source": Path(__file__).resolve().parents[1],
        "app": Path(os.environ.get("CORRAL_PRODUCTIVITY_APP_DIR", state / "app")),
        "state": state,
        "logs": Path(
            os.environ.get(
                "CORRAL_PRODUCTIVITY_LOG_DIR",
                state / "logs",
            )
        ),
        "systemd": Path(
            os.environ.get(
                "CORRAL_PRODUCTIVITY_SYSTEMD_DIR",
                home / ".config/systemd/user",
            )
        ),
    }


def install_linux_files(paths: dict[str, Path], python: str, timezone_name: str) -> None:
    package = paths["app"] / "agentic_productivity"
    package.mkdir(parents=True, exist_ok=True)
    for key in ("state", "logs", "systemd"):
        paths[key].mkdir(parents=True, exist_ok=True)
        paths[key].chmod(0o700)
    for src in (paths["source"] / "agentic_productivity").glob("*.py"):
        dest = package / src.name
        shutil.copy2(src, dest)
        dest.chmod(0o600)
    paths["app"].chmod(0o700)
    package.chmod(0o700)
    replacements = {
        "__PYTHON__": python,
        "__APP_DIR__": str(paths["app"]),
        "__STATE_DIR__": str(paths["state"]),
        "__HOME__": str(Path.home()),
        "__TZ__": timezone_name,
        "__OUT_LOG__": str(paths["logs"] / "agentic-productivity.out.log"),
        "__ERR_LOG__": str(paths["logs"] / "agentic-productivity.err.log"),
    }
    for name in (SERVICE, TIMER):
        rendered = (paths["source"] / "systemd" / f"{name}.in").read_text(encoding="utf-8")
        for token, value in replacements.items():
            rendered = rendered.replace(token, value)
        dest = paths["systemd"] / name
        dest.write_text(rendered, encoding="utf-8")
        dest.chmod(0o600)


def enable_linux_timer(systemd_dir: Path) -> None:
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        check=True,
        env=env,
        cwd=str(systemd_dir),
    )
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", TIMER],
        check=True,
        env=env,
    )


def install_linux(*, timezone_name: str, load: bool = True) -> dict[str, str]:
    paths = linux_paths()
    python = os.environ.get("CORRAL_PYTHON") or sys.executable
    install_linux_files(paths, python, timezone_name)
    if load:
        enable_linux_timer(paths["systemd"])
    return {
        "app": str(paths["app"]),
        "state": str(paths["state"] / "metrics.sqlite3"),
        "timer": str(paths["systemd"] / TIMER),
    }
