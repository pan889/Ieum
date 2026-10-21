---
description: How to upgrade Ieum to a new version and how to roll back. Version numbering, migrations, and restoring from backup.
---

# Upgrading and rolling back

## Upgrade steps

```bash
git pull
docker compose -f docker-compose.yml -f deploy/compose/prod.yml build
docker compose -f docker-compose.yml -f deploy/compose/prod.yml up -d
docker compose exec api alembic upgrade head
docker compose exec api python -m ieum.cli seed      # idempotent
```

Run `seed` **on every upgrade**. When a new version defines an additional
permission, seeding adds it to the built-in roles; skip it, and new features show
up as 403 even for admins.

On Kubernetes the migration and the seed run automatically as hook Jobs —
`pre-upgrade` runs the migration and `post-upgrade` runs the seed.

## Previewing migrations first

```bash
docker compose exec api alembic upgrade head --sql > migration.sql
```

If a version adds an index to a large table, you'll see it in this file ahead of time.

## Pages being edited during a rolling update

Pages being co-edited live **in process memory** and are saved every 3 seconds.
When a pod shuts down, the app closes the open rooms and **waits for the final
save** — so a graceful shutdown doesn't lose any edits.

A sudden death (SIGKILL, a lost node) loses everything since the last snapshot.
That's at most 3 seconds.

## Rolling back

### Code only

This is for going back to a version without crossing any migrations. Change the
image tag to the previous version and bring it up again — that's it.

### Code and migrations

```bash
docker compose exec api alembic downgrade -1
```

!!! warning "Rolling back a migration that drops a column loses data"
    `downgrade` reverts the schema, but **it can't bring back the values that
    were in that column.** Before upgrading to a version that drops or narrows a
    column, make sure you have a backup.

### Code, migrations and data

Use [the restore procedure in Operations, section 4](operations.md#4-backups).
You need the Postgres dump, the attachment bucket and **`IEUM_SECRET_KEY`**
together for the restored data to be readable.

## Version numbering

`MAJOR.MINOR.PATCH`.

| | |
|---|---|
| PATCH | Fixes only. Migrations, if any, must be reversible |
| MINOR | New features. Migrations are written so they can run alongside the previous version |
| MAJOR | Irreversible changes, renamed settings, dropped support |

The API is versioned at `/api/v1`. A MINOR release may **add fields** but never
**removes them or changes what they mean** — that would break webhook receivers
and scripts that call the API with access tokens.

What each version changed is in the
[CHANGELOG](https://github.com/pan889/Ieum/blob/main/CHANGELOG.md).

### Pre-releases (`-rc1`)

A version with a suffix, like `1.0.0-rc1`, is **a candidate for `1.0.0`**.

- The suffix stays in image tags (`ieum-api:1.0.0-rc1`). If a candidate used the
  final version's name, you couldn't tell what's actually running.
- GitHub marks it as a pre-release.
- `apps/api/pyproject.toml` doesn't carry the suffix — it says `1.0.0`, and the
  release gate checks **the version with the suffix stripped** against that value.
- The release notes reuse the CHANGELOG section of the target version (`1.0.0`).

**Don't run pre-releases in production.** The rollback procedure is the same, but
a candidate is still working its way through this manual's "Not yet verified" items.
