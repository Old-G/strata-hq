# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

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
