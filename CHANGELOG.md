# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

## [0.7.0] — 2026-11-05

### Added
- `hq_brain.py brief` — what a new chat of a project should know: recent decisions, the owner's corrections and
  preferences, open questions, error→fix; one line each, a chat never gets its own atoms. For a SessionStart hook.
- `hq_brain.py hint` — for one message, at most 3 atoms (one per chat) as background, for a UserPromptSubmit hook.
  An atom must share rare words with the message, some specific (an id with a digit, a capitalised name, a word
  rare in the raw chats); a message that recalls the past (`brain_recall_cues:`) needs fewer; below
  `brain_hint_min_score:` only a shared specific id keeps it. Skips diffs, code, `brain_hint_skip:` and relayed
  messages. `--once FILE` shows an atom once per chat, `--log FILE` writes ms and atom ids per call.
- `brain_worktree_roots:` — a chat in <root>/<repo folder>/<worktree> counts for the repo's project, also for
  chats indexed before the setting.

### Measured
- 439 real owner messages: a hint on 20 (4.6%), p50 5 ms, p95 49 ms in-process (~100 ms with Python start).
  A second model judged the shown atoms: 18 of 29 on topic (62%), 14 of 20 hints had at least one, 2 misleading.

## [0.6.3] — 2026-11-05

### Added
- `hq_brain.py questions` (doc2query): one model call per chat writes 2–3 searches per atom, indexed with it
  (table `atom_questions`, additive, no schema change).
- The checker looks for more slips: a filled-in object, a relay taken for the author, a feature called a fix,
  an instruction called a person fact; a question atom is checked against the chat's end.

### Measured
- 170 atoms over 60 chats, independent judge: 166 right (98%). Search recall on a 40-question set did not move
  (24/40 typed, 36/40 keywords): atoms need ranking work before they help search.

## [0.6.2] — 2026-11-04

### Added
- A checker: a second model call (`brain_verify_model:`, default `brain_model`) reads every atom next to
  the message its quote is from and keeps, corrects or drops it; no verdict means dropped. It looks for
  the slips a manual review found: done vs doing, an added cause or purpose, a name taken from elsewhere,
  stretched scope, an agent's plan called a decision, a flipped negation. `brain_verify: false` turns it off.
- Code checks before and after the checker: every id, hash and 3+ digit number a claim names must be in
  its message, the owner's message before it or the chat's first message; a preference or correction
  must be the owner's own words (not an agent's, not a relayed message).

### Measured
- On 29 chats re-distilled with an extractor and a stronger checker, an independent judge (a model that
  did not build it) found 101 of 101 atoms right; before the checker a 30-atom sample had 25–26 right.

## [0.6.1] — 2026-11-04

### Changed
- Atoms name who said it: the prompt tells the owner's own words from relayed messages, handoffs and
  task briefs written by another agent or a script; preference and correction come only from the
  owner, and an agent correcting itself is a fix or a fact. A claim says only what its quoted message
  supports. A 30-atom manual check had 5 misses: wrong author, an agent's self-correction typed as the
  owner's, an English claim in a Russian chat, details joined from other messages.
- Agent reports of what was done and checked (fixed, shipped, merged, measured) are kept as fix or fact.

### Added
- `brain_language:` (the claims' language), `brain_owner:` (the owner's name in claims) and
  `brain_relay_markers:` (prefixes of user messages that are not the owner's words; shown as relayed).
- `hq_brain.py atoms --doc ID` (repeatable) re-distills exactly those chats, e.g. a fixed sample
  after a prompt change.

### Fixed
- A model answer that repeats its JSON object no longer fails the chat: the first object is used.

## [0.6.0] — 2026-11-04

### Added
- The brain's atoms (layer 3): `hq_brain.py atoms [--limit N] [--source NAME ...] [--jobs ≤3]
  [--dry-run] [--redo]` picks chats not yet distilled, newest first (skipping automation runs and
  titles/prompts listed in `brain_atoms_skip:`), sends the dialogue only — owner and agent text, no
  tool output, emails / phones / keys masked, cut to `brain_atoms_budget:` chars — to the command
  in `brain_model:` (prompt on stdin; the model is config, not code) and stores typed atoms:
  decision · fact · fix · preference · question · person · correction, each with a claim, a
  verbatim quote, the source message, entities, date, confidence and `superseded_by`.
- Anti-hallucination check: an atom whose quote is not in the input is dropped (a quote in another
  message of the chat is relinked to it); counts of kept / dropped, tokens and seconds per chat are
  kept in `atom_runs` and shown by `stats`.
- `search` and `eval` rank atoms alongside raw units (an atom hit counts for its source chat and
  carries `atom` in `--json`); decay by atom kind; `--no-atoms` turns them off for comparison.
- Tests: a made-up model command, masking, the quote check, relinking, `--before` on atoms.

### Fixed
- hq_brain.py reads a Claude chat row as a message whatever its key order: a user/assistant row
  starting with "type" was skipped as metadata.

## [0.5.0] — 2026-11-04

### Added
- `template/scripts/hq_brain.py` — the brain's index layer: one local full-text search over agent
  chats, notes and wikis, with no model. Sources are declared under `brain:` in `hq.yaml`
  (`claude-transcripts` with an optional `*.jsonl.zst` archive of the same layout,
  `codex-sessions`, `markdown` folders or every registry project's `wiki/`, and `command` — any
  program printing JSONL units); the index is SQLite FTS5 at `brain_index:` (default outside git).
  `index` is incremental (changed files, changed rows); `search` takes a question as typed, with
  `--before`, `--project`, `--limit`, `--json`; `eval questions.yaml` reports strict and lenient
  recall@k for the question and its keywords, and median / p95 latency; `stats` per source.
- `template/scripts/hq_stem.py` — Snowball stemmers for Russian and English in plain Python.
- Ranking: BM25 over stemmed words plus an unstemmed id column; stopwords and filler dropped;
  OR semantics; type weights and decay by type (chat 60 days, errors 180, decisions and knowledge
  never); repeats of an earlier document collapse to a pointer at the first one; chats that open
  with a repeated prompt (automation runs) weigh less; markdown dated by `git blame`.
- `scripts/test_hq_brain.sh` with made-up chats, archive, Codex session, command rows, git-dated
  notes and an eval set; run by `validate.sh`.

## [0.4.1] — 2026-10-18

### Security
- A trusted project's `scripts/lib/pending_ingest.sh` now runs with a bare environment
  (`PATH=/usr/bin:/bin`, `HOME`, locale, `STRATA_WIKI_LOG`) and no stdin, so none of the
  gardener's tokens or keys reach it; a rule that is not a file inside its project (a symlink out
  of it) is not run, and Strata's default rule judges that project instead.

## [0.4.0] — 2026-10-17

### Added
- The gardener reports **ingests owed** — `pending_ingest` markers in a project's `wiki/log.md`
  that no later `ingest raw/…` retired and whose doc still exists (Strata's default rule, read
  without running anything from the project). A project that narrows the rule in its own
  `scripts/lib/pending_ingest.sh` is judged by it only when the owner lists its path under the new
  `hq.yaml` key `trust_ingest_rule:` — registering a repo is not trusting its code, and a path
  (not a slug) keeps that trust from moving to another repo on a registry rebuild.
- The gardener reports **no wiki** — git projects without a Strata wiki that had commits in the
  last 30 days: work landing with no knowledge trace.

### Changed
- Every garden check reads the project's default branch (the newer of the local one and
  origin's), not the branch the checkout happens to be parked on.
- A stale wiki no longer counts commits that only touch Strata's own plumbing (synced hooks,
  the `raw/` mirror, `CLAUDE.md`): a hook sync is upkeep, not code the wiki missed.
- Long doc lists in the report are cut at ten with a count of the rest; file names reach the
  report as inert text (no backticks, links or line breaks from a member repo).

## [0.3.0] — 2026-09-28

### Added
- `template/scripts/hq_group_chat.py` — the chat folder of one group, `chats/<group>/`: a symlink
  per local project, a CLAUDE.md pointing at the group page, its hub and each project page, and
  `.claude/settings.local.json` letting Claude Code read HQ and every project
  (`permissions.additionalDirectories`, the only key it owns there). Found by group id first, so a
  renamed group keeps its chat; rebuilt on each run, a second run rewrites nothing; projects on
  another host are listed, not linked.
- A new HQ ignores `chats/`.

## [0.2.1] — 2026-09-28

### Fixed
- The garden report says "no commits yet" for a repo without a commit instead of "last commit None".

## [0.2.0] — 2026-09-28

### Added
- `/strata-hq:garden` — the nightly upkeep: the sync, then the checks, one commit in HQ.
- `template/scripts/hq_garden.py` — stale Strata wikis (≥5 commits the wiki missed, the oldest
  >14 days old), docs whose `raw/` mirror is missing or stale, projects with no commit for 90
  days; the report `wiki/garden.md` carries no timestamp, so a quiet night rewrites nothing.

### Fixed
- A new HQ ignores `scripts/__pycache__/`, so running the scripts leaves the tree clean.

## [0.1.0] — 2026-09-28

First release — HQ moved out of Strata into its own plugin, so Strata stays small and HQ is
installed only by those who want it.

### Added
- `/strata-hq:init` — scaffold HQ, pick the registry's source, first sync.
- `/strata-hq:sync` — rebuild the registry, refresh project and group pages, write prose only
  where the script asks for it.
- `template/scripts/hq_registry.py` — registry from `paths` or the projects added to Orca.
- `template/scripts/hq_sync.py` — pages from git; owns only marked blocks; unchanged projects
  are not written; credentials in an http(s) remote never reach a page; group pages with their
  hub (`hq.yaml` `hubs:` first, else the Strata project above the folders).
- ADR-1 (from Strata ADR #6): projects are listed where they live, from one declared source.
