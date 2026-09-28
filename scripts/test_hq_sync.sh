#!/usr/bin/env bash
# Behavioural test for template/scripts/hq_sync.py — project pages refreshed from the registry
# (ADR-1). Every repo, file and commit below is MADE UP, created in a throwaway dir.
#
# Usage: bash scripts/test_hq_sync.sh          (exit 0 = all green)

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

# made-up projects: a Next.js app, a FastAPI service under a hub, a plain folder
mkdir -p "$WORK/code/web" "$WORK/hub/wiki" "$WORK/hub/api" "$WORK/code/notes"
printf '# hub\n' > "$WORK/hub/WIKI.md"; printf '# hub index\n' > "$WORK/hub/wiki/index.md"
printf '{"dependencies":{"next":"15","react":"19"}}\n' > "$WORK/code/web/package.json"
printf 'services: {}\n' > "$WORK/code/web/docker-compose.yml"
printf '[project]\ndependencies = ["fastapi"]\n' > "$WORK/hub/api/pyproject.toml"
printf '# notes\n' > "$WORK/code/notes/todo.md"
for r in "$WORK/code/web" "$WORK/hub/api"; do
  git init -q -b main "$r" && G "$r" add -A && G "$r" commit -q -m "init"
done
G "$WORK/code/web" remote add origin https://example.invalid/web.git
G "$WORK/hub/api" remote add origin "https://fixture-user:made-up-token-123@example.invalid/api.git"

HQ="$WORK/HQ"; cp -R "$ROOT/template" "$HQ"
cat > "$HQ/hq.yaml" <<EOF
source: paths
paths:
  - "$WORK/code/web"
  - "$WORK/hub/api"
  - "$WORK/code/notes"
short:
  - "notes"
EOF
reg()  { python3 "$HQ/scripts/hq_registry.py" --hq "$HQ" >/dev/null; }
sync() { python3 "$HQ/scripts/hq_sync.py" --hq "$HQ" "$@"; }
jq_()  { python3 -c 'import json,sys;d=json.load(sys.stdin);v=d;[v:=v[k] for k in sys.argv[1:]];print(v)' "$@"; }
fm()   { sed -n "/^---$/,/^---$/p" "$HQ/projects/$1.md" | grep "^$2:" | head -1 | sed "s/^$2: *//"; }
snap() { (cd "$HQ" && find . -type f -not -path './.git/*' -exec cksum {} + | sort); }

echo "== hq sync: first run =="
reg
out="$(sync)"; check "exit 0" "$?" 0
check "creates a page per project" "$(printf '%s' "$out" | jq_ created)" "['api', 'notes', 'web']"
check "new pages need a summary from the agent" "$(printf '%s' "$out" | jq_ needs_summary)" "['api', 'notes', 'web']"
check "branch, status, strata from the repo" "$(fm web branch) $(fm web status) $(fm web strata)" "main active none"
check "remote read from origin" "$(fm web remote)" "https://example.invalid/web.git"
check "credentials in a remote URL never reach the page" "$(fm api remote) $(grep -c 'made-up-token' "$HQ/projects/api.md")" "https://example.invalid/api.git 0"
check "stack detected from manifests" "$(fm web stack | grep -c 'Next.js')" 1
check "hub = nearest full-Strata ancestor" "$(fm api hub)" "$WORK/hub"
check "no hub → null" "$(fm web hub)" null
check "folder: fingerprint instead of a sha" "$(fm notes synced_sha | cut -c1-3)" "fp:"
check "a slug listed under short: uses the short template" "$(grep -c '^## Stack$' "$HQ/projects/notes.md")" 1
check "a new short page (no changes block by design) raises no note" "$(printf '%s' "$out" | jq_ notes)" "{}"
check "changes block filled" "$(sed -n '/hq-sync:changes/,/hq-sync:end/p' "$HQ/projects/web.md" | grep -c 'init')" 1
check "index lists every project inside its markers" "$(sed -n '/hq-sync:index/,/hq-sync:end/p' "$HQ/wiki/index.md" | grep -c '](../projects/')" 3

echo "== hq sync: nothing changed =="
before="$(snap)"
out="$(sync)"
check "second run: all unchanged" "$(printf '%s' "$out" | jq_ unchanged) $(printf '%s' "$out" | jq_ updated)" "3 []"
check "…and not one file in HQ rewritten (log included)" "$(snap)" "$before"

echo "== hq sync: your text is yours =="
python3 - "$HQ/projects/web.md" <<'PY'
import sys, re
p = sys.argv[1]; t = open(p).read()
t = t.replace("stack: ", "ownership: ai\nstack: ", 1)
t = re.sub(r"^stack:.*$", "stack: hand-written stack", t, count=1, flags=re.M)
t = re.sub(r"(## Summary\n\n).*?(\n\n## )", r"\1A made-up summary written by hand.\2", t, count=1, flags=re.S)
open(p, "w").write(t)
PY
sync >/dev/null
check "own key, hand-written stack and summary survive a sync" "$(fm web ownership) | $(fm web stack) | $(grep -c 'made-up summary' "$HQ/projects/web.md")" "ai | hand-written stack | 1"

echo "== hq sync: a commit in one project =="
before_api="$(cksum < "$HQ/projects/api.md")"; before_notes="$(cksum < "$HQ/projects/notes.md")"
printf 'export const x = 1\n' > "$WORK/code/web/app.ts"; G "$WORK/code/web" add -A; G "$WORK/code/web" commit -q -m "feat: made-up change in code only"
out="$(sync)"
check "only that page is updated" "$(printf '%s' "$out" | jq_ updated) $(printf '%s' "$out" | jq_ unchanged)" "['web'] 2"
check "…the other pages are byte-identical" "$(cksum < "$HQ/projects/api.md") $(cksum < "$HQ/projects/notes.md")" "$before_api $before_notes"
check "…its changes block lists the new commit" "$(sed -n '/hq-sync:changes/,/hq-sync:end/p' "$HQ/projects/web.md" | grep -c 'made-up change')" 1
check "…a code-only change needs no new summary" "$(printf '%s' "$out" | jq_ needs_summary)" "[]"
check "…synced_sha follows HEAD" "$(fm web synced_sha)" "$(G "$WORK/code/web" rev-parse HEAD)"
printf '# web\n' > "$WORK/code/web/README.md"; G "$WORK/code/web" add -A; G "$WORK/code/web" commit -q -m "docs: made-up readme"
out="$(sync)"
check "a README change asks the agent for a new summary" "$(printf '%s' "$out" | jq_ needs_summary)" "['web']"

echo "== hq sync: a project that cannot be read =="
before_notes="$(cksum < "$HQ/projects/notes.md")"
rm -rf "$WORK/code/notes"
out="$(sync)"
check "missing path is skipped with a reason" "$(printf '%s' "$out" | jq_ skipped notes)" missing
check "…its page is left as it was" "$(cksum < "$HQ/projects/notes.md")" "$before_notes"

echo "== hq sync: dry run =="
printf 'more\n' >> "$WORK/code/web/app.ts"; G "$WORK/code/web" commit -q -am "feat: made-up second change"
before="$(snap)"
out="$(sync --dry-run)"
check "dry run reports the update" "$(printf '%s' "$out" | jq_ updated)" "['web']"
check "…but writes nothing" "$(snap)" "$before"

echo "== hq sync: groups and hubs =="
# A registry as the orca source writes it, by hand (made-up groups): two projects under the hub,
# one project whose group names its hub explicitly in hq.yaml.
mkdir -p "$WORK/hub/api2" "$WORK/otherhub/wiki"
printf '# other hub\n' > "$WORK/otherhub/WIKI.md"; printf '# idx\n' > "$WORK/otherhub/wiki/index.md"
printf '{}\n' > "$WORK/hub/api2/package.json"; git init -q -b main "$WORK/hub/api2" && G "$WORK/hub/api2" add -A && G "$WORK/hub/api2" commit -q -m init
HQ2="$WORK/HQ2"; cp -R "$ROOT/template" "$HQ2"
cat > "$HQ2/hq.yaml" <<EOF
source: orca
hubs:
  - group: "Mapped Group"
    path: "$WORK/otherhub"
EOF
entry() { printf '  - id: "%s"\n    slug: "%s"\n    name: "%s"\n    path: "%s"\n    kind: "git"\n    host: "local"\n    group: "%s"\n    group_id: "%s"\n' "$@"; }
{ printf 'source: "orca"\nprojects:\n'
  entry r-api api api "$WORK/hub/api" "Fixture Group" g1
  entry r-api2 api2 api2 "$WORK/hub/api2" "Fixture Group" g1
  entry r-web web web "$WORK/code/web" "Mapped Group" g2; } > "$HQ2/registry.yaml"
out="$(python3 "$HQ2/scripts/hq_sync.py" --hq "$HQ2")"
check "a group page per group" "$(printf '%s' "$out" | jq_ groups_created)" "['fixture-group', 'mapped-group']"
check "a hub named for the group in hq.yaml wins over the path" "$(sed -n '/^---$/,/^---$/p' "$HQ2/projects/web.md" | grep '^hub:' | sed 's/^hub: *//')" "$WORK/otherhub"
check "group page names the shared hub" "$(grep -cF "Hub:** \`$WORK/hub\`" "$HQ2/groups/fixture-group.md")" 1
check "…and lists its projects" "$(grep -c '](../projects/api' "$HQ2/groups/fixture-group.md")" 2
check "explicit hub is marked as set in hq.yaml" "$(grep -c 'set in hq.yaml' "$HQ2/groups/mapped-group.md")" 1
check "index links the group page" "$(grep -cF '[Fixture Group](../groups/fixture-group.md)' "$HQ2/wiki/index.md")" 2
printf 'My own note about this group.\n' >> "$HQ2/groups/fixture-group.md"
before="$(cd "$HQ2" && find . -type f -exec cksum {} + | sort)"
out="$(python3 "$HQ2/scripts/hq_sync.py" --hq "$HQ2")"
check "second run: group pages unchanged, notes kept" "$(printf '%s' "$out" | jq_ groups_updated) $(grep -c 'My own note' "$HQ2/groups/fixture-group.md")" "[] 1"
check "…and nothing in HQ rewritten" "$(cd "$HQ2" && find . -type f -exec cksum {} + | sort)" "$before"

echo
echo "HQ sync: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
