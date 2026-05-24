# cursor-assassin

Tray app that auto-closes [Cursor](https://www.cursor.com/) when you're not actively using it, so a forgotten Remote-SSH session doesn't keep a billable Databricks cluster alive overnight.

Runs on **macOS** and **Windows**. Sits in the menu bar / notification area and shows a live countdown to the next scheduled close.

## How it decides to close Cursor

Three independent triggers (each can be toggled on/off and given its own timer from the tray menu):

| Trigger | When it fires |
|---|---|
| **System idle** | No keyboard/mouse input anywhere on the OS for *N* minutes. |
| **Cursor unfocused** | Cursor hasn't been the foreground app for *N* minutes (you've been in a browser, Slack, etc.). |
| **Hard cap** | *N* minutes have elapsed since Cursor was launched, regardless of activity. |

Whichever trigger trips first wins. Defaults: System idle = 30 min, the rest disabled.

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

## Setup

Requires [`uv`](https://github.com/astral-sh/uv) and Python 3.12.

```bash
git clone <this repo>
cd cursor-assassin
uv venv --python 3.12
uv sync
uv run cursor-assassin
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
Quit Cursor Assassin
```

All changes persist immediately to the config file:

- **macOS:** `~/Library/Application Support/cursor-assassin/config.toml`
- **Windows:** `%APPDATA%\cursor-assassin\config.toml`

## Auto-start on login

**macOS** — drop this LaunchAgent at `~/Library/LaunchAgents/com.cursor-assassin.plist` (replace the path to `uv` if different):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.cursor-assassin</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.local/bin/uv</string>
    <string>run</string>
    <string>--directory</string>
    <string>/Users/YOU/Desktop/cursor-assassin</string>
    <string>cursor-assassin</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

Then `launchctl load ~/Library/LaunchAgents/com.cursor-assassin.plist`.

**Windows** — `Win+R` → `shell:startup` → drop a shortcut whose target is:

```
"C:\Users\YOU\.local\bin\uv.exe" run --directory "C:\path\to\cursor-assassin" cursor-assassin
```

## Releasing

Tagged pushes trigger `.github/workflows/release.yml`, which uses PyInstaller on `macos-latest` and `windows-latest` runners to build:

- `CursorAssassin-macos.zip` — contains `CursorAssassin.app` (menu-bar-only via `LSUIElement`)
- `CursorAssassin-windows.zip` — contains `CursorAssassin.exe`

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
uv run pyinstaller --noconfirm --name CursorAssassin --windowed \
  --collect-all pystray --collect-all PIL \
  src/cursor_assassin/__main__.py
# macOS only: plutil -insert LSUIElement -bool true "dist/CursorAssassin.app/Contents/Info.plist"
```

## License

See [LICENSE](LICENSE).
