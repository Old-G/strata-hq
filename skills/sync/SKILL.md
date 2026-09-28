---
name: sync
description: Use when an EXISTING HQ repo (hq.yaml + registry.yaml already there) must catch up with the projects it lists — 'sync HQ', 'refresh the project pages', 'update HQ', 'what changed across my projects', «синхронизируй HQ», «обнови HQ», «обнови страницы проектов», «прогони hq-sync», «что изменилось по проектам». No HQ yet (setting one up, a first wiki over all projects) → hq-init instead. Rebuilds the registry from its declared source, refreshes every page deterministically (unchanged projects are skipped, nothing is rewritten), then writes prose only where the script asks for it. Pure read of member projects.
---

# sync — refresh HQ from its projects

HQ is the meta-layer over many projects (ADR-1). Two scripts do the deterministic work; you write
only the prose they cannot. Never edit a member project from here.

1. **Orient.** The HQ root holds `hq.yaml`, `scripts/hq_registry.py`, `scripts/hq_sync.py`,
   `page-templates/`. Not there → stop and point at `/strata-hq:init`.
2. **Registry.** `python3 scripts/hq_registry.py`. Exit 2 = the source could not be read (e.g. the
   workspace manager is not running): report it and continue with the registry as it is. Exit 3 =
   `hq.yaml` is broken: stop and show the error. Warnings go into your final report.
3. **Pages.** `python3 scripts/hq_sync.py` → JSON with `created`, `updated`, `unchanged`,
   `skipped`, `needs_summary`, `notes`. It owns the frontmatter keys it lists, the
   `<!-- hq-sync:changes -->` block, the `wiki/index.md` block and the `<!-- hq-sync:group -->`
   block of each `groups/<group>.md`; everything else on a page is the owner's and stays. A
   group's hub comes from `hq.yaml` `hubs:`, else from the Strata project above its folders.
4. **Prose — only for `needs_summary`.** For each slug, read cheaply, in this order: the hub page
   (frontmatter `hub` → `<hub>/docs/projects/<name>.md`), the project's `wiki/index.md` head,
   `CLAUDE.md` / `README.md` head, and the page's own changes block. Then write:
   - the one-line title after `# <Name> — `, the summary section (2–4 sentences: what it does,
     for whom, where it runs, its state), the relations section (only projects that are in the
     registry, as `[slug](slug.md)` with how they relate), and `stack` if it is still a placeholder;
   - `relations: [...]` in the frontmatter to match.
   Use the page's own language and headings. Facts you cannot confirm from those sources are
   written as open questions under the gaps section, never guessed.
5. **Index.** Re-run `python3 scripts/hq_sync.py` once so `wiki/index.md` picks up the new titles
   (it rewrites nothing else when the pages did not move).
6. **Commit** in HQ if it is a git repo, by its own rules — one commit for the sync. HQ has no
   remote by default; push only if the owner asked.

**verify:** a second `python3 scripts/hq_sync.py` reports `created: []`, `updated: []`,
`needs_summary: []`; `git status --short` in HQ is clean after the commit; no member project's
`git status` changed.

## Do NOT use when

- A question about ONE project — answer it from that project's wiki (`/strata:wiki-ingest`).
- HQ does not exist yet — `/strata-hq:init`.
- The user wants a weekly report or a status message for people — this refreshes pages only and
  sends nothing.
