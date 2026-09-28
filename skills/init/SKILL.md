---
name: init
description: Use when someone wants ONE place above all their projects and there is no HQ yet — 'set up HQ', 'create a meta-wiki over my projects', 'one wiki for all my repos', «создай HQ», «подними HQ», «сделай общую вики по всем проектам», «одно место для всех проектов». Scaffolds a separate HQ repo from the Strata template (projects stay where they are — ADR-1), asks where the list of projects comes from (explicit paths, or the projects added to Orca), builds the registry and the first pages via hq-sync.
---

# init — scaffold HQ

1. **Ask two things, one at a time:** where HQ lives (default `~/hq`; must not be inside any
   project), and the source of its registry — `paths` (the user lists project folders) or `orca`
   (the projects added to Orca; ask for the CLI name if it is not `orca`). Nothing is moved.
2. **Copy** `${CLAUDE_PLUGIN_ROOT}/template/` into it. Rename `CLAUDE.md.tmpl` → `CLAUDE.md`
   and `gitignore.tmpl` → `.gitignore`. Refuse to overwrite an existing `hq.yaml`.
3. **Write `hq.yaml`** with the chosen `source` (and `paths:` or `orca_cli:`). Optional `short:` —
   slugs whose pages use the short template (projects someone else owns).
4. **`git init`** and a first commit of the skeleton, unless the user said no.
5. **First sync:** run `/strata-hq:sync` — it builds `registry.yaml` and writes a page per project.
6. **Offer the route** (do not write it unasked): three lines in the user's `~/.claude/CLAUDE.md`
   saying that cross-project questions start at `<HQ>/wiki/index.md`.

**verify:** `python3 scripts/hq_registry.py` exits 0 and lists exactly the projects the user named
(or the ones in Orca); `python3 scripts/hq_sync.py` run twice reports nothing created or updated
the second time.

## Do NOT use when

- HQ already exists — `/strata-hq:sync`.
- The user wants Strata inside ONE project — `/strata:adopt` or `/strata:init`.
