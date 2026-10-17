#!/usr/bin/env bash
# Behavioural test for template/scripts/hq_garden.py — the nightly checks over HQ's projects.
# Every repo, file and commit below is MADE UP, created in a throwaway dir.
#
# Usage: bash scripts/test_hq_garden.sh          (exit 0 = all green)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ok()   { echo "  ✓ $1"; pass=$((pass+1)); }
bad()  { echo "  ✗ $1"; fail=$((fail+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export PYTHONDONTWRITEBYTECODE=1
export GIT_AUTHOR_NAME=Fixture GIT_AUTHOR_EMAIL=fixture@example.invalid GIT_COMMITTER_NAME=Fixture GIT_COMMITTER_EMAIL=fixture@example.invalid
G() { git -c core.hooksPath=/dev/null -c commit.gpgsign=false -C "$@"; }
ago() { python3 -c 'import datetime,sys;print((datetime.datetime.now()-datetime.timedelta(days=int(sys.argv[1]))).strftime("%Y-%m-%dT12:00:00"))' "$1"; }
# commit_at <repo> <days ago> <message> — commits whatever is in the tree, dated in the past
commit_at() { GIT_AUTHOR_DATE="$(ago "$2")" GIT_COMMITTER_DATE="$(ago "$2")" G "$1" commit -q -am "$3"; }
strata_repo() {  # a full-Strata repo whose first commit (at <days ago>) touches wiki/
  mkdir -p "$1/wiki"; printf '# wiki\n' > "$1/WIKI.md"; printf '# index\n' > "$1/wiki/index.md"
  printf 'x\n' > "$1/app.ts"; git init -q -b main "$1"; G "$1" add -A; commit_at "$1" "$2" "init"
}
code_commits() {  # code_commits <repo> <count> <days ago>
  for i in $(seq 1 "$2"); do printf '%s\n' "$i" >> "$1/app.ts"; commit_at "$1" "$3" "feat: made-up change $i"; done
}

# made-up projects
strata_repo "$WORK/stale" 60;  code_commits "$WORK/stale" 6 30   # wiki left behind a month ago
strata_repo "$WORK/fresh" 60;  code_commits "$WORK/fresh" 6 1    # busy, but only since yesterday
strata_repo "$WORK/few" 60;    code_commits "$WORK/few" 2 30     # old, but two small commits
strata_repo "$WORK/synced" 60; code_commits "$WORK/synced" 6 30
printf 'more\n' >> "$WORK/synced/wiki/index.md"; commit_at "$WORK/synced" 29 "docs: wiki drift-close"
# only Strata's plumbing moved since the wiki: synced hooks are upkeep, not code the wiki missed
strata_repo "$WORK/upkeep" 60; mkdir -p "$WORK/upkeep/scripts/hooks"
for i in 1 2 3 4 5 6; do printf '%s\n' "$i" >> "$WORK/upkeep/scripts/hooks/gate.sh"; G "$WORK/upkeep" add -A; commit_at "$WORK/upkeep" 30 "chore: sync hooks $i"; done
# the checkout is parked on an old branch; the default branch moved on without the wiki
strata_repo "$WORK/parked" 60; G "$WORK/parked" branch spike; code_commits "$WORK/parked" 6 30; G "$WORK/parked" checkout -q spike
# owed ingests: a.md ingested (in a multi-file clause), b.md never, c.md ingested then edited again
mkdir -p "$WORK/owing/wiki"; printf '# wiki\n' > "$WORK/owing/WIKI.md"; printf '# index\n' > "$WORK/owing/wiki/index.md"
printf '%s\n' '- pending_ingest: docs/a.md (mirrored)' '- pending_ingest: docs/b.md (mirrored)' \
  '- pending_ingest: docs/c.md (mirrored)' 'ingest raw/c.md, raw/a.md -> made-up page' \
  '- pending_ingest: docs/c.md (mirrored again)' '- pending_ingest: all docs await (prose, not a path)' > "$WORK/owing/wiki/log.md"
# the same markers, but the project's own rule (scripts/lib/pending_ingest.sh) narrows the debt
mkdir -p "$WORK/narrow/scripts/lib"; cp -R "$WORK/owing/WIKI.md" "$WORK/owing/wiki" "$WORK/narrow/"
printf '%s\n' 'strata_pending_paths() { echo docs/b.md; }' > "$WORK/narrow/scripts/lib/pending_ingest.sh"
# …and a project nobody trusted ships a rule too: registering a repo must not run its code
mkdir -p "$WORK/stranger/scripts/lib"; cp -R "$WORK/owing/WIKI.md" "$WORK/owing/wiki" "$WORK/stranger/"
printf '%s\n' "strata_pending_paths() { touch '$WORK/stranger-ran'; }" > "$WORK/stranger/scripts/lib/pending_ingest.sh"
for p in owing narrow stranger; do mkdir -p "$WORK/$p/docs"; for d in a b c; do printf '%s\n' "$d" > "$WORK/$p/docs/$d.md"; done; done
rm "$WORK/owing/docs/a.md"   # owing: a.md is gone — nothing left to ingest, not a debt
for p in owing narrow stranger; do G "$WORK/$p" init -q -b main 2>/dev/null || git init -q -b main "$WORK/$p"; G "$WORK/$p" add -A; commit_at "$WORK/$p" 2 "init"; done
# docs→raw mirror: a.md mirrored, b.md edited without its mirror, c.md never mirrored
mkdir -p "$WORK/stale/docs" "$WORK/stale/raw"
printf 'a\n' > "$WORK/stale/docs/a.md"; cp "$WORK/stale/docs/a.md" "$WORK/stale/raw/a.md"
printf 'b2\n' > "$WORK/stale/docs/b.md"; printf 'b1\n' > "$WORK/stale/raw/b.md"
printf 'c\n' > "$WORK/stale/docs/c.md"
printf 'e\n' > "$WORK/stale/docs/e\`x\`[y](z).md"
G "$WORK/stale" add -A; commit_at "$WORK/stale" 30 "docs: made-up docs"
# docs without any raw/ — a project that does not use the mirror is not drift
mkdir -p "$WORK/nomirror/docs"; printf 'd\n' > "$WORK/nomirror/docs/d.md"
git init -q -b main "$WORK/nomirror"; G "$WORK/nomirror" add -A; commit_at "$WORK/nomirror" 3 "init"
# quiet: last commit four months ago
mkdir -p "$WORK/old"; printf 'o\n' > "$WORK/old/o.txt"
git init -q -b main "$WORK/old"; G "$WORK/old" add -A; commit_at "$WORK/old" 120 "init"
mkdir -p "$WORK/empty"; git init -q -b main "$WORK/empty"      # git, but no commit yet
mkdir -p "$WORK/notes"; printf '# notes\n' > "$WORK/notes/todo.md"   # folder, not git

HQ="$WORK/HQ"; cp -R "$ROOT/template" "$HQ"
entry() { printf '  - id: "%s"\n    slug: "%s"\n    name: "%s"\n    path: "%s"\n    kind: "%s"\n    host: "%s"\n' "$@"; }
{ printf 'source: "paths"\nprojects:\n'
  for p in stale fresh few synced nomirror old empty upkeep parked owing narrow stranger; do entry "r-$p" "$p" "$p" "$WORK/$p" git local; done
  entry r-notes notes notes "$WORK/notes" folder local
  entry r-gone gone gone "$WORK/gone" git local
  entry r-far far far "/srv/far" git "ssh:made-up-host"; } > "$HQ/registry.yaml"
printf 'trust_ingest_rule:\n  - "%s"\n  - "stranger"\n' "$WORK/narrow" >> "$HQ/hq.yaml"   # a slug is not a trust
garden() { python3 "$HQ/scripts/hq_garden.py" --hq "$HQ" "$@"; }
jq_()  { python3 -c 'import json,sys;d=json.load(sys.stdin);v=d;[v:=v[k] for k in sys.argv[1:]];print(v)' "$@"; }
snap() { (cd "$HQ" && find . -type f -not -path './.git/*' -exec cksum {} + | sort); }
members() { for p in stale fresh few synced nomirror old upkeep parked owing narrow stranger; do G "$WORK/$p" status --porcelain; G "$WORK/$p" rev-parse HEAD; done; }

echo "== hq garden: first run =="
members_before="$(members)"
out="$(garden)"; check "exit 0" "$?" 0
check "stale wiki: ≥5 code commits the wiki never caught up with, the oldest ≥14 days old; plumbing-only commits are not code; the default branch counts, not the parked checkout" "$(printf '%s' "$out" | jq_ stale | python3 -c 'import ast,sys;print(sorted(ast.literal_eval(sys.stdin.read())))')" "['parked', 'stale']"
check "owed ingests: markers no later ingest retired, a re-edit reopens, prose is not a path, a deleted doc is no debt; a trusted project's own rule wins" "$(printf '%s' "$out" | jq_ owed)" "{'narrow': ['docs/b.md'], 'owing': ['docs/b.md', 'docs/c.md'], 'stranger': ['docs/b.md', 'docs/c.md']}"
check "an untrusted project's rule never runs" "$([ -e "$WORK/stranger-ran" ] && echo ran || echo not-run)" not-run
check "no wiki: a non-Strata repo with recent commits; a quiet one is not listed" "$(printf '%s' "$out" | jq_ nowiki)" "{'nomirror': 1}"
check "report has the new sections" "$(grep -c '^## Ingests owed (3)\|^## No wiki (1)' "$HQ/wiki/garden.md")" 2
check "stale entry counts the commits behind" "$(printf '%s' "$out" | jq_ stale stale behind)" 7
check "doc drift: edited or never-mirrored docs" "$(printf '%s' "$out" | jq_ drift stale)" "['docs/b.md', 'docs/c.md', 'docs/e\`x\`[y](z).md']"
check "no raw/ at all → not a drift" "$(printf '%s' "$out" | jq_ drift | grep -c nomirror)" 0
check "quiet: no commit for 90 days, or none at all" "$(printf '%s' "$out" | jq_ quiet)" "['empty', 'old']"
check "a repo with no commit says so, not 'None'" "$(grep -c 'empty.md) — no commits yet' "$HQ/wiki/garden.md") $(grep -c 'None' "$HQ/wiki/garden.md")" "1 0"
check "missing and remote projects are not guessed" "$(printf '%s' "$out" | jq_ not_checked)" "{'far': 'unverifiable', 'gone': 'missing'}"
check "report written" "$(printf '%s' "$out" | jq_ changed)" True
check "report links the page of each finding" "$(grep -c '](../projects/stale.md)' "$HQ/wiki/garden.md")" 2
check "a file name carries no markup into the report" "$(grep -c 'docs/e?x??y??z?.md' "$HQ/wiki/garden.md") $(grep -c '\[y\](z)' "$HQ/wiki/garden.md")" "1 0"
check "report names the drifted doc" "$(grep -c 'stale.md) — raw/ mirror.*docs/c.md' "$HQ/wiki/garden.md")" 1
check "a log line for the change" "$(grep -c 'garden → stale 2, drift 1, quiet 2, owed 3, no wiki 1' "$HQ/wiki/log.md")" 1
check "member projects untouched" "$(members)" "$members_before"

echo "== hq garden: nothing changed =="
before="$(snap)"
out="$(garden)"
check "second run reports no change" "$(printf '%s' "$out" | jq_ changed)" False
check "…and rewrites nothing, not even the log" "$(snap)" "$before"

echo "== hq garden: a finding goes away =="
cp "$WORK/stale/docs/b.md" "$WORK/stale/raw/b.md"; cp "$WORK/stale/docs/c.md" "$WORK/stale/raw/c.md"; cp "$WORK/stale/docs/e"* "$WORK/stale/raw/"
G "$WORK/stale" add -A; commit_at "$WORK/stale" 0 "docs: mirror"
before="$(snap)"
out="$(garden --dry-run)"
check "dry run sees the drift is gone" "$(printf '%s' "$out" | jq_ drift)" "{}"
check "…but writes nothing" "$(snap)" "$before"
out="$(garden)"
check "real run updates the report" "$(printf '%s' "$out" | jq_ changed) $(grep -c 'stale.md) — raw/ mirror' "$HQ/wiki/garden.md")" "True 0"

echo
echo "HQ garden: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
