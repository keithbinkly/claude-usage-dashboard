# Template Diff Audit — claude-usage-dashboard vs canonical
Generated: 2026-06-12

## Summary Counts
- MISSING-FEATURE hunks: 45 (python) + 239 (HTML) = 284 total
- PRESERVE hunks: 2 (python: none) + 2 (HTML) = 4 total
- CONFLICT hunks: 1 (HTML: font/CSS color system — resolved in favour of canonical)

---

## claude_usage.py — Diff Classification

| Region (standalone lines) | Class | Description | Action |
|---|---|---|---|
| L36: comment suffix | MISSING-FEATURE | Canonical adds "— Opus 4.5+ era" to price table comment | PORT |
| L38: opus pricing | MISSING-FEATURE | Opus repriced $15/75/1.50/18.75 → $5/25/0.50/6.25 | PORT |
| L38-39: fable entry | MISSING-FEATURE | New "fable" row at $10/50/1.00/12.50 | PORT |
| L55-60: short_model() | MISSING-FEATURE | Add fable/mythos detection before opus check | PORT |
| L123: Turn.model comment | MISSING-FEATURE | Add "fable" to model docstring | PORT |
| L221: stype detection | MISSING-FEATURE | Expand headless: `== "sdk-cli"` → `in ("sdk-cli", "sdk-ts")` | PORT |
| L261: file_entrypoint init | MISSING-FEATURE | Add `file_entrypoint: str = ""` initializer | PORT |
| L280-286: entrypoint capture | MISSING-FEATURE | Capture file_entrypoint from first event in Pass 1 | PORT |
| L319: fable daily counter | MISSING-FEATURE | Add "fable": 0 to daily dict model counts | PORT |
| L332-344: cost/token fable | MISSING-FEATURE | Add cost_fable, tokens_fable fields to daily dict | PORT |
| L342-355: comment update | MISSING-FEATURE | Update cross-tab comment: Opus → big-context models (Opus+Fable) | PORT |
| L345-346: band comment | MISSING-FEATURE | Update "Opus-only" → "big-context models (Opus+Fable)" | PORT |
| L362-373: headless fields | MISSING-FEATURE | Add tokens_headless, turns_headless to daily dict | PORT |
| L365-379: headless increment | MISSING-FEATURE | Increment tokens_headless/turns_headless when entrypoint in sdk-cli/sdk-ts | PORT |
| L385-386: model detection | MISSING-FEATURE | Use short_model() helper instead of inline `m.lower()` + "opus" in m | PORT |
| L390-421: fable branch + refactor | MISSING-FEATURE | Add fable elif branch; fix model dispatch ordering; update bands to cover fable | PORT |
| L405-412: old model elif block | MISSING-FEATURE | Old sonnet/haiku elif block removed (replaced by proper if/elif chain) | PORT |
| L483: fable daily export | MISSING-FEATURE | Add "fable": d["fable"] to daily_list | PORT |
| L505: cost_fable export | MISSING-FEATURE | Add cost_fable to daily_list | PORT |
| L508: tokens_fable export | MISSING-FEATURE | Add tokens_fable to daily_list | PORT |
| L522: turns_fable export | MISSING-FEATURE | Add turns_fable to daily_list | PORT |
| L524-551: headless exports | MISSING-FEATURE | Add tokens_headless, turns_headless to daily_list | PORT |
| L530-619: backfill_daily_from_csv() | MISSING-FEATURE | New function: CSV backfill for pruned transcript history | PORT |
| L846: model_counts fable | MISSING-FEATURE | Add "fable": 0 to aggregate_sessions model_counts dict | PORT |
| L940: mix docstring | MISSING-FEATURE | Update docstring: opus/sonnet/haiku → opus/fable/sonnet/haiku | PORT |
| L995: tokens_fable sum | MISSING-FEATURE | Add `tokens_fable = sum_("tokens_fable")` | PORT |
| L1020: fable_share in mix | MISSING-FEATURE | Add fable_share to efficiency model mix dict | PORT |
| L1113: ribbon comment | MISSING-FEATURE | Update "Opus-only" → "Big-context by-band" | PORT |
| L1116-1118: ribbon band comment | MISSING-FEATURE | Update comment: Opus-only → Opus+Fable | PORT |
| L1137: activity comment | MISSING-FEATURE | Update "Opus+Sonnet+Haiku" → "Opus+Fable+Sonnet+Haiku" | PORT |
| L1142: fable turn/token shares | MISSING-FEATURE | Add fable_turn_share, fable_token_share calculations | PORT |
| L1156-1157: ribbon comment | MISSING-FEATURE | Update 4 Opus bands → 4 Opus+Fable context bands | PORT |
| L1178: fable in by_model | MISSING-FEATURE | Add fable entry to by_model dict | PORT |
| L1732: is_sample in payload | MISSING-FEATURE | Add "is_sample": getattr(ds, "is_sample", False) to payload | PORT |
| L1852-1959+: _build_daily_stats fable | MISSING-FEATURE | Add fable support throughout _build_daily_stats_from_turns | PORT |
| L1862+: headless in _build_daily | MISSING-FEATURE | Add tokens_headless, turns_headless fields to _build_daily_stats | PORT |
| L1869+: headless increment in _build | MISSING-FEATURE | Increment headless fields in _build_daily_stats | PORT |
| L1883-1885→1991-2002: band fix | MISSING-FEATURE | Fix band cross-tab to cover fable (was opus-only); add separate fable elif | PORT |
| L2028-2031→2145-2167: sample gen | MISSING-FEATURE | Refactor generate_sample_dataset: decouple heavy flag from model pick; fix Sonnet ctx cap; add Opus heavy bias | PORT |
| L2104+: --backfill-csv arg | MISSING-FEATURE | Add --backfill-csv argparse argument | PORT |
| L2119+: backfill_csv call | MISSING-FEATURE | Add backfill_daily_from_csv() call in main() | PORT |

**PRESERVE (python):** None. All standalone-unique code examined:
- `args.out = Path("claude-usage-sample.html")` — identical in canonical
- `--sample` handling — identical in canonical
- version string — not present in either (no standalone-specific)

---

## dashboard-wide-template.html — Diff Classification

### Header / Meta (lines 1–130 canonical)

| Region | Class | Description | Action |
|---|---|---|---|
| L10: font CDN | CONFLICT | Standalone: Fontshare Cabinet Grotesk + Satoshi. Canonical: Google Inter Tight. **Resolution: use canonical (Inter Tight).** Cabinet Grotesk is a paid Fontshare CDN used for data-centered.com's site integration. The standalone is the public-ship repo; Inter Tight is the correct public font. | USE CANONICAL |
| L56: --body-texture | CONFLICT | Standalone: `url('/showcase/data-stories/textures/dark-leather.png')`. Canonical: `none`. **Resolution: use canonical (`none`).** The texture path is an absolute URL to data-centered.com's server root and would be a broken reference in the standalone public repo. | USE CANONICAL |
| L58-60: --display/--sans/--body CSS vars | CONFLICT | Standalone: Satoshi/Source Serif 4. Canonical: Inter Tight/Avenir Next. **Resolution: use canonical.** | USE CANONICAL |
| L63-77: --model-fable + band palette | MISSING-FEATURE | Canonical adds --model-fable (#e26a3f), --band-ctx-1 through --band-ctx-4, --band-sonnet, --band-haiku, --dir-good, --dir-bad CSS vars (designer warm ramp) | PORT |
| L115-138: canonical light-mode additions | MISSING-FEATURE | Canonical adds --model-fable light color (#b93f64) and band palette light-mode variants | PORT |
| L328, L352: body font references | MISSING-FEATURE | Update body font-family CSS | PORT |
| L422, L818-872: layout/grid changes | MISSING-FEATURE | Pane chrome, layout, grid structure changes (heavy-pane-toolbar, data-trend-group wiring) | PORT |
| L1007-1092: CSS additions | MISSING-FEATURE | New CSS for headless KPI tile, pane toolbar, group toggle, headless band colors | PORT |
| L1617-1701: trend section chrome | MISSING-FEATURE | Range/Group promoted to pane chrome; data-trend-group toggle; heavy-pane-toolbar HTML | PORT |
| L1800-1859: KPI strip additions | MISSING-FEATURE | 7-up KPI strip with headless tile; locked ribbon/stack widths | PORT |
| L1959+: sample demo mode shim | MISSING-FEATURE | New block: `if (DATA.is_sample)` — synthesizes active 5h window for public demo | PORT |
| L2072-2109: headless group data + CSS | MISSING-FEATURE | Bands|Headless group toggle logic; headless series data | PORT |
| L2280-2338: headless chart section | MISSING-FEATURE | Headless ribbon/stacked chart rendering | PORT |
| L2532-2631: canonical legend section | MISSING-FEATURE | Unified pane chrome + canonical legend | PORT |
| L2687-2794: Fable 5 capability marker | MISSING-FEATURE | Fable 5 event marker on trends chart | PORT |
| L3167-3294: 7-up KPI strip JS | MISSING-FEATURE | 7-up KPI strip JS rendering (headless tile, fable tile, etc.) | PORT |
| L3290-3414: tour step updates | MISSING-FEATURE | Tour step 5 updated to mention headless tile; step 7 adds granWeekly demo; step 8 gets targets array | PORT |
| L3706-3830: pane toolbar + group toggle | MISSING-FEATURE | heavy-pane-toolbar JS; Bands|Headless group toggle handler | PORT |
| L3847-3979: headless group rendering | MISSING-FEATURE | Headless group chart build path | PORT |
| L3950-4088: sample mode + RL_LIVE shim | MISSING-FEATURE | Sample demo mode: DATA.is_sample guard around live fetch | PORT |
| L4757-4986: tour 12-stop expansion | MISSING-FEATURE | Tour expanded from 10 to 12 steps (added "Light & dark mode" step with flipTheme demo, "Pane controls" step with heavy-pane-toolbar target, "Chart toggles" step with cycleTrendToggles demo; CSV preview step gets openCsvPreview demo) | PORT |
| L5038-5218: trend group data paths | MISSING-FEATURE | data-trend-group JS paths for headless vs bands | PORT |
| L5267-5402: ribbon chart updates | MISSING-FEATURE | Ribbon chart updated for fable model, headless group mode | PORT |
| L5381-5543: stacked chart updates | MISSING-FEATURE | Stacked evolution chart updated for Fable + headless group | PORT |
| L5719-5870: efficiency section | MISSING-FEATURE | Efficiency section JS refactor: removed old segs/mixD/barSegs block; cleaned up | PORT |
| L5802-5845: standalone composition bar | MISSING-FEATURE (old code removed) | Standalone has old segs/barSegs/legendSegs block that canonical removed as part of composition-bar deprecation. Use canonical version. | USE CANONICAL |
| L5930-6039: counterfactual JS | MISSING-FEATURE | Counterfactual rendering updated | PORT |
| L6150-6308: tour demos | MISSING-FEATURE | flipTheme, granWeekly, cycleTrendToggles, openCsvPreview demo handlers | PORT |
| L6344-6652: tour JS expansion | MISSING-FEATURE | Tour JS updated for 12-step navigation with spotlights + zero-rect guard | PORT |
| L6659-6975: tour spotlights | MISSING-FEATURE | Union-rect spotlight logic with zero-rect guard for multi-target steps | PORT |
| L6994-7238: tour launch + demos | MISSING-FEATURE | Tour launch button, demo handlers (flipTheme, granWeekly, cycleTrendToggles, openCsvPreview) | PORT |
| L7262-7363: CSV preview demo | MISSING-FEATURE | openCsvPreview handler implementation | PORT |
| L7537-7590: STEPS array (standalone) vs 7810-7900 (canonical) | MISSING-FEATURE | STEPS grows from 10 to 12 entries (adds "Light & dark mode" + "Pane controls"); existing steps updated with Fable/headless references, multi-target arrays, and new demo names | PORT |
| L7749-8211: remaining canonical additions | MISSING-FEATURE | Footer, version meta updates | PORT |

**PRESERVE (HTML):**

| Item | Why PRESERVE | Action |
|---|---|---|
| GitHub/issue links in tour final step | Present in both; standalone version has "The button on the right flips light ↔ dark; preference persists across refreshes." as opening sentence. Canonical's final step lacks this opener (slightly different lead sentence). | Canonical version accepted — both have GH/LinkedIn links; slight text variation acceptable |
| `href="https://github.com/keithbinkly/claude-usage-dashboard"` header link | Present in both (standalone L3025, canonical L3108) | Already in canonical — no action |

---

## Judgment Calls

1. **Font choice (CONFLICT → use canonical Inter Tight):** The brief lists font as a potential "PRESERVE" area. However, Cabinet Grotesk is served from Fontshare CDN (`api.fontshare.com`) — a paid/rate-limited CDN not appropriate for a public GitHub repo distributed to arbitrary users. The canonical uses Google Fonts (Inter Tight), which is free and always available. Decision: use canonical font. This also removes the Satoshi dependency.

2. **Body texture (CONFLICT → use canonical `none`):** The `url('/showcase/data-stories/textures/dark-leather.png')` path is an absolute URL that works only on data-centered.com's server. In the standalone public repo it would be a 404. Canonical already sets it to `none`. Decision: use canonical.

3. **Composition bar / segs block (old code → use canonical):** Standalone has ~35 lines of segs/barSegs/legendSegs JS that canonical removed as part of deprecating the composition bar. This is not a PRESERVE item — it's dead code. Use canonical.

4. **Sample dataset narrative:** The `generate_sample_dataset()` function in canonical has a significantly improved model-mixing algorithm (heavy turns are Opus-biased, Sonnet gets a 50K ctx cap). This produces more realistic sample data for the public demo. Port the canonical version entirely.

5. **Tour final step text:** Standalone's step 10 says "The button on the right flips light ↔ dark; preference persists across refreshes. The dashboard runs fully offline…". Canonical's step 12 says just "The dashboard runs fully offline…". Since canonical now has a dedicated "Light & dark mode" step 2, the opener was removed from the final step. Use canonical text (no information loss).

6. **`--layout wide` sample output path:** Canonical generates `claude-usage-sample.html` for `--sample` mode. The brief asks us to regenerate `claude-usage-sample-wide.html`. This requires invoking with `--layout wide --out claude-usage-sample-wide.html`. Both files' argparse is identical, so the invocation `python3 claude_usage.py --sample --layout wide --out claude-usage-sample-wide.html` is the correct form.

---

## Port Strategy
Start from canonical (copy both files), verify PRESERVE items. The missing-feature set is overwhelmingly larger than the preserve set (284 vs ~0 effective). No PRESERVE items require re-applying standalone content back onto canonical.
