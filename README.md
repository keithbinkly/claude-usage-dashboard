# Claude Usage Dashboard

A local dashboard for tracking your own Claude Code token usage. Single-file,
stdlib-only Python. Nothing leaves your machine.

![Wide dashboard layout](https://github.com/keith-gd/claude-usage-dashboard/raw/main/screenshot.png)

---

## What it shows

| Section | What it tells you |
|---------|------------------|
| Pacing gauges | Tokens burned in the last 5h / 7d vs your rate limit |
| Per-day columns | Turn count + token burn broken down by model |
| Context ribbon | Which context-size bands eat more tokens than their share of turns |
| Token burn evolution | Area chart over your lookback window, toggle by model |
| KPI strip | Cost, avg context, cache hit rate, opus share — vs prior period |
| Heavy sessions | Per-session table sorted by token cost |

---

## Requirements

- Python 3.9+ (stdlib only — no pip, no venv needed)
- macOS (for the live OAuth pacing gauges; all other features work on any OS)
- Claude Code installed and logged in

---

## Quickstart

```bash
git clone https://github.com/keith-gd/claude-usage-dashboard
cd claude-usage-dashboard

# Generate and open the wide dashboard
python3 claude_usage.py --layout wide --open

# Narrow layout (also works)
python3 claude_usage.py --open

# Longer lookback
python3 claude_usage.py --layout wide --days 30 --open
```

The generated HTML is self-contained — open it in any browser, share it, or
archive it. Re-run the command to refresh.

---

## How it works

`claude_usage.py` walks `~/.claude/projects/**/*.jsonl` — the conversation logs
Claude Code writes automatically for every session. Every model API call
(tokens in/out, model, cache tokens, timestamp) is recorded there. The script
aggregates them into daily/hourly rollups and injects them into the template.

No account credentials are required for the core dashboard.

---

## Live server (auto-refresh pacing gauges)

For pacing gauges that update without regenerating the whole HTML, run the
local server:

```bash
python3 serve.py
# → http://localhost:8922/dashboard.html
```

The server exposes:
- `GET /api/rate-limits` — fetches your current usage from Anthropic's OAuth
  endpoint (primary account)
- `GET /api/rate-limits-max` — second account (see below)
- `GET /api/pacing` — combined pacing computation

The dashboard polls these endpoints on load and every 5 minutes.

### Persistent background server (macOS launchd)

Create `~/Library/LaunchAgents/com.claude-usage.server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.claude-usage.server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3</string>
        <string>/Users/YOU/claude-usage-dashboard/serve.py</string>
        <string>--root</string>
        <string>/Users/YOU/claude-usage-dashboard</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key>
    <string>/Users/YOU/Library/Logs/claude-usage-server.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOU/Library/Logs/claude-usage-server.err.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.claude-usage.server.plist
```

---

## OAuth pacing gauges

The 5h and 7d pacing gauges show usage against your actual rate limits by
calling Anthropic's OAuth usage endpoint — the same one Claude Code's `/usage`
command calls.

**This works automatically on macOS** if Claude Code is logged in. The script
reads your OAuth access token from the macOS Keychain (service:
`Claude Code-credentials`) and refreshes it silently as needed.

No manual credential setup is needed for your primary account.

### Second account (optional)

If you have two Claude accounts (e.g., personal + work), the dashboard can
show a second pacing gauge alongside your primary. To enable:

1. In a terminal, temporarily switch Claude Code to your second account and
   log in. Then capture its credentials from the Keychain:

   ```bash
   security find-generic-password -s "Claude Code-credentials" -w | python3 -c "
   import sys, json
   creds = json.load(sys.stdin)
   oauth = creds.get('claudeAiOauth', {})
   print(json.dumps({
     'accessToken':  oauth.get('accessToken'),
     'refreshToken': oauth.get('refreshToken'),
     'expiresAt':    oauth.get('expiresAt'),
   }, indent=2))
   "
   ```

2. Save the output to `~/.claude/rate-limits-token-max.json`:

   ```bash
   # Run the command above and pipe it:
   security find-generic-password -s "Claude Code-credentials" -w | python3 -c "
   import sys, json, pathlib, stat
   creds = json.load(sys.stdin)
   oauth = creds.get('claudeAiOauth', {})
   snap = {
     'accessToken':  oauth.get('accessToken'),
     'refreshToken': oauth.get('refreshToken'),
     'expiresAt':    oauth.get('expiresAt'),
   }
   p = pathlib.Path.home() / '.claude' / 'rate-limits-token-max.json'
   p.write_text(json.dumps(snap, indent=2))
   p.chmod(stat.S_IRUSR | stat.S_IWUSR)
   print('Saved to', p)
   "
   ```

3. Switch Claude Code back to your primary account.

The token refreshes automatically from then on — you only need to do this once
(or if the refresh token ever expires).

---

## About the OAuth client ID

The code contains a hardcoded value:

```python
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
```

This is Claude Code's **application** client ID — it identifies the Claude Code
CLI to Anthropic's OAuth server, not your personal account. It is a public
value baked into the Claude Code binary that anyone can extract. It is not a
secret and is not specific to any user.

---

## Troubleshooting

**"No projects found"** — Claude Code hasn't run on this machine yet, or
`~/.claude/projects/` is empty.

**Dashboard shows 0 turns** — Check `--days`. Default is 21 days; if you
started using Claude Code recently, try `--days 7`.

**Pacing gauges show "—"** — The OAuth fetch failed. Run with `--layout wide`
and check the browser console. Most common causes: Claude Code is not logged
in, or you're not on macOS.

**Port conflict** — `python3 serve.py --port 9000`

---

## License

MIT
