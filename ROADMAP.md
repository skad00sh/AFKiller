# Roadmap

- **v2 — Databricks REST API cluster stop.** Call `POST /api/2.0/clusters/delete` with a stored host + PAT token so the cluster shuts down directly instead of relying on the SSH session timeout. Belt-and-suspenders next to closing Cursor.
- **Custom per-trigger minute input.** v1 ships preset values only (5/10/15/30/60 etc.); add a "Custom..." option that prompts for an arbitrary number of minutes.
- **Windows auto-start installer.** Currently documented as a manual shortcut in `shell:startup`; ship a one-click installer that drops it for the user.
