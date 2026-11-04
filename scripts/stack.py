"""Shared helpers for `make eval` and `make loadtest` (stdlib only).

Each harness runs in its own compose project (own containers, volumes and host ports), so it never
touches the demo stack started by `make up`, and tearing it down only deletes its own data.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"{path} not found: run `cp .env.example .env` and fill it in")
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Stack:
    def __init__(self, project: str, env_file: Path, ports: dict[str, int]):
        self.project = project
        self.env_file = env_file
        self.env = load_env(env_file)
        self.ports = ports
        # Shell env wins over --env-file in compose interpolation: this is how the ports are isolated.
        self._proc_env = {**os.environ, **{k: str(v) for k, v in ports.items()}}

    @property
    def network(self) -> str:
        return f"{self.project}_default"

    def compose(self, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["docker", "compose", "-p", self.project, "--env-file", str(self.env_file), *args]
        return subprocess.run(cmd, cwd=ROOT, env=self._proc_env, check=check, text=True, encoding="utf-8",
                              capture_output=capture)

    def up(self, *services: str) -> None:
        log(f"starting {self.project}: {' '.join(services)}")
        self.compose("up", "-d", "--build", "--wait", "--wait-timeout", "300", *services, capture=True)

    def down(self) -> None:
        log(f"removing {self.project} (its own containers and volumes only)")
        self.compose("down", "-v", "--remove-orphans", capture=True, check=False)

    def psql(self, db: str, sql: str) -> str:
        r = self.compose("exec", "-T", "postgres", "psql", "-U", "postgres", "-d", db, "-At", "-v",
                         "ON_ERROR_STOP=1", "-c", sql, capture=True)
        return r.stdout.strip()

    def rows(self, db: str, sql: str) -> list[dict]:
        return json.loads(self.psql(db, f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t") or "[]")

    def row(self, db: str, sql: str) -> dict:
        rows = self.rows(db, sql)
        return rows[0] if rows else {}


def http_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_http(url: str, timeout_s: float = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if http_json(url, timeout=5).get("status") == "UP":
                return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {url}")
        time.sleep(2)


def _powershell(cmd: str) -> str:
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                              timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def machine_specs() -> dict:
    """Host CPU/RAM/OS plus what Docker's VM actually gets (the containers' real ceiling)."""
    specs: dict = {"os": f"{platform.system()} {platform.release()} ({platform.version()})",
                   "logical_cpus": os.cpu_count()}
    if platform.system() == "Windows":
        specs["cpu"] = _powershell("(Get-CimInstance Win32_Processor | Select-Object -First 1).Name")
        ram = _powershell("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
        specs["ram_gb"] = round(int(ram) / 2**30, 1) if ram.isdigit() else None
    else:
        specs["cpu"] = platform.processor() or None
        try:
            pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
            specs["ram_gb"] = round(pages * size / 2**30, 1)
        except (ValueError, OSError, AttributeError):
            specs["ram_gb"] = None
    try:
        out = subprocess.run(["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True,
                             timeout=60).stdout
        info = json.loads(out)
        specs["docker"] = {"server_version": info.get("ServerVersion"), "vm_cpus": info.get("NCPU"),
                           "vm_memory_gb": round(info.get("MemTotal", 0) / 2**30, 1),
                           "vm_os": info.get("OperatingSystem"), "kernel": info.get("KernelVersion")}
    except (OSError, subprocess.SubprocessError, ValueError):
        specs["docker"] = None
    return specs


def timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S")
