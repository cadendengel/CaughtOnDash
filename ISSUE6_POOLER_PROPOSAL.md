# ISSUE-6 — Transaction pooler exhaustion: analysis & proposed fix

**Severity: BLOCKER.** Reproduced during end-to-end testing on 8/9 Aug: sustained
load drove the Supabase transaction pooler to its 200-client cap (`EMAXCONN`),
and every DB-backed endpoint returned 500/503 for ~39 minutes.

This is not a code bug with a one-line fix — it is a connection-lifecycle /
capacity issue that needs an operational decision plus load verification. This
document is the proposal; nothing here is applied to production automatically.

## Evidence
- Health poller caught the exact cliff: `200 at 01:45:46 → 503 at 01:46:46`,
  detail `FATAL: (EMAXCONN) max client connections reached, limit: 200` (port 6543).
- Recovery at 02:25:32 — **~39 min total, ~36 of them with all load already stopped.**
- The load that triggered it was largely **sequential** (one request at a time).

## Why sequential load hitting a 200 cap matters
A single sequential client should hold 1–2 pooler connections. Reaching 200 under
sequential load means client connections **accumulate / drain slowly**, not that
concurrency spiked. Leading hypothesis:

> With `conn_max_age=0`, every request opens a brand-new client connection to
> Supavisor and closes it. Sustained traffic churns hundreds of short-lived
> client connections. If Supavisor reaps closed ones slowly (TIME_WAIT / idle
> timeout), they pile toward the 200 cap and drain slowly afterward — matching
> the ~36-minute post-load drain.

`conn_max_age=0` was set deliberately to protect the **session** pooler (15-client
cap, `EMAXCONNSESSION`). On the **transaction** pooler (200 cap) it is a liability:
it maximizes connection churn.

## Proposed fix (in order)

### 1. Raise `DB_CONN_MAX_AGE` on the transaction pooler — NO CODE CHANGE
`settings.py` already reads `conn_max_age=get_int('DB_CONN_MAX_AGE', 0)`. On
Render, set `DB_CONN_MAX_AGE=30` (or 60). Connections are then **reused** across
requests instead of opened fresh each time, collapsing the churn.
- Keep it **0 on the session pooler** (persistent connections there re-create the
  15-cap outage). This is why it's an env var, not hardcoded.
- `ASGI_THREADS=8` already bounds concurrent connections to ≤8 per instance, well
  under 200 — so reuse won't approach the cap.

### 2. Verify under load before trusting it
- Run a sustained sequential + concurrent load (a few hundred requests over
  ~10 min) against a staging deploy with `DB_CONN_MAX_AGE=30` and watch
  `/api/health/`. It should stay 200 with no upward latency drift.
- If Supabase is reachable, confirm the client-connection count on the pooler
  stays flat rather than climbing.

### 3. Confirm the recovery mechanism
We never confirmed whether the 39-min outage self-healed or needed a restart. If
it needed a restart, add a Render **health check** pointed at `/api/health/` so a
503 auto-restarts the instance and drops leaked connections — turning a 39-min
outage into a ~1-min one. (The endpoint already returns 503 correctly.)

### 4. If churn is not the cause
If load testing shows connections genuinely leak (held open, never closed) rather
than churn, the next suspects are: the WebSocket consumers'
`database_sync_to_async` calls, and Django's ASGI connection cleanup under the
asgiref thread pool. Instrument `pg_stat_activity` (state = idle vs
idle-in-transaction) and Supavisor client counts under load to tell which.

## What was changed in code for this issue
Only documentation: a comment in `settings.py` recording the transaction-pooler
caveat so whoever tunes `DB_CONN_MAX_AGE` sees the tradeoff. The behavioral change
(the env var value) is intentionally left to you.
