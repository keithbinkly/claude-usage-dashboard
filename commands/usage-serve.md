---
description: Start the live pacing endpoint (serve.py) so the dashboard's 5h/weekly gauges refresh without regenerating the HTML.
---

Start the local server that streams live rolling-5h and weekly rate-limit data to the dashboard's pacing gauges.

## What to do

1. Confirm the user is on macOS — `serve.py` reads the Claude Code OAuth token from the macOS Keychain. On Linux/Windows, tell the user the live pacing endpoint won't work; they should use `/usage-dashboard` for static snapshots instead (everything except the pacing badges still renders).

2. Confirm a dashboard HTML exists in the cwd or has been generated recently. If not, run `/usage-dashboard` first.

3. Start the server in the foreground (or background if user asks):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/serve.py"
   ```

4. The server listens on `http://localhost:8922` by default. Tell the user to open `http://localhost:8922/<dashboard-filename>.html` in a browser.

5. If port 8922 is taken, suggest re-running with `--port 9000` (or any other free port) and updating the bookmark.

## Stopping

`Ctrl+C` in the foreground process, or `pkill -f serve.py` if backgrounded.

## Output

State the server URL and any flags used. Don't keep polling status — the user will tell you if anything's wrong.
