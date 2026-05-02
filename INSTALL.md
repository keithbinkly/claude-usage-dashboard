# Install via Claude — paste this prompt

Copy everything below the line and paste it into a fresh Claude Code session (or Claude.ai chat with file access). The agent will install and run the dashboard for you.

---

```
You are helping me install the Claude Usage Dashboard — a self-contained HTML
dashboard that reads my own Claude Code session logs from ~/.claude/projects/ and
shows pacing, heavy sessions, context depth, and historical trends.

Repo: https://github.com/keithbinkly/claude-usage-dashboard

Please do the following steps in order, stopping and telling me clearly if anything fails.

## Step 1 — Prerequisites check

Run these checks and report results:
- python3 --version          (need 3.9 or later)
- git --version              (need any recent version)
- ls ~/.claude/projects/     (must exist and contain at least one subdirectory)

If ~/.claude/projects/ does not exist or is empty, stop and tell me:
  "Claude Code hasn't run a session yet. Use Claude Code for at least one
   conversation first, then re-run this installer."

Count the .jsonl files found: find ~/.claude/projects -name "*.jsonl" | wc -l
Report the count. If it is 0, stop with the same message above.

## Step 2 — Clone

git clone https://github.com/keithbinkly/claude-usage-dashboard
cd claude-usage-dashboard

If the directory already exists, do: cd claude-usage-dashboard && git pull

## Step 3 — Generate dashboard

python3 claude_usage.py --layout wide --out my-dashboard.html

This reads your ~/.claude/projects/*.jsonl files. No data leaves your machine.
Expected output: "wrote N turns → my-dashboard.html (XXX KB) [layout: wide]"

If you see "No turns found": try adding --days 7 (you may have few recent sessions).
If you see "ERROR: ~/.claude/projects not found": stop and tell me — Step 1 missed it.

## Step 4 — Open the dashboard

open my-dashboard.html

(On Linux, use: xdg-open my-dashboard.html)

Tell me: how many turns did it write, and what is today's date shown in the dashboard?

## Step 5 — Optional: live pacing endpoint

If I want the pacing gauges to refresh without regenerating the HTML file,
run the local server:

python3 serve.py

Then open http://localhost:8922/my-dashboard.html in a browser.

Note: the pacing endpoint reads your Claude Code OAuth token from the macOS
Keychain. It only works on macOS with Claude Code logged in. On other platforms,
the dashboard will show "--" for the pacing badges but everything else works.

## What success looks like

- my-dashboard.html opens in a browser
- The header shows your usage over your session history
- The "Usage pace" section shows a chart with your actual turn data
- You can click the ? button to launch the guided tour

If the dashboard is blank or shows 0 turns, re-run with:
  python3 claude_usage.py --layout wide --days 30 --out my-dashboard.html
and report back what the script printed.
```
