"""Persistent config in TOML at the platform's user-config dir."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "cursor-assassin"


@dataclass
class TriggerConfig:
    enabled: bool
    threshold_minutes: int


@dataclass
class Config:
    close_mode: str = "graceful_warn"  # or "force_kill"
    warning_seconds: int = 30
    triggers: dict[str, TriggerConfig] = field(
        default_factory=lambda: {
            "system_idle": TriggerConfig(enabled=True, threshold_minutes=30),
            "cursor_unfocused": TriggerConfig(enabled=False, threshold_minutes=20),
            "hard_cap": TriggerConfig(enabled=False, threshold_minutes=240),
        }
    )


VALID_CLOSE_MODES = {"graceful_warn", "force_kill"}
TRIGGER_KEYS = ("system_idle", "cursor_unfocused", "hard_cap")


def config_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / "config.toml"


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

    return cfg


def save(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f'close_mode = "{cfg.close_mode}"',
        f"warning_seconds = {cfg.warning_seconds}",
        "",
    ]
    for key, trig in cfg.triggers.items():
        lines.append(f"[triggers.{key}]")
        lines.append(f"enabled = {'true' if trig.enabled else 'false'}")
        lines.append(f"threshold_minutes = {trig.threshold_minutes}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def to_dict(cfg: Config) -> dict:
    return asdict(cfg)
