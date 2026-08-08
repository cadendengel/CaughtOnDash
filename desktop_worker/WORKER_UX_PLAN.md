# Worker UX and Live Status Plan

Three related pieces of work, in dependency order:

1. **Worker UI rework** — layout, and two display bugs
2. **Approval queue** — approve, select and reorder what gets analyzed
3. **Live status** — WebSockets, replacing both polling and the HTTP heartbeat

Phase 1 is independent. Phase 2 needs backend state. Phase 3 changes how the
backend is *served*, so it lands last.

---

## Phase 1 — Worker UI rework

The right column holds the short "Processing" card and the left holds the tall
activity log, so the window has dead space on the right and a cramped log on the
left. Swap them.

```
┌─────────────────────────────────────────────────┐
│ CaughtOnDash Desktop Worker                     │
├──────────────────────┬──────────────────────────┤
│ Status               │  Activity Log            │
│  Status / Heartbeat  │   full height,           │
│  Current job         │   auto-scrolled          │
│ ── Processing ────   │                          │
│  [progress bar]      │                          │
│  Stage / details     │                          │
│  [Start][Stop][Cancel]                          │
├──────────────────────┴──────────────────────────┤
│ v0.1.0   Backend: …                             │
└─────────────────────────────────────────────────┘
```

- [x] Activity log to the right, full height
- [x] Status and progress stacked on the left
- [x] **Auto-scroll the log to the newest entry.** The WPF host calls
      `ScrollIntoView`; the Avalonia port never did, which is why the log pins
      to the top on macOS and behaves on Windows.
- [x] **Hide the progress section when idle** rather than showing a 0% bar. A
      zeroed bar reads as "stuck at zero"; absence reads as "nothing running".
      An idle card takes its place saying whether the worker is stopped,
      waiting for a job, or unconfigured.
- [x] Same treatment for both hosts, so they do not drift

The left column is a fixed 380px rather than half the window, so the log gets
the remaining width instead of an even split.

---

## Phase 2 — Approval queue

Two features that were worth separating, both wanted:

**Approval** — uploads are not analyzed until approved. Needs a state between
"uploaded" and "queued", and a decision about what the uploader sees meanwhile.

**Selection and reorder** — the worker shows the approved queue and takes jobs
in an order you control.

### 2a — Approval and history (backend) — DONE

- [x] `approval_status` on Video so a video can be `ready` without being
      claimable. `get_next_pending_job` requires `approved`
- [x] `AnalysisRun` model recording every attempt
- [x] Approve / reject endpoint, owner-or-admin
- [x] Re-analysis returns the video to `pending_review` rather than queueing it
- [x] Migration backfilling existing videos as approved, with a synthetic run
      for anything already analyzed

A run is opened when analysis is *requested*, not when it finishes, so
attempts that were never approved or that failed are still history. Each run
keeps its own summary, tags and metadata, which is what makes "back for the
3rd review, here is what run 2 concluded" possible.

Existing videos are backfilled as approved deliberately: they predate the gate,
and retroactively gating them would strand already-analyzed work.

Cancelled jobs also return to `pending_review` — a job was approved once, but
re-running it is a fresh decision.

### 2b — Queue listing and priority — DONE

- [x] `analysis_priority` field. Ordering is priority, then
      `analysis_requested_at`, then `created_at`
- [x] `GET /api/videos/worker/jobs/` — the approved queue in run order
- [x] `GET /api/videos/worker/jobs/review/` — videos awaiting a decision
- [x] `POST /api/videos/worker/jobs/reorder/` — takes the whole ordered list
- [x] `POST /api/videos/worker/jobs/<id>/approval/` — worker-token approval

Rows carry what a person needs to decide: duration, playback URL for preview,
attempt number, how many previous attempts there were, and what the last
finished run concluded. Listing stays at two queries regardless of queue
length, which a test pins.

Reorder takes the entire ordered list rather than move-up/move-down, because
the caller already knows the order it wants and one write avoids two clients
fighting over adjacent swaps. Priorities descend from the list length, so a
video arriving mid-review lands behind anything explicitly ordered.

**Worth knowing:** approval is reachable with the worker token, because the
desktop app holds that token rather than Clerk credentials. It is not a wider
grant -- that token can already claim jobs and write results -- but it does
mean the token approves as well as processes. The Clerk endpoint for owners
still exists alongside it.

Ordering lives server-side deliberately: with three hosts, "the queue" should
mean one thing rather than three, and a local-only order dies on restart.

### 2c — the queue table

Three columns, window grown to 1320x800 from 900x650:

```
┌──────────────┬───────────────────┬──────────────┐
│ Status       │ Queue             │ Activity Log │
│ Progress     │  1. clip A  ▲ ▼   │              │
│ Controls     │  2. clip B  ▲ ▼   │              │
└──────────────┴───────────────────┴──────────────┘
```

The app's whole job is watching a queue drain, so the queue should be visible
at the same time as the log rather than behind a tab.

**Avalonia host: done.** Checkbox rows, select all/clear, Preview (opens the
video in a browser), Up/Down reordering that writes server-side priority,
Reject, and Start Batch. Ticks survive the ten-second refresh, because a poll
that silently cleared your selection would make choosing a large batch
impossible. The grid's highlighted row and the ticked rows are deliberately
different things: Preview acts on what you are looking at, batches act on what
you have ticked.

The worker no longer auto-starts. With an approval gate, starting it before
anything is approved just polls an empty queue; Start Batch approves and starts
in one action.

**WPF host: done.** MainViewModel now drives the shared WorkerSession instead
of owning a WorkerLoopService and duplicating its state handling, so the two
hosts render the same logic and cannot drift.

Verified on the Windows machine over SSH: builds clean, launches, and logs
"Queue: 3 awaiting review, 0 approved" against a real backend. What is *not*
verified is how it looks -- the window cannot be seen from here, and WPF's
DataGrid differs from Avalonia's in column sizing and checkbox commit
behaviour. Worth one look before trusting it.

Two environment notes found while testing there: the Windows backend had never
had `pip install -r requirements.txt` re-run after PyJWT was added, so it 500d
on every request; and `localhost` resolves to IPv6 first on Windows, which
fails against a Django server bound to 127.0.0.1. The worker config now uses
the explicit address.

**Still to do in 2c:** poster-frame capture at upload, so the table shows a
thumbnail rather than only a title. Until then Preview is how you see a video
before approving it.

### Frontend

- [ ] Approve / reject controls for owners and admins
- [ ] Show "awaiting approval" distinctly from "queued"

---

## Phase 3 — Live status over WebSockets

Today the site never refreshes itself: `loadFeed()` runs on mount and after your
own actions, and nothing stale-checks. That is why a status change needs a
manual reload.

### Replacing the heartbeat

The connection itself is the liveness signal, and a disconnect is known
immediately rather than after the five-minute stale window. Two caveats keep the
old mechanism partly alive:

- A socket can be open while the process is wedged, and Cloudflare idles
  connections at ~100s, so periodic pings are still required — keepalive moves
  into the transport rather than disappearing.
- A dropped socket must not instantly free a job that is still being analyzed,
  or two workers do the same work. Keep a stale grace period, just a shorter one.
- Keep the HTTP heartbeat as a fallback, so a worker behind a proxy that blocks
  WebSockets degrades to slower liveness instead of appearing dead.

### Deployment changes this forces

Worth stating plainly, because it converts deployment from "git push" into
"git push plus remembering something":

| | Today | After |
|---|---|---|
| Server | `gunicorn` (WSGI) | `uvicorn` or `daphne` (ASGI) |
| Start command | set in the Render dashboard, not in the repo | must be changed by hand |
| Channel layer | n/a | `InMemoryChannelLayer`, **single process only** |

`InMemoryChannelLayer` works because Render free is one instance and uvicorn
defaults to one worker. It breaks silently at two workers, so if this ever
scales, Redis is required. Cloudflare supports WebSockets on the free plan, so
the proxy in front is not a problem.

- [ ] Add `channels` and an ASGI server; move `asgi.py` to Channels routing
- [ ] Consumer publishing analysis state per video
- [ ] Worker connects, sends progress and stage over the socket
- [ ] Frontend subscribes and updates status without a reload
- [ ] Keep HTTP heartbeat and progress endpoints as the fallback path
- [ ] Commit the start command to the repo so it stops being tribal knowledge

---

## Risks

| Risk | Mitigation |
|---|---|
| Channel layer breaks silently if the process count changes | Document it; assert a single worker at startup |
| WebSocket blocked by a network in the middle | HTTP heartbeat fallback retained |
| Dropped socket frees a job still being worked | Keep a stale grace period |
| Start command drifts from the repo | Add a Procfile or render.yaml |
| Three-column layout on a small laptop screen | Minimum window size; verify at 1280x800 |
