---
description: Running Ieum — health checks and metrics, backups and restore drills, deploying on Kubernetes (Helm), and running multiple instances.
---

# Operations — is it alive, can it work, can you get it back?

This page answers three questions: **is it OK right now** (health checks), **what
is it doing** (metrics), and **if you lose it, can you get it back** (backups).
Without the first two, you find out about outages when someone calls you. Without
the third, a single outage is the end.

---

## 1. Health checks

There are two. **They ask different questions.**

| Path | Meaning | What it checks | On failure |
|---|---|---|---|
| `GET /healthz` | The process is alive | Nothing | The orchestrator **restarts it** |
| `GET /readyz` | It can do work right now | DB, Redis, S3 | The load balancer **takes it out of rotation** |

**`/healthz` does not check the database.** If it did, a brief database hiccup
would restart the whole app, and the restarted app would find the database just
as shaky and restart again — one fault becomes a restart storm.

**`/readyz` answers with its status code.** 200 when ready, **503** when not. A
load balancer doesn't read `{"status": "degraded"}` in the body — for a while the
endpoint only ever returned 200, and traffic kept flowing to dead instances.

```console
$ curl -s -w " [%{http_code}]" localhost:8000/readyz
{"status":"ready","checks":{"database":"ok","redis":"ok","storage":"ok"}} [200]

$ docker compose stop redis && curl -s -w " [%{http_code}]" localhost:8000/readyz
{"status":"degraded","checks":{"database":"ok","redis":"error: ConnectionError","storage":"ok"}} [503]
```

When one dependency is down, **it still checks the rest.** An operator needs to
know not just "it's broken" but "what is broken."

### Why it checks all three

- **DB** — nothing works without it.
- **Redis** — the worker's queue. When it dies, notifications, email, webhooks,
  and SLAs all stop, **yet the UI looks fine.** Tickets keep coming in and nobody
  gets a reply.
- **S3** — attachments stop working entirely. The upload screen opens; only the
  save fails.

### On Kubernetes

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 10
  failureThreshold: 3
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  periodSeconds: 5
  failureThreshold: 2
```

`/readyz` hits three backends on every call, so don't lower `periodSeconds` all
the way to 1 second.

---

## 2. Metrics

`GET /metrics` — Prometheus format. It has no authentication, so **don't expose
it publicly** (block it at the ingress or serve it on a separate port).

| Metric | Meaning | When to look |
|---|---|---|
| `ieum_http_requests_total{method,route,status}` | Request count | 5xx ratio |
| `ieum_http_request_duration_seconds{method,route}` | Processing time | Routes where p95 spikes |
| `ieum_http_requests_in_flight` | Requests in progress | Traffic surges |
| `ieum_outbox_pending` | Events not yet delivered | Backlog |
| `ieum_outbox_oldest_age_seconds` | Age of the oldest of those | **This is where a backlog shows up** |
| `ieum_worker_last_run_timestamp_seconds{task}` | When the worker last finished | Dead worker |
| `ieum_worker_last_duration_seconds{task}` | How long that run took | Slowdowns |
| `ieum_worker_last_failed{task}` | Whether the last run failed (1/0) | Jobs that keep failing |

### What stays out of labels

**User IDs, issue keys, and raw URLs are never used as labels.** The number of
time series would multiply (a cardinality explosion), and within days Prometheus
would be on its knees. Paths are grouped by the matched **route template**
(`/api/v1/issues/{issue_id}`), and requests that match no route are collapsed
into a single `route="unmatched"` — scanners don't get to create time series.

### The worker is a separate process

Metrics held in the worker's memory can't be served from the API's `/metrics`.
So the worker records "the time it last **finished**" in a single `heartbeat`
row in the database, and the API reads it out when it is scraped. Why finished
rather than started: if only the start were recorded, a worker that stalled
mid-run would keep looking alive.

### Three alerts you must set

```promql
# 1. The worker is dead. The sweep runs every 15 seconds, so 2 minutes is conclusive.
time() - ieum_worker_last_run_timestamp_seconds{task="sweep"} > 120

# 2. The outbox is backing up. Look at age, not count —
#    a hundred events that just arrived is normal; one event stuck for ten minutes is an outage.
ieum_outbox_oldest_age_seconds > 300

# 3. 5xx responses are rising.
sum(rate(ieum_http_requests_total{status=~"5.."}[5m]))
  / sum(rate(ieum_http_requests_total[5m])) > 0.01
```

The first matters most. When the worker dies, **nothing happens, yet the UI
looks fine** — it takes people days to notice.

### One thing you can't get back if the worker doesn't run

Most of what the sweep does **still works if it runs late**: the outbox keeps
its events, emails go out later, and SLA breaches get caught on the next cycle.

Burndown snapshots are different. The whole point is to record each day's value
on that day, so if the worker is down for a day, **that day's point is gone for
good.** If you backfilled it by recalculating, you would get today's value, not
that day's — and then there would be no reason to keep snapshots at all.

A gap in the dates returned by `GET /sprints/{id}/burndown` means the worker
didn't run that day. The chart draws straight lines between points — it doesn't
invent missing values, and it spaces points in proportion to their dates, so a
gap looks like a gap.

---

## 3. What the image must include — typesetting libraries and CJK fonts

Exports (PDF, Word) are typeset inside the app process (ADR-0011). So the API
and worker images need two things.

| What | Without it | How it shows up |
|---|---|---|
| `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz0b`, `libfribidi0`, `libcairo2`, `libgdk-pixbuf-2.0-0` | The WeasyPrint import fails | The first PDF request returns 500. **You'll see it** |
| `fonts-noto-cjk` | Korean, Japanese, and Chinese text renders as boxes (□) | **Nothing fails.** A normal-looking PDF comes out, and only the person who opens it finds out |

The second row is why this section exists. Nothing shows up in the logs and no
metric moves, so no alert can catch it. **If you build your own image or change
the base image**, check that the fonts came along:

```sh
docker compose exec api fc-list :lang=ko | head -1   # must print at least one line
```

`fonts-noto-cjk` is over 100 MB. It is not the place to cut image size — remove
it and exporting Korean documents quietly stops being usable. If you want to
slim it down by narrowing the languages, the furthest you can go is keeping the
base package without `fonts-noto-cjk-extra`.

Typesetting is CPU-bound. The router hands it off to a thread
(`asyncio.to_thread`), but the number of worker processes is the upper limit on
how many typesetting jobs run at once. If your installation exports entire large
spaces, set `--workers` to match, or look at the request timeout as well.

## 4. Backups

There are two things to protect. **Without both, it isn't a restore.**

- **Postgres** — issues, pages, tickets, settings, audit logs. Everything.
- **Object storage (S3/MinIO)** — attachments. The database only holds their keys.

Keep `IEUM_SECRET_KEY` safe as well. If you lose it, **you can't open the
encrypted columns** even with an intact database — IdP client secrets, mailbox
passwords, TOTP secrets, SAML SP keys. That is a secrets-management job rather
than a backup job, but forget it and your restore is only half a restore.

### Taking a backup

```console
# Postgres — custom format (-Fc). Supports parallel and selective restore.
pg_dump -h $HOST -U $USER -d ieum -Fc -f ieum-$(date +%F).dump

# Attachments — the whole bucket.
aws s3 sync s3://ieum-attachments ./attachments-$(date +%F)/
```

`pg_dump` takes a **consistent snapshot** (it reads everything in a single
transaction). You don't need to stop the app.

### Restoring

```console
createdb -h $HOST -U $USER ieum_restored
pg_restore -h $HOST -U $USER -d ieum_restored --no-owner ieum-2026-09-07.dump
aws s3 sync ./attachments-2026-09-07/ s3://ieum-attachments
```

Pass `--no-owner`. The dump contains the original owner's role name, and if the
roles on the target are named differently, that alone makes the restore fail.

### A backup you've never restored is not a backup

**Do a real restore at least once a quarter.** Restore into a scratch database
and check three things.

```console
# 1. Did all the rows come back?
psql -d ieum_restored -tAc "select count(*) from project;"

# 2. Are the extensions there? Without them, search dies entirely.
psql -d ieum_restored -tAc "select extname from pg_extension order by 1;"
# You should see all five: citext / ltree / pg_trgm / pgroonga / plpgsql

# 3. Does Korean search actually work? Sometimes the index exists but doesn't work.
psql -d ieum_restored -tAc "select count(*) from issue where summary &@~ '프린터';"
```

(`프린터` is Korean for "printer". Use any Korean word that appears in your issue
summaries.)

Why you need to go as far as the third check: if the extension is missing, a
PGroonga index makes the restore itself fail, but if the extension version
differs, **the index gets created and only the results come out wrong.**
Counting rows won't reveal that.

Also check that the migration revision matches the code.

```console
alembic current   # must match the code's head
```

### How often, and for how long

Your organization decides, but if nobody decides, the answer becomes "never." A
starting point:

| | Frequency | Retention |
|---|---|---|
| Postgres full dump | Daily | 30 days |
| Postgres WAL archive (PITR) | Continuous | 7 days |
| Attachments bucket | Daily | 30 days + versioning |

With PITR, you can go back to "just before the incident" instead of "last
night's backup." Incidents are usually someone deleting something, and losing
the whole day in between hurts even when you have a backup.

## 5. Deploying on Kubernetes (Helm)

The chart lives in `deploy/helm/ieum`, and its `README.md` covers how to use it
and **why it is built the way it is**. This section only carries over what
operators need to know first.

### This chart doesn't create the services it connects to

Postgres, Redis, S3, and SMTP are given to it as addresses only. That keeps
`helm uninstall` from becoming a command that deletes data, and a production
database needs its own backups and redundancy anyway. Also, Postgres needs the
PGroonga extension (ADR-0005), so a common Postgres chart can't stand in for it
— shipping defaults that look as if they work would produce installations where
search quietly doesn't.

### Values you must set

`config.baseUrl` (the app URL people type), `config.webOrigins`,
`config.s3PublicEndpointUrl`, and the secrets (`secrets.existingSecret` is
recommended). Without them, the deployment fails. Before the chart had
`config.baseUrl`, the app came up with the development default
`http://localhost:5173`, so links in emails and SSO return URLs pointed at
localhost.

The address the app uses to reach storage (`config.s3EndpointUrl`), its keys
(`secrets.s3AccessKey`, `s3SecretKey`), and the first admin
(`seed.adminEmail`, `secrets.seedAdminPassword`) are values too. **If you don't
set the first admin's password, no admin is created.** Email is configured
through `IEUM_SMTP_*` in `config.extra` and `secrets.extra`. The full table is
in the chart's `README.md`.

### A hook Job upgrades the schema

`alembic upgrade head` runs once at `pre-install,pre-upgrade`. As an
initContainer it would run once per pod, all at the same time, racing each
other. Failed Jobs are kept — when a migration fails, its log is the only clue.

This Job reads **hook-only copies** of the config, secrets, and service account
(`-migrate-config`, `-migrate-secrets`). Hooks are created before regular
resources, so while the Job pointed at the chart's main ConfigMap and Secret,
the first `helm install` couldn't start it and failed with a timeout. The copies
are deleted on success. An `existingSecret` already exists, so the Job points at
it directly.

The seed runs at `post-install,post-upgrade`. When a new release defines an
additional permission, the seed adds it to the built-in roles, so it has to run
on `upgrade` too.

### You can run multiple workers

There are three reasons, and all three are in the code: outbox polling uses
`FOR UPDATE SKIP LOCKED`; periodic jobs are arq `unique` crons (the job id is
derived from the scheduled run time, so only one gets queued); and the sweep,
which can overlap right after startup, is idempotent. **The second one is why
digest emails don't go out once per worker.**

We verified this by attaching two workers to the same Redis — the sweep ran on
only one of them.

### Use separate probes

`readinessProbe` uses `/readyz` (checks the dependencies), and `livenessProbe`
uses `/healthz` (checks only the process). **Don't use the same one for both** —
when the database briefly hiccups, Kubernetes keeps restarting pods and gets in
the way of recovery. With them separated, only the traffic is pulled; the pod
stays up and takes traffic again as soon as the database is back.

This behavior has been verified. Cut off storage and `/readyz` returns 503 with
`storage: error`, while `/healthz` keeps returning 200 the whole time.

### Not yet deployed on a real cluster

Verified so far: `helm lint`, manifest validation against a real API server
(`kubectl apply --dry-run=server`), the image's four commands running with a
read-only root filesystem as a non-root user, the probes' actual responses, and
the safety of running multiple worker replicas. **We have not verified that pods
get scheduled and become Ready** (a limitation of nested containers in the
development sandbox). Treat your first real deployment with that in mind.

## 6. Running multiple instances (HA)

You can run multiple API instances. **For that to hold, no process may hold
anything that only it knows** — if one does, the app behaves differently
depending on which pod a person lands on, and that doesn't show up as an error.
It shows up as "I got logged out" or "what I wrote is gone."

What follows is what we verified by **actually running two instances**, and what
we couldn't verify.

### Three rules you must follow

1. **All instances share the same `IEUM_SECRET_KEY`.** This key is used in nine
   places — things that are stored (MFA secrets, IdP client secrets, webhook
   secrets, mail channels, repository integrations) and **things that travel
   between processes** (the sealed OIDC `state`, invitation tokens, CSAT survey
   tokens). If each instance has a different key, the former becomes data that
   "opens on some pods and not on others," and the latter becomes a login that
   "fails whenever the callback lands on a different pod." Both are failures
   that are hard to reproduce. The chart has every pod read one shared Secret,
   and if the value is missing it **fails the deployment** (the point is not to
   generate a random one per pod).
2. **Share Postgres, Redis, and S3.** Those three are the entire state. Give each
   instance its own Redis and concurrent edits silently diverge; give each its
   own S3 and attachments are visible only on the pod that uploaded them.
3. **Don't run migrations per pod.** A hook Job runs them once (section 5).

### Sessions don't need to be sticky (verified)

We started two instances (`:8000`, `:8001`) pointing at the same Postgres and
Redis, then took what we got from one and presented it to the other.

| Action | Result |
|---|---|
| Call `/auth/me` on B with the token from logging in on A | 200, same user |
| List sessions on A and on B | Same list |
| Refresh on B → send the new token to A | 200 (the rotated old token gets 401 on A too) |
| Log out on B → call `/auth/me` on A | 401 |
| After B rotates it, **reuse the old refresh token on A** | A rejects it, **and the session on B is cut off too** |
| Step up on A (MFA verify) → call a step-up-protected endpoint on B | Before verify, 403 on both A and B; after, 200 on both |
| Present an edit ticket issued by A to **B's WebSocket** | Accepted. A second use is rejected (single use) |
| Read a space on B that was created and edited on A | Visible immediately |

The refresh-reuse row is the important one: reuse detection is a **sign of
compromise**, so it cuts off the session, and that cut-off crosses instances.
Had sessions lived in process memory, the other pod would have kept letting the
token through.

Permissions work the same way. They are resolved from the database on every
request, and the cache lives **only within a single request** (`Actor.cached`
in `core/context.py`). So a role revoked on A applies on B from the very next
request — a revocation never takes effect late on some pods.

The only things a process keeps are derived values: settings, translation
catalogs, the IQL grammar, the Markdown dialect, and JWKS (which handles key
rotation on its own). The only module-level mutable state is **two registries
populated at import time**.

### Collaborative editing is bridged through Redis (verified, and two bugs found)

Edit rooms (CRDT state) live in process memory, and processes are bridged
through Redis pub/sub. **Code that looks bridged and code that actually bridges
turned out to be two different things** — as soon as we started two instances,
two problems surfaced, and neither produced an error.

**One: a room created later couldn't see people who were already connected.**
Pub/sub doesn't deliver the past, and presence is broadcast only when it
changes, so to someone connected to the second pod the room looked empty. Now,
when a room is created it sends a **hello** on the channel, and rooms that
receive it reply with the presence they know about.

**Two: a room created later never received other people's unsaved edits.**
Snapshots are saved every 3 seconds, and a room doesn't re-read the snapshot
until it writes one itself. So that person **edited a stale body, believing it
was the current document** — and it stayed that way after 5 seconds, after 10
seconds. Now the hello also carries a **state vector** asking "send me what I'm
missing," and rooms that receive it send the answer back over the channel.

Both are covered by tests that reproduce them **structurally rather than by
timing**, using rooms that never save at all
(`test_a_late_room_gets_edits_that_are_not_saved_yet`). A test that waits 3
seconds flips its meaning on a slow machine.

We also verified it in a browser. Two windows attached to **different API
instances** exchange edits and presence in both directions (the test
`다른 API 인스턴스에 붙어도 같이 편집된다` — "editing works together even when
attached to different API instances" — in `collab.spec.ts`; it runs only when
`E2E_SECOND_ORIGIN` is set).

Two processes saving a snapshot at the same time is safe: a save takes a row
lock and does read-merge-write, so the later writer absorbs what the earlier one
wrote.

### Rolling updates were losing the last edits (fixed)

`RoomRegistry.aclose()` is the function that "closes every room when the app
shuts down and **waits for them to save**," but **nothing called it.** When a
socket disconnects, closing the room is spawned as a task, but nobody awaits
it, so if the event loop is torn down first as the process exits, the last few
seconds of edits vanish. In a rolling update, which takes pods down one at a
time, that isn't an edge case — it happens **every time**.

It is now wired into lifespan shutdown — rooms are closed **before the engine is
disposed** (closing includes saving, and saving uses a session). And if that
final save fails, it is logged: it was the last chance, and if it fails, those
edits now exist nowhere.

A test pins this wiring down by opening and closing the real app's lifespan
(`test_shutting_down_saves_open_rooms`). A test that calls the registry directly
only tells you that **the function exists** — and what was missing was the
wiring.

### What the ingress needs

WebSockets must pass through (`/api/v1/pages/{id}/collab`). Sticky sessions are
**not needed** — a ticket obtained from A works on B. However, if the proxy's
idle timeout is short, connections drop in the middle of editing. When that
happens the client reconnects, and on reconnecting it catches up through the
hello and state vector described above — but people do notice the few seconds in
between.

### What we couldn't verify

- **Pods becoming Ready on a real cluster.** The same limitation as at the end
  of section 5.
- **Three or more instances behind a real load balancer.** We verified two, and
  the properties that showed up with two (no stickiness needed, rooms find each
  other) should hold with three, but **"should be the same" is not "is the
  same."**
- **The ingress's WebSocket configuration.** We verified by connecting directly
  to two origins without going through a proxy.
- **Pods dying abruptly** (SIGKILL, node loss). The fix above covers graceful
  shutdown. On an abrupt death, edits made since the last snapshot are lost — at
  most 3 seconds' worth. Getting that to zero would mean writing on every edit or
  keeping an append-only log in Redis, and both cost more than they are worth
  right now.

## 7. Switching the search backend (OpenSearch)

The default is PGroonga, and it is enough across a wide range of sizes
(ADR-0005: 600,000 documents). **Before switching, check whether you have a
reason to** — unless one of the three below applies, switching is a net loss.

- Indexed documents exceed 1 million
- Search p95 exceeds 800 ms (the request histogram in `/metrics`)
- You need something like similar-document recommendations

### What changes and what doesn't

**Writes don't change.** The index is still written to Postgres's
`search_document` in the same transaction as the source data. OpenSearch is a
read side that mirrors that table (ADR-0015). So:

- If OpenSearch goes down, **saving issues doesn't fail.** Only search goes
  stale.
- Rolling back needs no reindex. Switch back to `postgres` and it is up to date
  that very moment.

**Reads change.** And there are three costs, all verified in practice.

| | Postgres (default) | OpenSearch |
|---|---|---|
| When something just created becomes searchable | **Immediately** (same transaction) | About 4 seconds (5-second mirror cycle + refresh) |
| Korean | Morphological analysis | Two-character bigrams (`cjk`) or `nori` |
| What it depends on | Nothing (lives inside Postgres) | One cluster, plus running it |

The first row is the biggest. "A newly created issue is searchable right away"
is literally the name of a browser test (`방금 만든 이슈가 곧바로 검색된다`), and
on the OpenSearch backend five of those tests go red — not because anything is
broken, but because this promise is different.

### Turning it on

```bash
# 1. Stand up a cluster (this chart and Compose file don't create one).
#    For development: docker compose --profile opensearch up -d opensearch
# 2. Set the configuration.
IEUM_SEARCH_BACKEND=opensearch
IEUM_OPENSEARCH_URL=https://search.internal:9200
# 3. **Reindex once.** Anything created before you turned it on isn't in the mirror queue.
uv run --project apps/api python -m ieum.cli reindex
```

If you skip step 3, **none of the old documents show up in search.** There is no
error — only newly created items show up, so it gets discovered days later as
"I can't find old issues."

Reindexing fills an empty index and then moves the alias over in one step. So
search keeps working while the new index fills, and the old index is deleted
when it's done.

### Korean — the default settings make it worse

OpenSearch does **not ship with** `analysis-nori`. The default, `cjk`, is part
of Lucene and works without a plugin, but it splits Korean into two-character
chunks:

```
배포 절차를 문서로 남긴다  →  배포 · 절차 · 차를 · 문서 · 서로 · 남긴 · 긴다
```

(The sentence means "write the deployment procedure down as a document." Of the
bigrams, `문서` means "document" and `서로` means "each other.")

Recall still works (searching for `문서` finds it). But you get false hits —
someone who searches for `서로` gets this document. PGroonga looks at morphemes,
so it does better here.

If you use an image that includes `analysis-nori`:

```bash
IEUM_OPENSEARCH_ANALYZER=nori
uv run --project apps/api python -m ieum.cli reindex   # the analyzer is applied at index time
```

### What to watch

```promql
# 1. The mirror is falling behind. People run into this as "new documents don't show up in search."
#    Look at age, not count — a hundred items that just arrived is normal.
ieum_search_mirror_oldest_age_seconds > 120

# 2. The mirror job has died. When it does, there is no error; search just gets staler and staler.
time() - ieum_worker_last_run_timestamp_seconds{task="search_mirror"} > 60
ieum_worker_last_failed{task="search_mirror"} == 1
```

`/readyz` checks OpenSearch **only when it is the backend** (`checks.search`).
On an installation that doesn't use it, a red line there would send operators
chasing something irrelevant.

### Rolling back

```bash
IEUM_SEARCH_BACKEND=postgres   # that's all
```

Reindexing and cleaning up the cluster can wait. The Postgres index kept being
updated the whole time — that is exactly why the copy was kept as just a copy.

### Not yet verified

- **Large indexes.** We verified with 5,000 documents. Reindex time at 600,000
  and 1 million documents, and the bulk size (currently 100 documents), have not
  been measured.
- **Multi-node clusters.** Verified on a single node only. The shard/replica
  defaults (1/0) are single-node values, so change them to fit your cluster in a
  real deployment.
- **Clusters with security enabled.** Username and password settings are in
  place, but we haven't run against a cluster that actually requires
  authentication. The same goes for TLS.
