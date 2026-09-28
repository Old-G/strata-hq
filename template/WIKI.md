# WIKI.md — HQ protocol

HQ's wiki is a *meta-index*: the table of contents of every project's table of contents.

## Sources

| What | Source of truth | Who writes |
|---|---|---|
| Which projects exist | the source in `hq.yaml` | `scripts/hq_registry.py` → `registry.yaml` |
| A project's page | the project's own repo and wiki (read only) | the sync |
| Decisions, ideas, people | the owner | sessions opened in HQ |

## Operations

- **sync** — `python3 scripts/hq_registry.py`, then refresh the pages of changed projects. Exit 2
  means the source was not readable: nothing changed, try again later.
- **query** — `wiki/index.md` first, then the project page, then its hub page (frontmatter `hub` →
  `<hub>/docs/projects/<name>.md`), then the project's wiki. Answer with links to each page used.
- **hub** — a Strata project that owns a set of project pages (`docs/projects/<name>.md`). A project
  belongs to the hub named for its group in `hq.yaml` `hubs:`, else to the nearest Strata project
  above its path. To give a group a hub: point `hubs:` at a Strata project (adopt a folder with
  `/strata:adopt` if there is none).
- **log** — every sync or decision appends one line to `wiki/log.md`:
  `[<UTC ISO time>] <op> <subject> → <what changed>`.
