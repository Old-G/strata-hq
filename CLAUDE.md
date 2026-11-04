# strata-hq

Claude Code plugin: HQ, a meta-wiki over many projects (see README.md). Public repo — nothing
personal goes in: no real names, paths, hosts, ids or project names; tests use made-up data
(`validate.sh` §5 + local `.private-markers`).

## Layout

- `.claude-plugin/` — `plugin.json` + `marketplace.json` (this repo is its own marketplace); versions must match.
- `skills/<name>/SKILL.md` — `init`, `sync`, `garden` (invoked as `/strata-hq:<name>`); descriptions carry EN+RU triggers and a `## Do NOT use when`.
- `template/` — what `init` copies into a new HQ, including `scripts/hq_registry.py`, `scripts/hq_sync.py`, `scripts/hq_garden.py`, `scripts/hq_group_chat.py` and the brain's `scripts/hq_brain.py` + `scripts/hq_stem.py` (stdlib only).
- `scripts/` — `validate.sh`, `test_hq_registry.sh`, `test_hq_sync.sh`, `test_hq_garden.sh`, `test_hq_group_chat.sh`, `test_hq_brain.sh`.
- `docs/decisions/` — ADRs.

## Rules

- Scripts stay stdlib-only and read member projects only; nothing writes into a project.
- The sync owns only its marked blocks and listed frontmatter keys; everything else on a page is the owner's.
- Changing a skill description changes routing — check the trigger phrase in a fresh session.
- Bump the version in both manifests and add a CHANGELOG entry for every user-facing change.
- Run `bash scripts/validate.sh` before every commit.
