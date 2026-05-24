# Roadmap

- **Custom per-trigger minute input.** v1 ships preset values only (5/10/15/30/60 etc.); add a "Custom..." option that prompts for an arbitrary number of minutes.
- **Windows auto-start installer.** Currently documented as a manual shortcut in `shell:startup`; ship a one-click installer that drops it for the user.

## Shipped

- **v2 — Databricks cluster stop.** Terminates the cluster a configurable interval after Cursor closes, via the `databricks` CLI (so it handles both OAuth and PAT auth). The target cluster is auto-detected from the live SSH tunnel, and the stop is held off while any SSH session is active. See the "Databricks cluster stop" section in the README. *(Note: implemented against the CLI rather than the originally-planned raw `POST /api/2.0/clusters/delete`, because OAuth profiles have no static token to read.)*
