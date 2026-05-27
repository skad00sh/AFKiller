# Roadmap

- **Custom per-trigger minute input.** v1 ships preset values only (5/10/15/30/60 etc.); add a "Custom..." option that prompts for an arbitrary number of minutes.
- **Windows auto-start installer.** Currently documented as a manual shortcut in `shell:startup`; ship a one-click installer that drops it for the user.
- **DBU → dollar conversion.** Cost tracking reports DBUs; add a `$/DBU` rate so the live meter and "saved" total can also show dollars.
- **Auto-detect DBU/hour from node types.** Bundle a `node_type → DBU/hour` lookup so the rate auto-fills from the cluster's shape (`clusters get`) instead of being entered by hand; fall back to the manual rate when a node type is unknown.

## Shipped

- **v2 — Databricks cluster stop.** Terminates the cluster a configurable interval after Cursor closes, via the `databricks` CLI (so it handles both OAuth and PAT auth). The target cluster is auto-detected from the live SSH tunnel, and the stop is held off while any SSH session is active. See the "Databricks cluster stop" section in the README. *(Note: implemented against the CLI rather than the originally-planned raw `POST /api/2.0/clusters/delete`, because OAuth profiles have no static token to read.)*
