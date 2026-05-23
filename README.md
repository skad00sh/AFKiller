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

```
Cursor closes in 12:34 (system idle)
─────────────────────
Triggers ▸
    System idle (30 min) ▸  [Enabled ✓] [5/10/15/30/60 min]
    Cursor unfocused (20 min) ▸  [Enabled] [5/10/15/20/30/60 min]
    Hard cap (4 h) ▸  [Enabled] [1/2/4/8 h]
Close mode ▸
    ● Graceful w/ warning
    ○ Force kill
Pause 30 min
─────────────────────
Quit Cursor now
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
