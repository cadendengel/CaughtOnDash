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

**Poster frames: captured and stored.** The browser grabs a frame during the
same decode it already did to read duration, and sends it with the video in one
multipart request. Analysis cannot supply this: approval happens before
analysis, so the thumbnail the decision needs has to exist beforehand.

A frame is taken a second in, or at the midpoint of a very short clip, because
most videos open on black. Capture failures are swallowed -- the video is the
point, the thumbnail is a convenience -- and there is a timeout, because some
containers never fire onseeked and an upload must not hang on a nicety.

This also gives the website thumbnails it never had: `thumbnail_url` was on the
model from the start but nothing ever wrote it, so feed videos showed a black
rectangle until played. They now have a poster.

**Thumbnail column: done.** `ThumbnailCache` in the shared core fetches and
caches bytes; each host decodes them into its own image type. Stopping at bytes
is what keeps a UI dependency out of the core -- `QueueRow.Thumbnail` is typed
`object` for the same reason, and both frameworks' `Image.Source` bindings
accept it.

The caching matters more than the fetching. The queue refreshes every ten
seconds and rebuilds every row, so an uncached column would re-fetch each
thumbnail six times a minute for as long as the window is open. Tasks are
cached rather than results, so the rows that all ask at once share one request.
Failures are cached too -- a 404 thumbnail would otherwise retry forever.

Verified on the running Avalonia app against real images: three fetched exactly
once each across several refresh cycles, a 404 URL fetched once and not
retried, and both the broken URL and a video with no thumbnail falling back to
the grey placeholder.

Adding the column exposed two layout problems it also caused: the Title
truncated to "Thumb …", and Start Batch was clipped off the edge of a 570px
column. The button row is now a WrapPanel so it can never clip regardless of
width, the three panels were rebalanced (the status panel had dead space the
queue did not), and the window's default width went 1320 -> 1440. Squeezing the
fixed columns instead just moved the truncation from the data to the headers.

### 2e — Reordering the review list — DONE

Up/Down now works on both tabs, so a batch can be arranged before it is
approved. Priority is stored per video regardless of approval state and
`decide_approval` does not reset it, so the order set on the review tab
survives the decision.

Enabling the buttons was the small part. The real work was that reordering
either tab alone was already wrong: the backend numbers priorities descending
from the length of the list it is sent, so sending one tab's rows renumbers
them into the *other* tab's band. A batch of 3 approved alongside 5 queued
videos got priorities 3,2,1 and interleaved with work that was already there --
approving quietly meant "jump the queue". Both reorder and Start Batch now send
one order spanning both tabs: queued first, review behind it.

That logic lives in `QueueOrdering` in the shared core rather than in each host,
because getting it wrong is invisible -- the rows look right and the queue runs
in the wrong order minutes later. Seven tests cover it, including a video that
appears in both snapshots because another host approved it mid-refresh; it is
placed in the queued band, since that is where it now is, and never listed
twice (two priorities would mean the later write silently undid the order).

**The WPF host can be compile-verified on macOS after all**, which removes a
standing gap in this plan:

```
dotnet build src/worker/CaughtOnDash.Worker.csproj -p:EnableWindowsTargeting=true -t:Compile
```

XAML markup compilation runs too. A full build stops only at copying
`appsettings.json`, which is gitignored and lives on the Windows machine, so
`-t:Compile` is the useful target. This does not replace looking at the window
-- layout and DataGrid behaviour still need a real Windows run -- but it does
mean host changes no longer ship unbuilt.

### 2d — Frontend — DONE

- [x] Approve / reject controls for owners and admins
- [x] Show "awaiting approval" distinctly from "queued"

Approval is reported before analysis, because it comes first in the pipeline:
a video nobody has looked at reads "Pending review" rather than "Cancelled" or
"Pending 0%", which described the machinery instead of the situation. A
rejected video reads "Not selected for analysis" and stays in the feed.

The controls appear on the feed card and the detail view for the owner and for
admins, matching what the backend will accept, so the buttons never invite a
request that would 403.

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

- [x] Add `channels` and an ASGI server; move `asgi.py` to Channels routing
- [x] Consumer publishing analysis state
- [x] Frontend subscribes and updates status without a reload
- [x] Keep HTTP heartbeat and progress endpoints as the fallback path
- [x] Document the start command
- [x] Worker connects and sends progress over the socket, with HTTP retained
      as the fallback
- [x] Shorten the stale grace period now that death is visible sooner

daphne rather than uvicorn: it is Channels' own server, it provides the ASGI
runserver used locally, and the test utilities import it, so one dependency
covers all three.

The socket is read-only and unauthenticated. Its payload is a subset of what
the public feed already returns, so it grants nothing the feed does not, and
treating it as write-only removes any question of what a client may ask for --
a test pins the payload's field list to keep it that way.

Publishing is best-effort at every call site. A missing channel layer, a
failing one, or a deployment still on WSGI leaves analysis working and the site
behaving as it always has. The frontend reconnects with backoff up to 30s so a
deployment without WebSockets does not retry in a tight loop.

Verified end to end against a real socket: approving over HTTP pushes
immediately, and a full job streams claimed / downloading 40% / analyzing 70% /
uploading 95% / complete 100%.

That live test caught a real bug. The first version published from inside
complete_job *before* the tag merge and the save, so browsers were told a
finished video had no tags. Every other call site already saved first. There is
now a test pinning that what gets pushed is what was saved.

### 3b — the worker's heartbeat — DONE

`/ws/worker/` is a second consumer rather than a mode of the first, because it
inverts both of the browser stream's rules: it is authenticated and it accepts
writes. Separate endpoints mean no misconfiguration can let a browser reach it.

Auth is the same bearer token the HTTP worker API uses, read from the
Authorization header. Browsers cannot set headers on a WebSocket; the desktop
worker is a native client that can, so the token stays out of the URL and
therefore out of access logs and proxy history. A rejected connection closes
with 4401 and the worker latches onto HTTP rather than retrying a socket that
will keep refusing it.

Both transports now go through one `apply_worker_heartbeat`, so they cannot
drift on what a heartbeat means -- including the scoping that stops a late
heartbeat reverting a finished job to "Processing: 100% (complete)". Which
transport delivered it is deliberately not recorded: a worker failing over from
socket to HTTP mid-job should look continuously alive, not briefly dead.

The worker waits for an ack rather than trusting the write. A socket can be
open while nothing reads it, and a heartbeat silently going nowhere is exactly
the failure that once hid the backend rejecting every worker POST.

**The stale window drops from five minutes to two**, and three places that
disagreed -- two minutes in `claim_job` and `reset_stale_jobs`, five in
`_is_stale_processing` -- now share one constant. It does not go to zero. A
dropped connection is not a dead worker: one on a flaky network is still
decoding frames, and freeing its job the moment the socket closed would hand
that video to a second worker. Twelve missed heartbeats is the price of not
doing the work twice. A clean shutdown closes the socket, so an ordinary stop
marks the worker offline immediately rather than waiting the window out.

Verified against a real daphne server twice over: a Python client for the
server side (bad and missing tokens refused, three beats acked, each one
reaching a watching browser, worker marked offline on disconnect with
last_seen_at left alone), and the actual C# `HeartbeatChannel` for the client
side, which connected with its header, delivered, rejected a bad token, and
latched to HTTP.

---

## Risks

| Risk | Mitigation |
|---|---|
| Channel layer breaks silently if the process count changes | Document it; assert a single worker at startup |
| WebSocket blocked by a network in the middle | HTTP heartbeat fallback retained |
| Dropped socket frees a job still being worked | Keep a stale grace period |
| Start command drifts from the repo | Add a Procfile or render.yaml |
| Three-column layout on a small laptop screen | Minimum window size; verify at 1280x800 |
