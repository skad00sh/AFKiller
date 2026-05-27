"""Persistent config in TOML at the platform's user-config dir."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "afkiller"


@dataclass
class TriggerConfig:
    enabled: bool
    threshold_minutes: int


PAUSE_DURATION_SEC = 30 * 60


@dataclass
class DatabricksConfig:
    enabled: bool = False
    profile: str = "DEFAULT"  # ~/.databrickscfg profile; OAuth or PAT (CLI handles both)
    cluster_id: str = ""  # "" = auto-detect from the live SSH proxy process
    delay_minutes: int = 10  # grace window after Cursor closes; 0 = stop immediately
    require_system_idle: bool = False  # also require the OS to be idle for the window
    notify: bool = True  # native notification when a cluster is stopped
    cli_path: str = ""  # "" = auto-resolve the databricks binary


@dataclass
class CostConfig:
    enabled: bool = True
    dbu_per_hour: float = 0.0  # flat DBU/hr for the connected cluster; 0 = unknown
    show_in_tray: bool = True
    idle_alert_enabled: bool = False  # notify when a RUNNING cluster sits idle with no SSH
    idle_alert_minutes: int = 15


@dataclass
class Stats:
    """Running totals, persisted separately from settings (see stats_path)."""

    total_dbus_saved: float = 0.0
    stops_count: int = 0
    since: str = ""  # ISO date counting started / was last reset


@dataclass
class Config:
    close_mode: str = "graceful_warn"  # or "force_kill"
    warning_seconds: int = 30
    # Only close Cursor while it holds a remote SSH session (a Databricks Remote Dev tunnel
    # or a raw ssh to the driver). Closing it otherwise frees no cluster, so it's pointless.
    close_only_when_ssh_connected: bool = True
    # Pop a tray notification when a remote SSH session connects or disconnects.
    notify_on_ssh_change: bool = True
    # Wall-clock epoch (time.time()) until which triggers are paused; 0 = not
    # paused. Stored on disk so the separate settings process can drive pause.
    paused_until_epoch: float = 0.0
    triggers: dict[str, TriggerConfig] = field(
        default_factory=lambda: {
            "system_idle": TriggerConfig(enabled=True, threshold_minutes=30),
            "cursor_unfocused": TriggerConfig(enabled=False, threshold_minutes=20),
            "hard_cap": TriggerConfig(enabled=False, threshold_minutes=240),
        }
    )
    databricks: DatabricksConfig = field(default_factory=DatabricksConfig)
    cost: CostConfig = field(default_factory=CostConfig)


VALID_CLOSE_MODES = {"graceful_warn", "force_kill"}
TRIGGER_KEYS = ("system_idle", "cursor_unfocused", "hard_cap")

TRIGGER_LABELS: dict[str, str] = {
    "system_idle": "System idle",
    "cursor_unfocused": "Cursor unfocused",
    "hard_cap": "Hard cap",
}

# Per-trigger preset thresholds (minutes), offered in the menu/settings dropdowns.
TRIGGER_PRESETS: dict[str, tuple[int, ...]] = {
    "system_idle": (5, 10, 15, 30, 60),
    "cursor_unfocused": (5, 10, 15, 20, 30, 60),
    "hard_cap": (60, 120, 240, 480),
}

# Grace-window presets (minutes) for the Databricks cluster-stop, offered in settings.
# 0 = stop the cluster immediately when Cursor closes.
DATABRICKS_DELAY_PRESETS: tuple[int, ...] = (0, 5, 10, 15, 30, 60)


def human_minutes(m: int) -> str:
    if m % 60 == 0 and m >= 60:
        return f"{m // 60} h"
    return f"{m} min"


def human_delay(m: int) -> str:
    """Human label for a cluster-stop grace window; 0 means immediate."""
    return "Immediately" if m <= 0 else human_minutes(m)


def _toml_str(s: str) -> str:
    """Render a Python string as a TOML basic string (escape backslashes + quotes)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.toml"


def status_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "status.txt"


def write_status(text: str) -> None:
    """Best-effort publish of the live countdown for the settings window to read."""
    try:
        p = status_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    except OSError:
        pass


def read_status() -> str:
    try:
        return status_path().read_text(encoding="utf-8").strip() or "AFKiller"
    except OSError:
        return "AFKiller"


def stats_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "stats.toml"


def load_stats() -> Stats:
    """Running totals, stored separately from config.toml so the watcher's frequent writes
    don't trip the settings window's config mtime-reload, and a Reset can't clobber settings."""
    path = stats_path()
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return Stats(since=date.today().isoformat())

    stats = Stats(since=date.today().isoformat())
    section = data.get("cost", {})
    if isinstance(section, dict):
        saved = section.get("total_dbus_saved")
        if isinstance(saved, (int, float)) and not isinstance(saved, bool) and saved >= 0:
            stats.total_dbus_saved = float(saved)
        count = section.get("stops_count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            stats.stops_count = count
        since = section.get("since")
        if isinstance(since, str) and since.strip():
            stats.since = since.strip()
    return stats


def save_stats(stats: Stats) -> None:
    path = stats_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "[cost]",
            f"total_dbus_saved = {stats.total_dbus_saved}",
            f"stops_count = {stats.stops_count}",
            f"since = {_toml_str(stats.since)}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def reset_stats() -> Stats:
    """Zero the totals and restart the 'since' date. Returns the fresh Stats."""
    fresh = Stats(since=date.today().isoformat())
    save_stats(fresh)
    return fresh


def load() -> Config:
    path = config_path()
    if not path.exists():
        cfg = Config()
        save(cfg)
        return cfg

    with path.open("rb") as f:
        data = tomllib.load(f)

    cfg = Config()
    mode = data.get("close_mode")
    if mode in VALID_CLOSE_MODES:
        cfg.close_mode = mode
    warn = data.get("warning_seconds")
    if isinstance(warn, int) and warn > 0:
        cfg.warning_seconds = warn
    ssh_gate = data.get("close_only_when_ssh_connected")
    if isinstance(ssh_gate, bool):
        cfg.close_only_when_ssh_connected = ssh_gate
    ssh_notify = data.get("notify_on_ssh_change")
    if isinstance(ssh_notify, bool):
        cfg.notify_on_ssh_change = ssh_notify
    paused = data.get("paused_until_epoch")
    if isinstance(paused, (int, float)) and paused > 0:
        cfg.paused_until_epoch = float(paused)

    triggers = data.get("triggers", {})
    for key in TRIGGER_KEYS:
        raw = triggers.get(key)
        if not isinstance(raw, dict):
            continue
        existing = cfg.triggers[key]
        if isinstance(raw.get("enabled"), bool):
            existing.enabled = raw["enabled"]
        thr = raw.get("threshold_minutes")
        if isinstance(thr, int) and thr > 0:
            existing.threshold_minutes = thr

    db = data.get("databricks")
    if isinstance(db, dict):
        d = cfg.databricks
        if isinstance(db.get("enabled"), bool):
            d.enabled = db["enabled"]
        prof = db.get("profile")
        if isinstance(prof, str) and prof.strip():
            d.profile = prof.strip()
        cid = db.get("cluster_id")
        if isinstance(cid, str):
            d.cluster_id = cid.strip()
        delay = db.get("delay_minutes")
        if isinstance(delay, int) and not isinstance(delay, bool) and delay >= 0:
            d.delay_minutes = delay
        if isinstance(db.get("require_system_idle"), bool):
            d.require_system_idle = db["require_system_idle"]
        if isinstance(db.get("notify"), bool):
            d.notify = db["notify"]
        cli = db.get("cli_path")
        if isinstance(cli, str):
            d.cli_path = cli.strip()

    cost = data.get("cost")
    if isinstance(cost, dict):
        c = cfg.cost
        if isinstance(cost.get("enabled"), bool):
            c.enabled = cost["enabled"]
        rate = cost.get("dbu_per_hour")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate >= 0:
            c.dbu_per_hour = float(rate)
        if isinstance(cost.get("show_in_tray"), bool):
            c.show_in_tray = cost["show_in_tray"]
        if isinstance(cost.get("idle_alert_enabled"), bool):
            c.idle_alert_enabled = cost["idle_alert_enabled"]
        mins = cost.get("idle_alert_minutes")
        if isinstance(mins, int) and not isinstance(mins, bool) and mins > 0:
            c.idle_alert_minutes = mins

    return cfg


def save(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f'close_mode = "{cfg.close_mode}"',
        f"warning_seconds = {cfg.warning_seconds}",
        f"close_only_when_ssh_connected = "
        f"{'true' if cfg.close_only_when_ssh_connected else 'false'}",
        f"notify_on_ssh_change = {'true' if cfg.notify_on_ssh_change else 'false'}",
        f"paused_until_epoch = {cfg.paused_until_epoch}",
        "",
    ]
    for key, trig in cfg.triggers.items():
        lines.append(f"[triggers.{key}]")
        lines.append(f"enabled = {'true' if trig.enabled else 'false'}")
        lines.append(f"threshold_minutes = {trig.threshold_minutes}")
        lines.append("")

    d = cfg.databricks
    lines.append("[databricks]")
    lines.append(f"enabled = {'true' if d.enabled else 'false'}")
    lines.append(f"profile = {_toml_str(d.profile)}")
    lines.append(f"cluster_id = {_toml_str(d.cluster_id)}")
    lines.append(f"delay_minutes = {d.delay_minutes}")
    lines.append(f"require_system_idle = {'true' if d.require_system_idle else 'false'}")
    lines.append(f"notify = {'true' if d.notify else 'false'}")
    lines.append(f"cli_path = {_toml_str(d.cli_path)}")
    lines.append("")

    c = cfg.cost
    lines.append("[cost]")
    lines.append(f"enabled = {'true' if c.enabled else 'false'}")
    lines.append(f"dbu_per_hour = {c.dbu_per_hour}")
    lines.append(f"show_in_tray = {'true' if c.show_in_tray else 'false'}")
    lines.append(f"idle_alert_enabled = {'true' if c.idle_alert_enabled else 'false'}")
    lines.append(f"idle_alert_minutes = {c.idle_alert_minutes}")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def to_dict(cfg: Config) -> dict:
    return asdict(cfg)
