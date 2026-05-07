---
name: usage-dashboard
version: 0.1.0
description: >
  Local Claude Code token-usage dashboard. Aggregates ~/.claude/projects/*.jsonl
  conversation logs into a self-contained HTML dashboard with rolling 5-hour and
  weekly gauges, per-day pacing breakdowns by model, token-vs-turn imbalance
  ribbons, heaviest-session tables, and a 10-step interactive tour. No data
  leaves the machine.
  Trigger keywords: "claude usage", "token spend", "rate limit dashboard",
  "claude cost", "usage monitor", "tokens burned", "how much have I used",
  "5-hour limit", "weekly limit", "pace my claude usage", "show me my usage".
---

# Claude Usage Dashboard

A local-only token-usage observability tool packaged as a Claude Code plugin. Two modes:

- **One-shot snapshot** — render an HTML dashboard from your `~/.claude/projects/` logs and open it in the browser. No server, no config.
- **Live pacing endpoint** — run `serve.py` to stream rolling 5h / weekly limit data from Anthropic's API to the dashboard's pacing gauges.

## When to invoke

Trigger on phrases like:
- "show me my claude usage"
- "how close am I to the 5-hour limit?"
- "did I burn through the weekly limit"
- "what's my token spend looking like"
- "open the usage dashboard"

## Slash commands

- `/usage-dashboard` — render `claude_usage.py --layout wide` to HTML and open in browser
- `/usage-serve` — start `serve.py` (live pacing endpoint, macOS only — reads OAuth token from Keychain)

## File layout (after `claude plugin install`)

The plugin is cached at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` and slash commands resolve runtime files via `${CLAUDE_PLUGIN_ROOT}`:

| File | Purpose |
|------|---------|
| `${CLAUDE_PLUGIN_ROOT}/claude_usage.py` | Harvester — walks `~/.claude/projects/**/*.jsonl`, aggregates, renders HTML |
| `${CLAUDE_PLUGIN_ROOT}/serve.py` | Lightweight server with live pacing endpoint |
| `${CLAUDE_PLUGIN_ROOT}/dashboard-wide-template.html` | Wide-layout template |
| `${CLAUDE_PLUGIN_ROOT}/dashboard-template.html` | Editorial-layout template |

## Data privacy

`claude_usage.py` reads `~/.claude/projects/**/*.jsonl` only. The optional rate-limits endpoint in `serve.py` makes one outbound call to Anthropic's API using the OAuth token from your Claude Code session (the same token Claude Code already uses). No third-party telemetry, no cloud sinks, no data egress.

## Cross-platform notes

- One-shot dashboard mode works on any OS with Python 3.9+ (stdlib only — no pip, no venv)
- Live pacing endpoint (`serve.py`) requires macOS (reads OAuth from Keychain). On Linux, the dashboard will show "--" for pacing badges but everything else works.
