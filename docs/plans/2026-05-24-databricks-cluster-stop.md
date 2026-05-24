# Plan: Databricks cluster stop (v2)

> Status: planned, not yet implemented. Branch: `feat/databricks-cluster-stop`.
> Authored 2026-05-24. Implementation paused — to be continued.

## Context

cursor-assassin closes Cursor on idle so a forgotten Remote-SSH session doesn't keep a
billable Databricks cluster alive. But closing Cursor only drops the SSH session — the
cluster then runs until its *own* auto-termination, which can be 60–120 min away. That gap
is wasted spend. This feature closes it by proactively **terminating** the cluster via the
Databricks CLI a configurable interval after Cursor closes.

Refined requirement (supersedes the original ROADMAP framing of "call the API when Cursor
closes"):
- Stop the cluster **X minutes after Cursor closes** (X configurable; `0` = immediately).
- **Never** stop while Cursor is running, or while *any* SSH session to the cluster is
  active (the user may be running a long job while idle at the keyboard).
- **Stop = terminate** (`databricks clusters delete`, reversible) — never permanent-delete.

## Locked decisions (from discussion)

- **Inverse trigger.** Unlike existing triggers (count while Cursor runs → close Cursor),
  this counts while Cursor is *absent* → stops the cluster. It gets its own watcher state
  and its own config block, not an entry in the existing `triggers` dict.
- **Timer basis is configurable** (covers all three discussed behaviors): a
  `delay_minutes` threshold (`0` = stop immediately on close) plus a `require_system_idle`
  toggle (when on, also require the OS to be idle for the same window).
- **Startup safety:** only arm the timer after observing Cursor go running → closed *in
  this session*. Never act on a cluster that was already running with Cursor closed at
  startup.
- **API transport: shell out to the `databricks` CLI.** This supports **both OAuth and
  PAT** with no extra work and no "auth method" toggle: the CLI reads whatever is in the
  selected `~/.databrickscfg` profile (or env) and does the right thing —
    - OAuth (U2M): profile has `host` + OAuth fields (from `databricks auth login`); CLI
      mints/refreshes a token per call.
    - PAT: profile has `host` + `token`, or env `DATABRICKS_HOST` + `DATABRICKS_TOKEN`; CLI
      uses the token directly.
  We just pass `--profile <name>` (env-var fallback when unset). This reuses the user's
  existing auth, needs no credential UI, and adds no Python deps. (A hand-rolled REST path
  would have supported PAT only — the CLI is what makes "both" free. Remote Development
  already requires the CLI, so it's present regardless.)
- **Cluster auto-detection** via the SSH tunnel: Cursor's Databricks Remote Development
  runs a local `databricks ssh connect --cluster <id>` proxy process. While Cursor is
  connected, we read the cluster ID from that process's command line and remember it; after
  Cursor closes we act on the remembered cluster. Manual override: explicit `cluster_id` in
  config, or a dropdown populated from `databricks clusters list`.
- **SSH guard = same proxy process.** A live `databricks ssh connect` process means a
  session is active (covers Cursor *and* terminal `ssh <name>`); best-effort secondary check
  for a raw `ssh ... -p 2200` to the driver (classic direct method).
- **Features in scope:** A) manual "Stop cluster now", B) cluster-state check + display,
  C) "Test connection" button in settings, D) native notification on stop. Respect the
  existing global **Pause**.

## How the Databricks SSH connection actually works (verified)

`databricks ssh setup --name <n> --cluster <id>` writes an `~/.ssh/config` entry whose
ProxyCommand runs the CLI; `ssh <n>` (what Cursor does) spawns a local
`databricks ssh connect --cluster <id> [--profile <p>]` process that tunnels to the driver.
Default `--shutdown-delay 10m` tears down only the SSH *server/proxy* after the last client
disconnects — **not** the cluster. So terminating the cluster ourselves is the actual
money-saver, and the proxy process is a reliable, cluster-ID-bearing signal.

## Implementation

### New module: `src/cursor_assassin/databricks.py`
Thin wrapper over the CLI + process inspection. No new deps (uses stdlib `subprocess`/`json`
and existing `psutil`).
- `resolve_cli() -> str | None` — `shutil.which("databricks")` then common paths
  (`~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`); honor configured `cli_path`.
- `cluster_state(cluster_id, profile) -> str | None` — `databricks clusters get <id>
  --output json --profile <p>`, parse `state` (RUNNING/PENDING/TERMINATING/TERMINATED/…).
- `terminate_cluster(cluster_id, profile) -> bool` — `databricks clusters delete <id>
  --profile <p>` (terminate). Skip if state is already TERMINATED/TERMINATING.
- `list_clusters(profile) -> list[(id, name, state)]` — `databricks clusters list
  --output json` for the settings dropdown / test-connection.
- `detect_active_cluster_id() -> str | None` — scan `psutil.process_iter(['cmdline'])` for
  a `databricks … ssh … connect … --cluster <id>` process; return the id.
- `ssh_session_active(cluster_id) -> bool` — true if such a proxy process is alive; plus
  best-effort scan for an `ssh` proc with `-p 2200` to the driver host.
- All CLI calls wrapped with a short `timeout` and broad exception handling — a failed CLI
  call must never crash the watcher.

### `config.py`
Add a `DatabricksConfig` dataclass and a `databricks` field on `Config`, persisted in the
hand-rolled TOML writer/parser (mirror the existing `[triggers.*]` pattern in `save()`/
`load()` with type/range validation):
```
[databricks]
enabled = false
profile = "DEFAULT"      # ~/.databrickscfg profile; may be OAuth or PAT (CLI handles both).
                         # If unset, CLI falls back to DATABRICKS_HOST/DATABRICKS_TOKEN env.
cluster_id = ""          # "" = auto-detect from the SSH proxy process
delay_minutes = 10       # grace window after Cursor closes; 0 = immediate
require_system_idle = false
notify = true
cli_path = ""            # "" = auto-resolve
```

### `app.py` (watcher wiring)
- New `App` state: `cursor_closed_at: Optional[float]`, `last_connected_cluster_id:
  Optional[str]`.
- In `_tick`, when `databricks.enabled`:
  - While Cursor running: periodically (every ~5s, not every tick) call
    `detect_active_cluster_id()` and store into `last_connected_cluster_id` (unless an
    explicit `cluster_id` is configured).
  - On running → closed transition: set `cursor_closed_at = now`.
  - While Cursor not running and not paused: target = configured `cluster_id` or
    `last_connected_cluster_id`. If target known, elapsed-since-close ≥ `delay_minutes`
    (and, if `require_system_idle`, `idle.seconds_since_input()` ≥ window), **and**
    `not ssh_session_active(target)`, and `cluster_state(target) == "RUNNING"` →
    `terminate_cluster()`, fire notification (D), clear `cursor_closed_at`.
  - Countdown text: when Cursor is closed and a stop is armed, show
    `"Cluster stops in MM:SS"` instead of today's bare `"Cursor not running"`.
- New callback `_stop_cluster_now()` for feature A (resolve target, terminate, notify).
- Notifications (D): `self.icon.notify(...)` via pystray where supported; macOS fallback
  `osascript -e 'display notification …'`.

### `tray.py`
Add a `"Stop cluster now"` menu item (feature A), wired to `_stop_cluster_now`, shown only
when `databricks.enabled`.

### `settings.py`
New "Databricks" section: enable checkbox; profile entry; cluster control (auto-detect
label + dropdown from `list_clusters` with a Refresh button, or manual id); delay dropdown
(incl. an "Immediately" = 0 entry); "Also require system idle" checkbox; **Test connection**
button (C) that runs `list_clusters`/`cluster_state` and shows result + current state; and a
**Stop cluster now** button (A). All writes go straight to `config.toml` like the existing
controls (tray reloads on mtime change).

### Docs
- README: new "Databricks cluster stop" section (CLI prerequisite, OAuth profile, config
  keys, auto-detect vs. manual cluster id).
- ROADMAP: remove/mark-done the v2 item.

## Risks / notes
- Exact CLI subcommand syntax/flags vary by `databricks` CLI version — verify against the
  installed binary during implementation (`clusters get/delete/list`, positional id,
  `--output json`, `--profile`).
- Auto-detect relies on the CLI-tunnel Remote Dev flow. For raw direct-SSH setups, the user
  must set `cluster_id` explicitly; document this.
- Frozen/LaunchAgent PATH is minimal — hence `resolve_cli()` checks common locations and a
  `cli_path` override.

## Verification
- No existing test suite; verify manually.
- `databricks.py` in isolation: `uv run python -c "from cursor_assassin import databricks as d;
  print(d.resolve_cli()); print(d.list_clusters('DEFAULT'))"` against the user's real
  profile (read-only).
- Detection: with Cursor connected to a cluster, confirm `detect_active_cluster_id()` and
  `ssh_session_active()` return the right values; disconnect and confirm they flip.
- End-to-end (guarded): set `delay_minutes` low, enable, close Cursor, confirm the countdown
  appears and the cluster transitions to TERMINATING via `clusters get`; reconnect during the
  window and confirm the stop is cancelled. Recommend first dry-run by watching the countdown
  + state logs before trusting the terminate on a real cluster.
- Settings: Test connection reports state; "Stop cluster now" terminates on demand; Pause
  suppresses the auto-stop.
