# Deployment (Docker Compose) — **building from source**

**English** · [한국어](README.ko.md)

> If you just want to install Ieum, you're in the wrong place.
> **[`deploy/install/`](../install/)** pulls the released, version-tagged images
> and brings everything up with one command. This directory is for people who
> **modify the source and deploy their own images**.

`docker-compose.yml` (at the repository root) is for development; the files here
are the production overrides.

```bash
docker compose -f docker-compose.yml -f deploy/compose/prod.yml up -d
```

This path **doesn't run migrations for you.** Run this once after it's up:

```bash
docker compose -f docker-compose.yml -f deploy/compose/prod.yml \
    run --rm api alembic -c alembic.ini upgrade head
```

| File | Purpose |
|---|---|
| `postgres-init/01-extensions.sql` | Installs extensions on first start (citext, pg_trgm, ltree, pgroonga) |
| `prod.yml` | Production override: removes the code mounts, turns off reload, sets replicas |

## Values you must set in production

If any of these is missing, `docker compose` stops **before anything starts**.
That beats quietly running on wrong defaults.

| Variable | Meaning | Example |
|---|---|---|
| `IEUM_SECRET_KEY` | The root from which the application's encryption keys are derived | `make secret` |
| `IEUM_BASE_URL` | The app URL people type into their browser. Used for links in emails and as the SSO return address | `https://ieum.example.com` |
| `IEUM_S3_PUBLIC_ENDPOINT_URL` | The storage URL **as the browser sees it** | `https://files.example.com` |
| `IEUM_WEB_ORIGINS` | Web origins the API and storage accept (CORS) | `https://ieum.example.com` |
| `IEUM_DB_PASSWORD` | The Postgres password (the development default `postgres` is not carried over) | `make secret` |
| `IEUM_S3_ACCESS_KEY`, `IEUM_S3_SECRET_KEY` | Keys for the bundled MinIO (`minioadmin` is not carried over) | |
| `IEUM_SMTP_HOST` | The mail server. **To leave it empty, write `IEUM_SMTP_HOST=`** (meaning "don't send mail") | `smtp.example.com` |

Mail also takes `IEUM_SMTP_PORT` (default 587), `IEUM_SMTP_TLS` (default true),
`IEUM_SMTP_USER`, `IEUM_SMTP_PASSWORD`, and `IEUM_MAIL_FROM`. The base file
(`docker-compose.yml`) hard-wires mailpit for development, and for as long as the
production override didn't replace it, every invitation and notification email in
production landed in mailpit's inbox and reached no one — without a single error.
That's why it now **stops before starting if you don't set it.** If you created
`.env` by copying `.env.example`, be sure to change the `IEUM_SMTP_HOST=localhost`
in it.

There are also values the production override **deliberately ignores**:

- `IEUM_API_URL` is pinned to an empty value. This stack closes the api port, so
  the API is served only from the same origin as the UI, and when the value is
  empty the app uses `IEUM_BASE_URL`.
- `SEED_MFA_ADMIN_*` is pinned to empty values. It's a 2FA admin for development
  and E2E tests, so its password and TOTP secret are written in the repository.
  If it carried over into production, running the seed even once would create **a
  2FA admin whose credentials anyone knows**. The seed also refuses to create this
  account when `IEUM_ENV=production`.

> **If you were already running production this way,** check whether a
> `mfa-admin@example.com` account exists. If you ran the seed with the old
> override, it was created — deactivate it and review the audit log.

Why `IEUM_S3_PUBLIC_ENDPOINT_URL` is a separate setting: the browser uploads
attachments **directly** to storage (the bytes never pass through the server). If
presigned URLs are signed with the address seen from inside the containers
(`http://minio:9000`), the browser can't resolve that host. So you give one
address for the server and another for the browser.

You can't change MinIO's CORS with `mc cors set` — MinIO doesn't implement S3's
CORS API. The `MINIO_API_CORS_ALLOW_ORIGIN` environment variable is the only way.

To scale out across several nodes, see the Helm chart (`deploy/helm/ieum/`). For
a single node, Compose is enough — it's this project's first-class deployment
path.
