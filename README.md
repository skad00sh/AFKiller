# afkiller

Tray app that auto-closes [Cursor](https://www.cursor.com/) when you're not actively using it, so a forgotten editor session doesn't keep a billable cloud machine alive.

It's aimed at [Databricks Remote Development](https://docs.databricks.com/aws/en/dev-tools/remote-development): you connect the Cursor IDE to a Databricks cluster over SSH and your code runs on the cluster. That's great — until you walk away. The SSH session keeps the (billable) cluster alive long after you've stopped working, and it's easy to forget the IDE open overnight. **AFKiller solves this by closing the Cursor IDE when you're idle/AFK** — and, optionally, stopping the Databricks cluster directly.

Runs on **macOS** and **Windows**. Sits in the menu bar / notification area and shows a live countdown to the next scheduled close.

> **Not affiliated.** AFKiller is an independent, unofficial tool. It is not affiliated with, endorsed by, or sponsored by Cursor (Anysphere) or Databricks. "Cursor" and "Databricks" are referenced only to describe what this tool works with.

## How it decides to close Cursor

Three independent triggers (each can be toggled on/off and given its own timer from the tray menu):

| Trigger | When it fires |
|---|---|
| **System idle** | No keyboard/mouse input anywhere on the OS for *N* minutes. |
| **Cursor unfocused** | Cursor hasn't been the foreground app for *N* minutes (you've been in a browser, Slack, etc.). |
| **Hard cap** | *N* minutes have elapsed since Cursor was launched, regardless of activity. |

Whichever trigger trips first wins. Defaults: System idle = 30 min, the rest disabled.

By default, **Cursor is only closed while it's holding a remote SSH session** (a Databricks Remote Development tunnel, or a raw `ssh` to the driver) — closing it otherwise wouldn't free any cluster, so there's no point. Turn off *"Only close Cursor when connected over SSH"* in Settings if you'd rather have it close on idle regardless.

## Close mode

Pick one in the tray menu:

- **Graceful w/ warning** *(default)* — pops a 30s countdown dialog with a Cancel button, then asks Cursor to quit cleanly (your unsaved-file prompts still appear).
- **Force kill** — hard-kills the Cursor process tree immediately. Fastest cluster shutdown; may lose unsaved changes.

## Databricks cluster stop (optional)

Closing Cursor only drops the Remote-SSH session — the cluster then runs until its *own* auto-termination, which can be an hour or two away. Enable this to **terminate the cluster directly** a configurable interval after Cursor closes, closing that spend gap.

How it works:

- **Stops the cluster `N` minutes after Cursor closes** (`0` = immediately). Reopen Cursor within that window and the stop is cancelled. Optionally also require the system to be idle.
- **Never stops a cluster that's in use.** The stop is held off while Cursor is running or while *any* SSH session to the cluster is active (so a long-running job isn't killed just because you stepped away from the keyboard).
- **Auto-detects the cluster.** Cursor's Databricks Remote Development runs a local `databricks ssh connect --cluster <id>` tunnel; the cluster ID is read from that process while you're connected and remembered for after Cursor closes. You can also pin a cluster explicitly in settings (dropdown via `databricks clusters list`, or paste an ID).
- **Terminate, not delete.** It calls `databricks clusters delete`, which *stops* the cluster (reversible) — it never permanent-deletes.

Requirements:

- The [`databricks` CLI](https://docs.databricks.com/dev-tools/cli/) installed and authenticated. Both **OAuth** (`databricks auth login`) and **PAT** profiles work — we shell out to the CLI, which handles either. No tokens are stored by this app.
- Configure it in **Settings → Databricks cluster stop**: enable, pick the CLI profile, choose the cluster (or leave on auto-detect), set the delay, and use **Test connection** to confirm it's wired up. **Stop cluster now** (settings button and tray menu item) terminates on demand.

Config keys (under `[databricks]` in `config.toml`):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Master switch for the feature. |
| `profile` | `"DEFAULT"` | `~/.databrickscfg` profile (OAuth or PAT); falls back to `DATABRICKS_HOST`/`DATABRICKS_TOKEN` env if unset. |
| `cluster_id` | `""` | Explicit cluster; `""` = auto-detect from the SSH tunnel. |
| `delay_minutes` | `10` | Grace window after Cursor closes; `0` = stop immediately. |
| `require_system_idle` | `false` | Also require the OS to be idle for the window. |
| `notify` | `true` | Native notification when a cluster is stopped. |
| `cli_path` | `""` | Path to the `databricks` binary; `""` = auto-resolve (PATH + common locations). |

The **Pause** action suppresses the cluster stop along with the Cursor-close triggers.

## Cost tracking (DBUs, optional)

Puts a number on what the cluster is costing — and what AFKiller saves you. Three parts, all under **Settings → Cost tracking (DBUs)**:

- **Live meter** — while a cluster is detected, the tray shows its running consumption, e.g. `≈ 3.40 DBU · 2h13m` (uptime × your rate).
- **DBUs saved** — each time AFKiller stops a cluster early, it credits the auto-termination window it preempted to a running lifetime total (with a **Reset** button).
- **Idle alert** *(off by default)* — notifies you when a cluster is `RUNNING` with no SSH session attached for *N* minutes. Handy when auto-stop is off, or to catch a cluster you started outside Cursor.

Costs are reported in **DBUs** (Databricks' cloud-agnostic billing unit), not dollars. Databricks doesn't expose a DBU-per-node rate through the cluster API, so you enter your cluster's **DBU/hour once** in settings and every figure is an **estimate** (marked `≈`). It reuses the same `databricks` CLI profile / cluster detection as the cluster-stop feature. (Dollar conversion via a `$/DBU` rate is on the roadmap.)

> "DBUs saved" is estimated as `(autotermination_minutes − minutes already idle) × your DBU/hour`, clamped at zero. If the cluster has no auto-termination configured, the saved credit is 0 (we don't invent a number).

Config keys (under `[cost]` in `config.toml`):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch for cost tracking. |
| `dbu_per_hour` | `0.0` | Your cluster's DBU/hour; `0` = unknown (meter shows a hint instead of a number). |
| `show_in_tray` | `true` | Show the live meter line in the menu. |
| `idle_alert_enabled` | `false` | Notify when a cluster is idle but still running. |
| `idle_alert_minutes` | `15` | Idle window before the alert fires. |

Running totals live separately in `stats.toml` (next to `config.toml`), so resetting them never touches your settings.

## Setup

Requires [`uv`](https://github.com/astral-sh/uv) and Python 3.12.

```bash
git clone <this repo>
cd afkiller
uv venv --python 3.12
uv sync
uv run afkiller
```

A red "CA" badge appears in the menu bar (macOS) or notification area (Windows). Right-click it for the menu.

## Tray menu

The tray menu is a thin launcher (the native menu dismisses on every click, so trigger/threshold/close-mode/Databricks configuration lives in the **Settings** window):

```
Closes in 12:34 (system idle)
─────────────────────
Settings…
Pause 30 min
─────────────────────
Quit Cursor now
Stop cluster now        ← only when Databricks cluster stop is enabled
Quit AFKiller
```

All changes persist immediately to the config file:

- **macOS:** `~/Library/Application Support/afkiller/config.toml`
- **Windows:** `%APPDATA%\afkiller\config.toml`

## Releasing

Tagged pushes trigger `.github/workflows/release.yml`, which uses PyInstaller on `macos-latest` and `windows-latest` runners to build:

- `AFKiller-macos.zip` — contains `AFKiller.app` (menu-bar-only via `LSUIElement`)
- `AFKiller-windows.zip` — contains `AFKiller.exe`

Both are attached to a GitHub Release with auto-generated notes.

To cut a release:

```bash
git tag v0.1.0
git push --tags
```

To test the build without publishing, trigger the workflow manually from the Actions tab (the `release` job is skipped unless the ref is a tag).

To build locally for a single platform:

```bash
uv sync --group build
uv run pyinstaller --noconfirm --name AFKiller --windowed \
  --collect-all pystray --collect-all PIL \
  src/afkiller/__main__.py
# macOS only: plutil -insert LSUIElement -bool true "dist/AFKiller.app/Contents/Info.plist"
```

## License

See [LICENSE](LICENSE).
