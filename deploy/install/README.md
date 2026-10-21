# Single-node install

**English** · [한국어](README.ko.md)

One command brings it up. All you need is Docker and Compose (v2).

```bash
curl -fsSLO https://raw.githubusercontent.com/pan889/Ieum/v1.2.2/deploy/install/install.sh
sh install.sh --url https://ieum.example.com
```

> Rather than piping a script someone handed you straight into a shell, we
> recommend **opening it and reading it once**. It's a little over 400 lines, and
> everything it does is written down. If you've already cloned the repository,
> you can run `deploy/install/install.sh` as is — it picks up the `compose.yml`
> next to it on its own.

It asks for **just one thing: the URL**. Everything else (the secret key, the
database password, the storage keys) is generated on the spot and written to
`.env`.

## What it runs

| | |
|---|---|
| `web` | The UI. **This is the only port exposed to the outside** |
| `api`, `worker` | The app and its background jobs |
| `postgres` | An image with PGroonga built in (Korean search runs on top of it) |
| `redis` | Queues and cache |
| `minio` | Attachment storage |
| `migrate`, `seed`, `minio-init` | One-shot jobs that run once and exit |

Why a single port: when you put TLS in front, there should be **exactly one
place to point it at**. Attachments are served from the same origin too — the
nginx in `web` passes the bucket path (`/ieum-attachments/`) through to storage
as is. Leaving the path untouched matters: a presigned signature covers the path
and the Host header, so if even one character changes, storage answers with 403.

## Putting a proxy in front

```
https://ieum.example.com  →  127.0.0.1:8080
```

Set `IEUM_HTTP_BIND=127.0.0.1` in `.env`, and port 8080 can no longer be reached
directly from outside. `IEUM_PUBLIC_URL` must be **the address people actually
type** (`https://…`) — this value goes as is into cookies, attachment links, and
links in emails.

`IEUM_TRUSTED_PROXY_HOPS` is the number of proxies trusted when reading the
client's IP address. The default is **2**: the nginx in this install bundle plus
one TLS proxy in front of it (if you install with an https URL, `.env` gets 2 as
well). If you serve it directly over plain http with no proxy, change it to 1. If
it's 1 while you're behind a proxy, every client appears to come from the
proxy's single address, and the login-failure limit locks everyone out at once.

## Upgrading

```bash
sh install.sh --version 1.2.2
```

Secrets aren't regenerated. `compose.yml` is **replaced with the one from that
version** — if the images get updated but the configuration stays old, months
later it shows up as baffling symptoms. That's why **`compose.yml` is ours**. If
you need to change something, put a `compose.override.yml` next to it: compose
layers it on top, and the installer never touches that file.

When you run it from the install directory, it **downloads `compose.yml` and
`install.sh` from that version's tag** and swaps them in
(`raw.githubusercontent.com/…/v<version>/deploy/install/`), so the machine needs
to be able to reach that address. If the download fails, it stops without
changing the version in `.env` either — it never leaves you halfway, with a new
version number and an old `compose.yml`.

> Up to 1.2.1, `install.sh` did not replace `compose.yml` when you upgraded from
> the install directory (it printed "이미 이 자리에 있다" — "already in place").
> With that script nothing changes no matter how many times you run it, so
> **download the `install.sh` of the version you're upgrading to first**, then
> run it:
>
> ```bash
> cd <install-dir>
> curl -fsSLO https://raw.githubusercontent.com/pan889/Ieum/v<version>/deploy/install/install.sh
> sh install.sh --version <version>
> ```

## What to back up

| | |
|---|---|
| `.env` | **`IEUM_SECRET_KEY` lives here.** Lose it and MFA and webhook secrets can't be recovered |
| `ieum_pgdata` volume | All of your data |
| `ieum_miniodata` volume | Attachments |

`docker compose down` doesn't delete volumes. `down -v` **does.**

## If you also run from source on the same machine

Both compose projects are named `ieum`. So a `docker compose` run from the
repository **treats this install's containers as its own and removes them.** To
run both on one machine, give one of them a different name:

```bash
COMPOSE_PROJECT_NAME=ieum-dev docker compose up -d   # in the repository checkout
```

## What it doesn't do

- **It doesn't build images.** It pulls the released, version-tagged images
  from the registry — that way you and we run the same bytes. To build and run
  from source, use `docker-compose.yml` at the repository root together with
  `deploy/compose/prod.yml`.
- **It doesn't terminate TLS for you.** Certificates differ from one deployment
  to the next (an internal CA, Let's Encrypt, a load balancer). You're better off
  using whatever already sits in front.
- **It doesn't run a mail server.** Fill in `IEUM_SMTP_*` in `.env`. If you leave
  them empty, the app still starts, but invitation and notification emails don't
  go out.
- **It doesn't scale out.** This setup is for a single node. For multiple nodes,
  see the Helm chart (`deploy/helm/`) and the
  [HA guide in the manual](https://pan889.github.io/Ieum/en/operations/#6-running-multiple-instances-ha).
