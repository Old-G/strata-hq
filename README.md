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
| `hq.yaml` | Where the list of projects comes from: `paths` (a list you edit; default) or `orca` (the projects added to [Orca](https://github.com/stablyai/orca), with their groups). Optional `short:` slugs get a short page; optional `hubs:` names a group's hub; optional `trust_ingest_rule:` project paths let the gardener run that project's own ingest rule. |
| `scripts/hq_registry.py` | Builds `registry.yaml` from that source. A project leaves only when a successful read no longer lists it; a source that cannot be read changes nothing. |
| `scripts/hq_sync.py` | Refreshes `projects/<slug>.md`, `groups/<group>.md` and `wiki/index.md` from git (HEAD, branch, remote without credentials, manifests, recent commits). It owns only the marked parts; your text and your own frontmatter keys stay. An unchanged project is not written — a second run rewrites nothing. |
| `/strata-hq:sync` | Runs both scripts, then writes the prose (summary, relations) only for pages the script flags as new or stale. |
| `scripts/hq_garden.py` | Checks every project's default branch for rot: a Strata wiki the code left behind (≥5 commits beyond Strata's own plumbing, the oldest >14 days old), `pending_ingest` markers no ingest retired, `docs/` whose `raw/` mirror is missing or stale, projects with commits in the last 30 days and no wiki, no commits for 90 days. Writes `wiki/garden.md` only when a finding changed. |
| `/strata-hq:garden` | The nightly job: the sync, then the checks, one commit in HQ, a short report. Schedule it with any scheduler (cron, an Orca automation). |
| `scripts/hq_group_chat.py` | Builds `chats/<group>/`: a symlink per project of the group, a CLAUDE.md pointing at the group page, its hub and each project page, and a Claude Code setting that lets an agent there read every project. Open an agent in that folder to talk about the whole group at once. Rebuilt on each run; your own files there stay; not in git. |
| `scripts/hq_brain.py` | The brain's index: one local full-text search over agent chats (Claude Code transcripts, a zstd archive of them, Codex sessions), HQ and project wikis, and any source you plug in as a command printing JSONL. Sources go under `brain:` in `hq.yaml`, the index (SQLite FTS5) outside git. `index` is incremental; `search "<text>" [--before ISO] [--project p] [--json]` takes a question as typed; `eval questions.yaml` measures recall@8 and latency on your own questions. No model, no network, stdlib only; `atoms` adds the model of your choice (a command in `hq.yaml`). |
| `scripts/hq_stem.py` | Snowball stemmers for Russian and English in plain Python, so «ожиданиях» meets «ожидание» and *snippets* meets *snippet*. |
| `/strata-hq:init` | Scaffolds HQ from `template/`, asks for the source, runs the first sync. |

Nothing in HQ ever writes into a member project. Decisions: [`docs/decisions/`](docs/decisions/).

### The brain's index

Unit = one chat message, one markdown section or one command row (long ones split). Two FTS5
columns: stemmed words, and ids kept whole (`DEV-123`, `!19`, file names, `snake_case`, paths).
Tool calls and their output, thinking, hook and system boilerplate are not indexed. A paragraph
already seen in an earlier document of the same `group` counts as a repeat: a message that is
mostly a copy points to the first document and keeps a fraction of its score, and a chat that
opens with a repeated prompt (an automation run) weighs less. A question is searched as typed:
stopwords and filler («слушай», «мы же», «вроде») are dropped, the remaining stems are OR-ed under
BM25, ids match exactly. Score = BM25 × type weight × decay — chat halves every 60 days, errors
every 180, decisions and knowledge never; `--before` hides everything written later and ages from
that moment. Markdown sections are dated by `git blame`, so a section added later is not "known"
before it was written. Results are grouped by document — a chat session (with its aliases), a
file, a row.

A `command` source prints one JSON object per line: `{id, text, ts, doc?, title?, project?, link?,
kind?, role?, source?, aliases?}` — `kind` is `chatter`, `error`, `decision` or `knowledge`;
`aliases` are other keys of the same doc (a provider's session id). `BRAIN_SINCE` tells it what the
last successful run saw; unchanged rows are skipped by hash.

### Atoms

`hq_brain.py atoms --limit N` distills chats with a model you name in `hq.yaml` (`brain_model:`, a
command that reads the prompt on stdin). Only the dialogue goes out — user and assistant text, no
tool output — with emails, phones and keys masked, cut to `brain_atoms_budget:` chars. The answer is
JSON atoms of seven kinds (decision, fact, fix, preference, question, person, correction), each
with a verbatim quote; an atom whose quote is not in the input is dropped. Atoms sit in the same
index and `search` ranks them with the raw messages, pointing to the message they quote.
Decisions, preferences, facts about people and corrections do not decay — they are replaced
(`superseded_by`); open questions halve every 60 days, fixes every 180. Chats listed in
`brain_atoms_skip:` (automation runs) are not sent.

## Develop

```
bash scripts/validate.sh        # manifests, skills, templates, the behaviour suites, nothing private
claude --plugin-dir .           # try the skills against a throwaway HQ
```

Personal words that must never be committed (names, hosts, project names) go one per line into
`.private-markers` (gitignored); `validate.sh` fails if any tracked file contains one.

MIT © Old-G
