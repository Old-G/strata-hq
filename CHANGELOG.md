# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

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
