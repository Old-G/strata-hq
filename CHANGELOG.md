# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

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
