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

<1–3 sentences: its role for the projects that depend on it.>

## Stack

<one line>

## Relations

- [<slug>](<slug>.md) — <how>

## Where to read

- <the repo's own CLAUDE.md / README / hub page>
