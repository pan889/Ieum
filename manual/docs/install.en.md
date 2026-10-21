---
description: Installing Ieum — stand up an instance with docker compose. Server requirements, the settings you must decide on, and your first admin account, all in under 30 minutes.
---

# Install

!!! warning "The commands to run live in the code repository, not here"
    The steps you need to follow to install are in the code repository's
    [`deploy/install/README.md`](https://github.com/pan889/Ieum/tree/main/deploy/install),
    and **that file is the source of truth.** We don't copy them here — keep two
    copies, and one day only one of them gets fixed.

    This page covers **why things are shaped the way they are**, plus what the
    code repository doesn't cover (operations, multiple instances, managed
    backing services).

It comes up with a single command.

```bash
sh install.sh --url https://ieum.example.com
```

It asks for **one thing: the URL.** That value goes straight into login cookies,
attachment links and the links inside emails. The script handles the rest
(generating secrets, pulling images, the schema, seeding, waiting for health checks).

| | |
|---|---|
| Docker | Compose v2 (`docker compose`, no hyphen) |
| Memory | 4 GB or more. Postgres, Redis, MinIO, the API, the worker and the web app all run together |
| Disk | Attachment storage plus headroom |

For Kubernetes, use the [Helm chart](https://github.com/pan889/Ieum/tree/main/deploy/helm/ieum);
the reasoning behind it is in [Operations, section 5](operations.md).

## Why it asks for only one URL

Because only one port is exposed. The UI, the API and attachments are all served
from the same origin — nginx in the `web` container forwards `/api/` and SCIM
(`/scim/`) to the API, and the bucket path (`/ieum-attachments/`) to storage.

So when you put TLS in front, there's only one place to attach it.

```
https://ieum.example.com  →  127.0.0.1:8080
```

Set `IEUM_HTTP_BIND=127.0.0.1` in `.env`, and port 8080 can't be reached directly
from outside. `IEUM_PUBLIC_URL` must be **the address people type** (`https://…`).

!!! danger "Lose IEUM_SECRET_KEY and there's no way back"
    **The keys that decrypt stored secrets are derived from this key** — 2FA
    secrets, IdP client secrets, webhook secrets, mail channel passwords and
    repository integration tokens. If you lose it, that data can't be recovered.
    Put `.env` in your backups **along with** everything else (a backup without
    the key restores with half of it unreadable).

    If you run multiple instances, **they must all use the same key** — the
    reason is in [Operations, section 6](operations.md).

## Check that it's alive

`/readyz` is not served on the public address — it is an operations endpoint,
and the web container's nginx does not forward it. Ask from inside the container
on the server:

```bash
docker compose exec api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/readyz').read().decode())"
```

```json
{
  "status": "ready",
  "checks": { "database": "ok", "redis": "ok", "storage": "ok" }
}
```

If you get `degraded`, you can see which line is red. `/healthz` only checks the
process, so use it for your load balancer's liveness check, and make traffic
decisions with `/readyz` —
[there's a reason not to use one for both](operations.md#1-health-checks).

## First things to do

1. **Change the password** — the one the script printed is only the first key.
2. **Turn on 2FA** — `Settings → Security`. Don't leave an admin account
   protected by nothing but a password for long. Enforcing it for the whole
   organization is on the same screen.
3. **Turn on mail** — if `IEUM_SMTP_*` in `.env` is empty, invites and
   notifications **are not sent.** The app starts up just fine, so it's easy to
   miss. Fill them in and run `docker compose up -d` again.
4. **Set up backups** — [Operations, section 4](operations.md#4-backups). The
   Postgres volume, the attachment volume, and **`.env`** — all three.

## Upgrading

```bash
cd <install-dir> && ./install.sh --version 1.2.2
```

Secrets are not regenerated. `compose.yml` is **replaced with the one from that
version** — if the images are new but the configuration stays old, months later
it shows up as baffling symptoms. That's why `compose.yml` belongs to the
installer bundle. If you need to change something, put a `compose.override.yml`
next to it. Compose layers it on top, and the script never touches that file.

The script **downloads `compose.yml` and itself (`install.sh`) from that
version's tag** and swaps them in. If it can't download them, it stops and leaves
the version in `.env` unchanged too.

!!! warning "If you upgraded with `install.sh` from 1.2.1 or earlier"
    Scripts from those versions did not replace `compose.yml` when you ran an
    upgrade from the install directory. Your images may be on the new version
    while the configuration is still from the version you first installed.
    **Download the `install.sh` of the version you're upgrading to first** and
    run that, and everything lines up again:

    ```bash
    cd <install-dir>
    curl -fsSLO https://raw.githubusercontent.com/pan889/Ieum/v<version>/deploy/install/install.sh
    sh install.sh --version <version>
    ```

Rolling back is covered in [Upgrading and rolling back](upgrade.md).

## Building and running from source

This is the path if you modify the code and run your own images. Here you have to
supply **three** URLs — exactly the three that the one-line installer collapses
into one.

```bash
git clone https://github.com/pan889/Ieum.git && cd Ieum
cp .env.example .env
make secret          # put the output into IEUM_SECRET_KEY in .env
```

```bash
IEUM_BASE_URL=https://ieum.example.com          # the URL browsers open the app at
IEUM_WEB_ORIGINS=https://ieum.example.com       # CORS. Origins not listed here are blocked
IEUM_S3_PUBLIC_ENDPOINT_URL=https://files.example.com   # the URL browsers open attachments at
IEUM_SMTP_HOST=smtp.example.com                 # mail server. Leave empty to send no mail
```

- **You must change** `IEUM_SMTP_HOST`. The `localhost` in `.env.example` is a
  development value; leave it as is and the container sends mail to itself, so
  invites and notifications reach nobody, with no error. If you won't use mail,
  clear it with `IEUM_SMTP_HOST=`. Port, TLS and credentials are
  `IEUM_SMTP_PORT` (default 587), `IEUM_SMTP_TLS`, `IEUM_SMTP_USER`,
  `IEUM_SMTP_PASSWORD` and `IEUM_MAIL_FROM`.
- Also set `IEUM_DB_PASSWORD`, `IEUM_S3_ACCESS_KEY` and `IEUM_S3_SECRET_KEY`. The
  production override does not inherit the development defaults (`postgres`, `minioadmin`).

- If `IEUM_WEB_ORIGINS` is wrong, every API call the browser makes is blocked at
  the preflight and **the app doesn't load at all.** To a browser, `localhost` and
  `127.0.0.1` are different origins, so if you use both, list both, separated by a comma.
- If `IEUM_S3_PUBLIC_ENDPOINT_URL` is wrong, attachment links **keep being issued
  for an address nobody can open.** The server reaches storage by container name,
  but signed links are opened by the browser.

Without these values, **the app refuses to start.** Not starting at all is better
than quietly starting with defaults.

```bash
docker compose -f docker-compose.yml -f deploy/compose/prod.yml up -d
docker compose exec api alembic -c alembic.ini upgrade head
docker compose exec api python -m ieum.cli seed
```

`seed` is idempotent — running it twice gives the same result. It creates the
admin, the eight built-in roles, the default workflow and the issue types.
**Leave the three `SEED_MFA_ADMIN_*` lines empty** — that's a 2FA admin for
automated tests, and its password and TOTP secret are written in the repository.
Even if you leave them in, the production override doesn't pass those values into
the container, and seeding in production (`IEUM_ENV=production`) doesn't create
that account.

!!! warning "If you ever ran the seed with an older override"
    Some versions of the production override did not clear those three values.
    If you ran the seed in a production container back then, an
    `mfa-admin@example.com` admin exists **with a password and 2FA code that are
    publicly known**. Find it in the user list, deactivate it, and check the
    audit log for logins to that account.

!!! warning "Compose project names collide"
    The one-line installer bundle and the repository's compose both use the
    project name `ieum`. If you run both on one machine, `docker compose` run on
    one side **treats the other side's containers as its own and removes them.**
    Set `COMPOSE_PROJECT_NAME=ieum-dev` on one of them.

## Next

- [User guide](guide/index.md) — your first project, first page, first ticket
- [Upgrading and rolling back](upgrade.md) — moving to a new version
- [Operations](operations.md) — metrics, alerts, backup-restore drills

## Bringing your own backing services

The one-line installer runs Postgres, Redis and MinIO alongside the app. If you
use managed services, you only need to change the addresses, but **Postgres needs
the PGroonga extension** (for Korean full-text search).

```sql
CREATE EXTENSION IF NOT EXISTS pgroonga;
-- citext, ltree and pg_trgm are also required. The full list is in
-- the `postgres-extensions` config in deploy/install/compose.yml.
```

Without PGroonga, search **quietly stops working** — it shows up as zero results,
not as an error, so run one search in Korean right after installing to make sure.

If you send attachments to an external S3, point `IEUM_S3_ENDPOINT_URL` and
`IEUM_S3_PUBLIC_ENDPOINT_URL` at it. Attachments that used to be served from the
same origin are then served from that address, and the bucket proxy inside `web`
simply goes unused.
