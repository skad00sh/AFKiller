"""Databricks cluster control via the ``databricks`` CLI + local process inspection.

We shell out to the CLI (rather than hand-rolling REST) so it transparently handles both
OAuth (U2M) and PAT auth from ``~/.databrickscfg``/env — there is no credential handling
here. Which cluster to act on is auto-detected from the live SSH tunnel: Cursor's Databricks
Remote Development runs a local ``databricks ssh connect --cluster <id>`` proxy process, so
that process both names the cluster and signals "a session is active, don't stop".

Every CLI call is wrapped with a timeout and broad exception handling: a missing,
unauthenticated, or slow CLI must degrade to a no-op, never crash the watcher thread."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

import psutil

# Cluster states (subset of the Databricks API ClusterState enum) we care about.
RUNNING = "RUNNING"
# States where a terminate call is pointless — already stopped or on the way down.
INACTIVE_STATES = {"TERMINATED", "TERMINATING"}

_CLI_TIMEOUT = 15.0  # seconds for a single-cluster / auth call (get, delete, current-user)
_LIST_TIMEOUT = 60.0  # listing every cluster can be slow in a large workspace
_CLUSTER_ARG_RE = re.compile(r"--cluster(?:[=\s]+)([A-Za-z0-9._-]+)")


def resolve_cli(cli_path: str = "") -> str | None:
    """Locate the ``databricks`` binary. Honors an explicit ``cli_path``, then PATH, then
    common install locations (LaunchAgents/login items run with a minimal PATH)."""
    if cli_path:
        return cli_path if os.path.isfile(cli_path) and os.access(cli_path, os.X_OK) else None

    found = shutil.which("databricks")
    if found:
        return found

    name = "databricks.exe" if sys.platform == "win32" else "databricks"
    candidates = [
        os.path.expanduser(f"~/.local/bin/{name}"),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _run(cli: str, args: list[str], *, timeout: float = _CLI_TIMEOUT) -> subprocess.CompletedProcess | None:
    """Run the CLI, returning the completed process or None on any failure."""
    try:
        return subprocess.run(
            [cli, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[afkiller] databricks CLI call failed: {e}", file=sys.stderr)
        return None


def _profile_args(profile: str) -> list[str]:
    """Pass --profile only when set; otherwise the CLI uses DEFAULT / env vars."""
    return ["--profile", profile] if profile else []


def _cluster_get(cluster_id: str, profile: str, cli: str) -> dict | None:
    """Parsed ``clusters get`` JSON for one cluster, or None on any failure."""
    proc = _run(cli, ["clusters", "get", cluster_id, "--output", "json", *_profile_args(profile)])
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def cluster_state(cluster_id: str, profile: str = "", cli: str | None = None) -> str | None:
    """Return the cluster's current state (e.g. ``RUNNING``), or None if unknown."""
    if not cluster_id:
        return None
    cli = cli or resolve_cli()
    if not cli:
        return None
    data = _cluster_get(cluster_id, profile, cli)
    if data is None:
        return None
    state = data.get("state")
    return str(state) if isinstance(state, str) else None


def cluster_info(cluster_id: str, profile: str = "", cli: str | None = None) -> tuple[str, str]:
    """Return (cluster_name, state) for one cluster; empty strings if unknown. A single
    ``clusters get`` — fast, unlike listing every cluster."""
    if not cluster_id:
        return ("", "")
    cli = cli or resolve_cli()
    if not cli:
        return ("", "")
    data = _cluster_get(cluster_id, profile, cli)
    if data is None:
        return ("", "")
    name = data.get("cluster_name")
    state = data.get("state")
    return (
        str(name) if isinstance(name, str) else "",
        str(state) if isinstance(state, str) else "",
    )


@dataclass
class ClusterRuntime:
    """Snapshot of a cluster's runtime, for the DBU cost meter / saved estimate."""

    state: str  # e.g. "RUNNING", "TERMINATED"; "" if unknown
    uptime_seconds: float | None  # since last start; None if no usable timestamp
    autotermination_minutes: int | None  # the cluster's own idle-shutdown window; None if unset
    name: str  # cluster_name, or "" if unknown


def cluster_runtime(cluster_id: str, profile: str = "", cli: str | None = None) -> ClusterRuntime | None:
    """One ``clusters get`` returning the bits the cost meter needs: state, uptime, and the
    cluster's own auto-termination window. ``None`` on any failure (never raises).

    Uptime is measured from ``last_restarted_time`` (the current run) when present, else
    ``start_time`` — both epoch-milliseconds. A missing/zero timestamp yields ``uptime_seconds
    = None`` rather than a bogus huge value."""
    if not cluster_id:
        return None
    cli = cli or resolve_cli()
    if not cli:
        return None
    data = _cluster_get(cluster_id, profile, cli)
    if data is None:
        return None

    state = data.get("state")
    state = str(state) if isinstance(state, str) else ""

    name = data.get("cluster_name")
    name = str(name) if isinstance(name, str) else ""

    autoterm = data.get("autotermination_minutes")
    autoterm = autoterm if isinstance(autoterm, int) and not isinstance(autoterm, bool) else None

    ts_ms = data.get("last_restarted_time") or data.get("start_time")
    uptime_seconds: float | None = None
    if isinstance(ts_ms, (int, float)) and not isinstance(ts_ms, bool) and ts_ms > 0:
        uptime_seconds = max(0.0, time.time() - ts_ms / 1000.0)

    return ClusterRuntime(
        state=state,
        uptime_seconds=uptime_seconds,
        autotermination_minutes=autoterm,
        name=name,
    )


def terminate_cluster(cluster_id: str, profile: str = "", cli: str | None = None) -> bool:
    """Terminate (stop) the cluster via ``clusters delete``. Reversible — the cluster
    config is kept and can be restarted. Returns True on a successful CLI exit."""
    if not cluster_id:
        return False
    cli = cli or resolve_cli()
    if not cli:
        return False
    proc = _run(cli, ["clusters", "delete", cluster_id, *_profile_args(profile)])
    if proc is None:
        return False
    if proc.returncode != 0:
        print(
            f"[afkiller] cluster terminate failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    return True


def current_user(profile: str = "", cli: str | None = None) -> str | None:
    """Return the authenticated user's name. A fast way to validate the CLI + auth
    without listing every cluster (which can time out in a large workspace)."""
    cli = cli or resolve_cli()
    if not cli:
        return None
    proc = _run(cli, ["current-user", "me", "--output", "json", *_profile_args(profile)])
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    name = data.get("userName") or data.get("displayName")
    return str(name) if isinstance(name, str) else None


def list_clusters(
    profile: str = "", cli: str | None = None, timeout: float = _LIST_TIMEOUT
) -> list[tuple[str, str, str]]:
    """Return [(cluster_id, cluster_name, state), ...] for the settings dropdown.
    Empty list on any failure. Uses a generous timeout since listing every cluster is
    slow in large workspaces."""
    cli = cli or resolve_cli()
    if not cli:
        return []
    proc = _run(
        cli, ["clusters", "list", "--output", "json", *_profile_args(profile)], timeout=timeout
    )
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    # Newer CLI returns a JSON array; older wraps it as {"clusters": [...]}.
    rows = data.get("clusters", []) if isinstance(data, dict) else data
    out: list[tuple[str, str, str]] = []
    if isinstance(rows, list):
        for c in rows:
            if not isinstance(c, dict):
                continue
            cid = c.get("cluster_id")
            if not isinstance(cid, str) or not cid:
                continue
            name = c.get("cluster_name") if isinstance(c.get("cluster_name"), str) else ""
            state = c.get("state") if isinstance(c.get("state"), str) else ""
            out.append((cid, name, state))
    return out


def list_profiles(cli: str | None = None) -> list[str]:
    """Profile names from ``~/.databrickscfg`` via ``databricks auth profiles``. Lets the
    settings window offer a dropdown instead of making the user know the exact name."""
    cli = cli or resolve_cli()
    if not cli:
        return []
    proc = _run(cli, ["auth", "profiles", "--output", "json"])
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    rows = data.get("profiles", []) if isinstance(data, dict) else data
    names: list[str] = []
    if isinstance(rows, list):
        for p in rows:
            if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"]:
                names.append(p["name"])
    return names


def _iter_cmdlines():
    """Yield (proc, lowercased cmdline string, original-case cmdline string)."""
    for proc in psutil.process_iter(attrs=["cmdline"]):
        try:
            parts = proc.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not parts:
            continue
        joined = " ".join(parts)
        yield proc, joined.lower(), joined


def detect_active_cluster_ids() -> list[str]:
    """All cluster IDs from live ``databricks ssh connect --cluster <id>`` proxy processes
    (what Cursor's Remote Development spawns). Usually one; empty if none are connected."""
    ids: list[str] = []
    for _proc, low, orig in _iter_cmdlines():
        if "databricks" in low and "ssh" in low and "connect" in low and "--cluster" in low:
            m = _CLUSTER_ARG_RE.search(orig)
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
    return ids


def detect_active_cluster_id() -> str | None:
    """The cluster ID of the first live SSH tunnel, or None if none are connected."""
    ids = detect_active_cluster_ids()
    return ids[0] if ids else None


def scan_sessions() -> tuple[list[str], bool]:
    """One process scan returning ``(cluster_ids, ssh_active)``.

    ``cluster_ids`` come from live ``databricks ssh connect --cluster <id>`` proxies (what
    Cursor's Remote Development spawns); ``ssh_active`` is True if any such proxy — or a raw
    ``ssh ... -p 2200`` to a cluster driver — is alive. Combines what
    ``detect_active_cluster_ids()`` and ``ssh_session_active()`` do in a single pass, since
    the watcher needs both every tick."""
    ids: list[str] = []
    active = False
    for _proc, low, orig in _iter_cmdlines():
        if "databricks" in low and "ssh" in low and "connect" in low and "--cluster" in low:
            active = True
            m = _CLUSTER_ARG_RE.search(orig)
            if m and m.group(1) not in ids:
                ids.append(m.group(1))
        elif low.split(" ", 1)[0].endswith("ssh") and " 2200" in f" {low}":
            active = True
    return ids, active


def ssh_session_active(cluster_id: str | None = None) -> bool:
    """True if an SSH session to the cluster appears active, so we must not stop it.

    Primary signal: a live ``databricks ssh connect`` proxy (covers Cursor and a terminal
    ``ssh <name>``). If ``cluster_id`` is given, only a proxy for *that* cluster counts.
    Secondary best-effort signal: a raw ``ssh ... -p 2200`` to a cluster driver (the classic
    direct-SSH method)."""
    for _proc, low, orig in _iter_cmdlines():
        if "databricks" in low and "ssh" in low and "connect" in low and "--cluster" in low:
            if cluster_id is None:
                return True
            m = _CLUSTER_ARG_RE.search(orig)
            if m and m.group(1) == cluster_id:
                return True
        # Classic direct SSH to the driver node listens on 2200.
        if low.split(" ", 1)[0].endswith("ssh") and " 2200" in f" {low}":
            return True
    return False
