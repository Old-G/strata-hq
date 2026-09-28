# Strata HQ

One place above all your projects. HQ is a small git repo holding a **meta-wiki**: a page per
project and per group, an index, and a registry of which projects exist — while the projects
themselves stay exactly where they live. Ask a cross-project question in HQ and the answer comes
from HQ → the project's page → its hub → the project's own wiki, with links, instead of grepping
every repo.

A companion to [Strata](https://github.com/Old-G/strata) (a project with a Strata wiki gets the
richest page, and a Strata project can act as a hub), but it works on any folder.

## Install

```
/plugin marketplace add Old-G/strata-hq
/plugin install strata-hq@strata-hq
```

Then say *"set up HQ"* / «создай HQ» — or run `/strata-hq:init`.

## How it works

| Piece | What it does |
|---|---|
| `hq.yaml` | Where the list of projects comes from: `paths` (a list you edit; default) or `orca` (the projects added to [Orca](https://github.com/stablyai/orca), with their groups). Optional `short:` slugs get a short page; optional `hubs:` names a group's hub. |
| `scripts/hq_registry.py` | Builds `registry.yaml` from that source. A project leaves only when a successful read no longer lists it; a source that cannot be read changes nothing. |
| `scripts/hq_sync.py` | Refreshes `projects/<slug>.md`, `groups/<group>.md` and `wiki/index.md` from git (HEAD, branch, remote without credentials, manifests, recent commits). It owns only the marked parts; your text and your own frontmatter keys stay. An unchanged project is not written — a second run rewrites nothing. |
| `/strata-hq:sync` | Runs both scripts, then writes the prose (summary, relations) only for pages the script flags as new or stale. |
| `scripts/hq_garden.py` | Checks every project for rot: a Strata wiki the code left behind (≥5 commits, the oldest >14 days old), `docs/` whose `raw/` mirror is missing or stale, no commits for 90 days. Writes `wiki/garden.md` only when a finding changed. |
| `/strata-hq:garden` | The nightly job: the sync, then the checks, one commit in HQ, a short report. Schedule it with any scheduler (cron, an Orca automation). |
| `/strata-hq:init` | Scaffolds HQ from `template/`, asks for the source, runs the first sync. |

Nothing in HQ ever writes into a member project. Decisions: [`docs/decisions/`](docs/decisions/).

## Develop

```
bash scripts/validate.sh        # manifests, skills, templates, the behaviour suites, nothing private
claude --plugin-dir .           # try the skills against a throwaway HQ
```

Personal words that must never be committed (names, hosts, project names) go one per line into
`.private-markers` (gitignored); `validate.sh` fails if any tracked file contains one.

MIT © Old-G
