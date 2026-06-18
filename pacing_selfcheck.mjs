// Browser-free self-check for buildWeeklySeasonality (weekly pacing cyclicality).
// Mirrors the function inlined in dashboard-wide-template.html verbatim, then
// exercises it with 0 / 1 / 3 synthetic prior weeks. Run: node pacing_selfcheck.mjs
//
// Asserts, across tiers: no NaN/Infinity; C monotonic non-decreasing with
// C[0]=0,C[GRID]=1; expectedAt monotonic; projection finite and bounded;
// cold-start (n<3) baseline byte-identical to the prior linear formula.

// ─── VERBATIM COPY of buildWeeklySeasonality from dashboard-wide-template.html ───
function buildWeeklySeasonality(wins, win, cap, isUsd, windowMs, nowMs) {
  const GRID = 168;
  const turnVal = t => isUsd ? (t.c || 0)
                             : ((t.in || 0) + (t.out || 0) + (t.cr || 0) + (t.cw || 0));
  const minFinal = Math.max(cap * 0.0025, isUsd ? 0.01 : 1);
  const med = arr => {
    const a = arr.filter(v => v != null && isFinite(v)).slice().sort((x, y) => x - y);
    if (!a.length) return null;
    const m = a.length >> 1;
    return (a.length & 1) ? a[m] : (a[m - 1] + a[m]) / 2;
  };
  const interp = (grid, x) => {
    const xc = x <= 0 ? 0 : x >= 1 ? 1 : x;
    const gf = xc * GRID, lo = Math.floor(gf), hi = Math.min(GRID, lo + 1);
    return grid[lo] + (grid[hi] - grid[lo]) * (gf - lo);
  };

  const priors = [];
  for (const w of wins) {
    if (w === win) continue;
    if (!(w.end <= win.anchor)) continue;   // complete AND non-overlapping with active window
    const wm = (w.end - w.anchor) || windowMs;
    const ts = (w.turns || []).slice().sort((a, b) => a.ts - b.ts);
    let final = 0;
    for (const t of ts) final += turnVal(t);
    if (!(final >= minFinal)) continue;
    const cf = new Array(GRID + 1).fill(0);
    let run = 0, ti = 0;
    for (let g = 0; g <= GRID; g++) {
      const edge = w.anchor + (g / GRID) * wm;
      while (ti < ts.length && ts[ti].ts <= edge) { run += turnVal(ts[ti]); ti++; }
      cf[g] = final > 0 ? run / final : 0;
    }
    cf[0] = 0; cf[GRID] = 1;
    priors.push({ cf, final });
  }
  const used = priors.slice(-6);
  const n = used.length;
  const seasonalActive = n >= 3;

  let C = null;
  if (n > 0) {
    C = new Array(GRID + 1).fill(0);
    for (let g = 0; g <= GRID; g++) C[g] = med(used.map(p => p.cf[g])) || 0;
    C[0] = 0; C[GRID] = 1;
    for (let g = 1; g <= GRID; g++) if (C[g] < C[g - 1]) C[g] = C[g - 1];
    const sm = C.slice();
    for (let g = 1; g < GRID; g++) sm[g] = (C[g - 1] + C[g] + C[g + 1]) / 3;
    sm[0] = 0; sm[GRID] = 1;
    for (let g = 1; g <= GRID; g++) if (sm[g] < sm[g - 1]) sm[g] = sm[g - 1];
    if (sm[GRID] > 0 && sm[GRID] !== 1) for (let g = 0; g <= GRID; g++) sm[g] /= sm[GRID];
    C = sm;
  }

  const xOf = ts => (ts - win.anchor) / windowMs;

  function expectedAt(ts) {
    if (cap <= 0) return 0;
    const x = xOf(ts);
    return seasonalActive ? cap * interp(C, x) : cap * x;
  }

  function project() {
    const xc = Math.max(0, Math.min(1, xOf(nowMs)));
    const cNow = (n > 0) ? interp(C, xc) : xc;
    let current = 0;
    for (const t of (win.turns || [])) current += turnVal(t);

    let projectedFinal;
    if (n === 0) {
      projectedFinal = xc > 0 ? current / xc : current;
    } else {
      const rem = used.map(p => p.final - interp(p.cf, xc) * p.final);
      const pAdd = current + (med(rem) || 0);
      if (n === 1) {
        projectedFinal = pAdd;
      } else {
        let pMult = pAdd, wMult = 0;
        if (cNow >= 0.15) {
          pMult = current / cNow;
          wMult = Math.max(0, Math.min(1, (cNow - 0.15) / 0.15));
        }
        projectedFinal = (1 - wMult) * pAdd + wMult * pMult;
      }
    }
    if (!isFinite(projectedFinal) || projectedFinal < current) projectedFinal = current;

    let projCapMs = null;
    const overAlready = cap > 0 && current >= cap;
    if (overAlready) {
      projCapMs = nowMs;
    } else if (n >= 1 && cap > 0 && projectedFinal > cap && (1 - cNow) >= 0.02) {
      const denom = 1 - cNow;
      const Sx = x => current + (projectedFinal - current) * (interp(C, x) - cNow) / denom;
      let hitX = null;
      for (let g = 0; g <= GRID; g++) {
        const x = g / GRID;
        if (x < xc) continue;
        if (Sx(x) >= cap) {
          if (g === 0) { hitX = 0; }
          else {
            const x0 = (g - 1) / GRID, x1 = g / GRID, s0 = Sx(x0), s1 = Sx(x1);
            hitX = s1 > s0 ? x0 + (cap - s0) / (s1 - s0) * (x1 - x0) : x1;
          }
          break;
        }
      }
      if (hitX != null) {
        const tHit = win.anchor + hitX * windowMs;
        if (tHit > nowMs && tHit < win.end) projCapMs = tHit;
      }
    }
    return { projectedFinal, projCapMs, cNow, xNow: xc, current, n };
  }

  return { expectedAt, project, n, seasonalActive, _C: () => C, _GRID: 168 };
}

// ─── test harness ───
const DAY = 86400 * 1000;
const WK = 7 * DAY;
let failures = 0;
const ok = (cond, msg) => { if (!cond) { failures++; console.log('  ✗ FAIL: ' + msg); } else { console.log('  ✓ ' + msg); } };
const finite = v => typeof v === 'number' && isFinite(v);

// Build a week's turns with a weekday-heavy shape (workday spend Mon–Fri 9–5,
// light weekend). costPerTurn scaled so the week totals ~`weekTotal`.
function makeWeek(anchor, weekTotal) {
  const turns = [];
  // 5 weekdays, ~10 turns each during the workday; 2 weekend days, ~2 turns.
  const perWeekdayTurns = 10, perWeekendTurns = 2;
  const totalTurns = 5 * perWeekdayTurns + 2 * perWeekendTurns;
  const c = weekTotal / totalTurns;
  for (let d = 0; d < 7; d++) {
    const isWeekend = d >= 5;
    const k = isWeekend ? perWeekendTurns : perWeekdayTurns;
    for (let j = 0; j < k; j++) {
      // workday turns between 9:00 and 17:00
      const hourFrac = isWeekend ? (10 + j) / 24 : (9 + (j / k) * 8) / 24;
      turns.push({ ts: anchor + d * DAY + hourFrac * DAY, c });
    }
  }
  return turns;
}

const NOW = 1_700_000_000_000;            // fixed reference "now"
const cap = 100;

function priorWeek(weeksAgo, weekTotal, refAnchor) {
  const end = refAnchor - (weeksAgo - 1) * WK;  // weeksAgo=1 → ends exactly at refAnchor (no overlap)
  const anchor = end - WK;
  const turns = makeWeek(anchor, weekTotal);
  let cost = 0; for (const t of turns) cost += t.c;
  return { anchor, end, cost, tokens: 0, n: turns.length, turns };
}

// current (active) window: started ~14h ago (Monday morning), small spend so far
function currentWindow(spentSoFar, elapsedFrac) {
  const anchor = NOW - elapsedFrac * WK;
  const end = anchor + WK;
  // put a few turns in the elapsed portion totaling spentSoFar
  const turns = [];
  const k = 6;
  for (let j = 0; j < k; j++) turns.push({ ts: anchor + (j / k) * elapsedFrac * WK, c: spentSoFar / k });
  return { anchor, end, cost: spentSoFar, tokens: 0, n: k, turns };
}

function checkCommon(label, s, win, windowMs) {
  console.log('\n[' + label + '] n=' + s.n + ' seasonalActive=' + s.seasonalActive);
  // expectedAt finite + monotonic non-decreasing in ts across the window
  let prev = -Infinity, mono = true, allFinite = true;
  for (let g = 0; g <= 168; g++) {
    const ts = win.anchor + (g / 168) * windowMs;
    const e = s.expectedAt(ts);
    if (!finite(e)) allFinite = false;
    if (e < prev - 1e-9) mono = false;
    prev = e;
  }
  ok(allFinite, 'expectedAt finite across window');
  ok(mono, 'expectedAt monotonic non-decreasing');
  const pr = s.project();
  ok(finite(pr.projectedFinal), 'projectedFinal finite (' + pr.projectedFinal.toFixed(2) + ')');
  ok(pr.projectedFinal <= 5 * cap, 'projectedFinal <= 5*cap');
  ok(pr.projectedFinal >= pr.current - 1e-9, 'projectedFinal >= current');
  ok(pr.projCapMs == null || (pr.projCapMs > NOW - 1 && pr.projCapMs <= win.end),
     'projCapMs null or within (now, win.end]');
  ok(finite(pr.cNow), 'cNow finite (' + pr.cNow.toFixed(4) + ')');
  return pr;
}

// ── n=0 ──
{
  const win = currentWindow(8, 14 / (24 * 7)); // ~14h in, $8 spent
  const wins = [win];
  const s = buildWeeklySeasonality(wins, win, cap, true, WK, NOW);
  checkCommon('n=0 cold start', s, win, WK);
  ok(s.n === 0 && s.seasonalActive === false, 'n=0, baseline NOT reshaped');
  // linear parity: expectedAt == cap*(ts-anchor)/WK
  const ts = win.anchor + 0.37 * WK;
  ok(Math.abs(s.expectedAt(ts) - cap * 0.37) < 1e-9, 'n=0 baseline linear-identical');
  const pr = s.project();
  ok(pr.projCapMs == null, 'n=0 cap-hit marker suppressed (not already over)');
}

// ── n=1 ──
{
  const win = currentWindow(8, 14 / (24 * 7));
  const wins = [priorWeek(1, 90, win.anchor), win];
  const s = buildWeeklySeasonality(wins, win, cap, true, WK, NOW);
  checkCommon('n=1', s, win, WK);
  ok(s.n === 1 && s.seasonalActive === false, 'n=1, baseline still linear');
  const ts = win.anchor + 0.5 * WK;
  ok(Math.abs(s.expectedAt(ts) - cap * 0.5) < 1e-9, 'n=1 baseline linear-identical');
}

// ── n=3 (full seasonal) ──
{
  const win = currentWindow(8, 14 / (24 * 7));   // Monday morning, $8 in
  const wins = [priorWeek(3, 85, win.anchor), priorWeek(2, 95, win.anchor), priorWeek(1, 100, win.anchor), win];
  const s = buildWeeklySeasonality(wins, win, cap, true, WK, NOW);
  const pr = checkCommon('n=3 full seasonal', s, win, WK);
  ok(s.n === 3 && s.seasonalActive === true, 'n>=3, seasonal baseline active');
  // C curve checks
  const C = s._C();
  ok(C && C.length === 169, 'C grid has 169 pts');
  ok(Math.abs(C[0]) < 1e-9 && Math.abs(C[168] - 1) < 1e-9, 'C[0]=0, C[168]=1');
  let cmono = true, cfinite = true;
  for (let g = 1; g <= 168; g++) { if (C[g] < C[g - 1] - 1e-9) cmono = false; if (!finite(C[g])) cfinite = false; }
  ok(cfinite, 'C all finite');
  ok(cmono, 'C monotonic non-decreasing');
  // The whole point: Monday-morning seasonal projection must be MUCH lower
  // than the naive linear extrapolation (current / elapsedFrac).
  const linearProj = pr.current / pr.xNow;
  console.log('  · seasonal projectedFinal=' + pr.projectedFinal.toFixed(2) +
              ' vs naive linear=' + linearProj.toFixed(2));
  ok(pr.projectedFinal < linearProj, 'seasonal projection < naive linear (anti-over-projection)');
}

// ── division-guard / degenerate: zero-cost prior weeks (thin) → treated as n=0 ──
{
  const win = currentWindow(0.001, 0.01);
  const thin = priorWeek(1, 0.0001, win.anchor); // below minFinal → skipped
  const s = buildWeeklySeasonality([thin, win], win, cap, true, WK, NOW);
  checkCommon('thin-history guard', s, win, WK);
  ok(s.n === 0, 'thin prior week skipped (below minFinal)');
}

console.log('\n' + (failures === 0 ? 'ALL PASS' : failures + ' FAILURE(S)'));
process.exit(failures === 0 ? 0 : 1);
