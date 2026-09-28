---
name: garden
description: Use for the scheduled upkeep of an EXISTING HQ — a nightly job, or when asked 'tend HQ', 'run the gardener', 'which wikis are stale', 'where do docs drift', 'which projects went quiet', «садовник», «прогони садовника», «ночной обход HQ», «какие вики устарели», «где дрейф документации», «какие проекты заброшены». Runs the sync, then checks every project for a wiki the code left behind, docs whose raw/ mirror is stale, and no commits for 90 days, and writes the findings to wiki/garden.md. Pure read of member projects; fixes nothing in them and sends nothing.
---

# garden — nightly upkeep of HQ

The gardener keeps HQ current and says where the projects under it are rotting. It never edits a
member project and never messages anyone: each finding is fixed later in a session opened in that
project (`/strata:wiki-ingest`, `/strata:audit`).

1. **Orient.** The HQ root holds `hq.yaml`, `scripts/hq_registry.py`, `scripts/hq_sync.py`,
   `scripts/hq_garden.py`. No HQ → stop and point at `/strata-hq:init`. `hq_garden.py` missing
   (HQ made by an older version) → copy it from this plugin's `template/scripts/`.
2. **Sync.** Run steps 2–5 of `/strata-hq:sync` (registry, pages, prose for `needs_summary`,
   index). A source that cannot be read is reported and the run goes on with the last registry.
3. **Check.** `python3 scripts/hq_garden.py` → JSON with `stale` (slug → commits behind, the
   last wiki commit, the oldest commit it missed), `drift` (slug → docs without an up-to-date
   `raw/` mirror), `quiet`, `not_checked`, `changed`. It owns `wiki/garden.md` whole and appends
   to `wiki/log.md` only when the report changed.
4. **Commit** in HQ, by its own rules, only if the sync or the gardener changed something — one
   commit, e.g. `chore(garden): stale 2, drift 1, quiet 5`. Push only if the owner asked.
5. **Report** in a few lines: what the sync changed, then the findings that are new since the last
   report (from the commit's diff of `wiki/garden.md`), each with its project. Nothing new → say
   so in one line.

**verify:** a second `python3 scripts/hq_garden.py` reports `"changed": false`; `git status
--short` in HQ is clean; no member project's `git status` or HEAD changed.

## Do NOT use when

- HQ does not exist yet — `/strata-hq:init`.
- Only the pages should be refreshed, without the checks — `/strata-hq:sync`.
- A finding must be fixed — open a session in that project; the gardener only reports.
