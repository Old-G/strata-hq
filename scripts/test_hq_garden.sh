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
# docs→raw mirror: a.md mirrored, b.md edited without its mirror, c.md never mirrored
mkdir -p "$WORK/stale/docs" "$WORK/stale/raw"
printf 'a\n' > "$WORK/stale/docs/a.md"; cp "$WORK/stale/docs/a.md" "$WORK/stale/raw/a.md"
printf 'b2\n' > "$WORK/stale/docs/b.md"; printf 'b1\n' > "$WORK/stale/raw/b.md"
printf 'c\n' > "$WORK/stale/docs/c.md"
G "$WORK/stale" add -A; commit_at "$WORK/stale" 30 "docs: made-up docs"
# docs without any raw/ — a project that does not use the mirror is not drift
mkdir -p "$WORK/nomirror/docs"; printf 'd\n' > "$WORK/nomirror/docs/d.md"
git init -q -b main "$WORK/nomirror"; G "$WORK/nomirror" add -A; commit_at "$WORK/nomirror" 3 "init"
# quiet: last commit four months ago
mkdir -p "$WORK/old"; printf 'o\n' > "$WORK/old/o.txt"
git init -q -b main "$WORK/old"; G "$WORK/old" add -A; commit_at "$WORK/old" 120 "init"
mkdir -p "$WORK/notes"; printf '# notes\n' > "$WORK/notes/todo.md"   # folder, not git

HQ="$WORK/HQ"; cp -R "$ROOT/template" "$HQ"
entry() { printf '  - id: "%s"\n    slug: "%s"\n    name: "%s"\n    path: "%s"\n    kind: "%s"\n    host: "%s"\n' "$@"; }
{ printf 'source: "paths"\nprojects:\n'
  for p in stale fresh few synced nomirror old; do entry "r-$p" "$p" "$p" "$WORK/$p" git local; done
  entry r-notes notes notes "$WORK/notes" folder local
  entry r-gone gone gone "$WORK/gone" git local
  entry r-far far far "/srv/far" git "ssh:made-up-host"; } > "$HQ/registry.yaml"
garden() { python3 "$HQ/scripts/hq_garden.py" --hq "$HQ" "$@"; }
jq_()  { python3 -c 'import json,sys;d=json.load(sys.stdin);v=d;[v:=v[k] for k in sys.argv[1:]];print(v)' "$@"; }
snap() { (cd "$HQ" && find . -type f -not -path './.git/*' -exec cksum {} + | sort); }
members() { for p in stale fresh few synced nomirror old; do G "$WORK/$p" status --porcelain; G "$WORK/$p" rev-parse HEAD; done; }

echo "== hq garden: first run =="
members_before="$(members)"
out="$(garden)"; check "exit 0" "$?" 0
check "stale wiki: ≥5 code commits the wiki never caught up with, the oldest ≥14 days old" "$(printf '%s' "$out" | jq_ stale | python3 -c 'import ast,sys;print(sorted(ast.literal_eval(sys.stdin.read())))')" "['stale']"
check "stale entry counts the commits behind" "$(printf '%s' "$out" | jq_ stale stale behind)" 7
check "doc drift: edited or never-mirrored docs" "$(printf '%s' "$out" | jq_ drift stale)" "['docs/b.md', 'docs/c.md']"
check "no raw/ at all → not a drift" "$(printf '%s' "$out" | jq_ drift | grep -c nomirror)" 0
check "quiet: no commit for 90 days" "$(printf '%s' "$out" | jq_ quiet)" "['old']"
check "missing and remote projects are not guessed" "$(printf '%s' "$out" | jq_ not_checked)" "{'far': 'unverifiable', 'gone': 'missing'}"
check "report written" "$(printf '%s' "$out" | jq_ changed)" True
check "report links the page of each finding" "$(grep -c '](../projects/stale.md)' "$HQ/wiki/garden.md")" 2
check "report names the drifted doc" "$(grep -c 'docs/c.md' "$HQ/wiki/garden.md")" 1
check "a log line for the change" "$(grep -c 'garden → stale 1, drift 1, quiet 1' "$HQ/wiki/log.md")" 1
check "member projects untouched" "$(members)" "$members_before"

echo "== hq garden: nothing changed =="
before="$(snap)"
out="$(garden)"
check "second run reports no change" "$(printf '%s' "$out" | jq_ changed)" False
check "…and rewrites nothing, not even the log" "$(snap)" "$before"

echo "== hq garden: a finding goes away =="
cp "$WORK/stale/docs/b.md" "$WORK/stale/raw/b.md"; cp "$WORK/stale/docs/c.md" "$WORK/stale/raw/c.md"
G "$WORK/stale" add -A; commit_at "$WORK/stale" 0 "docs: mirror"
before="$(snap)"
out="$(garden --dry-run)"
check "dry run sees the drift is gone" "$(printf '%s' "$out" | jq_ drift)" "{}"
check "…but writes nothing" "$(snap)" "$before"
out="$(garden)"
check "real run updates the report" "$(printf '%s' "$out" | jq_ changed) $(grep -c 'docs/c.md' "$HQ/wiki/garden.md")" "True 0"

echo
echo "HQ garden: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
