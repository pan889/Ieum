# Ieum Helm chart

**English** · [한국어](README.ko.md)

```sh
helm install ieum ./deploy/helm/ieum \
  --set config.baseUrl=https://ieum.example.com \
  --set config.webOrigins=https://ieum.example.com \
  --set config.s3PublicEndpointUrl=https://files.example.com \
  --set config.s3EndpointUrl=http://minio.storage.svc:9000 \
  --set seed.adminEmail=admin@ieum.example.com \
  --set secrets.existingSecret=ieum-secrets
```

## Required values

Without these, **the deployment fails.** That beats quietly coming up with
development defaults.

| Value | Meaning |
|---|---|
| `config.baseUrl` | The app URL people type into their browser. Links in emails and the SSO return URL are built from it |
| `config.webOrigins` | Origins allowed by CORS. Usually the same as `baseUrl` |
| `config.s3PublicEndpointUrl` | The storage URL **the browser** uses to open attachments (presigned URLs) |
| `secrets.existingSecret`, or `secrets.secretKey`, `databaseUrl`, and `redisUrl` | Secrets (see below) |

Back when the chart didn't take `config.baseUrl`, the app came up with the
development default `http://localhost:5173`. The UI opened just fine, but the
links in invitation emails and the return URL handed to the IdP all pointed at
localhost.

## Optional values

| Value | Meaning | If unset |
|---|---|---|
| `config.s3EndpointUrl` | The storage URL **the app** calls (inside the cluster) | The default AWS S3 endpoint |
| `config.s3Bucket`, `config.s3Region` | Bucket and region | `ieum-attachments`, `us-east-1` |
| `secrets.s3AccessKey`, `s3SecretKey` | Storage keys | boto's default credential chain (IRSA, etc.) |
| `seed.adminEmail` | The first admin's email | `admin@example.com` |
| `secrets.seedAdminPassword` | The first admin's password | **No admin is created** |
| `config.extra`, `secrets.extra` | Any other `IEUM_*` variable | The app's defaults |

**Email is configured through `extra`**: `config.extra.IEUM_SMTP_HOST`,
`IEUM_SMTP_PORT`, `IEUM_SMTP_TLS`, `IEUM_SMTP_USER`, and `IEUM_MAIL_FROM`, with
the password in `secrets.extra.IEUM_SMTP_PASSWORD`. Without them the app still
starts, but invitation and notification emails aren't sent. If you also put a
key the chart sets (such as `IEUM_BASE_URL`) in `extra`, **the chart's value
wins.**

If you don't set the first admin's password, the seed logs a single "no
password" line and moves on — an installation that doesn't have SSO yet is left
with no account to sign in with. Once you've signed in and changed the
password, you can remove the value (the seed never touches users who already
exist).

## What this chart doesn't create

**It doesn't create Postgres, Redis, S3, SMTP, or OpenSearch.** It only takes
their addresses, for three reasons.

1. **`helm uninstall` deletes data.** If the chart owned the database, deleting
   the release and deleting the data would be the same command. Those two
   actions don't carry the same weight.
2. **A production database needs its own backups and redundancy.** A chart
   can't provide those, and if it pretends to, people end up without them.
3. **Postgres needs the PGroonga extension** (for Korean full-text search).
   A common Postgres chart won't do, and shipping defaults that look as if they
   work would produce installations where search quietly doesn't.

OpenSearch isn't created for the same reasons. The default search backend is
PGroonga, so **you don't need anything else** — you only provide a cluster
address when you switch `search.backend` to `opensearch`.

To spin up a single instance for development or testing, use `deploy/compose`.

## Secrets

**In production, use `secrets.existingSecret`.**

```sh
kubectl create secret generic ieum-secrets \
  --from-literal=IEUM_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=IEUM_DATABASE_URL='postgresql+asyncpg://…' \
  --from-literal=IEUM_REDIS_URL='redis://…' \
  --from-literal=IEUM_S3_ACCESS_KEY='…' \
  --from-literal=IEUM_S3_SECRET_KEY='…' \
  --from-literal=SEED_ADMIN_PASSWORD="$(openssl rand -base64 18)"
```

The first three are required. The pods start without the two storage keys and
`SEED_ADMIN_PASSWORD` (they're `optional`) — that covers S3 attached through an
IAM role, and installations that already have an admin.

Passing secrets as values, as with `secrets.secretKey`, is also supported, but
it's meant for development. Secrets passed as values stay in the release
history and can be read back with `helm get values` — at that point there's one
more plaintext copy of them inside the cluster.

## A Job applies the schema

A `pre-install,pre-upgrade` hook runs `alembic upgrade head` **once**.

Why not an initContainer: it would run once per pod, all at the same time,
racing each other. Alembic doesn't guard against that, so with bad luck two of
them try to apply the same revision, one fails, and that pod ends up in a crash
loop.

Failed Jobs are **kept** (`migrate.keepFailed`). If they were deleted, you
couldn't see why they failed, and for a failed migration that log is the only
clue you have.

**This Job reads hook-only copies of the config, secrets, and service account**
(`-migrate-config`, `-migrate-secrets`, `-migrate`). Hooks are created before
regular resources, so back when the Job pointed at the chart's `-config` and
`-secrets`, **the first `helm install` couldn't start the Job and failed with a
timeout.** The copies are rendered by the same helpers as the main resources
(so their contents can't drift apart), come up first thanks to a lower hook
weight, and are deleted on success. `existingSecret` already exists, so the Job
references it directly without a copy. Why the main resources weren't turned
into hooks instead is explained in the header comment of
`templates/migrate-job.yaml`.

The seed runs in `post-install,post-upgrade`. It's idempotent, so running it
again is safe, and there's a reason it also runs on `upgrade`: when a new
version defines an additional permission, that permission has to be added to
the built-in roles. Otherwise admins get a 403 on the new screen.

## Running multiple workers is safe

There are three reasons, and all three are in the code.

1. Outbox polling uses `FOR UPDATE SKIP LOCKED` (`core/outbox.py`) — the same
   event is never sent twice.
2. Periodic jobs (sweeps, digests) are arq `unique` crons. The job id is
   derived from the scheduled run time, so when four workers enqueue the same
   job, **only one** lands in the queue. That's why digest emails don't go out
   once per worker.
3. The sweep right after startup may run once per worker (`run_at_startup`).
   The sweep is an idempotent job that runs every 15 seconds, so one extra run
   isn't a problem.

Reason 2 was verified in practice: two workers attached to the same Redis ran
for a minute, and the sweep ran on only one of them.

## Switching the search backend

```yaml
search:
  backend: opensearch
  opensearch:
    url: https://search.internal:9200
```

If you don't provide the URL, **the deployment fails.** Otherwise the app would
come up fine and search alone would quietly return zero results — and zero
results look exactly like "nothing found," so nobody would think anything was
broken.

**After switching, run `ieum reindex` once.** Anything written before you
turned it on isn't in the mirror queue. Skip this step and older content never
shows up in search, with no error anywhere.

Rolling back is just `backend: postgres` — the index has been written to
Postgres the whole time.

## How the web pod finds the API

The nginx config in the web image is a **template**. It takes the upstream for
`/api/` and `/scim/` from `IEUM_API_UPSTREAM`, and the chart sets it to
`<fullname>-api:8000`. The image's default is the compose service name
(`api:8000`), so a compose install doesn't need to set anything — back when that
name was hard-coded, nginx in the Helm web pod couldn't resolve it and the pod
went into a crash loop.

With ingress enabled, the ingress routes `/api` and `/scim` straight to the
API. Without ingress, if you open the app with `port-forward svc/…-web`, the
web pod's nginx does the same job.

## Separate readiness and liveness probes

- `readinessProbe` → `/readyz`. Checks the services the app connects to (DB,
  Redis, S3).
- `livenessProbe` → `/healthz`. Checks only the process.

Don't use the same check for both. Otherwise, when the DB hiccups briefly,
Kubernetes keeps restarting pods and gets in the way of recovery. With the
current setup, the pods are only taken out of traffic and stay alive, then pick
traffic back up as soon as the DB returns.

Workers have no HTTP probe. They don't open a port, and arq's health signal is
a Redis key. A dead worker shows up as a backed-up queue, and those metrics are
already in `/metrics` (`outbox_pending`, `worker_last_run`).

## What has been verified

Here's what has and hasn't been verified with this chart.

Verified:

- `helm lint`, and manifest validation against **a real Kubernetes API server**
  (`kubectl apply --dry-run=server`) — including with ingress enabled.
- Config checks stop the deployment **before** anything starts: app URL, web
  origins, storage public URL, secrets.
- The image's four commands actually run on a **read-only root filesystem as a
  non-root user (uid 10001)**: `alembic upgrade`, `uvicorn`, `arq`,
  `python -m ieum.cli seed`.
- The probes actually respond. Cut off storage and `/readyz` returns 503 with
  `storage: error`; reconnect it and it returns 200 — `/healthz` stays at 200
  throughout.
- With two workers attached, the cron runs only once (see above).

**`--dry-run=server` can't see hook ordering.** It checks that each manifest is
valid on its own, but "a hook Job points at a ConfigMap that doesn't exist yet"
only surfaces on a real install — the migration Job that couldn't start on the
first install (see above) slipped past this check. Now `test_helm_chart.py`
checks the rendered output to make sure **everything a hook points at comes up
before the hook does.**

**Not verified: pods actually getting scheduled and becoming Ready.** In the
sandbox this repository was built in, k3s's runc can't create container
processes (`can't get final child's PID from pipe`). That's a nested-container
limitation, not a problem with the chart — but it doesn't make it verified
either. Until it has been deployed to a real cluster at least once, this chart
stands at "manifests and images checked to match."
