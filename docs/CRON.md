# Cron Schedule — gex_extractor

This document is the **canonical reference** for the host crontab entries that
manage the `gex_extractor` lifecycle. The crontab itself lives on the host
(`crontab -e`) and is **not** checked into this repo. When this doc changes,
update the host crontab to match.

Last updated: 2026-06-29 (TASK-2026-290 — single-instruction design)

---

## Lifecycle pattern (single-instruction)

The `gex_extractor` lifecycle is managed by **one** cron instruction: the
shared `extractor-watchdog.sh` (which also manages `premium_extractor`). It
is responsible for both **cold-start** (extractor down at market open) and
**post-crash recovery** (extractor dies mid-session). No separate START line.

This mirrors `ibkr_trader_engine` exactly after PR #20 (TASK-2026-268
follow-up). `premium_extractor` ships the same refactor in parallel
(TASK-2026-289 / PR #9). The watchdog's `check_one` loop iterates over both
extractors in a single script, so one cron line manages the whole lifecycle.

Helper scripts live in `/Users/ubexbot/.openclaw/scripts/` (host-managed, out
of repo scope):

- `run-extractor.sh` — start an extractor (idempotent via pidfile, loads
  `.env` if present).
- `stop-extractor.sh` — gracefully stop an extractor (SIGTERM → SIGKILL with
  pgrep fallback).
- `extractor-watchdog.sh` — fires every 5 min, restarts any extractor that's
  DOWN during market hours (09:30–16:00 ET).

---

## Current crontab (host)

```cron
# -- gex_extractor ---------------------------------------------------------
# Discord GEX polling — single-instruction lifecycle (watchdog-only).
# The watchdog fires every 5 minutes during the trading day and is responsible
# for BOTH cold-start (extractor down at market open) AND post-crash recovery.
# No separate START line. See docs/CRON.md for the design rationale.
# The same */5 line covers premium_extractor (shared script, see comment in
# extractor-watchdog.sh). STOP at 4:00 PM ET (Mon-Fri, market-aware) -- shared
# with premium_extractor.
0 16 * * 1-5 [ "$(/opt/homebrew/bin/python3 /Users/ubexbot/.openclaw/vault/vault/SharedResources/Scripts/is_market_open.py)" = "OPEN" ] && /Users/ubexbot/.openclaw/scripts/stop-extractor.sh /Users/ubexbot/.openclaw/workspace-venkat/premium_extractor && /Users/ubexbot/.openclaw/scripts/stop-extractor.sh /Users/ubexbot/.openclaw/workspace-venkat/gex_extractor >> /Users/ubexbot/.openclaw/logs/extractor-watchdog.log 2>&1
# WATCHDOG -- every 5 min, restarts crashed extractor during market hours.
# This is the ONLY instruction that starts the extractor. Shared with
# premium_extractor via extractor-watchdog.sh.
*/5 * * * 1-5 /Users/ubexbot/.openclaw/scripts/extractor-watchdog.sh >> /Users/ubexbot/.openclaw/logs/extractor-watchdog.log 2>&1
```

---

## Cold-start timing

The watchdog fires every 5 minutes (`*/5 * * * 1-5`) during the trading day.
Its behaviour at the start of a session:

| Time (ET)         | What happens                                                              |
|-------------------|---------------------------------------------------------------------------|
| 09:30 open        | First watchdog tick at `:30` (or `:35` if `:30` already passed).          |
| ≤ 09:35           | Extractor is DOWN; watchdog sees `MARKET_STATUS=OPEN && !is_alive` →     |
|                   | `run-extractor.sh` is invoked → Discord client connects, first poll at    |
|                   | ~30s. **30+ seconds of warm-up is normal.**                               |
| 09:35+            | Extractor is UP; watchdog sees `is_alive` → exits cleanly without action. |

**Worst-case cold-start latency: 5 minutes** (the gap between two watchdog
ticks). This is acceptable because:

1. The extractor's own warm-up is 30+ seconds (Discord API handshake + first
   poll + SQLite write + optional Supabase dual-write).
2. At 09:30 sharp, the pre-market auction is still settling — the first minute
   of trading is often wide spreads and unreliable prints on related feeds, so
   a 5-min delay to first-tick loses ~3 minutes of low-quality signals.
3. `run-extractor.sh` is idempotent (pidfile check at the top: if a live
   process already owns the pidfile, it exits 0). Two watchdog ticks in the
   same 5-minute window cannot fork duplicate processes — the second tick
   sees the pidfile written by the first and exits cleanly.

If the extractor crashes mid-session (e.g. Discord disconnect that isn't
auto-recovered, or a transient SQLite write failure), the watchdog's next
tick (≤ 5 minutes later) restarts it.

---

## Why single-instruction, not two

On **2026-06-29 at 09:30:00 ET**, two cron jobs fired in the same second for
the `ibkr_trader_engine`:

- `30 9 * * 1-5 ...run-ibkr-engine.sh` (START)
- `*/5 * * * * ...ibkr-engine-watchdog.sh` (WATCHDOG — `:00`, `:05`, ...,
  `:30`, `:35`, ...)

Both saw no pidfile. Both forked `run.py`. Both tried to claim IBKR `clientId
31`. Result: **two engine instances fought for the same clientId for ~3h 40m**
before the duplicate was detected.

`gex_extractor` had the **same dual-instruction pattern** at 09:30:00:

- `30 9 * * 1-5 ...run-extractor.sh /.../gex_extractor ...` (START)
- `*/5 * * * 1-5 ...extractor-watchdog.sh` (WATCHDOG)

In practice the race outcome is milder for the extractor (Discord API is
read-only — two extractors both poll the same snapshots, SQLite `INSERT OR
REPLACE` deduplicates by timestamp, no external state contention). But the
**structural risk** is identical: two instructions both think "service is
down, I'll start it." The single-instruction design eliminates the race by
construction.

The lessons:

1. **One instruction is structurally safer than two.** With one instruction
   there is no possible collision by construction — only one instruction can
   start the extractor. Two instructions require non-trivial coordination
   (mutex, stagger, failfast) to avoid the same bug recurring.
2. **Defence-in-depth still matters.** Even with one instruction, `run-extractor.sh`'s
   pidfile check keeps two simultaneous ticks (e.g. on a host wake-from-sleep
   race) from forking duplicates. The `idempotent` path logs "already running"
   and exits 0.
3. **The watchdog already does cold-start.** `extractor-watchdog.sh`
   branches on `MARKET_STATUS=OPEN && !is_alive` and calls
   `run-extractor.sh`. No separate START line is needed.

PR #20 (TASK-2026-268 follow-up) on `ibkr_trader_engine` shipped this design
first. PR #9 (TASK-2026-289) on `premium_extractor` and **this PR
(TASK-2026-290)** on `gex_extractor` apply the same refactor in parallel,
closing TASK-2026-287 (parent refactor).

---

## Applying the change

The crontab is host-managed. After this PR is merged, run this **one-time**
command to remove the redundant START line from the host crontab:

```bash
# Backup first
crontab -l > /tmp/crontab.backup-$(date +%Y-%m-%d-%H%M)

# Edit and remove the line starting with "30 9 * * 1-5" that references
# run-extractor.sh with the gex_extractor path. Keep the */5 watchdog and
# the 0 16 STOP lines (STOP is shared with premium_extractor).
crontab -e
```

The line to **remove**:

```
30 9 * * 1-5 [ "$(/opt/homebrew/bin/python3 ...is_market_open.py)" = "OPEN" ] && /Users/ubexbot/.openclaw/scripts/run-extractor.sh /Users/ubexbot/.openclaw/workspace-venkat/gex_extractor /Users/ubexbot/.openclaw/workspace-venkat/gex_extractor/.venv/bin/python cron.log >> /Users/ubexbot/.openclaw/workspace-venkat/gex_extractor/logs/cron.log 2>&1
```

The lines to **keep**:

```
0 16 * * 1-5 [ ... ] && /Users/ubexbot/.openclaw/scripts/stop-extractor.sh /Users/ubexbot/.openclaw/workspace-venkat/premium_extractor && /Users/ubexbot/.openclaw/scripts/stop-extractor.sh /Users/ubexbot/.openclaw/workspace-venkat/gex_extractor ...
*/5 * * * 1-5 /Users/ubexbot/.openclaw/scripts/extractor-watchdog.sh ...
```

Verify after the edit:

```bash
crontab -l | grep -E "gex_extractor|extractor-watchdog"
# Expected: 2 lines (STOP + WATCHDOG). The START line is gone.
```

---

## Disabling for a day

To skip the extractor for a single trading day (e.g. a known-bad Discord
data day), there is no `dry_run` flag in this repo — the only lever is the
crontab. Comment out the watchdog line for the day, then restore it before
the next session. The STOP line does not need to change.

To pause for an extended period (vacation, Discord API maintenance window),
comment out both the STOP and the WATCHDOG lines. The extractor will stay
down until you re-enable them.

---

## Inspecting the state

```bash
# Is the extractor running?
pgrep -fl "gex_extractor.*run\.py"

# When was the last poll?
tail -n 5 /Users/ubexbot/.openclaw/workspace-venkat/gex_extractor/logs/gex_extractor.log

# What has the watchdog done today?
tail -n 30 /Users/ubexbot/.openclaw/logs/extractor-watchdog.log

# What did the STOP script do at 16:00?
tail -n 20 /Users/ubexbot/.openclaw/workspace-venkat/gex_extractor/logs/stop.log

# What does the crontab look like right now?
crontab -l | grep -E "gex_extractor|extractor-watchdog"
```

---

## Related

- **TASK-2026-290 (this PR)** — drop the redundant START line; watchdog-only
  design for `gex_extractor`.
- **TASK-2026-289 (PR #9, premium_extractor)** — same refactor, ships in
  parallel. Both repos share `extractor-watchdog.sh`, so this PR is
  **docs-only** — the watchdog already iterates over both extractors.
- **TASK-2026-287** — parent refactor: "we should have a similar process
  for [services], where the start and the watchdog can be clubbed into a
  single cron" (Sarthak, 2026-06-29 22:03 EDT).
- **TASK-2026-288 (PR #8, listener)** — same refactor for the
  TradingView listener (`run-listener.sh` + `listener-watchdog.sh`).
- **TASK-2026-268 follow-up (PR #20, ibkr_trader_engine)** — original
  single-instruction design that this PR mirrors.
- **TASK-2026-278** — parent incident (two IBKR engine instances, ~3h 40m
  downtime on 2026-06-29) that motivated all of the above.