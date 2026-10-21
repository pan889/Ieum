---
description: A self-hosted collaboration platform that replaces Jira, Confluence and a service desk with one tool. Issues, wiki pages and tickets share the same users, permissions and search, and your data stays on your own servers.
---

# Ieum (이음)

> *이음* (ieum) — Korean for "a joint": the thing that connects. Ieum joins scattered issues, documents and requests into one.

**A self-hosted collaboration platform that replaces Jira + Confluence + JSM (service desk).**
Your data lives only on your own infrastructure. It works fully without any external SaaS.

## Why three tools in one

When issues, documents and tickets live in separate tools, **the three drift apart.**
Finding the page attached to a ticket means switching windows, permissions are
managed in three places, and you search three times.

Ieum puts all three on **the same users, permissions, search and notifications.**

- **A ticket is a kind of issue** — a request a customer submits through the
  portal shows up as an issue for the agent. SLAs and queues sit on top of that.
- **Pages and issues point at each other** — a page can render an issue list
  with `::issues`, and an issue can find the pages that reference it.
- **Search is a single box** — find issues and pages in one go. Permissions are
  built into the query, so anything you can't see never reaches the results in
  the first place.

## What's included

| | |
|---|---|
| **Issues** | Project hierarchy, custom fields, workflows, time tracking, issue links, bulk edit, recurring issues |
| **Planning** | Kanban boards (swimlanes), sprints and burndown, Gantt, calendar |
| **Wiki** | Spaces, page tree, version comparison, inline comments, templates, macros, **real-time co-editing** |
| **Service Desk** | Customer portal, request types, queues, SLAs and escalation, email channel, KB suggestions, CSAT, approvals, assets (CMDB) |
| **Search** | Unified search (Korean morphological analysis), IQL query language, saved filters, aggregate reports |
| **Administration** | OIDC and SAML SSO, enforced 2FA, SCIM provisioning, roles and permissions, audit log |
| **Integrations** | Webhooks, personal access tokens, app slots, code repository integration |

## Where to start

<div class="grid cards" markdown>

- :material-download: **[Install](install.md)**

    Stand up an instance with `docker compose`. Under 30 minutes.

- :material-book-open-variant: **[User guide](guide/index.md)**

    For newcomers: from first login to your first issue, first page and first ticket.

- :material-server: **[Operations](operations.md)**

    Health checks, metrics, backups, Kubernetes, running multiple instances.

- :material-update: **[Upgrading and rolling back](upgrade.md)**

    Moving to a new version — and going back after you have.

</div>

## Scale and performance targets

| Dimension | Target |
|---|---|
| Data | 500k issues / 100k pages / 500 GB of attachments |
| Response time | Lists p95 400 ms, search p95 800 ms |
| Concurrent users | 300 on a single 4 vCPU / 16 GB node |

These numbers are **targets**. How far they have actually been verified is in the
[release notes](https://github.com/pan889/Ieum/blob/main/CHANGELOG.md) — what was
verified and what wasn't are listed separately.

## The build log

The author keeps a blog about **what broke and why**.

→ **[Peter Pan · Ieum issue tracker (Korean)](https://panbakery.tistory.com/category/%ED%94%84%EB%A1%9C%EA%B7%B8%EB%9E%98%EB%B0%8D/%EC%9D%B4%EC%8A%88%ED%8A%B8%EB%9E%98%EC%BB%A4-%EC%9D%B4%EC%9D%8C%28Ieum%29)**

It isn't a feature tour — it's **a record of failures**. The time a single invite
link was enough to take over an account; the time `WAS NOT` silently hid
unassigned issues; the time one request froze the entire event loop; the features
that were defined and then never switched on. Wherever this manual says "this is
how it works", the blog tells you what went wrong before it got there.

If you're building something similar, that may be more useful to you than this manual.

## License

**AGPL-3.0-only**. Individuals and companies alike are **free to use it and modify
it.** There's no charge for running it in-house.

It asks for just one thing: **if you modify it and offer it as a service to
others, you must publish that source.** A web app gets used by other people even
if you never distribute a copy, and the ordinary GPL can't ask anything of you in
that situation. Section 13 of the AGPL closes that gap.

So **anyone running a modified version must link to their own source in the UI.**
Pass `VITE_SOURCE_URL` when you build the web app, and the link at the bottom of
the sidebar points there instead. If you run it unmodified, you don't need to do
anything.

[Full text](https://github.com/pan889/Ieum/blob/main/LICENSE) ·
[GNU's explanation](https://www.gnu.org/licenses/agpl-3.0.html)

## Korean first

Korean is a first-class language. The UI defaults to Korean (English is fully
supported too), and search uses PGroonga to analyze Korean morphology — search for
`배포` ("deploy") and you'll find pages that say `배포를` ("deploy" with an object
particle attached). Notification messages are written in Korean, not translated
into it.
