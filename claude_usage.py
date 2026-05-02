#!/usr/bin/env python3
"""Claude Usage Monitor — track your own Claude Code token usage.

Single-file, stdlib-only. Walks ~/.claude/projects/**/*.jsonl, computes rolling
5-hour and weekly windows, and emits a self-contained HTML dashboard you can
open locally. Nothing leaves your machine.

Usage:
    python3 claude_usage.py                   # dashboard.html in cwd
    python3 claude_usage.py --out ~/usage.html
    python3 claude_usage.py --days 7          # lookback window (default 21)
    python3 claude_usage.py --open            # open the file when done

The dashboard includes:
    - Rolling 5hr window gauge (tokens + cost in the last 5 hours)
    - Rolling 7d window gauge (from last Thu 21:00 PT reset, if applicable)
    - Per-turn scatter (time x context_size, colored by model)
    - Hourly rollup (turn counts + avg context)
    - Click-to-mark throttle points (saved in your browser's localStorage)

Source: https://data-centered.com/tools/claude-usage-monitor/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ─── Model price table (USD per million tokens, Anthropic list prices) ───
MODEL_RATES: dict[str, dict[str, float]] = {
    "opus":   {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "sonnet": {"input":  3.0, "output": 15.0, "cache_read": 0.30, "cache_write":  3.75},
    "haiku":  {"input":  1.0, "output":  5.0, "cache_read": 0.10, "cache_write":  1.25},
}
DEFAULT_RATES = {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}


def pt_tz():
    """Return America/Los_Angeles tzinfo, falling back to -08:00 if zoneinfo is missing."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:
        return timezone(timedelta(hours=-8))


def short_model(model: str | None) -> str:
    if not model:
        return "unknown"
    m = model.lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "unknown"


def rates_for(model: str) -> dict[str, float]:
    return MODEL_RATES.get(short_model(model), DEFAULT_RATES)


def cost_for_usage(usage: dict, model: str) -> float:
    rates = rates_for(model)
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cr  = int(usage.get("cache_read_input_tokens") or 0)
    cw  = int(usage.get("cache_creation_input_tokens") or 0)
    return (
        inp * rates["input"]
        + out * rates["output"]
        + cr * rates["cache_read"]
        + cw * rates["cache_write"]
    ) / 1_000_000.0


def parse_iso_utc(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def week_start_for(dt_utc: datetime) -> datetime:
    """Return the Thu 21:00 PT weekly-window start containing dt_utc, as UTC.

    Anthropic's weekly ceiling appears to reset on Thursdays at 21:00 Pacific
    (calibrated empirically — not an official published boundary). If your
    reset lands somewhere else, mark a throttle event in the dashboard to
    override this default visually.
    """
    pt = pt_tz()
    dt_pt = dt_utc.astimezone(pt)
    days_back = (dt_pt.weekday() - 3) % 7  # Thu = 3
    candidate = (dt_pt - timedelta(days=days_back)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )
    if candidate > dt_pt:
        candidate -= timedelta(days=7)
    return candidate.astimezone(timezone.utc)


# ─── Data model ──────────────────────────────────────────────────────────

@dataclass
class Turn:
    ts_ms: int
    ctx: int          # cache_read + cache_write + input_tokens (what the model saw)
    input_t: int
    output_t: int
    cache_read: int
    cache_write: int
    model: str        # "opus" | "sonnet" | "haiku" | "unknown"
    sid: str          # session id (first 8 chars)
    stype: str        # "interactive" | "headless"
    side: int         # 1 if sidechain (subagent), else 0
    cost: float


@dataclass
class Dataset:
    turns: list[Turn] = field(default_factory=list)
    generated_at: str = ""
    lookback_days: int = 21
    first_ms: int = 0
    last_ms: int = 0


# ─── JSONL parsing ───────────────────────────────────────────────────────

def iter_jsonl(path: Path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def parse_session(path: Path, cutoff_ms: int) -> list[Turn]:
    """Return every assistant turn in this JSONL file with ts >= cutoff_ms.

    Streaming dedup: multiple chunk events for the same assistant message
    share a message.id. We keep the LAST event per (file, message_id) so
    running totals from intermediate chunks don't inflate token counts.
    """
    session_id = path.stem[:8]
    entrypoint = ""

    # Per-file dedup: msg_id → last qualifying event's extracted data.
    # Only events within the same file collapse — different files are
    # independent sessions and may legitimately share message IDs.
    seen: dict[str, dict] = {}
    no_id: list[dict] = []

    for evt in iter_jsonl(path):
        if evt.get("type") != "assistant":
            continue
        msg = evt.get("message") or {}
        usage = msg.get("usage") or {}
        if not usage:
            continue
        dt_utc = parse_iso_utc(evt.get("timestamp", ""))
        if dt_utc is None:
            continue
        ts_ms = int(dt_utc.timestamp() * 1000)
        if ts_ms < cutoff_ms:
            continue
        if not entrypoint:
            entrypoint = evt.get("entrypoint", "") or ""

        model_full = msg.get("model", "") or ""
        inp = int(usage.get("input_tokens") or 0)
        outp = int(usage.get("output_tokens") or 0)
        cr = int(usage.get("cache_read_input_tokens") or 0)
        cw = int(usage.get("cache_creation_input_tokens") or 0)
        if inp + outp + cr + cw == 0:
            continue  # init/placeholder turn, no real usage

        record = {
            "ts_ms": ts_ms,
            "inp": inp, "outp": outp, "cr": cr, "cw": cw,
            "model_full": model_full,
            "usage": usage,
            "side": 1 if (evt.get("isSidechain") or evt.get("is_sidechain")) else 0,
        }
        msg_id = msg.get("id")
        if msg_id:
            seen[msg_id] = record  # last-wins: streaming partials overwrite earlier chunks
        else:
            no_id.append(record)

    out: list[Turn] = []
    for record in list(seen.values()) + no_id:
        out.append(Turn(
            ts_ms=record["ts_ms"],
            ctx=record["cr"] + record["cw"] + record["inp"],
            input_t=record["inp"],
            output_t=record["outp"],
            cache_read=record["cr"],
            cache_write=record["cw"],
            model=short_model(record["model_full"]),
            sid=session_id,
            stype="headless" if entrypoint == "sdk-cli" else "interactive",
            side=record["side"],
            cost=cost_for_usage(record["usage"], record["model_full"]),
        ))
    return sorted(out, key=lambda t: t.ts_ms)


def collect(days: int) -> Dataset:
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(days=days)
    cutoff_ms = int(cutoff_utc.timestamp() * 1000)
    mtime_floor = cutoff_utc.timestamp() - 86400  # small safety buffer

    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        print(f"ERROR: {root} not found. Is Claude Code installed?", file=sys.stderr)
        sys.exit(1)

    # ── Pass 1: full-history anchored windows ──
    # Walk EVERY JSONL file (no mtime filter) to collect turn timestamps
    # and costs. This is the only way to correctly identify the current
    # 5h/7d window anchor, since each anchor depends on the previous
    # window's expiry, which can be older than the dashboard lookback.
    full_turns: list[tuple[int, float, int]] = []  # (ts_ms, cost_usd, tokens)
    daily: dict[str, dict] = {}  # PT-date → aggregates
    pt = pt_tz()
    # Track first 1M-context activation: first turn where context_size > 200k.
    # All pre-1M Claude models cap at 200k, so any ctx > 200k proves the 1M
    # window was enabled for that session.
    first_1m_ms: int | None = None
    for proj in root.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.rglob("*.jsonl"):
            try:
                # Per-file dedup: msg_id → last event's extracted record.
                # Streaming transcripts emit multiple chunk events for the
                # same message_id with running token totals — last-wins so
                # only the final (complete) count is counted.
                file_seen: dict[str, dict] = {}
                file_no_id: list[dict] = []

                for evt in iter_jsonl(f):
                    if evt.get("type") != "assistant":
                        continue
                    msg = evt.get("message") or {}
                    usage = msg.get("usage") or {}
                    if not usage:
                        continue
                    inp = int(usage.get("input_tokens") or 0)
                    outp = int(usage.get("output_tokens") or 0)
                    cr = int(usage.get("cache_read_input_tokens") or 0)
                    cw = int(usage.get("cache_creation_input_tokens") or 0)
                    total_tokens = inp + outp + cr + cw
                    if total_tokens == 0:
                        continue
                    dt = parse_iso_utc(evt.get("timestamp", ""))
                    if dt is None:
                        continue
                    model_full = msg.get("model", "") or ""
                    record = {
                        "ts_ms": int(dt.timestamp() * 1000),
                        "cost": cost_for_usage(usage, model_full),
                        "total_tokens": total_tokens,
                        "dt": dt,
                        "inp": inp, "outp": outp, "cr": cr, "cw": cw,
                        "model_full": model_full,
                        "usage": usage,
                    }
                    msg_id = msg.get("id")
                    if msg_id:
                        file_seen[msg_id] = record  # last-wins
                    else:
                        file_no_id.append(record)

                for record in list(file_seen.values()) + file_no_id:
                    inp = record["inp"]
                    outp = record["outp"]
                    cr = record["cr"]
                    cw = record["cw"]
                    total_tokens = record["total_tokens"]
                    dt = record["dt"]
                    model_full = record["model_full"]
                    cost = record["cost"]
                    full_turns.append((
                        record["ts_ms"],
                        cost,
                        total_tokens,
                    ))

                    # Daily aggregate (PT date)
                    day = dt.astimezone(pt).strftime("%Y-%m-%d")
                    d = daily.get(day)
                    if d is None:
                        d = {
                            "turns": 0, "tokens": 0, "cost": 0.0,
                            "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                            "ctx_sum": 0,  # for avg ctx
                            "opus": 0, "sonnet": 0, "haiku": 0,  # turn counts per model
                            # Heavy-turn ctx buckets — count of turns AND
                            # sum of ctx tokens for turns whose context
                            # exceeds each threshold. The `ctx_sum_gt_*`
                            # pair feeds the "% of tokens" metric on the
                            # heavy-bucket tiles, which reveals how
                            # overrepresented long-running sessions are
                            # in total token spend.
                            "ctx_gt_200k": 0, "ctx_gt_500k": 0, "ctx_gt_800k": 0,
                            "ctx_sum_gt_200k": 0, "ctx_sum_gt_500k": 0, "ctx_sum_gt_800k": 0,
                            # Per-model cost + tokens — enables counterfactual
                            # estimates like "what if these Opus turns ran on
                            # Sonnet" for the efficiency levers card.
                            "cost_opus": 0.0, "cost_sonnet": 0.0, "cost_haiku": 0.0,
                            "tokens_opus": 0, "tokens_sonnet": 0, "tokens_haiku": 0,
                            # Cross-tab: Opus tokens by context tier. Feeds
                            # the efficiency mix bar so each Opus slice
                            # visually separates <200K / 200-500K /
                            # 500-800K / >800K context-size bands.
                            "tokens_opus_u200k": 0,
                            "tokens_opus_200_500k": 0,
                            "tokens_opus_500_800k": 0,
                            "tokens_opus_o800k": 0,
                            # Parallel turn counts for each Opus context
                            # tier — needed for the heavy-turn ribbon's
                            # lift × calculation (tokens% / turns%) per
                            # band, isolated to Opus only since Opus is
                            # where the 1M-tier pricing problem lives.
                            "turns_opus_u200k": 0,
                            "turns_opus_200_500k": 0,
                            "turns_opus_500_800k": 0,
                            "turns_opus_o800k": 0,
                            # Per-context-tier — cost/tokens of turns with
                            # ctx > 200K. These are the ones hit by the 1M
                            # context-window tier premium.
                            "cost_gt_200k": 0.0, "tokens_gt_200k": 0,
                            # Counterfactual shadow cost — what each turn
                            # would have cost on Sonnet at the same usage.
                            # Kept separately for heavy-ctx (>200K) turns
                            # so we can model the realistic lever "route
                            # heavy turns to Sonnet subagents, keep Opus
                            # for reasoning."
                            "cost_if_sonnet": 0.0,
                            "cost_if_sonnet_gt_200k": 0.0,
                        }
                        daily[day] = d
                    d["turns"] += 1
                    d["tokens"] += total_tokens
                    d["cost"] += cost
                    d["input"] += inp
                    d["output"] += outp
                    d["cache_read"] += cr
                    d["cache_write"] += cw
                    ctx_size = inp + cr + cw
                    d["ctx_sum"] += ctx_size  # same as Turn.context_size
                    if ctx_size > 200_000:
                        d["ctx_gt_200k"] += 1
                        d["ctx_sum_gt_200k"] += ctx_size
                        d["cost_gt_200k"] += cost
                        d["tokens_gt_200k"] += total_tokens
                    if ctx_size > 500_000:
                        d["ctx_gt_500k"] += 1
                        d["ctx_sum_gt_500k"] += ctx_size
                    if ctx_size > 800_000:
                        d["ctx_gt_800k"] += 1
                        d["ctx_sum_gt_800k"] += ctx_size
                    m = model_full.lower()
                    if "opus" in m:
                        d["opus"] += 1
                        d["cost_opus"] += cost
                        d["tokens_opus"] += total_tokens
                        # Opus-by-ctx-tier cross-tab — both turn count
                        # and token sum, so the heavy-turn ribbon can
                        # compute lift × per band Opus-only.
                        if ctx_size <= 200_000:
                            d["tokens_opus_u200k"] += total_tokens
                            d["turns_opus_u200k"] += 1
                        elif ctx_size <= 500_000:
                            d["tokens_opus_200_500k"] += total_tokens
                            d["turns_opus_200_500k"] += 1
                        elif ctx_size <= 800_000:
                            d["tokens_opus_500_800k"] += total_tokens
                            d["turns_opus_500_800k"] += 1
                        else:
                            d["tokens_opus_o800k"] += total_tokens
                            d["turns_opus_o800k"] += 1
                    elif "sonnet" in m:
                        d["sonnet"] += 1
                        d["cost_sonnet"] += cost
                        d["tokens_sonnet"] += total_tokens
                    elif "haiku" in m:
                        d["haiku"] += 1
                        d["cost_haiku"] += cost
                        d["tokens_haiku"] += total_tokens
                    # Shadow cost at Sonnet rates. Replay the same usage
                    # dict with Sonnet pricing to answer "what if this turn
                    # had run on Sonnet?" Ignores quality differences.
                    sonnet_shadow = cost_for_usage(
                        {
                            "input_tokens": inp,
                            "output_tokens": outp,
                            "cache_read_input_tokens": cr,
                            "cache_creation_input_tokens": cw,
                        },
                        "sonnet",
                    )
                    d["cost_if_sonnet"] += sonnet_shadow
                    # Same shadow cost, scoped to only turns that crossed
                    # 200K ctx — lets us model "route the big-ctx turns to
                    # a Sonnet subagent, keep Opus for focused reasoning."
                    if ctx_size > 200_000:
                        d["cost_if_sonnet_gt_200k"] += sonnet_shadow

                    # 1M activation: first-ever turn with context > 200k
                    if ctx_size > 205_000:
                        ts_ms = int(dt.timestamp() * 1000)
                        if first_1m_ms is None or ts_ms < first_1m_ms:
                            first_1m_ms = ts_ms
            except OSError:
                continue
    full_turns.sort(key=lambda x: x[0])
    ds_first_1m = first_1m_ms  # captured for later embedding via closure dance

    # ── Pass 2: per-turn detail for the dashboard lookback window ──
    turns: list[Turn] = []
    for proj in root.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.rglob("*.jsonl"):
            try:
                if f.stat().st_mtime < mtime_floor:
                    continue
            except OSError:
                continue
            turns.extend(parse_session(f, cutoff_ms))
    turns.sort(key=lambda t: t.ts_ms)

    ds = Dataset(
        turns=turns,
        generated_at=datetime.now(pt_tz()).isoformat(timespec="seconds"),
        lookback_days=days,
        first_ms=turns[0].ts_ms if turns else 0,
        last_ms=turns[-1].ts_ms if turns else 0,
    )
    ds.full_turns = full_turns  # type: ignore[attr-defined]
    # Convert daily dict to sorted list with derived metrics
    daily_list = []
    for day in sorted(daily.keys()):
        d = daily[day]
        turns_count = d["turns"]
        denom = (d["cache_read"] + d["cache_write"] + d["input"]) or 1
        daily_list.append({
            "day": day,
            "turns": turns_count,
            "tokens": d["tokens"],
            "cost": round(d["cost"], 2),
            "input": d["input"],
            "output": d["output"],
            "cache_read": d["cache_read"],
            "cache_write": d["cache_write"],
            "avg_ctx": round(d["ctx_sum"] / turns_count) if turns_count else 0,
            "tokens_per_turn": round(d["tokens"] / turns_count) if turns_count else 0,
            "cost_per_turn": round(d["cost"] / turns_count, 4) if turns_count else 0,
            "cache_hit_rate": round(d["cache_read"] / denom, 4),
            "opus": d["opus"],
            "sonnet": d["sonnet"],
            "haiku": d["haiku"],
            "turns_ctx_gt_200k": d["ctx_gt_200k"],
            "turns_ctx_gt_500k": d["ctx_gt_500k"],
            "turns_ctx_gt_800k": d["ctx_gt_800k"],
            "pct_ctx_gt_200k": round(d["ctx_gt_200k"] / turns_count, 4) if turns_count else 0,
            "pct_ctx_gt_500k": round(d["ctx_gt_500k"] / turns_count, 4) if turns_count else 0,
            "pct_ctx_gt_800k": round(d["ctx_gt_800k"] / turns_count, 4) if turns_count else 0,
            # Token-weighted share — "of all the context tokens the
            # model saw today, what fraction came from heavy turns".
            # Ratio of pct_tokens / pct_turns is the overrepresentation
            # factor: 2.0× means heavy turns are pulling double their
            # weight in spend.
            "tokens_ctx_gt_200k": d["ctx_sum_gt_200k"],
            "tokens_ctx_gt_500k": d["ctx_sum_gt_500k"],
            "tokens_ctx_gt_800k": d["ctx_sum_gt_800k"],
            "pct_tokens_gt_200k": round(d["ctx_sum_gt_200k"] / d["ctx_sum"], 4) if d["ctx_sum"] else 0,
            "pct_tokens_gt_500k": round(d["ctx_sum_gt_500k"] / d["ctx_sum"], 4) if d["ctx_sum"] else 0,
            "pct_tokens_gt_800k": round(d["ctx_sum_gt_800k"] / d["ctx_sum"], 4) if d["ctx_sum"] else 0,
            # Per-model + per-context-tier cost/tokens for the efficiency
            # model (counterfactuals "all Sonnet" + "compact <200K").
            "cost_opus": round(d["cost_opus"], 4),
            "cost_sonnet": round(d["cost_sonnet"], 4),
            "cost_haiku": round(d["cost_haiku"], 4),
            "tokens_opus": d["tokens_opus"],
            "tokens_sonnet": d["tokens_sonnet"],
            "tokens_haiku": d["tokens_haiku"],
            "cost_gt_200k": round(d["cost_gt_200k"], 4),
            "tokens_gt_200k": d["tokens_gt_200k"],
            "cost_if_sonnet": round(d["cost_if_sonnet"], 4),
            "cost_if_sonnet_gt_200k": round(d["cost_if_sonnet_gt_200k"], 4),
            "tokens_opus_u200k":    d["tokens_opus_u200k"],
            "tokens_opus_200_500k": d["tokens_opus_200_500k"],
            "tokens_opus_500_800k": d["tokens_opus_500_800k"],
            "tokens_opus_o800k":    d["tokens_opus_o800k"],
            "turns_opus_u200k":     d["turns_opus_u200k"],
            "turns_opus_200_500k":  d["turns_opus_200_500k"],
            "turns_opus_500_800k":  d["turns_opus_500_800k"],
            "turns_opus_o800k":     d["turns_opus_o800k"],
            "turns_sonnet":         d["sonnet"],
            "turns_haiku":          d["haiku"],
        })
    ds.daily_stats = daily_list  # type: ignore[attr-defined]
    ds.first_1m_ms = ds_first_1m  # type: ignore[attr-defined]
    return ds


RATE_LIMIT_RESET_RE = re.compile(
    r"You've hit your limit [·.]+\s*resets (\d{1,2})\s*(am|pm)\s*\(([^)]+)\)",
    re.IGNORECASE,
)


def harvest_rate_limit_errors(
    projects_root: Path,
    since_ms: int = 0,
) -> list[dict]:
    """Walk all JSONL files and extract every isApiErrorMessage event
    whose text matches 'You've hit your limit · resets Xpm (TZ)'. Parse
    the reset hour into a full timestamp and derive the implied anchor
    (reset_ts - 5h for 5h-cap hits, or None for weekly hits).

    Returns a list of {error_ts_ms, reset_ts_ms, implied_anchor_ts_ms,
    kind, source_file, raw_text} dicts, deduped by error_ts_ms.
    Only events with error_ts_ms > since_ms are returned.
    """
    pt = pt_tz()
    five_hrs_ms = 5 * 3600 * 1000
    seven_days_ms = 7 * 86400 * 1000
    hits: list[dict] = []

    for proj in projects_root.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.rglob("*.jsonl"):
            try:
                if f.stat().st_mtime < (since_ms / 1000) - 3600:
                    continue
            except OSError:
                continue
            try:
                fh = open(f, "r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in fh:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if not isinstance(e, dict):
                    continue
                if not e.get("isApiErrorMessage"):
                    continue
                # Text can be nested in message.content[*].text
                text = ""
                msg = e.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            text += item["text"]
                elif isinstance(content, str):
                    text = content
                if "hit your limit" not in text.lower():
                    continue
                m = RATE_LIMIT_RESET_RE.search(text)
                if not m:
                    continue

                error_dt = parse_iso_utc(e.get("timestamp", ""))
                if error_dt is None:
                    continue
                error_ms = int(error_dt.timestamp() * 1000)
                if error_ms <= since_ms:
                    continue

                hour = int(m.group(1))
                ampm = m.group(2).lower()
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0

                # Build reset ts: today at `hour`:00 PT, bump to tomorrow
                # if that's before the error timestamp
                error_pt = error_dt.astimezone(pt)
                reset_pt = error_pt.replace(hour=hour, minute=0, second=0, microsecond=0)
                while reset_pt <= error_pt:
                    reset_pt += timedelta(days=1)
                reset_ms = int(reset_pt.astimezone(timezone.utc).timestamp() * 1000)

                # Classify: 5h cap vs weekly cap
                gap = reset_ms - error_ms
                if gap <= five_hrs_ms + 60_000:  # small fudge
                    kind = "5h"
                    implied = reset_ms - five_hrs_ms
                elif gap <= seven_days_ms + 60_000:
                    kind = "weekly"
                    implied = None  # weekly anchor is a fixed clock boundary
                else:
                    kind = "unknown"
                    implied = None

                hits.append({
                    "error_ts_ms": error_ms,
                    "reset_ts_ms": reset_ms,
                    "reset_hour_pt": hour,
                    "implied_anchor_ts_ms": implied,
                    "kind": kind,
                    "source_file": str(f.relative_to(projects_root)),
                    "raw_text": text[:200],
                })
            fh.close()

    # Dedup by error_ts_ms
    seen = {}
    for h in hits:
        ts = h["error_ts_ms"]
        if ts not in seen:
            seen[ts] = h
    return sorted(seen.values(), key=lambda x: x["error_ts_ms"])


def load_sidecar_calibrations() -> dict:
    """Load ~/.claude-usage-monitor/calibrations.json, returning a
    default structure if the file is missing or unparseable."""
    path = Path.home() / ".claude-usage-monitor" / "calibrations.json"
    default = {
        "schema_version": 1,
        "last_harvested_ts_ms": 0,
        "auto_anchors": [],
        "manual_entries": [],
    }
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Merge missing keys from default
        for k, v in default.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return default


def save_sidecar_calibrations(data: dict) -> None:
    """Write the calibrations sidecar, creating the directory if needed."""
    path = Path.home() / ".claude-usage-monitor" / "calibrations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def refresh_sidecar(projects_root: Path) -> dict:
    """Harvest any new rate-limit errors since last run and merge them
    into the sidecar file. Returns the updated sidecar data."""
    sidecar = load_sidecar_calibrations()
    last_seen = int(sidecar.get("last_harvested_ts_ms") or 0)
    new_hits = harvest_rate_limit_errors(projects_root, since_ms=last_seen)
    if new_hits:
        # Dedup against existing entries
        existing_ts = {a["error_ts_ms"] for a in sidecar["auto_anchors"]}
        for h in new_hits:
            if h["error_ts_ms"] not in existing_ts:
                sidecar["auto_anchors"].append(h)
                existing_ts.add(h["error_ts_ms"])
        sidecar["auto_anchors"].sort(key=lambda x: x["error_ts_ms"])
    # Advance the watermark to the latest error_ts we've seen
    if sidecar["auto_anchors"]:
        sidecar["last_harvested_ts_ms"] = max(
            a["error_ts_ms"] for a in sidecar["auto_anchors"]
        )
    save_sidecar_calibrations(sidecar)
    return sidecar


def find_anchored_windows(
    full_turns: list[tuple[int, float, int]],
    window_ms: int,
    gap_reset_ms: int = 0,
) -> list[dict]:
    """Walk forward; each window starts at the first turn after either
    (a) the previous window's end (window_ms elapsed from its anchor), OR
    (b) an idle gap of at least gap_reset_ms between consecutive turns.
    Returns [{anchor, end, cost, tokens, n}].

    Used for the 5-hour window. Gap-based reset rule derived from
    ground-truth rate-limit error logs in Keith's JSONL history: the
    winning hypothesis was "first activity after idle gap >= 15 min"
    (mean absolute error 19.6 min across 3 testable anchors vs Anthropic's
    hour-quantized reset timestamps). Set gap_reset_ms=0 to disable.
    """
    windows: list[dict] = []
    i = 0
    n = len(full_turns)
    while i < n:
        anchor = full_turns[i][0]
        end = anchor + window_ms
        cost = 0.0
        tokens = 0
        count = 0
        j = i
        prev_ts = None
        while j < n and full_turns[j][0] < end:
            # Idle-gap reset: if this turn is >= gap_reset_ms after the
            # previous turn in the current window, stop accumulating and
            # let this turn anchor a new window on the next iteration.
            if gap_reset_ms > 0 and prev_ts is not None:
                if full_turns[j][0] - prev_ts >= gap_reset_ms:
                    break
            cost += full_turns[j][1]
            tokens += full_turns[j][2]
            count += 1
            prev_ts = full_turns[j][0]
            j += 1
        windows.append({
            "anchor": anchor,
            "end": end,
            "cost": round(cost, 4),
            "tokens": tokens,
            "n": count,
        })
        i = j
    return windows


def thursday_week_anchor(dt_utc: datetime) -> datetime:
    """Return the most recent Thu 21:00 PT at-or-before dt_utc (as UTC).

    Keith's Max weekly limit resets Thursday at 21:00 Pacific. This is
    a fixed-clock boundary, not a personal-anchor rolling window — so it
    needs its own bucket logic (not find_anchored_windows).
    """
    pt = pt_tz()
    dt_pt = dt_utc.astimezone(pt)
    days_back = (dt_pt.weekday() - 3) % 7  # Thu = 3 in weekday()
    candidate = (dt_pt - timedelta(days=days_back)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )
    if candidate > dt_pt:
        candidate -= timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def thursday_weekly_windows(
    full_turns: list[tuple[int, float, int]],
) -> list[dict]:
    """Bucket every turn into the Thu 21:00 PT → Thu 21:00 PT week it
    belongs to. Returns sorted [{anchor, end, cost, tokens, n}].

    This is the correct calibration for the Max weekly cap — hardcoded
    to Keith's observed reset time. When shipping to other users this
    should become configurable (or auto-detected from their throttle
    events).
    """
    week_ms = 7 * 86400 * 1000
    buckets: dict[int, dict] = {}
    for ts_ms, cost, tokens in full_turns:
        dt_utc = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        anchor_dt = thursday_week_anchor(dt_utc)
        anchor_ms = int(anchor_dt.timestamp() * 1000)
        w = buckets.get(anchor_ms)
        if w is None:
            w = {
                "anchor": anchor_ms,
                "end": anchor_ms + week_ms,
                "cost": 0.0,
                "tokens": 0,
                "n": 0,
            }
            buckets[anchor_ms] = w
        w["cost"] += cost
        w["tokens"] += tokens
        w["n"] += 1
    out = [buckets[k] for k in sorted(buckets.keys())]
    for w in out:
        w["cost"] = round(w["cost"], 4)
    return out


# ─── Preview helpers: session agg, histogram, heavy buckets ─────────────
#
# Only consumed by the `preview` layout (Phase 0 chart previews).
# Stdlib-only; derives everything from the already-parsed `ds.turns`
# list plus the daily_stats ctx-bucket counters.

ACTIVE_IDLE_MS = 15 * 60 * 1000       # still exposed for the preview payload
SESSIONS_LOOKBACK_MS = 7 * 86_400 * 1000   # heaviest-sessions list covers last 7 days
HISTOGRAM_BIN_WIDTH = 25_000           # 40 bins across 0–1M ctx
HISTOGRAM_MAX_CTX = 1_000_000
HISTOGRAM_WINDOW_DAYS = 7
HEAVY_BUCKET_THRESHOLDS = (200_000, 500_000, 800_000)
SCATTER_WINDOW_DAYS = 7
TOP_HEAVIEST_SESSIONS = 30            # top-N heaviest sessions (scrollable list)


def aggregate_sessions(
    turns: list[Turn],
    now_ms: int,
    lookback_ms: int = SESSIONS_LOOKBACK_MS,
    top_n: int = TOP_HEAVIEST_SESSIONS,
) -> list[dict]:
    """Group turns by sid; return top-N heaviest sessions in the window.

    `lookback_ms` defines what "recent" means for the list — default is
    the last 7 days, wide enough that the list is useful outside an
    active chat. Sort key is `max_ctx` descending — the single biggest
    turn in the session is the strongest signal that compaction would
    free real budget.
    """
    buckets: dict[str, dict] = {}
    for t in turns:
        b = buckets.get(t.sid)
        if b is None:
            b = {
                "sid": t.sid,
                "n_turns": 0,
                "total_ctx": 0,
                "max_ctx": 0,
                "total_cost": 0.0,
                "first_ts_ms": t.ts_ms,
                "last_ts_ms": t.ts_ms,
                # Model usage counts — dominant model wins for display
                "model_counts": {"opus": 0, "sonnet": 0, "haiku": 0, "unknown": 0},
                "stype": t.stype,
            }
            buckets[t.sid] = b
        b["n_turns"] += 1
        b["total_ctx"] += t.ctx
        if t.ctx > b["max_ctx"]:
            b["max_ctx"] = t.ctx
        b["total_cost"] += t.cost
        if t.ts_ms < b["first_ts_ms"]:
            b["first_ts_ms"] = t.ts_ms
        if t.ts_ms > b["last_ts_ms"]:
            b["last_ts_ms"] = t.ts_ms
        key = t.model if t.model in b["model_counts"] else "unknown"
        b["model_counts"][key] += 1

    out: list[dict] = []
    for b in buckets.values():
        # Include any session whose last activity is within the lookback
        # window. Still surface `idle_ms` so the UI can render an "Xm ago"
        # or "2d ago" label consistently.
        if (now_ms - b["last_ts_ms"]) > lookback_ms:
            continue
        dominant = max(b["model_counts"].items(), key=lambda kv: kv[1])[0]
        out.append({
            "sid": b["sid"],
            "model": dominant,
            "stype": b["stype"],
            "n_turns": b["n_turns"],
            "total_ctx": b["total_ctx"],
            "max_ctx": b["max_ctx"],
            "total_cost": round(b["total_cost"], 4),
            "first_ts_ms": b["first_ts_ms"],
            "last_ts_ms": b["last_ts_ms"],
            "idle_ms": now_ms - b["last_ts_ms"],
        })
    out.sort(key=lambda s: s["max_ctx"], reverse=True)
    return out[:top_n]


def compute_ctx_histogram(
    turns: list[Turn],
    now_ms: int,
    window_days: int = HISTOGRAM_WINDOW_DAYS,
    bin_width: int = HISTOGRAM_BIN_WIDTH,
    max_ctx: int = HISTOGRAM_MAX_CTX,
) -> dict:
    """Bin ctx-per-turn for turns in the last `window_days` days.

    Returns {bin_width, window_days, bins: [{edge, count}, ...]}.
    Bins are linearly spaced 0..max_ctx; any turn whose ctx exceeds
    max_ctx lands in the final (overflow) bin.
    """
    cutoff = now_ms - window_days * 86_400_000
    n_bins = max_ctx // bin_width
    counts = [0] * (n_bins + 1)  # +1 for the overflow bin
    total = 0
    for t in turns:
        if t.ts_ms < cutoff:
            continue
        total += 1
        idx = min(t.ctx // bin_width, n_bins)
        counts[idx] += 1
    bins = [
        {"edge": i * bin_width, "count": counts[i]}
        for i in range(n_bins + 1)
    ]
    return {
        "bin_width": bin_width,
        "window_days": window_days,
        "max_ctx": max_ctx,
        "total_turns": total,
        "bins": bins,
    }


def compute_efficiency_model(daily_stats: list[dict], lookback_days: int = 7) -> dict:
    """Build the efficiency/leverage model from the last N days of usage.

    The dashboard uses this for two things:
      1. A more honest token-cap estimate. Instead of naive
         `cap_tokens = tokens_this_window / util`, we use
         `cap_tokens = cap_usd / trailing_blended_rate` so the cap
         reflects sustainable habits rather than one-off mix.
      2. The efficiency levers card, which shows explicit counterfactuals:
         "how much bigger would your token budget be if X?"

    Returned fields:
      - blended_usd_per_mtok: current trailing $/million-tokens rate.
      - counterfactuals:
          - all_sonnet: $/Mtok if every Opus turn had run on Sonnet.
          - compact_below_200k: $/Mtok if heavy-ctx turns (>200K) avoided
            the 1M-tier premium (approximated as ~1.5x).
          - both: applying both levers simultaneously.
      - mix: current opus/sonnet/haiku token share (for UI).
      - heavy_ctx_share: fraction of tokens from turns with ctx > 200K.
      - lookback_days
    """
    recent = daily_stats[-lookback_days:] if daily_stats else []

    def sum_(key: str) -> float:
        return sum((d.get(key) or 0) for d in recent)

    total_cost = sum_("cost")
    total_tokens = sum_("tokens")
    if total_tokens <= 0:
        return {
            "lookback_days": lookback_days,
            "blended_usd_per_mtok": None,
            "counterfactuals": {},
            "mix": {},
            "heavy_ctx_share": 0.0,
        }

    blended = total_cost / total_tokens * 1_000_000.0  # $/Mtok

    # ── Counterfactual 1: route heavy-ctx turns to Sonnet ────────
    # Realistic behavioral lever: keep Opus for reasoning turns, but
    # delegate big-context work (>200K ctx — usually reads, searches,
    # scouts) to Sonnet. Models swapping the >200K slice to the Sonnet
    # shadow cost while leaving everything else untouched.
    cost_gt_200k = sum_("cost_gt_200k")
    cost_if_sonnet_gt_200k = sum_("cost_if_sonnet_gt_200k")
    cost_if_route_heavy = total_cost - cost_gt_200k + cost_if_sonnet_gt_200k
    rate_if_route_heavy = cost_if_route_heavy / total_tokens * 1_000_000.0

    # ── Counterfactual 2: compact below 200K ─────────────────────
    # Heavy-context turns (>200K input) trigger Anthropic's 1M context
    # tier premium: 2× input, 1.5× output for tokens above the 200K
    # threshold. Our internal cost model uses standard-tier pricing, so
    # the savings here represent the premium we WOULD have avoided
    # relative to real billing. Approximation: 33% savings on the cost
    # of turns that ran over 200K.
    heavy_premium_factor = 0.33
    saved_from_compact = cost_gt_200k * heavy_premium_factor
    rate_if_compact = (total_cost - saved_from_compact) / total_tokens * 1_000_000.0

    # ── Counterfactual 3: both levers ────────────────────────────
    # Route heavy turns to Sonnet AND have the remaining heavy turns
    # actually compact below 200K. If both are applied, there's no
    # heavy-ctx cost left (it was routed + shrunk). Best-case ceiling.
    # Approximation: start from cost_if_route_heavy, then apply compact
    # savings to the Sonnet shadow's heavy portion (1M tier applies to
    # Sonnet too).
    saved_compact_on_sonnet = cost_if_sonnet_gt_200k * heavy_premium_factor
    cost_both = cost_if_route_heavy - saved_compact_on_sonnet
    rate_both = cost_both / total_tokens * 1_000_000.0 if total_tokens else None

    # Mix shares (by tokens)
    tokens_opus = sum_("tokens_opus")
    tokens_sonnet = sum_("tokens_sonnet")
    tokens_haiku = sum_("tokens_haiku")
    tokens_gt_200k = sum_("tokens_gt_200k")

    return {
        "lookback_days": lookback_days,
        "total_cost": round(total_cost, 2),
        "total_tokens": total_tokens,
        "blended_usd_per_mtok": round(blended, 3),
        "counterfactuals": {
            "route_heavy_to_sonnet": {
                "usd_per_mtok": round(rate_if_route_heavy, 3),
                "pct_reduction": round((1 - rate_if_route_heavy / blended) * 100, 1) if blended else 0,
            },
            "compact_below_200k": {
                "usd_per_mtok": round(rate_if_compact, 3),
                "pct_reduction": round((1 - rate_if_compact / blended) * 100, 1) if blended else 0,
            },
            "both": {
                "usd_per_mtok": round(rate_both, 3) if rate_both is not None else None,
                "pct_reduction": round((1 - rate_both / blended) * 100, 1) if blended and rate_both else 0,
            },
        },
        "mix": {
            "opus_share": tokens_opus / total_tokens if total_tokens else 0,
            "sonnet_share": tokens_sonnet / total_tokens if total_tokens else 0,
            "haiku_share": tokens_haiku / total_tokens if total_tokens else 0,
        },
        "mix_detailed": {
            "opus_u200k":    sum_("tokens_opus_u200k")    / total_tokens if total_tokens else 0,
            "opus_200_500k": sum_("tokens_opus_200_500k") / total_tokens if total_tokens else 0,
            "opus_500_800k": sum_("tokens_opus_500_800k") / total_tokens if total_tokens else 0,
            "opus_o800k":    sum_("tokens_opus_o800k")    / total_tokens if total_tokens else 0,
            "sonnet":        tokens_sonnet                 / total_tokens if total_tokens else 0,
            "haiku":         tokens_haiku                  / total_tokens if total_tokens else 0,
        },
        "heavy_ctx_share": tokens_gt_200k / total_tokens if total_tokens else 0,
    }


def compute_heavy_bucket_summary(daily_stats: list[dict]) -> dict:
    """Roll per-day heavy-bucket fields into last-7d vs prior-7d comps
    for the preview tiles. Emits BOTH turn-weighted and token-weighted
    shares per threshold — the ratio of the two is the overrepresentation
    factor, the actual lever for reducing token spend.

    Each threshold returns:
      - turns:  {recent, prior, series[21]}  (% of turns ≥ threshold)
      - tokens: {recent, prior, series[21]}  (% of ctx tokens from
                                              turns ≥ threshold)
      - overrep_recent: pct_tokens / pct_turns over last 7d
    """
    out: dict[str, dict] = {}
    recent = daily_stats[-7:]
    prior = daily_stats[-14:-7] if len(daily_stats) >= 14 else []
    spark_days = daily_stats[-21:] if len(daily_stats) >= 21 else daily_stats[:]
    for thresh in (200_000, 500_000, 800_000):
        kk = str(thresh // 1000) + "k"
        turn_key = "pct_ctx_gt_" + kk
        tok_key = "pct_tokens_gt_" + kk
        turn_count_key = "turns_ctx_gt_" + kk
        tok_sum_key = "tokens_ctx_gt_" + kk

        def avg_turns(days: list[dict]) -> float:
            if not days:
                return 0.0
            total = sum(d.get("turns", 0) for d in days)
            if not total:
                return 0.0
            heavy = sum(d.get(turn_count_key, 0) for d in days)
            return heavy / total

        def avg_tokens(days: list[dict]) -> float:
            if not days:
                return 0.0
            total_ctx = sum(
                d.get("avg_ctx", 0) * d.get("turns", 0) for d in days
            )
            if not total_ctx:
                return 0.0
            heavy_ctx = sum(d.get(tok_sum_key, 0) for d in days)
            return heavy_ctx / total_ctx

        turns_recent = round(avg_turns(recent), 4)
        tokens_recent = round(avg_tokens(recent), 4)
        overrep = round(tokens_recent / turns_recent, 2) if turns_recent else 0.0
        # Rich per-day series — each entry carries everything the
        # sparkline tooltip needs (date, pct turns, pct tokens, raw
        # counts) so the hover experience can surface complementary
        # data without another lookup against daily_stats.
        rich_series = [
            {
                "day": d.get("day", ""),
                "pct_turns": d.get(turn_key, 0),
                "pct_tokens": d.get(tok_key, 0),
                "heavy_turns": d.get(turn_count_key, 0),
                "total_turns": d.get("turns", 0),
                "heavy_tokens": d.get(tok_sum_key, 0),
            }
            for d in spark_days
        ]
        out[str(thresh)] = {
            "threshold": thresh,
            "turns": {
                "recent": turns_recent,
                "prior": round(avg_turns(prior), 4),
                "series": [d.get(turn_key, 0) for d in spark_days],
            },
            "tokens": {
                "recent": tokens_recent,
                "prior": round(avg_tokens(prior), 4),
                "series": [d.get(tok_key, 0) for d in spark_days],
            },
            "rich_series": rich_series,
            "overrep_recent": overrep,
        }

    # ── Opus-only by-band breakdown for the 5-tier ribbon ───────────
    # The all-models bands above mix Sonnet/Haiku into <200K (which
    # distorts the lift × narrative since Sonnet has flat pricing
    # regardless of context). Below we compute Opus-only shares per
    # band (as a fraction of total Opus turns/tokens for the recent
    # window) and Sonnet/Haiku totals as flat single-segment shares.
    def _opus_share(field: str) -> float:
        if not recent:
            return 0.0
        opus_total = sum(d.get("turns_opus_u200k", 0)
                         + d.get("turns_opus_200_500k", 0)
                         + d.get("turns_opus_500_800k", 0)
                         + d.get("turns_opus_o800k", 0) for d in recent) \
                     if "turns_opus_" in field else \
                     sum(d.get("tokens_opus_u200k", 0)
                         + d.get("tokens_opus_200_500k", 0)
                         + d.get("tokens_opus_500_800k", 0)
                         + d.get("tokens_opus_o800k", 0) for d in recent)
        if not opus_total:
            return 0.0
        band_total = sum(d.get(field, 0) for d in recent)
        return round(band_total / opus_total, 4)

    # Activity totals across recent — used to size each model's share
    # of all activity (Opus+Sonnet+Haiku) so the ribbon segments are
    # proportional across model bands too.
    def _sum(field: str) -> int:
        return sum(d.get(field, 0) for d in recent)
    total_turns = _sum("turns")
    total_tokens_all = sum(d.get("tokens", 0) for d in recent)
    sonnet_turn_share = round(_sum("turns_sonnet") / total_turns, 4) if total_turns else 0
    sonnet_token_share = round(_sum("tokens_sonnet") / total_tokens_all, 4) if total_tokens_all else 0
    haiku_turn_share = round(_sum("turns_haiku") / total_turns, 4) if total_turns else 0
    haiku_token_share = round(_sum("tokens_haiku") / total_tokens_all, 4) if total_tokens_all else 0

    opus_total_turns = (_sum("turns_opus_u200k") + _sum("turns_opus_200_500k")
                        + _sum("turns_opus_500_800k") + _sum("turns_opus_o800k"))
    opus_total_tokens = (_sum("tokens_opus_u200k") + _sum("tokens_opus_200_500k")
                         + _sum("tokens_opus_500_800k") + _sum("tokens_opus_o800k"))
    opus_turn_share_total = round(opus_total_turns / total_turns, 4) if total_turns else 0
    opus_token_share_total = round(opus_total_tokens / total_tokens_all, 4) if total_tokens_all else 0

    # Per-band shares of TOTAL activity (so ribbon segments add to
    # ~100% across all 5 bands: 4 Opus context bands + 1 Sonnet band,
    # with Haiku optionally surfaced if material).
    out["by_model"] = {
        "opus_u200k": {
            "turn_share":  round(_sum("turns_opus_u200k") / total_turns, 4) if total_turns else 0,
            "token_share": round(_sum("tokens_opus_u200k") / total_tokens_all, 4) if total_tokens_all else 0,
        },
        "opus_200_500k": {
            "turn_share":  round(_sum("turns_opus_200_500k") / total_turns, 4) if total_turns else 0,
            "token_share": round(_sum("tokens_opus_200_500k") / total_tokens_all, 4) if total_tokens_all else 0,
        },
        "opus_500_800k": {
            "turn_share":  round(_sum("turns_opus_500_800k") / total_turns, 4) if total_turns else 0,
            "token_share": round(_sum("tokens_opus_500_800k") / total_tokens_all, 4) if total_tokens_all else 0,
        },
        "opus_o800k": {
            "turn_share":  round(_sum("turns_opus_o800k") / total_turns, 4) if total_turns else 0,
            "token_share": round(_sum("tokens_opus_o800k") / total_tokens_all, 4) if total_tokens_all else 0,
        },
        "sonnet": {
            "turn_share":  sonnet_turn_share,
            "token_share": sonnet_token_share,
        },
        "haiku": {
            "turn_share":  haiku_turn_share,
            "token_share": haiku_token_share,
        },
        "totals": {
            "opus_turn_share":  opus_turn_share_total,
            "opus_token_share": opus_token_share_total,
            "total_turns":      total_turns,
            "total_tokens":     total_tokens_all,
        },
    }
    return out


# ─── Ground-truth rate limits (Anthropic OAuth usage endpoint) ───────────
#
# Claude Code's /usage command hits https://api.anthropic.com/api/oauth/usage
# with the OAuth token from macOS Keychain (service "Claude Code-credentials").
# This endpoint returns the REAL utilization + resets_at for both five_hour
# and seven_day rolling windows — the same data Anthropic sends as HTTP
# headers on every Messages API response. No tokens are consumed; this is a
# metadata endpoint, not /v1/messages.
#
# Called once per dashboard regen. Output baked into the JSON payload so the
# HTML can render the true reset anchor instead of the hardcoded Thursday
# 21:00 PT assumption.

RATE_LIMITS_CACHE = Path.home() / ".claude" / "rate-limits-latest.json"
RATE_LIMITS_LOG = Path.home() / ".claude" / "rate-limits.jsonl"
RATE_LIMITS_RAW = Path.home() / ".claude" / "rate-limits-raw-latest.json"
RATE_LIMITS_CACHE_TTL_S = 60  # Anthropic 429s if hit more than ~1/min
FIVE_HRS_MS = 5 * 3600 * 1000
SEVEN_DAYS_MS = 7 * 86400 * 1000

# Second-account ("Max") path: snapshot of an OAuth credential blob captured
# via a one-time `claude /login` to the Max account. Lets us probe a different
# account's rate limits without disturbing the keychain entry that drives the
# active Claude Code session. Refresh handled silently via the OAuth refresh
# endpoint (snapshot is rewritten on every refresh — refresh tokens rotate).
RATE_LIMITS_MAX_SNAPSHOT = Path.home() / ".claude" / "rate-limits-token-max.json"
RATE_LIMITS_MAX_CACHE = Path.home() / ".claude" / "rate-limits-latest-max.json"
RATE_LIMITS_MAX_LOG = Path.home() / ".claude" / "rate-limits-max.jsonl"
RATE_LIMITS_MAX_RAW = Path.home() / ".claude" / "rate-limits-raw-latest-max.json"
OAUTH_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code prod client_id (extracted from CLI binary)
OAUTH_SCOPES = ("user:profile", "user:inference", "user:sessions:claude_code", "user:mcp_servers", "user:file_upload")
OAUTH_USER_AGENT = "claude-cli/2.1.121 (external, cli)"  # Cloudflare blocks default urllib UA
OAUTH_REFRESH_LEEWAY_S = 300  # refresh if token expires within 5 minutes

PACING_LIVE_CACHE = Path.home() / ".claude" / "pacing-live-cache.json"
PACING_LIVE_CACHE_TTL_S = 60  # refresh at most once per minute


def compute_pacing_live() -> dict:
    """Recompute the current 5h pacing window from fresh JSONL without a
    full collect() scan. Reads only JSONL files modified in the last 6h
    (fast), then runs find_anchored_windows on the result to get the
    correct gap-based anchor for the current window.

    Cached for 60s (same TTL as rate-limits) to avoid repeated I/O.
    Returns {"five_hour": {anchor, end, cost, tokens, active}, "computed_at_ms"}.
    """
    import time as _time

    now_ms = int(_time.time() * 1000)

    # ── Cache check ──────────────────────────────────────────────────
    try:
        if PACING_LIVE_CACHE.exists():
            cached = json.loads(PACING_LIVE_CACHE.read_text())
            age_s = _time.time() - (cached.get("computed_at_ms", 0) / 1000)
            if age_s < PACING_LIVE_CACHE_TTL_S:
                return cached
    except Exception:
        pass

    FIFTEEN_MIN_MS = 15 * 60 * 1000
    # Look back 6 hours: covers the full 5h window plus an idle gap before it.
    lookback_ms = now_ms - 6 * 3600 * 1000

    root = Path.home() / ".claude" / "projects"
    raw_turns: list[tuple[int, float, int]] = []  # (ts_ms, cost_usd, tokens)

    try:
        for jsonl_file in root.rglob("*.jsonl"):
            # Skip files whose mtime predates our lookback (coarse filter)
            try:
                if jsonl_file.stat().st_mtime * 1000 < lookback_ms - 3_600_000:
                    continue
            except OSError:
                continue
            try:
                for line in jsonl_file.read_text(errors="replace").splitlines():
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") != "assistant":
                        continue
                    ts_str = msg.get("timestamp")
                    if not ts_str:
                        continue
                    dt = parse_iso_utc(ts_str)
                    if dt is None:
                        continue
                    ts_ms = int(dt.timestamp() * 1000)
                    if ts_ms < lookback_ms:
                        continue
                    usage = (msg.get("message") or {}).get("usage") or {}
                    model = short_model((msg.get("message") or {}).get("model"))
                    cost = cost_for_usage(usage, model)
                    tok = (
                        usage.get("input_tokens", 0)
                        + usage.get("output_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                    )
                    if cost > 0 or tok > 0:
                        raw_turns.append((ts_ms, cost, tok))
            except Exception:
                continue
    except Exception as e:
        return {"error": str(e), "computed_at_ms": now_ms}

    raw_turns.sort()

    # Compute 5h windows via the same gap-based algorithm the full pipeline uses.
    wins = find_anchored_windows(raw_turns, FIVE_HRS_MS, FIFTEEN_MIN_MS)
    result: dict = {"computed_at_ms": now_ms}
    if wins:
        last = wins[-1]
        result["five_hour"] = {
            "anchor": last["anchor"],
            "end":    last["end"],
            "cost":   last["cost"],
            "tokens": last["tokens"],
            "active": now_ms < last["end"],
        }

    # Cache and return
    try:
        PACING_LIVE_CACHE.write_text(json.dumps(result))
    except Exception:
        pass
    return result


def _window_cost_at(turns: list["Turn"], anchor_ms: int, end_ms: int) -> dict:
    """Sum cost and tokens for turns within [anchor_ms, end_ms).

    Used at log time so every rate-limit reading gets paired with the
    cost that was accumulated in that window — enabling reverse-inference
    of the true cap from the historical log alone.
    """
    cost = 0.0
    tokens = 0
    count = 0
    for t in turns:
        if anchor_ms <= t.ts_ms < end_ms:
            cost += t.cost
            tokens += t.input_t + t.output_t + t.cache_read + t.cache_write
            count += 1
    return {
        "cost_at_fetch": round(cost, 4),
        "tokens_at_fetch": tokens,
        "turns_in_window": count,
    }


def fetch_rate_limits_live(
    force: bool = False,
    turns: list["Turn"] | None = None,
) -> dict | None:
    """Fetch live rate-limit utilization + resets_at from Anthropic's
    OAuth usage endpoint. Reads the Max/Pro OAuth token from the macOS
    Keychain (Claude Code stores it under service "Claude Code-credentials").

    Caches the result to ~/.claude/rate-limits-latest.json for 60s to
    avoid 429 throttling (Anthropic rate-limits this endpoint aggressively).
    Also appends every successful fetch to ~/.claude/rate-limits.jsonl
    as a historical time-series log.

    On any error — 429, network, keychain — falls back to the cached value
    (even if stale) so the dashboard still shows ground truth. Returns
    None only when no cache exists and the live fetch also fails.

    Set force=True to bypass cache (e.g., manual refresh from UI).
    """
    import subprocess
    import time
    import urllib.request
    import urllib.error

    # ── Cache check ─────────────────────────────────────────────
    cached: dict | None = None
    try:
        if RATE_LIMITS_CACHE.exists():
            cached = json.loads(RATE_LIMITS_CACHE.read_text())
            age_s = time.time() - (cached.get("fetched_at_ms", 0) / 1000)
            if not force and age_s < RATE_LIMITS_CACHE_TTL_S:
                return cached
    except Exception:
        cached = None

    # ── Token ───────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return cached
        creds = json.loads(result.stdout)
        token = creds.get("claudeAiOauth", {}).get("accessToken")
        if not token:
            return cached
    except Exception:
        return cached

    # ── Live fetch ──────────────────────────────────────────────
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return cached  # fall back to stale cache on 429/network err

    # ── Save raw response for forensic inspection ───────────────
    # Anthropic's OAuth surface is undocumented and evolves (e.g., new
    # codenames like iguana_necktie surface without notice). Persist the
    # last successful raw response so we can diff shapes across accounts
    # / plan migrations without burning rate limits on probes.
    try:
        raw_envelope = {
            "fetched_at_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
            "response": data,
        }
        RATE_LIMITS_RAW.write_text(json.dumps(raw_envelope, indent=2))
    except OSError:
        pass

    out = _normalize_oauth_usage(data, turns)
    if out is None:
        return cached

    # ── Persist ─────────────────────────────────────────────────
    try:
        RATE_LIMITS_CACHE.write_text(json.dumps(out, separators=(",", ":")))
        with open(RATE_LIMITS_LOG, "a") as fh:
            fh.write(json.dumps(out, separators=(",", ":")) + "\n")
    except OSError:
        pass  # cache write failure is non-fatal
    return out


def _normalize_oauth_usage(data: dict, turns: list["Turn"] | None) -> dict | None:
    """Convert raw /api/oauth/usage response into the normalized shape
    consumed by the dashboard. Returns None if the response carries no
    usable signal (so callers can fall back to a cached value).

    Each window's reset_at timestamp defines the END of its current
    rolling window, so anchor = reset - window_duration. When turns are
    provided, snapshot cost/tokens at this (anchor, end) so a future
    pass over the log can reverse-infer the true cap:
      inferred_cap = cost_at_fetch / (utilization / 100)
    """
    fetched_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    out: dict = {"fetched_at_ms": fetched_ms}
    window_durations = {
        "five_hour": FIVE_HRS_MS,
        "seven_day": SEVEN_DAYS_MS,
        "seven_day_opus": SEVEN_DAYS_MS,
        "seven_day_sonnet": SEVEN_DAYS_MS,
        "seven_day_oauth_apps": SEVEN_DAYS_MS,
        "seven_day_cowork": SEVEN_DAYS_MS,
        "seven_day_omelette": SEVEN_DAYS_MS,
        "iguana_necktie": SEVEN_DAYS_MS,  # undocumented codename, all-null on tested accounts; bucket assumed weekly
    }
    for key, duration_ms in window_durations.items():
        w = data.get(key)
        if isinstance(w, dict) and w.get("resets_at"):
            try:
                reset_dt = datetime.fromisoformat(w["resets_at"].replace("Z", "+00:00"))
                reset_ms = int(reset_dt.timestamp() * 1000)
                entry = {
                    "utilization": w.get("utilization"),
                    "resets_at": w["resets_at"],
                    "resets_at_ms": reset_ms,
                }
                if turns is not None and key in ("five_hour", "seven_day"):
                    anchor_ms = reset_ms - duration_ms
                    entry.update(_window_cost_at(turns, anchor_ms, fetched_ms))
                out[key] = entry
            except (ValueError, TypeError):
                pass

    # Monthly credit model (plan migration from 5h/7d windows). When the
    # account is on monthly billing, five_hour/seven_day are null but
    # extra_usage carries the live utilization.
    eu = data.get("extra_usage")
    if isinstance(eu, dict) and eu.get("is_enabled"):
        # Anthropic returns monetary values in cents despite currency="USD".
        raw_limit = eu.get("monthly_limit") or 0
        raw_used  = eu.get("used_credits")  or 0
        out["monthly"] = {
            "utilization":   eu.get("utilization"),
            "used_credits":  round(raw_used  / 100, 2),
            "monthly_limit": round(raw_limit / 100, 2),
            "currency":      eu.get("currency", "USD"),
        }
    op = data.get("omelette_promotional")
    if isinstance(op, dict):
        out["omelette_promotional"] = {
            "utilization": op.get("utilization"),
            "resets_at":   op.get("resets_at"),
        }

    has_data = any(k != "fetched_at_ms" for k in out)
    if not has_data:
        return None

    if "five_hour" in out or "seven_day" in out:
        out["mode"] = "windows"
    elif "monthly" in out:
        out["mode"] = "monthly"
    else:
        out["mode"] = "unknown"
    return out


def _refresh_oauth_token(snapshot: dict) -> dict | None:
    """Use the snapshot's refresh_token to mint a new access_token via
    Anthropic's OAuth refresh endpoint. Returns the updated snapshot dict
    (with new accessToken/refreshToken/expiresAt) on success, None on failure.

    The refresh_token rotates on every call — caller MUST persist the
    returned dict immediately or lose access to the account.
    """
    import time
    import urllib.request
    import urllib.error

    body = json.dumps({
        "grant_type":    "refresh_token",
        "refresh_token": snapshot["refreshToken"],
        "client_id":     OAUTH_CLIENT_ID,
        "scope":         " ".join(OAUTH_SCOPES),
    }).encode()
    req = urllib.request.Request(
        OAUTH_TOKEN_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   OAUTH_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tok = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None
    if "access_token" not in tok:
        return None
    new_snap = dict(snapshot)
    new_snap["accessToken"]    = tok["access_token"]
    new_snap["refreshToken"]   = tok.get("refresh_token", snapshot["refreshToken"])
    new_snap["expiresAt"]      = int(time.time() * 1000) + (tok.get("expires_in", 28800) * 1000)
    new_snap["last_refreshed"] = datetime.now().isoformat()
    return new_snap


def fetch_rate_limits_for_max(
    force: bool = False,
    turns: list["Turn"] | None = None,
) -> dict | None:
    """Fetch live rate-limit utilization for the secondary ("Max") account
    snapshotted at ~/.claude/rate-limits-token-max.json.

    Mirrors fetch_rate_limits_live() but reads its OAuth token from a
    file-backed snapshot (not the macOS keychain). Silently refreshes the
    access token via the OAuth refresh endpoint when within 5 min of expiry,
    persisting the rotated refresh_token back to the snapshot file.

    Returns None when the snapshot doesn't exist or all paths fail with no
    cached value.
    """
    import os
    import stat
    import time
    import urllib.request
    import urllib.error

    # ── Cache check ─────────────────────────────────────────────
    cached: dict | None = None
    try:
        if RATE_LIMITS_MAX_CACHE.exists():
            cached = json.loads(RATE_LIMITS_MAX_CACHE.read_text())
            age_s = time.time() - (cached.get("fetched_at_ms", 0) / 1000)
            if not force and age_s < RATE_LIMITS_CACHE_TTL_S:
                return cached
    except Exception:
        cached = None

    # ── Snapshot load ───────────────────────────────────────────
    if not RATE_LIMITS_MAX_SNAPSHOT.exists():
        return cached
    try:
        snap = json.loads(RATE_LIMITS_MAX_SNAPSHOT.read_text())
    except (OSError, json.JSONDecodeError):
        return cached
    if not snap.get("accessToken") or not snap.get("refreshToken"):
        return cached

    # ── Refresh if token is expired or close to it ─────────────
    expires_ms = snap.get("expiresAt", 0)
    seconds_left = (expires_ms / 1000) - time.time()
    if seconds_left < OAUTH_REFRESH_LEEWAY_S:
        refreshed = _refresh_oauth_token(snap)
        if refreshed is None:
            # Refresh failed; try the existing access token anyway in case
            # the leeway was overly conservative. If that fails, fall back.
            pass
        else:
            snap = refreshed
            try:
                RATE_LIMITS_MAX_SNAPSHOT.write_text(json.dumps(snap, indent=2))
                os.chmod(RATE_LIMITS_MAX_SNAPSHOT, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass

    # ── Live fetch ──────────────────────────────────────────────
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization":  f"Bearer {snap['accessToken']}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type":   "application/json",
            "User-Agent":     OAUTH_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return cached

    # If the access token was rejected (401), force a refresh and retry once
    # — covers the case where the snapshot had a recently-revoked token.
    # urlopen raises HTTPError before reaching here, so this branch is for
    # the rare "200 with auth error in body" shape Anthropic occasionally returns.
    if isinstance(data, dict) and data.get("error", {}).get("type") in ("authentication_error", "permission_error"):
        refreshed = _refresh_oauth_token(snap)
        if refreshed:
            snap = refreshed
            try:
                RATE_LIMITS_MAX_SNAPSHOT.write_text(json.dumps(snap, indent=2))
                os.chmod(RATE_LIMITS_MAX_SNAPSHOT, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            req2 = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/usage",
                headers={
                    "Authorization":  f"Bearer {snap['accessToken']}",
                    "anthropic-beta": "oauth-2025-04-20",
                    "Content-Type":   "application/json",
                    "User-Agent":     OAUTH_USER_AGENT,
                },
            )
            try:
                with urllib.request.urlopen(req2, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception:
                return cached

    # ── Save raw response ────────────────────────────────────────
    try:
        RATE_LIMITS_MAX_RAW.write_text(json.dumps({
            "fetched_at_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
            "account": snap.get("account"),
            "response": data,
        }, indent=2))
    except OSError:
        pass

    out = _normalize_oauth_usage(data, turns)
    if out is None:
        return cached
    # Tag the payload with account identity so the dashboard can label it.
    if isinstance(snap.get("account"), dict):
        out["account_label"] = snap["account"].get("email") or "Max account"
        out["account_uuid"]  = snap["account"].get("uuid")
        out["plan"]          = "max" if snap["account"].get("has_claude_max") else (
                               "pro" if snap["account"].get("has_claude_pro") else "unknown")

    # ── Persist ─────────────────────────────────────────────────
    try:
        RATE_LIMITS_MAX_CACHE.write_text(json.dumps(out, separators=(",", ":")))
        with open(RATE_LIMITS_MAX_LOG, "a") as fh:
            fh.write(json.dumps(out, separators=(",", ":")) + "\n")
    except OSError:
        pass
    return out


# ─── Dashboard emission ──────────────────────────────────────────────────

def _fmt_compact_num(n: float | int | None) -> str:
    if n is None:
        return "unknown"
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "unknown"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.0f}K"
    return str(int(round(v)))


def _fmt_pct_points(v: float | int | None) -> str:
    if v is None:
        return "unknown"
    return f"{float(v):.0f}%"


def _metric_delta(recent: list[dict], prior: list[dict], key: str, mode: str = "avg") -> float | None:
    if not recent or not prior:
        return None
    if mode == "sum":
        r = sum(d.get(key, 0) or 0 for d in recent)
        p = sum(d.get(key, 0) or 0 for d in prior)
    else:
        r = sum(d.get(key, 0) or 0 for d in recent) / len(recent)
        p = sum(d.get(key, 0) or 0 for d in prior) / len(prior)
    if not p:
        return None
    return (r - p) / p


def _build_usage_fact_packet(payload: dict) -> dict:
    """Small, non-sensitive aggregate packet used for dashboard assertions.

    This intentionally excludes raw turns, prompts, file paths, and session ids.
    Claude-authored copy, when enabled, only sees these aggregate facts.
    """
    daily = payload.get("daily_stats") or []
    recent = daily[-7:]
    prior = daily[-14:-7] if len(daily) >= 14 else []

    def _date_range(arr):
        if not arr:
            return None
        first, last = arr[0].get("day", ""), arr[-1].get("day", "")
        MO = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        try:
            y0, m0, d0 = [int(x) for x in first.split("-")]
            y1, m1, d1 = [int(x) for x in last.split("-")]
            ms0, ms1 = MO[m0 - 1], MO[m1 - 1]
            if ms0 == ms1 and y0 == y1:
                return f"{ms0} {d0}–{d1}"
            return f"{ms0} {d0}–{ms1} {d1}"
        except Exception:
            return None

    recent_range = _date_range(recent)
    prior_range = _date_range(prior)
    rl = payload.get("rate_limits_live") or {}
    preview = payload.get("preview") or {}
    heavy = (preview.get("heavy_buckets") or {}).get("by_model") or {}
    eff = preview.get("efficiency") or {}

    window = None
    if rl.get("seven_day"):
        window = {"label": "weekly", "ms": SEVEN_DAYS_MS, **rl["seven_day"]}
    elif rl.get("five_hour"):
        window = {"label": "current session", "ms": FIVE_HRS_MS, **rl["five_hour"]}
    elif rl.get("monthly"):
        window = {"label": "monthly credits", "ms": 30 * 86400 * 1000, **rl["monthly"]}

    pacing = {"status": "unknown", "label": "usage", "pct_used": None, "pct_elapsed": None}
    now_ms = payload.get("now_ms") or int(datetime.now(timezone.utc).timestamp() * 1000)
    if window:
        pct_used = window.get("utilization")
        reset_ms = window.get("resets_at_ms")
        pct_elapsed = None
        if reset_ms:
            anchor = int(reset_ms) - int(window["ms"])
            pct_elapsed = max(0, min(100, ((now_ms - anchor) / int(window["ms"])) * 100))
        if pct_used is not None and pct_elapsed is not None:
            delta = float(pct_used) - pct_elapsed
            status = "over_pace" if delta >= 8 else "near_pace" if delta >= -8 else "under_pace"
            if float(pct_used) >= 90:
                status = "cap_risk"
            pacing = {
                "status": status,
                "label": window["label"],
                "pct_used": round(float(pct_used), 1),
                "pct_elapsed": round(float(pct_elapsed), 1),
                "pace_delta_points": round(delta, 1),
                "reset_at_ms": reset_ms,
                "freshness": "live" if rl.get("fetched_at_ms") else "snapshot",
            }

    metric_specs = [
        ("avg_ctx", "average context", "avg"),
        ("cost", "cost per day", "avg"),
        ("cost_per_turn", "cost per turn", "avg"),
        ("cache_hit_rate", "cache hit rate", "avg"),
        ("tokens", "tokens per day", "avg"),
        ("turns", "turns per day", "avg"),
    ]
    largest_change = None
    for key, label, mode in metric_specs:
        d = _metric_delta(recent, prior, key, mode)
        if d is None:
            continue
        item = {"key": key, "label": label, "delta_pct": round(d * 100, 1)}
        if largest_change is None or abs(item["delta_pct"]) > abs(largest_change["delta_pct"]):
            largest_change = item

    top_driver = None
    labels = {
        "opus_u200k": "<200K Opus",
        "opus_200_500k": "200-500K Opus",
        "opus_500_800k": "500-800K Opus",
        "opus_o800k": ">=800K Opus",
        "sonnet": "Sonnet",
        "haiku": "Haiku",
    }
    for key, vals in heavy.items():
        if key == "totals" or not isinstance(vals, dict):
            continue
        turn_share = vals.get("turn_share") or 0
        token_share = vals.get("token_share") or 0
        lift = (token_share / turn_share) if turn_share else 0
        item = {
            "key": key,
            "label": labels.get(key, key),
            "turn_share": round(turn_share * 100, 1),
            "token_share": round(token_share * 100, 1),
            "lift": round(lift, 2),
        }
        if top_driver is None or item["lift"] > top_driver["lift"]:
            top_driver = item

    best_lever = None
    for key, label in [
        ("route_heavy_to_sonnet", "Route heavy-context work to Sonnet subagents"),
        ("compact_below_200k", "Compact before turns cross 200K context"),
        ("both", "Route heavy work and compact earlier"),
    ]:
        cfo = (eff.get("counterfactuals") or {}).get(key) or {}
        if cfo.get("pct_reduction") is None:
            continue
        item = {
            "key": key,
            "label": label,
            "pct_reduction": cfo.get("pct_reduction"),
            "usd_per_mtok": cfo.get("usd_per_mtok"),
        }
        if best_lever is None or item["pct_reduction"] > best_lever["pct_reduction"]:
            best_lever = item

    return {
        "pacing": pacing,
        "largest_change": largest_change,
        "top_driver": top_driver,
        "best_lever": best_lever,
        "lookback_days": payload.get("lookback_days"),
        "turn_count": payload.get("turn_count"),
        "recent_range": recent_range,
        "prior_range": prior_range,
    }


def _deterministic_insight_from_facts(facts: dict) -> dict:
    pacing = facts.get("pacing") or {}
    change = facts.get("largest_change") or {}
    driver = facts.get("top_driver") or {}
    lever = facts.get("best_lever") or {}

    status = pacing.get("status", "unknown")
    label = str(pacing.get("label") or "usage")
    subject = label if "usage" in label.lower() else f"{label} usage"
    if status == "cap_risk":
        headline = f"{subject.title()} is close to the limit."
        tone = "critical"
    elif status == "over_pace":
        headline = f"{subject.title()} is running ahead of pace."
        tone = "warning"
    elif status == "under_pace":
        headline = f"{subject.title()} is comfortably under pace."
        tone = "good"
    elif status == "near_pace":
        headline = f"{subject.title()} is roughly on pace."
        tone = "neutral"
    else:
        # No rate-limit window — infer headline from change + driver facts
        has_change = change and abs(change.get("delta_pct", 0)) >= 15
        has_driver = driver and driver.get("lift", 0) > 1.5
        if has_change and has_driver:
            direction = "upswing" if change["delta_pct"] >= 0 else "drop"
            headline = (
                f"{driver['label']} is driving a {abs(change['delta_pct']):.0f}%"
                f" {direction} in {change['label']}."
            )
            tone = "warning" if change["delta_pct"] >= 15 else ("good" if change["delta_pct"] <= -15 else "neutral")
        elif has_change:
            direction = "up" if change["delta_pct"] >= 0 else "down"
            headline = (
                f"{change['label'].title()} is {direction}"
                f" {abs(change['delta_pct']):.0f}% vs the prior period."
            )
            tone = "warning" if change["delta_pct"] >= 30 else ("good" if change["delta_pct"] <= -20 else "neutral")
        elif has_driver:
            headline = f"{driver['label']} is taking an outsized share of tokens."
            tone = "neutral"
        else:
            headline = "Usage patterns are ready to inspect."
            tone = "neutral"

    subparts = []
    if pacing.get("pct_used") is not None and pacing.get("pct_elapsed") is not None:
        subparts.append(
            f"{_fmt_pct_points(pacing.get('pct_used'))} used with "
            f"{_fmt_pct_points(pacing.get('pct_elapsed'))} of the window elapsed"
        )
    if change:
        direction = "up" if change["delta_pct"] >= 0 else "down"
        prior_range = facts.get("prior_range")
        recent_range = facts.get("recent_range")
        period_label = f"vs {prior_range}" if prior_range else "vs the prior period"
        window_note = f" ({recent_range} vs {prior_range})" if (recent_range and prior_range) else ""
        subparts.append(
            f"{change['label']} is {direction} {abs(change['delta_pct']):.0f}%"
            f" {period_label}{window_note}"
        )
    if driver and driver.get("lift", 0) > 1.15:
        subparts.append(
            f"{driver['label']} takes {driver['lift']:.1f}x its share of tokens "
            f"({driver['token_share']:.0f}% tokens / {driver['turn_share']:.0f}% turns)"
        )
    subhead = ". ".join(subparts) + "." if subparts else "No strong usage driver stands out yet."

    action = "Keep watching the trend chart for the next usage shift."
    if lever and lever.get("pct_reduction", 0) >= 5:
        action = f"{lever['label']} could cut the trailing token rate by about {lever['pct_reduction']:.0f}%."
    elif status in {"over_pace", "cap_risk"}:
        action = "Pause heavy Opus work, compact active sessions, or split the next task into a fresh session."

    return {
        "mode": "deterministic",
        "tone": tone,
        "headline": headline,
        "subhead": subhead,
        "action": action,
        "facts": facts,
    }


def _claude_insight_from_facts(facts: dict) -> dict | None:
    prompt = (
        "You write concise, factual dashboard assertions for a local Claude Code usage dashboard.\n"
        "Use ONLY the JSON facts provided. Do not infer beyond them. Return strict JSON with keys: "
        "headline, subhead, action, tone. tone must be one of good, neutral, warning, critical.\n\n"
        f"FACTS:\n{json.dumps(facts, separators=(',', ':'))}"
    )
    try:
        import subprocess
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = json.loads(raw[start:end + 1])
        if not isinstance(parsed, dict) or not parsed.get("headline"):
            return None
        parsed["mode"] = "claude"
        parsed["facts"] = facts
        return parsed
    except Exception:
        return None


def build_usage_insights(payload: dict, mode: str = "deterministic") -> dict | None:
    if mode == "off":
        return None
    facts = _build_usage_fact_packet(payload)
    deterministic = _deterministic_insight_from_facts(facts)
    if mode == "claude":
        authored = _claude_insight_from_facts(facts)
        if authored:
            authored["fallback"] = deterministic
            return authored
        deterministic["mode"] = "deterministic_fallback"
    return deterministic


def to_json(ds: Dataset, insights_mode: str = "deterministic") -> str:
    full = getattr(ds, "full_turns", [])
    FIVE_HRS_MS = 5 * 3600 * 1000
    FIFTEEN_MIN_MS = 15 * 60 * 1000
    # 5-hour window: reset on idle gap >= 15 min OR 5h elapsed, whichever
    # comes first. Gap threshold derived from ground-truth analysis of
    # rate-limit error logs (see find_anchored_windows docstring).
    anchored_5h = find_anchored_windows(full, FIVE_HRS_MS, FIFTEEN_MIN_MS)
    # 7-day: fixed Thu 21:00 PT bucket, NOT gap-based (Keith's empirical
    # calibration — the Max weekly cap resets on a clock, not on activity).
    anchored_7d = thursday_weekly_windows(full)

    # Harvest any new rate-limit error anchors since the last run and
    # merge them into the sidecar calibrations.json. This is a cheap
    # incremental pass (uses the stored last_harvested_ts_ms watermark).
    projects_root = Path.home() / ".claude" / "projects"
    try:
        sidecar = refresh_sidecar(projects_root)
    except Exception:
        sidecar = load_sidecar_calibrations()
    # Compact summary for the dashboard payload
    calibrations_summary = {
        "auto_count":   len(sidecar.get("auto_anchors", [])),
        "manual_count": len(sidecar.get("manual_entries", [])),
        "recent_auto": [
            {
                "error_ts_ms": a["error_ts_ms"],
                "kind": a.get("kind"),
                "reset_hour_pt": a.get("reset_hour_pt"),
                "implied_anchor_ts_ms": a.get("implied_anchor_ts_ms"),
            }
            for a in sidecar.get("auto_anchors", [])[-10:]
        ],
    }

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    daily_stats = getattr(ds, "daily_stats", [])
    rate_limits_live = getattr(ds, "sample_rate_limits", None) or fetch_rate_limits_live(turns=ds.turns)
    preview = {
        "active_sessions": aggregate_sessions(ds.turns, now_ms),
        "ctx_histogram": compute_ctx_histogram(ds.turns, now_ms),
        "heavy_buckets": compute_heavy_bucket_summary(daily_stats),
        "efficiency": compute_efficiency_model(daily_stats, lookback_days=7),
        "thresholds": list(HEAVY_BUCKET_THRESHOLDS),
        "active_idle_ms": ACTIVE_IDLE_MS,
        "sessions_lookback_ms": SESSIONS_LOOKBACK_MS,
        "scatter_window_days": SCATTER_WINDOW_DAYS,
    }

    payload = {
        "generated_at": ds.generated_at,
        "lookback_days": ds.lookback_days,
        "turn_count": len(ds.turns),
        "first_ms": ds.first_ms,
        "last_ms": ds.last_ms,
        "now_ms": now_ms,
        "week_start_ms": int(week_start_for(datetime.now(timezone.utc)).timestamp() * 1000),
        "full_turn_count": len(full),
        "anchored_5h": anchored_5h,
        "anchored_7d": anchored_7d,
        "daily_stats": daily_stats,
        "first_1m_ms": getattr(ds, "first_1m_ms", None),
        "calibrations": calibrations_summary,
        # ── Ground-truth rate limits (live fetch at regen time) ──
        "rate_limits_live": rate_limits_live,
        # ── Secondary account (Max) — populated only when snapshot exists ──
        "rate_limits_max":  fetch_rate_limits_for_max(turns=ds.turns),
        # ── Preview payload (Phase 0 chart previews) ──
        "preview": preview,
        "turns": [
            {
                "ts": t.ts_ms,
                "ctx": t.ctx,
                "in": t.input_t,
                "out": t.output_t,
                "cr": t.cache_read,
                "cw": t.cache_write,
                "m": t.model,
                "s": t.sid,
                "t": t.stype,
                "sc": t.side,
                "c": round(t.cost, 4),
            }
            for t in ds.turns
        ],
    }
    insights = build_usage_insights(payload, insights_mode)
    if insights:
        payload["insights"] = insights
    return json.dumps(payload, separators=(",", ":"))


def load_template(layout: str = "editorial") -> str:
    """Load the dashboard HTML template for the requested layout.

    Two layouts ship today:
      * editorial  (default) — narrow column, mobile-friendly, full feature set
      * wide       — desktop-dashboard grid, summary-focused

    Looks in two places, in order:
      1. <layout>-template.html next to this script (dev mode)
      2. ~/.claude-usage-monitor/<layout>-template.html (user install)
      3. Hardcoded EMBEDDED_TEMPLATE below (fully self-contained build,
         editorial only — wide is dev-path only until we need to ship it)
    """
    filename = {
        "editorial": "dashboard-template.html",
        "wide":      "dashboard-wide-template.html",
        "preview":   "dashboard-preview-template.html",
    }.get(layout)
    if not filename:
        raise RuntimeError(f"Unknown layout: {layout!r}")

    script_dir = Path(__file__).resolve().parent
    for candidate in [
        script_dir / filename,
        Path.home() / ".claude-usage-monitor" / filename,
    ]:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    if layout == "editorial" and EMBEDDED_TEMPLATE:
        return EMBEDDED_TEMPLATE
    raise RuntimeError(
        f"No {filename} found and no embedded template compiled in. "
        "Download it from https://data-centered.com/tools/claude-usage-monitor/"
    )


def render_html(ds: Dataset, layout: str = "editorial", insights_mode: str = "deterministic") -> str:
    tpl = load_template(layout)
    data_json = to_json(ds, insights_mode=insights_mode)
    # Substitute the __USAGE__ placeholder. The template uses
    # `window.__USAGE__ = {};` as a marker — replace the {} with our data.
    marker = "window.__USAGE__ = {};"
    if marker not in tpl:
        raise RuntimeError(
            "Template is missing the `window.__USAGE__ = {};` marker. "
            "Make sure you're using the release template."
        )
    return tpl.replace(marker, f"window.__USAGE__ = {data_json};")


# Populated at release time by bundle.py. Kept empty in dev so edits to
# dashboard-template.html are picked up immediately.
EMBEDDED_TEMPLATE = ""


# ─── CLI ─────────────────────────────────────────────────────────────────

def _build_daily_stats_from_turns(turns: list[Turn]) -> list[dict]:
    """Aggregate Turn objects into the daily_stats list that to_json() expects."""
    pt = pt_tz()
    daily: dict[str, dict] = {}
    for t in turns:
        dt = datetime.fromtimestamp(t.ts_ms / 1000, tz=timezone.utc)
        day = dt.astimezone(pt).strftime("%Y-%m-%d")
        if day not in daily:
            daily[day] = {
                "turns": 0, "tokens": 0, "cost": 0.0,
                "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "ctx_sum": 0, "opus": 0, "sonnet": 0, "haiku": 0,
                "ctx_gt_200k": 0, "ctx_gt_500k": 0, "ctx_gt_800k": 0,
                "ctx_sum_gt_200k": 0, "ctx_sum_gt_500k": 0, "ctx_sum_gt_800k": 0,
                "cost_opus": 0.0, "cost_sonnet": 0.0, "cost_haiku": 0.0,
                "tokens_opus": 0, "tokens_sonnet": 0, "tokens_haiku": 0,
                "tokens_opus_u200k": 0, "tokens_opus_200_500k": 0,
                "tokens_opus_500_800k": 0, "tokens_opus_o800k": 0,
                "turns_opus_u200k": 0, "turns_opus_200_500k": 0,
                "turns_opus_500_800k": 0, "turns_opus_o800k": 0,
                "cost_gt_200k": 0.0, "tokens_gt_200k": 0,
                "cost_if_sonnet": 0.0, "cost_if_sonnet_gt_200k": 0.0,
            }
        d = daily[day]
        total_tok = t.input_t + t.output_t + t.cache_read + t.cache_write
        sr = MODEL_RATES["sonnet"]
        cif = (t.input_t * sr["input"] + t.output_t * sr["output"] +
               t.cache_read * sr["cache_read"] + t.cache_write * sr["cache_write"]) / 1_000_000
        d["turns"] += 1; d["tokens"] += total_tok; d["cost"] += t.cost
        d["input"] += t.input_t; d["output"] += t.output_t
        d["cache_read"] += t.cache_read; d["cache_write"] += t.cache_write
        d["ctx_sum"] += t.ctx; d["cost_if_sonnet"] += cif
        if t.ctx > 200_000:
            d["ctx_gt_200k"] += 1; d["ctx_sum_gt_200k"] += t.ctx
            d["cost_gt_200k"] += t.cost; d["tokens_gt_200k"] += total_tok
            d["cost_if_sonnet_gt_200k"] += cif
        if t.ctx > 500_000:
            d["ctx_gt_500k"] += 1; d["ctx_sum_gt_500k"] += t.ctx
        if t.ctx > 800_000:
            d["ctx_gt_800k"] += 1; d["ctx_sum_gt_800k"] += t.ctx
        if t.model == "opus":
            d["opus"] += 1; d["cost_opus"] += t.cost; d["tokens_opus"] += total_tok
            tier = ("u200k" if t.ctx <= 200_000 else "200_500k" if t.ctx <= 500_000
                    else "500_800k" if t.ctx <= 800_000 else "o800k")
            d[f"tokens_opus_{tier}"] += total_tok; d[f"turns_opus_{tier}"] += 1
        elif t.model == "sonnet":
            d["sonnet"] += 1; d["cost_sonnet"] += t.cost; d["tokens_sonnet"] += total_tok
        elif t.model == "haiku":
            d["haiku"] += 1; d["cost_haiku"] += t.cost; d["tokens_haiku"] += total_tok
    result = []
    for day in sorted(daily.keys()):
        d = daily[day]; n = d["turns"]
        denom = (d["cache_read"] + d["cache_write"] + d["input"]) or 1
        cs = d["ctx_sum"] or 1
        result.append({
            "day": day, "turns": n, "tokens": d["tokens"],
            "cost": round(d["cost"], 2), "input": d["input"], "output": d["output"],
            "cache_read": d["cache_read"], "cache_write": d["cache_write"],
            "avg_ctx": round(d["ctx_sum"] / n) if n else 0,
            "tokens_per_turn": round(d["tokens"] / n) if n else 0,
            "cost_per_turn": round(d["cost"] / n, 4) if n else 0,
            "cache_hit_rate": round(d["cache_read"] / denom, 4),
            "opus": d["opus"], "sonnet": d["sonnet"], "haiku": d["haiku"],
            "turns_ctx_gt_200k": d["ctx_gt_200k"], "turns_ctx_gt_500k": d["ctx_gt_500k"],
            "turns_ctx_gt_800k": d["ctx_gt_800k"],
            "pct_ctx_gt_200k": round(d["ctx_gt_200k"] / n, 4) if n else 0,
            "pct_ctx_gt_500k": round(d["ctx_gt_500k"] / n, 4) if n else 0,
            "pct_ctx_gt_800k": round(d["ctx_gt_800k"] / n, 4) if n else 0,
            "tokens_ctx_gt_200k": d["ctx_sum_gt_200k"], "tokens_ctx_gt_500k": d["ctx_sum_gt_500k"],
            "tokens_ctx_gt_800k": d["ctx_sum_gt_800k"],
            "pct_tokens_gt_200k": round(d["ctx_sum_gt_200k"] / cs, 4),
            "pct_tokens_gt_500k": round(d["ctx_sum_gt_500k"] / cs, 4),
            "pct_tokens_gt_800k": round(d["ctx_sum_gt_800k"] / cs, 4),
            "cost_opus": round(d["cost_opus"], 4), "cost_sonnet": round(d["cost_sonnet"], 4),
            "cost_haiku": round(d["cost_haiku"], 4),
            "tokens_opus": d["tokens_opus"], "tokens_sonnet": d["tokens_sonnet"],
            "tokens_haiku": d["tokens_haiku"],
            "cost_gt_200k": round(d["cost_gt_200k"], 4), "tokens_gt_200k": d["tokens_gt_200k"],
            "cost_if_sonnet": round(d["cost_if_sonnet"], 4),
            "cost_if_sonnet_gt_200k": round(d["cost_if_sonnet_gt_200k"], 4),
            "tokens_opus_u200k": d["tokens_opus_u200k"],
            "tokens_opus_200_500k": d["tokens_opus_200_500k"],
            "tokens_opus_500_800k": d["tokens_opus_500_800k"],
            "tokens_opus_o800k": d["tokens_opus_o800k"],
            "turns_opus_u200k": d["turns_opus_u200k"],
            "turns_opus_200_500k": d["turns_opus_200_500k"],
            "turns_opus_500_800k": d["turns_opus_500_800k"],
            "turns_opus_o800k": d["turns_opus_o800k"],
            "turns_sonnet": d["sonnet"], "turns_haiku": d["haiku"],
        })
    return result


def generate_sample_dataset(days: int = 90) -> Dataset:
    """Build a Dataset from synthetic turns for demo/documentation purposes.

    Narrative arc (90 days):
      - Days 90-61: pre-1M-context baseline — Sonnet-heavy, small contexts (<150K), steady pace
      - Days 60-55: 1M context window rollout — visible volume surge, sudden appearance of
                    500K-900K turns, Opus share jumps as users push the new ceiling
      - Days 54-30: post-rollout sustained heavy period, large contexts now routine
      - Days 29-10: mixed sprints and quieter stretches
      - Days  9- 1: recent moderate-to-heavy, Opus/Sonnet mix

    Deterministic (seed=42). No real user data.
    Usage: python3 claude_usage.py --sample --layout wide --open
    """
    import random
    rng = random.Random(42)
    DAY_MS = 86_400_000
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Rollout week: day_offset range where 1M context becomes available.
    # Placed ~60 days ago so the before/after contrast is visible in the chart.
    ROLLOUT_START = 60  # day_offset where the surge begins (older end)
    ROLLOUT_END   = 55  # day_offset where the surge peaks and normalizes

    turns: list[Turn] = []

    for day_offset in range(days, 0, -1):  # oldest first

        if day_offset > ROLLOUT_START:
            # ── Pre-rollout baseline ──────────────────────────────────────
            # Small contexts only, no heavy turns, mostly Sonnet.
            n_turns   = rng.randint(18, 32)
            opus_f, son_f = 0.12, 0.72
            heavy_f   = 0.0   # no large contexts yet
            ctx_cap   = 140_000

        elif day_offset >= ROLLOUT_END:
            # ── 1M rollout surge week ─────────────────────────────────────
            # Volume spikes sharply, large contexts appear for the first time,
            # Opus share jumps as users experiment with the new ceiling.
            n_turns   = rng.randint(70, 95)
            opus_f, son_f = 0.52, 0.38
            heavy_f   = 0.55  # majority of turns now hitting large contexts
            ctx_cap   = 980_000

        elif day_offset >= 40:
            # ── Post-rollout sustained heavy ──────────────────────────────
            n_turns   = rng.randint(48, 72)
            opus_f, son_f = 0.42, 0.46
            heavy_f   = 0.38
            ctx_cap   = 980_000

        elif day_offset >= 25:
            # ── Quieter stretch / lighter projects ────────────────────────
            n_turns   = rng.randint(20, 38)
            opus_f, son_f = 0.22, 0.65
            heavy_f   = 0.15
            ctx_cap   = 980_000

        elif day_offset >= 10:
            # ── Second sprint ─────────────────────────────────────────────
            n_turns   = rng.randint(55, 80)
            opus_f, son_f = 0.48, 0.42
            heavy_f   = 0.40
            ctx_cap   = 980_000

        elif day_offset <= 3:
            # ── Most recent days: moderate ────────────────────────────────
            n_turns   = rng.randint(25, 42)
            opus_f, son_f = 0.28, 0.62
            heavy_f   = 0.18
            ctx_cap   = 980_000

        else:
            # ── Recent wind-down ──────────────────────────────────────────
            n_turns   = rng.randint(30, 55)
            opus_f, son_f = 0.35, 0.55
            heavy_f   = 0.25
            ctx_cap   = 980_000

        haiku_f = 1.0 - opus_f - son_f
        t_cursor = now_ms - day_offset * DAY_MS + rng.randint(8 * 3_600_000, 10 * 3_600_000)

        n_sessions = max(1, min(4, n_turns // 12))
        session_ids = [f"{rng.randint(0, 0xFFFFFFFF):08x}" for _ in range(n_sessions)]
        sess_sizes = [0] * n_sessions
        for _ in range(n_turns):
            sess_sizes[rng.randint(0, n_sessions - 1)] += 1

        for sess_idx, (sid, n_in_sess) in enumerate(zip(session_ids, sess_sizes)):
            cache_pool = 0
            for turn_idx in range(n_in_sess):
                t_cursor += rng.randint(90_000, 720_000)  # 1.5–12 min per turn

                r = rng.random()
                model = "opus" if r < opus_f else ("sonnet" if r < opus_f + son_f else "haiku")

                if heavy_f > 0 and rng.random() < heavy_f:
                    ctx = rng.choices(
                        [rng.randint(200_001, 499_999),
                         rng.randint(500_000, 799_999),
                         min(ctx_cap, rng.randint(800_000, 980_000))],
                        weights=[0.45, 0.32, 0.23],
                    )[0]
                else:
                    ctx = rng.randint(4_000, min(ctx_cap, 185_000))

                if turn_idx == 0:
                    input_t = max(3_000, int(ctx * rng.uniform(0.15, 0.28)))
                    cache_write = max(0, int(ctx * rng.uniform(0.50, 0.68)))
                    cache_read = max(0, ctx - input_t - cache_write)
                    cache_pool = input_t + cache_write
                else:
                    input_t = max(2_000, int(ctx * rng.uniform(0.04, 0.12)))
                    cache_read = min(cache_pool, max(0, ctx - input_t - rng.randint(0, 8_000)))
                    cache_write = max(0, ctx - input_t - cache_read)
                    cache_pool = max(cache_pool, input_t + cache_read + cache_write)

                output_t = rng.randint(400, 3_800)
                rates = MODEL_RATES.get(model, DEFAULT_RATES)
                cost = (
                    input_t * rates["input"] + output_t * rates["output"] +
                    cache_read * rates["cache_read"] + cache_write * rates["cache_write"]
                ) / 1_000_000

                stype = "headless" if (sess_idx > 0 and rng.random() < 0.12) else "interactive"
                side = 1 if (stype == "headless" and rng.random() < 0.4) else 0

                turns.append(Turn(
                    ts_ms=t_cursor,
                    ctx=input_t + cache_read + cache_write,
                    input_t=input_t, output_t=output_t,
                    cache_read=cache_read, cache_write=cache_write,
                    model=model, sid=sid, stype=stype, side=side,
                    cost=round(cost, 4),
                ))

            t_cursor += rng.randint(30 * 60_000, 180 * 60_000)  # gap between sessions

    turns.sort(key=lambda t: t.ts_ms)
    full_turns = [(t.ts_ms, t.cost, t.input_t + t.output_t + t.cache_read + t.cache_write)
                  for t in turns]
    ds = Dataset(
        turns=turns,
        generated_at=datetime.now(pt_tz()).isoformat(timespec="seconds"),
        lookback_days=days,
        first_ms=turns[0].ts_ms if turns else 0,
        last_ms=turns[-1].ts_ms if turns else 0,
    )
    ds.full_turns = full_turns                              # type: ignore[attr-defined]
    ds.daily_stats = _build_daily_stats_from_turns(turns)  # type: ignore[attr-defined]
    ds.first_1m_ms = None                                  # type: ignore[attr-defined]
    # Synthetic rate limits so the sample pacing charts render yellow/red:
    # 87% of weekly cap used but only ~65% of window elapsed → over-pace.
    _now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ds.sample_rate_limits = {                              # type: ignore[attr-defined]
        "fetched_at_ms": _now_ms - 300_000,
        "mode": "windows",
        "five_hour": {
            "utilization": 74,
            "resets_at_ms": _now_ms + 45 * 60_000,
        },
        "seven_day": {
            "utilization": 87,
            "resets_at_ms": _now_ms + int(2.5 * 86_400_000),
        },
    }
    return ds


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate a local Claude Code usage dashboard from ~/.claude/projects.",
    )
    ap.add_argument("--days", type=int, default=9999, help="PT days to look back (default: all-time)")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output HTML path (default: claude-usage-dashboard.html, or -sample.html with --sample)")
    ap.add_argument("--layout", choices=["editorial", "wide", "preview"], default="editorial",
                    help="Template layout: editorial, wide, or preview (phase-0 chart previews)")
    ap.add_argument("--all-layouts", action="store_true",
                    help="Emit both layouts. Uses --out as a base; appends '-wide' for the wide variant.")
    ap.add_argument("--open", action="store_true", help="Open the dashboard in your browser when done")
    ap.add_argument("--json-only", action="store_true", help="Write usage data as JSON instead of HTML")
    ap.add_argument("--sample", action="store_true",
                    help="Generate from synthetic demo data instead of ~/.claude/ (no personal data)")
    ap.add_argument(
        "--insights",
        choices=["off", "deterministic", "claude"],
        default="deterministic",
        help=(
            "Executive assertion mode: off, deterministic template copy, or "
            "claude-authored copy from aggregate facts only (default: deterministic)"
        ),
    )
    args = ap.parse_args()

    if args.sample:
        sample_days = args.days if args.days != 9999 else 90
        ds = generate_sample_dataset(sample_days)
        if args.out is None:
            args.out = Path("claude-usage-sample.html")
    else:
        if args.out is None:
            args.out = Path("claude-usage-dashboard.html")
        ds = collect(args.days)
    if not ds.turns:
        print("No turns found in the lookback window. Nothing to render.", file=sys.stderr)
        sys.exit(1)

    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.json_only:
        args.out.write_text(to_json(ds, insights_mode=args.insights), encoding="utf-8")
        size_kb = args.out.stat().st_size / 1024
        print(f"wrote {len(ds.turns):,} turns → {args.out} ({size_kb:.0f} KB)")
        return

    outputs: list[tuple[Path, str]] = []
    if args.all_layouts:
        outputs.append((args.out, "editorial"))
        wide_out = args.out.with_name(args.out.stem + "-wide" + args.out.suffix)
        outputs.append((wide_out, "wide"))
    else:
        outputs.append((args.out, args.layout))

    for out_path, layout in outputs:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_html(ds, layout, insights_mode=args.insights), encoding="utf-8")
        size_kb = out_path.stat().st_size / 1024
        print(f"wrote {len(ds.turns):,} turns → {out_path} ({size_kb:.0f} KB) [layout: {layout}]")

    if args.open:
        webbrowser.open(outputs[0][0].resolve().as_uri())


if __name__ == "__main__":
    main()
