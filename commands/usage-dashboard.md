---
description: Render a fresh Claude usage dashboard snapshot and open it in the browser.
---

Run a one-shot snapshot of the user's Claude Code token usage and open the HTML in their default browser.

## What to do

1. The harvester lives at `${CLAUDE_PLUGIN_ROOT}/claude_usage.py`. Use that env var so the path resolves regardless of where the plugin was installed.

2. Default invocation (wide layout, all-time):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/claude_usage.py" --layout wide --open
   ```

3. If the user wants a shorter window or a specific output path, accept arguments:
   - `--days N` (default: all-time)
   - `--layout {editorial,wide,preview}` (default wide)
   - `--out /path/to/file.html` (default: `claude-usage-dashboard.html` in cwd)

4. If `~/.claude/projects/` is empty or missing, tell the user Claude Code hasn't logged any sessions yet on this machine — they need to run at least one Claude Code conversation first.

5. If the output reports "No turns found" with the user's chosen `--days` window, suggest re-running with a wider window: `--days 30`.

## Output

State the path of the HTML file written, the turn count, and confirm the browser was opened. Don't paste the file contents.
