---
project: <slug from registry.yaml>
id: <registry id: Orca repo id, or the absolute path>
group: <group or null>
hub: <path of the hub that owns this project's page, or null>
path: <absolute path>
remote: <git remote origin, or null>
branch: <checked-out branch>
stack: <one line: language · framework · stores · deploy>
status: <active — commits in the last 90 days | quiet — none>
strata: <full — WIKI.md + wiki/index.md | claude-md — only CLAUDE.md at the root | none>
synced_sha: <HEAD at the last sync>
synced_at: <UTC ISO time of the last sync>
relations: [<slugs of registered projects this one calls or is called by>]
# The sync owns project, id, group, hub, path, remote, branch, status, strata, synced_*; every other
# key (stack after creation, relations, your own) is yours and is kept.
---

# <Display name> — <what it is in a few words>

## Summary

<2–4 sentences: what it does, for whom, where it runs, what state it is in — in your own words,
from the project's wiki or hub page, never copied wholesale.>

## Stack and entry points

- **Stack:** <from manifests>
- **Entry:** <main entrypoint file(s)>
- **Run / test:** <commands from the repo's own docs>

## Relations

- [<slug>](<slug>.md) — <how: calls / is called by / shares a store / co-tenant>

## What changed

<!-- hq-sync:changes -->
<Filled by the sync: commits since the previous synced_sha, newest first.>
<!-- /hq-sync:end -->

## Gaps

- <Drift found by the sync (the hub says X, the repo says Y) and open gaps from the hub or wiki.>

## Where to read

- Project wiki: <path/wiki/index.md, or "none">
- Hub page: <hub/docs/projects/<name>.md, or "none">
