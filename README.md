# afkiller

Tray app that auto-closes your code editor — **VS Code, Cursor, Windsurf, Antigravity, Kiro** — when you're not actively using it, so a forgotten editor session doesn't keep a billable cloud machine alive.

It's aimed at [Databricks Remote Development](https://docs.databricks.com/aws/en/dev-tools/remote-development): you connect your editor to a Databricks cluster over SSH and your code runs on the cluster. That's great — until you walk away. The SSH session keeps the (billable) cluster alive long after you've stopped working, and it's easy to forget the editor open overnight. **AFKiller solves this by closing your editor when you're idle/AFK** — and, optionally, stopping the Databricks cluster directly.

Runs on **macOS** and **Windows**. Sits in the menu bar / notification area and shows a live countdown to the next scheduled close.

> **Not affiliated.** AFKiller is an independent, unofficial tool. It is not affiliated with, endorsed by, or sponsored by any editor vendor (Cursor/Anysphere, Microsoft, Google, AWS, Codeium) or Databricks. Product names are referenced only to describe what this tool works with.

## Supported editors

AFKiller watches these VS Code-based editors (it closes whichever ones are running):

| Editor | Vendor |
|---|---|
| VS Code | Microsoft |
| Cursor | Anysphere |
| Windsurf | Codeium |
| Antigravity | Google |
| Kiro | AWS |

All are watched by default; toggle individual ones under **Settings → Watched editors**. Adding another editor is a one-line entry in `src/afkiller/editors.py`.

## How it decides to close your editor

Three independent triggers (each can be toggled on/off and given its own timer from the tray menu):

| Trigger | When it fires |
|---|---|
| **System idle** | No keyboard/mouse input anywhere on the OS for *N* minutes. |
| **Editor unfocused** | Your editor hasn't been the foreground app for *N* minutes (you've been in a browser, Slack, etc.). |
| **Hard cap** | *N* minutes have elapsed since the editor was launched, regardless of activity. |

Whichever trigger trips first wins. Defaults: System idle = 30 min, the rest disabled.

By default, **your editor is only closed while it's holding a remote SSH session** (a Databricks Remote Development tunnel, or a raw `ssh` to the driver) — closing it otherwise wouldn't free any cluster, so there's no point. Turn off *"Only close the editor when connected over SSH"* in Settings if you'd rather have it close on idle regardless.

## Close mode

Pick one in the tray menu:

- **Graceful w/ warning** *(default)* — pops a 30s countdown dialog with a Cancel button, then asks your editor to quit cleanly (your unsaved-file prompts still appear).
- **Force kill** — hard-kills the editor process tree immediately. Fastest cluster shutdown; may lose unsaved changes.

## Databricks cluster stop (optional)

Closing your editor only drops the Remote-SSH session — the cluster then runs until its *own* auto-termination, which can be an hour or two away. Enable this to **terminate the cluster directly** a configurable interval after the editor closes, closing that spend gap.

How it works:

- **Stops the cluster `N` minutes after the editor closes** (`0` = immediately). Reopen your editor within that window and the stop is cancelled. Optionally also require the system to be idle.
- **Never stops a cluster that's in use.** The stop is held off while your editor is running or while *any* SSH session to the cluster is active (so a long-running job isn't killed just because you stepped away from the keyboard).
- **Auto-detects the cluster.** Your editor's Databricks Remote Development runs a local `databricks ssh connect --cluster <id>` tunnel; the cluster ID is read from that process while you're connected and remembered for after the editor closes. You can also pin a cluster explicitly in settings (dropdown via `databricks clusters list`, or paste an ID).
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
| `delay_minutes` | `10` | Grace window after the editor closes; `0` = stop immediately. |
| `require_system_idle` | `false` | Also require the OS to be idle for the window. |
| `notify` | `true` | Native notification when a cluster is stopped. |
| `cli_path` | `""` | Path to the `databricks` binary; `""` = auto-resolve (PATH + common locations). |

The **Pause** action suppresses the cluster stop along with the editor-close triggers.

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
Quit editor now
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
